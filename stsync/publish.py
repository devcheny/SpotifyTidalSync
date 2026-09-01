"""Llevar las playlists de iTunes a Spotify y TIDAL.

Al reves de lo que hace itunes.py: aqui se parte de una lista local y se busca
cada cancion en el servicio para crear alli la misma lista.

Hay una diferencia grande entre los dos destinos, y conviene saberla:

- **Spotify** tiene busqueda por texto, asi que basta con titulo y artista.
- **TIDAL** no la tiene en su API v2, asi que solo se puede enlazar una cancion
  si se conoce su ISRC. iTunes no lo da, pero muchos ficheros lo llevan en sus
  etiquetas y de ahi se saca con ffprobe. Las que no lo traigan se quedaran
  fuera de TIDAL y se apuntan en el informe.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import Config
from .convert import _buscar_ffprobe, _leer_tags, find_ffmpeg
from .http import ApiError
from .itunes import ITunesError, ITunesLibrary, _com_items
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
    anadidas: int = 0
    sin_equivalencia: list[tuple[str, str, str]] = field(default_factory=list)
    fallidas: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        texto = (f"Publicadas: {self.playlists} listas | {self.creadas} creadas | "
                 f"{self.anadidas} canciones anadidas | "
                 f"{len(self.sin_equivalencia)} sin equivalencia")
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
        for playlist in listas:
            if parar():
                log("  detenido por el usuario")
                break
            nombre = str(playlist.Name)
            canciones = _canciones_de(playlist, ffprobe)
            if not canciones:
                log(f"  ~ {nombre}: vacia, se omite")
                continue
            for servicio, cliente in clientes.items():
                if parar():
                    break
                try:
                    _publicar(cliente, servicio, nombre, canciones, cfg, state,
                              log, stats)
                except (ApiError, ITunesError) as exc:
                    log(f"  ! {nombre} en {servicio}: {exc}")
                    stats.fallidas.append((f"{nombre} ({servicio})", str(exc)))
    finally:
        library.close()
        state.save()

    log(f"  {stats.summary()}")
    return stats


def _elegidas(library: ITunesLibrary, cfg: Config,
              log: Callable[[str], None]) -> list[Any]:
    """Las playlists de iTunes marcadas; si no hay ninguna marcada, ninguna."""
    querem = [normalize(n) for n in (cfg.get("publish_playlists") or []) if n]
    todas = library.user_playlists()
    if not querem:
        log("  no has marcado ninguna playlist de iTunes que publicar")
        return []
    # Las que crea la propia app al traer cosas de TIDAL no se devuelven.
    prefijo = normalize(str(cfg.get("itunes_playlist_prefix", "")))
    elegidas = [p for p in todas
                if normalize(str(p.Name)) in querem
                and not (prefijo and normalize(str(p.Name)).startswith(prefijo))]
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

    stats.playlists += 1
    if nuevas:
        cliente.add_to_playlist(str(playlist_id), nuevas)
        stats.anadidas += len(nuevas)
    log(f"  ~ {nombre} -> {servicio}: {len(canciones)} en iTunes, "
        f"{len(nuevas)} anadidas")


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
