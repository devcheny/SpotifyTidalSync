"""Llevar las playlists de iTunes a Spotify y TIDAL, y traerlas de vuelta.

Al reves de lo que hace itunes.py: aqui se parte de una lista local y se busca
cada cancion en el servicio para crear alli la misma lista.

Hay una diferencia grande entre los dos destinos, y conviene saberla:

- **Spotify** tiene busqueda por texto, asi que basta con titulo y artista.
- **TIDAL** no la tiene en su API v2, asi que solo se puede enlazar una cancion
  si se conoce su ISRC. iTunes no lo da, pero muchos ficheros lo llevan en sus
  etiquetas y de ahi se saca con ffprobe. Las que no lo traigan se quedaran
  fuera de TIDAL y se apuntan en el informe.

Cada lista elegida puede ir en un sentido o en los dos:

- **llevar** (iTunes -> fuera): lo de siempre, y el unico sentido posible para
  las listas inteligentes y para TIDAL.
- **traer** (Spotify -> iTunes): lo que hayas anadido en Spotify se busca en la
  biblioteca local y se mete en la misma lista de iTunes. Lo que no tengas se
  queda apuntado en una lista aparte de Spotify, "<lista> - Faltantes en
  iTunes", que la sincronizacion con TIDAL ignora a proposito.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import Config
from .convert import _buscar_ffprobe, _leer_tags, find_ffmpeg
from .http import ApiError
from .itunes import (ITunesError, ITunesLibrary, LibraryIndex, _add_track,
                     _com_items, _is_missing_list)
from .model import Track, normalize
from .spotify import SpotifyClient
from .store import StateStore, TokenStore
from .tidal import TidalClient

SERVICIOS = ("spotify", "tidal")

# Una busqueda fallida no se repite hasta pasado un mes, como en la
# sincronizacion: los catalogos cambian, pero no de un dia para otro.
NEGATIVE_TTL = 30 * 24 * 3600


@dataclass
class PublishStats:
    playlists: int = 0
    creadas: int = 0
    anadidas: int = 0       # canciones que han salido de iTunes
    traidas: int = 0        # canciones que han entrado en iTunes desde Spotify
    faltantes: int = 0      # estan en Spotify pero no en tu biblioteca
    sin_equivalencia: list[tuple[str, str, str]] = field(default_factory=list)
    fallidas: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        texto = (f"Publicadas: {self.playlists} listas | {self.creadas} creadas | "
                 f"{self.anadidas} canciones anadidas | "
                 f"{len(self.sin_equivalencia)} sin equivalencia")
        if self.traidas or self.faltantes:
            texto += (f" | {self.traidas} traidas a iTunes | "
                      f"{self.faltantes} te faltan")
        if self.fallidas:
            texto += f" | {len(self.fallidas)} con error"
        return texto


def publish_playlists(cfg: Config, tokens: TokenStore,
                      log: Callable[[str], None],
                      should_stop: Callable[[], bool] | None = None
                      ) -> PublishStats:
    """Replica en Spotify y TIDAL las playlists de iTunes que hayas elegido."""
    parar = should_stop or (lambda: False)
    stats = PublishStats()

    destinos = [s for s in SERVICIOS
                if cfg.get(f"publish_to_{s}", False) and tokens.has(s)]
    if not destinos:
        raise ApiError("Elige al menos un destino (Spotify o TIDAL) y conecta "
                       "esa cuenta.")

    log("")
    log("== Publicar las listas de iTunes ==")
    if cfg.dry_run:
        log("  (simulacion: no se crea ni se anade nada)")
    log(f"  destinos: {', '.join(destinos)}")

    ffprobe = _buscar_ffprobe(find_ffmpeg(str(cfg.get("ffmpeg_path", ""))) or "")
    if not ffprobe and "tidal" in destinos:
        log("  sin ffprobe no se puede leer el ISRC de los ficheros, y TIDAL "
            "solo sabe buscar por ese codigo: iran muy pocas.")

    clientes: dict[str, Any] = {}
    if "spotify" in destinos:
        clientes["spotify"] = SpotifyClient(cfg, tokens, log)
    if "tidal" in destinos:
        clientes["tidal"] = TidalClient(cfg, tokens, log)

    state = StateStore()
    library = ITunesLibrary(log)
    library.connect()
    try:
        listas = _elegidas(library, cfg, log)
        # El indice de la biblioteca cuesta leerlo entero, asi que solo se hace
        # si de verdad hay algo que traer de Spotify.
        index: LibraryIndex | None = None
        if any(traer for _, _, traer in listas):
            if "spotify" in clientes:
                index = library.index()
            else:
                log("  hay listas marcadas para traer, pero Spotify no esta "
                    "entre los destinos: no se trae nada")

        for playlist, llevar, traer in listas:
            if parar():
                log("  detenido por el usuario")
                break
            nombre = str(playlist.Name)
            canciones = _canciones_de(playlist, ffprobe) if llevar else []
            if llevar and not canciones and not traer:
                log(f"  ~ {nombre}: vacia, se omite")
                continue
            stats.playlists += 1

            if llevar and canciones:
                for servicio, cliente in clientes.items():
                    if parar():
                        break
                    try:
                        _publicar(cliente, servicio, nombre, canciones, cfg,
                                  state, log, stats)
                    except (ApiError, ITunesError) as exc:
                        log(f"  ! {nombre} en {servicio}: {exc}")
                        stats.fallidas.append((f"{nombre} ({servicio})", str(exc)))

            if traer and index is not None and not parar():
                try:
                    _traer(clientes["spotify"], playlist, nombre, cfg, index,
                           library, log, stats)
                except (ApiError, ITunesError) as exc:
                    log(f"  ! traer '{nombre}' de Spotify: {exc}")
                    stats.fallidas.append((f"{nombre} (traer)", str(exc)))
    finally:
        library.close()
        state.save()

    log(f"  {stats.summary()}")
    return stats


def _elegidas(library: ITunesLibrary, cfg: Config,
              log: Callable[[str], None]) -> list[tuple[Any, bool, bool]]:
    """Las playlists marcadas, cada una con su sentido: (lista, llevar, traer).

    Marcar solo 'llevar' la copia hacia fuera, solo 'traer' la rellena desde
    Spotify, y marcar las dos la mantiene igual en los dos sitios.
    """
    llevar = {normalize(n) for n in (cfg.get("publish_playlists") or []) if n}
    traer = {normalize(n) for n in (cfg.get("publish_import") or []) if n}
    querem = llevar | traer
    if not querem:
        log("  no has marcado ninguna playlist de iTunes que publicar")
        return []

    # Las que crea la propia app al traer cosas de TIDAL no se devuelven.
    prefijo = normalize(str(cfg.get("itunes_playlist_prefix", "")))
    elegidas: list[tuple[Any, bool, bool]] = []
    for playlist in library.user_playlists():
        nombre = str(playlist.Name)
        clave = normalize(nombre)
        if clave not in querem or _is_missing_list(nombre):
            continue
        if prefijo and clave.startswith(prefijo):
            continue
        elegidas.append((playlist, clave in llevar, clave in traer))
    log(f"  {len(elegidas)} de {len(querem)} playlists encontradas en iTunes")
    return elegidas


def _canciones_de(playlist: Any, ffprobe: str | None) -> list[Track]:
    """Las canciones de una playlist de iTunes, con su ISRC si el fichero lo trae."""
    out: list[Track] = []
    for com in _com_items(playlist.Tracks):
        try:
            titulo = str(com.Name or "")
            artista = str(com.Artist or "")
            duracion = int(com.Duration or 0)
        except Exception:  # noqa: BLE001
            continue
        if not titulo:
            continue
        out.append(Track(service="itunes", id=str(getattr(com, "TrackDatabaseID", "")),
                         title=titulo, artist=artista,
                         duration_ms=duracion * 1000,
                         isrc=_isrc_de(com, ffprobe)))
    return out


def _isrc_de(com: Any, ffprobe: str | None) -> str:
    """El ISRC guardado en las etiquetas del fichero, si lo tiene."""
    if not ffprobe:
        return ""
    try:
        ruta = str(com.Location or "")
    except Exception:  # noqa: BLE001
        return ""
    if not ruta or not Path(ruta).is_file():
        return ""
    return _leer_tags(ffprobe, Path(ruta)).get("isrc", "").strip().upper()


def _publicar(cliente: Any, servicio: str, nombre: str, canciones: list[Track],
              cfg: Config, state: StateStore, log: Callable[[str], None],
              stats: PublishStats) -> None:
    destino = f"{cfg.get('publish_prefix', '')}{nombre}"
    publica = normalize(nombre) in [normalize(n) for n
                                    in (cfg.get("publish_public") or []) if n]

    existente = _buscar_lista(cliente, servicio, destino)
    if existente is None:
        log(f"  + creando en {servicio}: {destino}"
            + ("  (publica)" if publica else ""))
        existente = cliente.create_playlist(
            destino, "Copia de la lista del mismo nombre en iTunes", publica)
        stats.creadas += 1
    playlist_id = existente.get("id")
    if not playlist_id or playlist_id == "dry-run":
        return

    ya = {t.id for t in cliente.playlist_tracks(str(playlist_id))}
    nuevas: list[str] = []
    for track in canciones:
        encontrada = _resolver(cliente, servicio, track, state)
        if encontrada is None:
            stats.sin_equivalencia.append(
                (f"{servicio} / {nombre}", str(track),
                 "sin ISRC en el fichero" if servicio == "tidal" and not track.isrc
                 else "no esta en el catalogo"))
            continue
        if encontrada not in ya:
            nuevas.append(encontrada)
            ya.add(encontrada)

    if nuevas:
        cliente.add_to_playlist(str(playlist_id), nuevas)
        stats.anadidas += len(nuevas)
    log(f"  ~ {nombre} -> {servicio}: {len(canciones)} en iTunes, "
        f"{len(nuevas)} anadidas")


# --------------------------------------------------------------------------
# El sentido de vuelta: de Spotify a iTunes
# --------------------------------------------------------------------------
def _traer(cliente: Any, playlist: Any, nombre: str, cfg: Config,
           index: LibraryIndex, library: ITunesLibrary,
           log: Callable[[str], None], stats: PublishStats) -> None:
    """Mete en la lista de iTunes lo que hayas anadido en la de Spotify.

    Solo se anade lo que ya tengas en la biblioteca: esto no descarga nada. Lo
    que no tengas se queda apuntado en '<lista> - Faltantes en iTunes'.
    """
    destino = f"{cfg.get('publish_prefix', '')}{nombre}"
    cruda = _buscar_lista(cliente, "spotify", destino)
    if cruda is None:
        log(f"  ~ {nombre}: no hay '{destino}' en Spotify, nada que traer")
        return
    playlist_id = cruda.get("id")
    if not playlist_id or playlist_id == "dry-run":
        return

    de_spotify = cliente.playlist_tracks(str(playlist_id))
    if not de_spotify:
        log(f"  ~ {nombre}: '{destino}' esta vacia en Spotify")
        return

    escribible = library.is_writable(playlist)
    if not escribible:
        log(f"  ~ {nombre}: es una lista inteligente de iTunes y no admite "
            "canciones; solo se apunta lo que te falta")

    actuales = _ids_de(playlist)
    anadidas = 0
    faltan: list[Track] = []
    for track in de_spotify:
        entry = index.find(track)
        if entry is None:
            faltan.append(track)
            stats.sin_equivalencia.append(
                (f"itunes / {nombre}", str(track), index.explain(track)))
            continue
        if entry.db_id in actuales or not escribible:
            continue
        if not cfg.dry_run:
            _add_track(playlist, entry.track, nombre)
        actuales.add(entry.db_id)
        anadidas += 1

    stats.traidas += anadidas
    stats.faltantes += len(faltan)
    log(f"  ~ {nombre} <- spotify: {len(de_spotify)} en Spotify, "
        f"{anadidas} anadidas a iTunes, {len(faltan)} sin encontrar")

    # Tambien con la lista vacia: hay que sacar de ahi lo que ya no falta.
    if cfg.get("publish_missing_playlist", True):
        _publicar_faltantes(cliente, destino, faltan, cfg, log, stats)


def _ids_de(playlist: Any) -> set[int]:
    """Los ids de biblioteca de lo que ya tiene la lista de iTunes."""
    out: set[int] = set()
    for com in _com_items(playlist.Tracks):
        try:
            out.add(int(com.TrackDatabaseID))
        except Exception:  # noqa: BLE001 - entrada ilegible, se ignora
            continue
    return out


def _publicar_faltantes(cliente: Any, destino: str, faltan: list[Track],
                        cfg: Config, log: Callable[[str], None],
                        stats: PublishStats) -> None:
    """Mantiene en Spotify la lista de lo que no tienes en iTunes.

    Se pone al dia en los dos sentidos, para que sea siempre lo que te falta
    AHORA y no un historico. Nunca es publica: es tu lista de la compra.

    Estas listas se quedan en Spotify a proposito y no viajan a TIDAL: la
    sincronizacion las ignora por el nombre (ver sync._playlist_allowed).
    """
    nombre = f"{destino} - Faltantes en iTunes"
    cruda = _buscar_lista(cliente, "spotify", nombre)
    if cruda is None:
        if not faltan:
            return              # nada que apuntar: no se crea una lista vacia
        log(f"  + creando en spotify: {nombre}")
        cruda = cliente.create_playlist(
            nombre, "Canciones de esta lista que no estan en tu iTunes", False)
        stats.creadas += 1
    playlist_id = cruda.get("id")
    if not playlist_id or playlist_id == "dry-run":
        return

    ya = {t.id for t in cliente.playlist_tracks(str(playlist_id))}
    quiero = {t.id for t in faltan}

    nuevas = sorted(quiero - ya)
    if nuevas:
        cliente.add_to_playlist(str(playlist_id), nuevas)
        log(f"    {len(nuevas)} anadidas a '{nombre}'")

    resueltas = sorted(ya - quiero)
    if resueltas:
        try:
            cliente.remove_from_playlist(str(playlist_id), resueltas)
            log(f"    {len(resueltas)} ya no faltan, fuera de '{nombre}'")
        except ApiError as exc:
            # Limpiarla es lo accesorio: lo importante ya esta en iTunes.
            log(f"    no se pudo limpiar '{nombre}': {exc}")


def _buscar_lista(cliente: Any, servicio: str, nombre: str) -> dict[str, Any] | None:
    objetivo = normalize(nombre)
    for cruda in cliente.my_playlists():
        if servicio == "tidal":
            actual = (cruda.get("attributes") or {}).get("name", "")
        else:
            actual = cruda.get("name", "")
        if normalize(str(actual)) == objetivo:
            return cruda
    return None


def _resolver(cliente: Any, servicio: str, track: Track,
              state: StateStore) -> str | None:
    """El id de esa cancion en el servicio, recordando lo ya buscado."""
    cache = state.data.setdefault("publish", {}).setdefault(servicio, {})
    clave = track.text_key
    entrada = cache.get(clave)
    if isinstance(entrada, dict):
        if entrada.get("id"):
            return str(entrada["id"])
        # Que hoy no este no significa que no llegue nunca: los catalogos
        # cambian, asi que pasado un tiempo se vuelve a mirar.
        if time.time() - entrada.get("ts", 0) < NEGATIVE_TTL:
            return None

    encontrada = None
    if track.isrc:
        encontrada = cliente.find_by_isrc(track.isrc)
    if encontrada is None:
        encontrada = cliente.find_by_text(track.title, track.artist)
        if encontrada and encontrada.text_key != track.text_key:
            encontrada = None       # el buscador ha devuelto otra cancion
    cache[clave] = {"id": encontrada.id if encontrada else "", "ts": time.time()}
    return encontrada.id if encontrada else None
