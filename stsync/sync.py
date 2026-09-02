"""Motor de sincronizacion entre Spotify y TIDAL.

La identidad de una cancion es su ISRC (el codigo estandar de la industria);
si falta, se cae a una firma normalizada "artista|titulo". Eso evita duplicados
cuando las dos plataformas nombran la misma cancion de forma distinta.
"""
from __future__ import annotations

import csv
import datetime as dt
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import Config
from .convert import ConvertError, FlacConverter
from .http import ApiError
from .itunes import ITunesError, ITunesSync, _is_missing_list
from .model import (DURATION_TOLERANCE_S, Track, normalize, same_recording)
from .paths import reports_dir
from .spotify import SpotifyClient
from .store import StateStore, TokenStore
from .tidal import TidalClient

# Reintentar una busqueda fallida solo cada 30 dias (evita gastar cuota).
NEGATIVE_TTL = 30 * 24 * 3600


@dataclass
class Stats:
    added_to_spotify: int = 0
    added_to_tidal: int = 0
    removed_from_spotify: int = 0
    removed_from_tidal: int = 0
    playlists_created: int = 0
    added_to_itunes: int = 0
    itunes_playlists: int = 0
    flac_converted: int = 0
    unmatched: list[tuple[str, str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"Anadidas a Spotify: {self.added_to_spotify}",
            f"Anadidas a TIDAL: {self.added_to_tidal}",
            f"Quitadas de Spotify: {self.removed_from_spotify}",
            f"Quitadas de TIDAL: {self.removed_from_tidal}",
            f"Playlists creadas: {self.playlists_created}",
        ]
        if self.itunes_playlists or self.added_to_itunes:
            parts.append(f"Anadidas a iTunes: {self.added_to_itunes} "
                         f"({self.itunes_playlists} playlists)")
        if self.flac_converted:
            parts.append(f"FLAC convertidos: {self.flac_converted}")
        parts.append(f"Sin equivalencia: {len(self.unmatched)}")
        return " | ".join(parts)


class SyncEngine:
    def __init__(self, cfg: Config, log: Callable[[str], None] | None = None,
                 should_stop: Callable[[], bool] | None = None) -> None:
        self.cfg = cfg
        self.log = log or (lambda msg: print(msg))
        self.should_stop = should_stop or (lambda: False)
        self.tokens = TokenStore()
        self.state = StateStore()
        self.spotify = SpotifyClient(cfg, self.tokens, self.log)
        self.tidal = TidalClient(cfg, self.tokens, self.log)
        self.stats = Stats()

    # ---------------------------------------------------------------- publico
    def run(self) -> Stats:
        started = time.time()
        self.log("=" * 62)
        self.log(f"Sincronizacion iniciada {dt.datetime.now():%Y-%m-%d %H:%M:%S}")
        if self.cfg.dry_run:
            self.log("MODO SIMULACION: no se escribira nada en las cuentas")

        if not self.tokens.has("spotify") or not self.tokens.has("tidal"):
            raise ApiError("Faltan cuentas por conectar. Abre la interfaz y "
                           "conecta Spotify y TIDAL.")

        self.log(f"Spotify: {self.spotify.display_name}")
        self.log(f"TIDAL:   {self.tidal.display_name}")

        if self.cfg.sync_favorites:
            self._sync_favorites()
        if self.cfg.sync_playlists and not self.should_stop():
            self._sync_playlists()

        # Y detras, lo que este marcado en la cola. Van en este orden a
        # proposito: primero lo que trae canciones nuevas, luego lo que las
        # convierte, luego lo que las arregla y al final lo que le cuenta a
        # iTunes que han cambiado.
        for paso in PASOS:
            if self.should_stop():
                break
            if self.cfg.get(paso.clave):
                self._encadenado(paso)

        self.state.last_sync = dt.datetime.now().isoformat(timespec="seconds")
        self.state.data["last_summary"] = self.stats.summary()
        self.state.save()

        self._write_unmatched_report()
        self.log("-" * 62)
        self.log(self.stats.summary())
        self.log(f"Terminado en {time.time() - started:.1f}s")
        return self.stats

    def run_itunes(self, only_playlist: str | None = None) -> Stats:
        """Solo el volcado TIDAL -> iTunes, sin tocar Spotify."""
        started = time.time()
        self.log("=" * 62)
        self.log(f"Volcado a iTunes iniciado {dt.datetime.now():%Y-%m-%d %H:%M:%S}")
        if self.cfg.dry_run:
            self.log("MODO SIMULACION: no se escribira nada")

        if not self.tokens.has("tidal"):
            raise ApiError("Conecta TIDAL antes de sincronizar con iTunes.")
        self.log(f"TIDAL: {self.tidal.display_name}")

        self._sync_itunes(only_playlist)

        self.state.data["last_itunes_sync"] = (
            dt.datetime.now().isoformat(timespec="seconds"))
        self.state.save()

        self._write_unmatched_report()
        self.log("-" * 62)
        self.log(f"Terminado en {time.time() - started:.1f}s")
        return self.stats

    # ----------------------------------------------------------------- iTunes
    def _sync_itunes(self, only_playlist: str | None = None) -> None:
        engine = ITunesSync(self.cfg, self.tidal, self.log, self.should_stop)
        try:
            result = engine.run(only_playlist)
        except ITunesError as exc:
            message = f"iTunes: {exc}"
            self.log(f"  ! {message}")
            self.stats.errors.append(message)
            return

        self.stats.added_to_itunes += result.added
        self.stats.itunes_playlists += result.playlists
        self.stats.errors.extend(f"playlist '{nombre}': {motivo}"
                                 for nombre, motivo in result.failed)
        # Van al mismo informe CSV que las que no tienen equivalencia online.
        self.stats.unmatched.extend(
            (f"itunes / {playlist}", song, motivo)
            for playlist, song, motivo in result.missing)
        self.log(f"  {result.summary()}")

    # ------------------------------------------------------- cola de la sync
    def _encadenado(self, paso: "Paso") -> None:
        """Ejecuta un paso de la cola sin que un fallo suyo pare los demas.

        Cada uno es independiente: que iTunes este cerrado no puede impedir
        que se conviertan los FLAC, ni al reves. Lo que falle se apunta en el
        resumen y se sigue.
        """
        self.log("")
        try:
            resumen = paso.hacer(self)
        except (ApiError, ITunesError, ConvertError, OSError) as exc:
            mensaje = f"{paso.nombre}: {exc}"
            self.log(f"  ! {mensaje}")
            self.stats.errors.append(mensaje)
            return
        except Exception as exc:  # noqa: BLE001 - un paso no tumba la cola
            mensaje = f"{paso.nombre}: error inesperado: {exc}"
            self.log(f"  ! {mensaje}")
            self.stats.errors.append(mensaje)
            return
        if resumen:
            self.log(f"  {resumen}")

    def _pasada(self, funcion: Callable[..., Any]) -> str:
        """Lanza una de las pasadas que recorren la biblioteca entera.

        Todas tienen la misma forma -cfg, log, parar- y devuelven algo que
        sabe resumirse, asi que aqui no hay nada especifico de ninguna.
        """
        return str(funcion(self.cfg, self.log, self.should_stop).summary())

    def _cliente_de_artistas(self) -> Any:
        """De donde salen los interpretes de una grabacion, por su ISRC.

        Spotify si esta conectado, que responde a mas ISRC; si no, TIDAL.
        """
        if self.tokens.has("spotify"):
            return self.spotify
        return self.tidal

    # ------------------------------------------------------------------- FLAC
    def _convert_flac(self) -> None:
        """Ultimo paso de la cola: pasar a ALAC lo que iTunes no pudo leer."""
        try:
            result = FlacConverter(self.cfg, self.log, self.should_stop).run()
        except ConvertError as exc:
            message = f"FLAC a ALAC: {exc}"
            self.log(f"  ! {message}")
            self.stats.errors.append(message)
            return

        self.stats.flac_converted += result.converted
        self.stats.errors.extend(f"FLAC '{name}': {reason}"
                                 for name, reason in result.failed)
        self.log(f"  {result.summary()}")

    # -------------------------------------------------------------- favoritos
    def _sync_favorites(self) -> None:
        self.log("")
        self.log("== Favoritos ==")
        sp_tracks = self.spotify.saved_tracks()
        td_tracks = self.tidal.favorite_tracks()

        plan = self._plan(sp_tracks, td_tracks, "fav")
        self._apply(
            plan,
            add_spotify=self.spotify.add_saved,
            add_tidal=self.tidal.add_favorites,
            remove_spotify=self.spotify.remove_saved,
            remove_tidal=self.tidal.remove_favorites,
            label="favoritos",
        )

    # -------------------------------------------------------------- playlists
    def _sync_playlists(self) -> None:
        self.log("")
        self.log("== Playlists ==")
        sp_lists = {_pl_key(p.get("name", "")): p for p in self.spotify.my_playlists()}
        td_raw = self.tidal.my_playlists()
        td_lists = {
            _pl_key((p.get("attributes") or {}).get("name", "")): p for p in td_raw
        }
        self.log(f"  Spotify: {len(sp_lists)} playlists propias | "
                 f"TIDAL: {len(td_lists)} playlists propias")

        names = set(sp_lists) | set(td_lists)
        for name in sorted(names):
            if self.should_stop():
                return
            sp_pl, td_pl = sp_lists.get(name), td_lists.get(name)
            display = (sp_pl or {}).get("name") or \
                      ((td_pl or {}).get("attributes") or {}).get("name") or name
            if not self._playlist_allowed(name, display):
                continue

            try:
                self._sync_one_playlist(display, name, sp_pl, td_pl)
            except ApiError as exc:
                msg = f"  ! playlist '{display}': {exc}"
                self.log(msg)
                self.stats.errors.append(msg)

    def _sync_one_playlist(self, display: str, key: str,
                           sp_pl: dict[str, Any] | None,
                           td_pl: dict[str, Any] | None) -> None:
        direction = self.cfg.direction
        target_name = f"{self.cfg.playlist_prefix}{display}"

        # Crear la playlist que falte en el otro lado.
        if sp_pl and not td_pl:
            if direction == "tidal_to_spotify":
                return
            self.log(f"  + creando en TIDAL: {target_name}")
            td_pl = self.tidal.create_playlist(target_name, "Sincronizada desde Spotify")
            self.stats.playlists_created += 1
        elif td_pl and not sp_pl:
            if direction == "spotify_to_tidal":
                return
            self.log(f"  + creando en Spotify: {target_name}")
            sp_pl = self.spotify.create_playlist(target_name, "Sincronizada desde TIDAL")
            self.stats.playlists_created += 1

        if not sp_pl or not td_pl:
            return
        sp_id, td_id = sp_pl.get("id"), td_pl.get("id")
        if not sp_id or not td_id or sp_id == "dry-run" or td_id == "dry-run":
            return

        sp_tracks = self.spotify.playlist_tracks(sp_id)
        td_tracks = self.tidal.playlist_tracks(td_id)
        plan = self._plan(sp_tracks, td_tracks, f"pl:{key}")
        if not (plan.to_spotify or plan.to_tidal or
                plan.remove_spotify or plan.remove_tidal):
            return

        self.log(f"  ~ {display}: Spotify {len(sp_tracks)} / TIDAL {len(td_tracks)}")
        self._apply(
            plan,
            add_spotify=lambda ids: self.spotify.add_to_playlist(sp_id, ids),
            add_tidal=lambda ids: self.tidal.add_to_playlist(td_id, ids),
            remove_spotify=lambda ids: self.spotify.remove_from_playlist(sp_id, ids),
            remove_tidal=lambda ids: self.tidal.remove_from_playlist(td_id, ids),
            label=display,
        )

    def _playlist_allowed(self, key: str, display: str = "") -> bool:
        # Las listas de "lo que me falta" son un apunte para ti, no musica que
        # replicar: se quedan donde nacen y no cruzan a la otra plataforma.
        if display and _is_missing_list(display):
            return False
        include = [_pl_key(n) for n in self.cfg.get("playlist_include", []) or []]
        exclude = [_pl_key(n) for n in self.cfg.get("playlist_exclude", []) or []]
        if include and key not in include:
            return False
        return key not in exclude

    # ------------------------------------------------------------ diferencias
    def _plan(self, sp_tracks: list[Track], td_tracks: list[Track],
              state_key: str) -> "Plan":
        sp_index = {t.key: t for t in sp_tracks}
        td_index = {t.key: t for t in td_tracks}

        prev_sp = self.state.snapshot(f"{state_key}:spotify")
        prev_td = self.state.snapshot(f"{state_key}:tidal")

        gone_sp: set[str] = set()
        gone_td: set[str] = set()
        if self.cfg.propagate_deletions and (prev_sp or prev_td):
            gone_sp = prev_sp - set(sp_index)   # borradas en Spotify
            gone_td = prev_td - set(td_index)   # borradas en TIDAL

        direction = self.cfg.direction
        to_tidal, to_spotify = set(), set()
        if direction in ("both", "spotify_to_tidal"):
            to_tidal = set(sp_index) - set(td_index) - gone_td
        if direction in ("both", "tidal_to_spotify"):
            to_spotify = set(td_index) - set(sp_index) - gone_sp

        return Plan(
            sp_index=sp_index,
            td_index=td_index,
            to_spotify=to_spotify,
            to_tidal=to_tidal,
            remove_spotify=gone_td & set(sp_index) if direction != "spotify_to_tidal" else set(),
            remove_tidal=gone_sp & set(td_index) if direction != "tidal_to_spotify" else set(),
            state_key=state_key,
        )

    # -------------------------------------------------------------- ejecucion
    def _apply(self, plan: "Plan", add_spotify: Callable[[list[str]], None],
               add_tidal: Callable[[list[str]], None],
               remove_spotify: Callable[[list[str]], None],
               remove_tidal: Callable[[list[str]], None], label: str) -> None:
        # --- borrados (primero, para no re-anadir lo que se acaba de quitar) --
        if plan.remove_spotify:
            ids = [plan.sp_index[k].id for k in plan.remove_spotify]
            self.log(f"  - quitando {len(ids)} de Spotify ({label})")
            remove_spotify(ids)
            self.stats.removed_from_spotify += len(ids)
        if plan.remove_tidal:
            ids = [plan.td_index[k].id for k in plan.remove_tidal]
            self.log(f"  - quitando {len(ids)} de TIDAL ({label})")
            remove_tidal(ids)
            self.stats.removed_from_tidal += len(ids)

        # --- altas -----------------------------------------------------------
        added_td_keys: set[str] = set()
        if plan.to_tidal:
            self.log(f"  > buscando {len(plan.to_tidal)} canciones en TIDAL...")
            ids, matched = self._resolve(
                [plan.sp_index[k] for k in sorted(plan.to_tidal)], self.tidal)
            if ids:
                add_tidal(ids)
                self.stats.added_to_tidal += len(ids)
                self.log(f"  + {len(ids)} anadidas a TIDAL ({label})")
            added_td_keys = matched

        added_sp_keys: set[str] = set()
        if plan.to_spotify:
            self.log(f"  > buscando {len(plan.to_spotify)} canciones en Spotify...")
            ids, matched = self._resolve(
                [plan.td_index[k] for k in sorted(plan.to_spotify)], self.spotify)
            if ids:
                add_spotify(ids)
                self.stats.added_to_spotify += len(ids)
                self.log(f"  + {len(ids)} anadidas a Spotify ({label})")
            added_sp_keys = matched

        # --- nuevo snapshot ---------------------------------------------------
        final_sp = (set(plan.sp_index) - plan.remove_spotify) | added_sp_keys
        final_td = (set(plan.td_index) - plan.remove_tidal) | added_td_keys
        self.state.set_snapshot(f"{plan.state_key}:spotify", final_sp)
        self.state.set_snapshot(f"{plan.state_key}:tidal", final_td)
        self.state.save()

    # ------------------------------------------------------------ equivalencias
    def _resolve(self, tracks: list[Track],
                 target: SpotifyClient | TidalClient) -> tuple[list[str], set[str]]:
        """Devuelve (ids en el destino, claves resueltas)."""
        service = "tidal" if isinstance(target, TidalClient) else "spotify"
        cache: dict[str, Any] = self.state.data.setdefault("resolve", {}) \
                                               .setdefault(service, {})
        comprobar = bool(self.cfg.get("match_check_duration", True))
        tolerancia = float(self.cfg.get("match_duration_tolerance",
                                        DURATION_TOLERANCE_S))
        ids: list[str] = []
        matched: set[str] = set()

        for i, track in enumerate(tracks, 1):
            if self.should_stop():
                break
            if i % 25 == 0:
                self.log(f"    {i}/{len(tracks)}...")

            entry = cache.get(track.key)
            if isinstance(entry, dict):
                if entry.get("id"):
                    ids.append(entry["id"])
                    matched.add(track.key)
                    continue
                if time.time() - entry.get("ts", 0) < NEGATIVE_TTL:
                    self.stats.unmatched.append(
                        (service, str(track),
                         entry.get("motivo") or "sin equivalencia en el catalogo"))
                    continue  # ya sabemos que no esta, no gastamos otra busqueda

            found: Track | None = None
            motivo = "sin equivalencia en el catalogo"
            if track.isrc:
                found = target.find_by_isrc(track.isrc)
            if found is None:
                found = target.find_by_text(track.title, track.artist)
                if found and found.text_key != track.text_key:
                    found = None  # el buscador devolvio otra cancion
            if found is not None and comprobar:
                # Llamarse igual no basta: el buscador por texto devuelve
                # tan contento el directo, el radio edit o una version ajena.
                aviso = same_recording(track, found, tolerancia)
                if aviso:
                    found, motivo = None, aviso

            if found:
                cache[track.key] = {"id": found.id, "ts": time.time()}
                ids.append(found.id)
                matched.add(track.key)
            else:
                cache[track.key] = {"id": "", "ts": time.time(), "motivo": motivo}
                self.stats.unmatched.append((service, str(track), motivo))

        return ids, matched

    # ---------------------------------------------------------------- informe
    def _write_unmatched_report(self) -> None:
        if not self.stats.unmatched:
            return
        limit = int(self.cfg.get("max_unmatched_report", 500))
        path = reports_dir() / f"sin-equivalencia-{dt.date.today():%Y%m%d}.csv"
        try:
            with path.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle, delimiter=";")
                writer.writerow(["destino", "cancion", "motivo"])
                writer.writerows(self.stats.unmatched[:limit])
            self.log(f"Canciones sin equivalencia listadas en: {path}")
        except OSError as exc:
            self.log(f"No se pudo escribir el informe: {exc}")


@dataclass
class Plan:
    sp_index: dict[str, Track]
    td_index: dict[str, Track]
    to_spotify: set[str]
    to_tidal: set[str]
    remove_spotify: set[str]
    remove_tidal: set[str]
    state_key: str


def _pl_key(name: str) -> str:
    return normalize(name)


# ===========================================================================
# La cola: que se encadena detras de la sincronizacion
# ===========================================================================
# Vive aqui como datos y no como una lista de "if" porque la ventana necesita
# exactamente lo mismo para pintar las casillas. Anadir un paso es anadir una
# linea, y sale a la vez en el motor, en la tarea programada y en la interfaz.
@dataclass(frozen=True)
class Paso:
    """Un trabajo que se puede encadenar detras de la sincronizacion."""
    clave: str                              # el ajuste que lo enciende
    nombre: str                             # como se llama, en todas partes
    detalle: str                            # que hace, para la casilla
    hacer: Callable[["SyncEngine"], str]    # devuelve su resumen, o ""
    aviso: str = ""                         # lo que conviene saber antes


def _paso_itunes(engine: "SyncEngine") -> str:
    engine._sync_itunes()
    return ""                   # ya cuenta lo suyo con su propio detalle


def _paso_publicar(engine: "SyncEngine") -> str:
    from .publish import publish_playlists
    return publish_playlists(engine.cfg, engine.tokens, engine.log,
                             engine.should_stop).summary()


def _paso_flac(engine: "SyncEngine") -> str:
    engine._convert_flac()
    return ""


def _paso_arreglar(engine: "SyncEngine") -> str:
    from .normalize import downsample_library
    return engine._pasada(downsample_library)


def _paso_caratulas(engine: "SyncEngine") -> str:
    from .artwork import check_artwork
    return engine._pasada(check_artwork)


def _paso_biblioteca(engine: "SyncEngine") -> str:
    from .normalize import normalize_library
    return engine._pasada(normalize_library)


def _paso_artistas(engine: "SyncEngine") -> str:
    from .itunes import complete_tags
    return complete_tags(engine.cfg, engine.tidal, engine.log,
                         engine.should_stop).summary()


def _paso_isrc(engine: "SyncEngine") -> str:
    from .itunes import complete_artists_by_isrc
    return complete_artists_by_isrc(engine.cfg, engine._cliente_de_artistas(),
                                    engine.log, engine.should_stop).summary()


def _paso_releer(engine: "SyncEngine") -> str:
    from .normalize import refresh_info
    return engine._pasada(refresh_info)


PASOS: list[Paso] = [
    Paso("itunes_enabled", "Volcar las playlists de TIDAL en iTunes",
         "Crea o actualiza en iTunes una lista por cada una de TIDAL, con lo "
         "que ya tengas en la biblioteca.",
         _paso_itunes),
    Paso("publish_after_sync", "Publicar tus listas de iTunes",
         "El camino contrario: sube a Spotify (y a TIDAL) las listas de iTunes "
         "que tengas marcadas en su pestana.",
         _paso_publicar),
    Paso("flac_after_sync", "Convertir a ALAC lo que haya llegado",
         "Pasa a ALAC los FLAC y WAV que hayan caido en la carpeta de "
         "auto-anadir, para que iTunes pueda leerlos.",
         _paso_flac),
    Paso("fix_after_sync", "Revisar y arreglar los ficheros",
         "Repasa la biblioteca buscando los que pasan del techo de calidad y "
         "los que tienen saltos en la linea de tiempo, y los reescribe.",
         _paso_arreglar,
         aviso="Recorre la biblioteca entera, aunque no mide el volumen."),
    Paso("artwork_after_sync", "Arreglar las caratulas",
         "Pasa a JPEG las portadas que un .m4a no admite, copiando el audio "
         "tal cual.",
         _paso_caratulas,
         aviso="Recorre la biblioteca entera."),
    Paso("artists_after_sync", "Completar datos desde TIDAL",
         "Rellena en iTunes lo que falte -artista, album, ano- buscando cada "
         "cancion en TIDAL.",
         _paso_artistas,
         aviso="Gasta cuota de la API de TIDAL."),
    Paso("isrc_after_sync", "Completar los artistas por ISRC",
         "Las que figuran a nombre de uno solo y son de varios: los "
         "interpretes salen del ISRC, que identifica esa grabacion exacta.",
         _paso_isrc,
         aviso="Gasta cuota de la API."),
    Paso("library_after_sync", "Repasar toda la biblioteca (volumen)",
         "Deja todas las canciones al mismo volumen y baja las que pasen del "
         "techo de calidad.",
         _paso_biblioteca,
         aviso="LO MAS LENTO de todo: mide cancion por cancion. La primera "
               "vez son horas; despues solo mira lo que haya cambiado."),
    Paso("refresh_after_sync", "Releer los datos en iTunes",
         "Obliga a iTunes a releer los kbps y la frecuencia de los ficheros "
         "que se hayan reescrito. Va al final por eso.",
         _paso_releer),
]
