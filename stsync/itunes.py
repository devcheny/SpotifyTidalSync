"""Volcado de las playlists de TIDAL a la biblioteca local de iTunes.

iTunes se maneja por COM, la misma interfaz que usa su propia ventana, asi que
hace falta iTunes para Windows (la version de Apple, no la de la Microsoft
Store) y el paquete pywin32.

Aqui no se descarga musica: se buscan en tu biblioteca las canciones que ya
tienes y se meten en una playlist con el nombre de la de TIDAL. Las que no
aparecen se apuntan en el informe y, si lo pides, en una playlist de TIDAL
"<nombre> - Faltantes en iTunes".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from .config import Config
from .model import Track, normalize
from .tidal import TidalClient

# Margen al comparar duraciones cuando varias canciones comparten titulo.
DURATION_TOLERANCE_S = 7.0

# Cada lectura de un campo cruza la frontera COM: avisamos de vez en cuando.
INDEX_PROGRESS_EVERY = 2000


class ITunesError(RuntimeError):
    """iTunes no esta disponible o ha rechazado una operacion."""


def diagnose() -> tuple[bool, str]:
    """Dice si se puede hablar con iTunes, sin llegar a abrirlo.

    Solo mira el registro de Windows: abrir iTunes de verdad arranca el
    programa, y eso no debe pasar solo por mirar la pestana de ajustes.
    """
    try:
        import win32com.client  # noqa: F401
    except ImportError:
        return False, ("Falta el paquete pywin32. Vuelve a ejecutar instalar.bat "
                       "en este equipo.")
    try:
        import winreg
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "iTunes.Application"))
    except OSError:
        return False, ("No se ve iTunes en este equipo. Instala iTunes para Windows "
                       "desde apple.com: la version de la Microsoft Store no se "
                       "puede automatizar.")
    return True, "iTunes detectado en este equipo."


# --------------------------------------------------------------------------
# Acceso a iTunes
# --------------------------------------------------------------------------
class ITunesLibrary:
    """Envoltorio minimo sobre la automatizacion COM de iTunes."""

    def __init__(self, log: Callable[[str], None] | None = None) -> None:
        self.log = log or (lambda _m: None)
        self.app: Any = None
        self._com_ready = False

    # -- conexion -----------------------------------------------------------
    def connect(self) -> None:
        try:
            import pythoncom  # type: ignore[import-not-found]
            import win32com.client  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ITunesError(
                "Falta el paquete pywin32, necesario para hablar con iTunes. "
                "Vuelve a ejecutar instalar.bat en este equipo."
            ) from exc

        # El motor corre en un hilo aparte: COM hay que inicializarlo en el.
        try:
            pythoncom.CoInitialize()
            self._com_ready = True
        except Exception:  # noqa: BLE001 - ya estaba inicializado en este hilo
            self._com_ready = False

        try:
            self.app = win32com.client.Dispatch("iTunes.Application")
            count = self.app.LibraryPlaylist.Tracks.Count
        except Exception as exc:  # noqa: BLE001 - pywin32 lanza com_error
            self.close()
            raise ITunesError(
                "No se ha podido abrir iTunes. Comprueba que iTunes para Windows "
                "esta instalado (la version de apple.com; la de la Microsoft Store "
                f"no se puede automatizar) y que se abre sin pedir nada. [{exc}]"
            ) from exc
        self.log(f"  iTunes conectado: {count} canciones en la biblioteca")

    def close(self) -> None:
        self.app = None
        if self._com_ready:
            try:
                import pythoncom  # type: ignore[import-not-found]
                pythoncom.CoUninitialize()
            except Exception:  # noqa: BLE001
                pass
            self._com_ready = False

    # -- playlists ----------------------------------------------------------
    def playlist(self, name: str) -> Any | None:
        """Playlist con ese nombre exacto, o None si no existe."""
        for item in _com_items(self.app.LibrarySource.Playlists):
            try:
                if item.Name == name:
                    return item
            except Exception:  # noqa: BLE001 - entrada ilegible, se ignora
                continue
        return None

    def create_playlist(self, name: str) -> Any:
        try:
            return self.app.CreatePlaylist(name)
        except Exception as exc:  # noqa: BLE001
            raise ITunesError(
                f"iTunes no ha podido crear la playlist '{name}': {exc}") from exc

    @staticmethod
    def is_writable(playlist: Any) -> bool:
        """Las listas inteligentes no admiten que se les anadan canciones."""
        try:
            return not bool(playlist.Smart)
        except Exception:  # noqa: BLE001 - no es una lista de usuario
            return True

    # -- biblioteca ---------------------------------------------------------
    def index(self) -> "LibraryIndex":
        index = LibraryIndex()
        tracks = self.app.LibraryPlaylist.Tracks
        total = tracks.Count
        self.log(f"  leyendo la biblioteca de iTunes ({total} canciones)...")
        for position, track in enumerate(_com_items(tracks), 1):
            index.add(track)
            if position % INDEX_PROGRESS_EVERY == 0:
                self.log(f"    {position}/{total}...")
        return index


def _com_items(collection: Any) -> Iterator[Any]:
    """Recorre una coleccion COM de iTunes (sus indices empiezan en 1)."""
    try:
        count = collection.Count
    except Exception:  # noqa: BLE001
        return
    for i in range(1, count + 1):
        try:
            item = collection.Item(i)
        except Exception:  # noqa: BLE001 - hueco en la coleccion
            continue
        if item is not None:
            yield item


# --------------------------------------------------------------------------
# Indice de la biblioteca para buscar por texto
# --------------------------------------------------------------------------
@dataclass
class _Entry:
    track: Any
    db_id: int
    tokens: frozenset[str]      # palabras del artista, ya normalizadas
    duration_s: float


class LibraryIndex:
    """Busca una cancion de TIDAL dentro de la biblioteca de iTunes.

    iTunes no expone el ISRC, asi que la unica via es el texto: se comparan
    titulo y artista normalizados (sin acentos, sin "feat.", sin "Remastered")
    y, si varias candidatas comparten titulo, se desempata por duracion.
    """

    def __init__(self) -> None:
        self._by_pair: dict[tuple[str, str], list[_Entry]] = {}
        self._by_title: dict[str, list[_Entry]] = {}
        self.size = 0

    def add(self, com_track: Any) -> None:
        try:
            title = normalize(com_track.Name)
            artist = com_track.Artist or ""
            db_id = int(com_track.TrackDatabaseID)
            duration = float(com_track.Duration or 0)
        except Exception:  # noqa: BLE001 - cancion ilegible (fichero perdido)
            return
        if not title:
            return

        entry = _Entry(com_track, db_id, _tokens(artist), duration)
        self._by_pair.setdefault((title, normalize(artist)), []).append(entry)
        self._by_title.setdefault(title, []).append(entry)
        self.size += 1

    def find(self, track: Track) -> _Entry | None:
        title = normalize(track.title)
        if not title:
            return None

        # 1. titulo + artista exactos, con todos los interpretes o solo con el
        #    principal: iTunes guarda unas veces uno y otras veces lo otro.
        for artist in (track.credit, track.artist):
            found = self._by_pair.get((title, normalize(artist)))
            if found:
                return found[0]

        # 2. mismo titulo y reparto compatible (uno contiene al otro).
        candidates = self._by_title.get(title) or []
        if not candidates:
            return None
        wanted = _tokens(track.credit)
        if not wanted:
            # Sin artista solo se acepta cuando no cabe ninguna duda.
            return candidates[0] if len(candidates) == 1 else None

        compatible = [e for e in candidates if _compatible(wanted, e.tokens)]
        if not compatible:
            return None
        if len(compatible) == 1:
            return compatible[0]
        return _closest_duration(compatible, track.duration_ms)


def _tokens(artist: str) -> frozenset[str]:
    return frozenset(normalize(artist).split())


def _compatible(a: frozenset[str], b: frozenset[str]) -> bool:
    """True si un reparto de artistas esta contenido en el otro."""
    return bool(a and b) and (a <= b or b <= a)


def _closest_duration(entries: list[_Entry], duration_ms: int) -> _Entry:
    if not duration_ms:
        return entries[0]
    wanted = duration_ms / 1000.0
    best = min(entries, key=lambda e: abs(e.duration_s - wanted))
    return best if abs(best.duration_s - wanted) <= DURATION_TOLERANCE_S else entries[0]


# --------------------------------------------------------------------------
# Motor TIDAL -> iTunes
# --------------------------------------------------------------------------
@dataclass
class ITunesStats:
    playlists: int = 0
    created: int = 0
    added: int = 0
    removed: int = 0
    missing: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"iTunes: {self.playlists} playlists | {self.created} creadas | "
            f"{self.added} canciones anadidas | {self.removed} quitadas | "
            f"{len(self.missing)} no estan en la biblioteca"
        )


class ITunesSync:
    def __init__(self, cfg: Config, tidal: TidalClient,
                 log: Callable[[str], None] | None = None,
                 should_stop: Callable[[], bool] | None = None) -> None:
        self.cfg = cfg
        self.tidal = tidal
        self.log = log or (lambda msg: print(msg))
        self.should_stop = should_stop or (lambda: False)
        self.stats = ITunesStats()
        self._tidal_playlists: list[dict[str, Any]] | None = None

    # ---------------------------------------------------------------- publico
    def run(self, only_playlist: str | None = None) -> ITunesStats:
        self.log("")
        self.log("== iTunes ==")
        if self.cfg.dry_run:
            self.log("  (simulacion: no se tocara iTunes)")

        selected = self._select_playlists(only_playlist)
        if not selected:
            return self.stats

        library = ITunesLibrary(self.log)
        library.connect()
        try:
            index = library.index()
            self.log(f"  indice listo: {index.size} canciones utilizables")
            for raw in selected:
                if self.should_stop():
                    self.log("  detenido por el usuario")
                    break
                name = (raw.get("attributes") or {}).get("name") or ""
                try:
                    self._sync_playlist(library, index, raw, name)
                except ITunesError as exc:
                    self.log(f"  ! {name}: {exc}")
        finally:
            library.close()
        return self.stats

    # -------------------------------------------------------------- seleccion
    def _tidal_lists(self) -> list[dict[str, Any]]:
        if self._tidal_playlists is None:
            self._tidal_playlists = self.tidal.my_playlists()
        return self._tidal_playlists

    def _select_playlists(self, only_playlist: str | None) -> list[dict[str, Any]]:
        chosen = [only_playlist] if only_playlist else \
                 (self.cfg.get("itunes_playlists", []) or [])
        wanted = [normalize(n) for n in chosen if n]

        out: list[dict[str, Any]] = []
        for raw in self._tidal_lists():
            name = (raw.get("attributes") or {}).get("name") or ""
            if not name or _is_missing_list(name):
                continue
            if wanted and normalize(name) not in wanted:
                continue
            out.append(raw)

        if wanted and not out:
            self.log(f"  no hay ninguna playlist de TIDAL llamada "
                     f"'{', '.join(chosen)}'")
        elif wanted:
            self.log(f"  {len(out)} de {len(wanted)} playlists elegidas encontradas")
        elif not out:
            self.log("  no tienes playlists en TIDAL que sincronizar")
        else:
            self.log(f"  {len(out)} playlists de TIDAL")
        return out

    # --------------------------------------------------------------- playlist
    def _sync_playlist(self, library: ITunesLibrary, index: LibraryIndex,
                       raw: dict[str, Any], name: str) -> None:
        playlist_id = raw.get("id")
        if not playlist_id:
            return

        tracks = self.tidal.playlist_tracks(str(playlist_id))
        if not tracks:
            self.log(f"  ~ {name}: vacia en TIDAL, se omite")
            return

        target_name = f"{self.cfg.get('itunes_playlist_prefix', '')}{name}"
        target = library.playlist(target_name)
        if target is not None and not library.is_writable(target):
            raise ITunesError(f"'{target_name}' es una lista inteligente de iTunes "
                              "y no admite canciones. Cambiale el nombre o el prefijo.")
        if target is None:
            if self.cfg.dry_run:
                self.log(f"  + [simulacion] crearia en iTunes: {target_name}")
            else:
                target = library.create_playlist(target_name)
                self.log(f"  + creada en iTunes: {target_name}")
            self.stats.created += 1

        current = self._playlist_contents(target)
        wanted_ids: set[int] = set()
        missing: list[Track] = []
        added = 0

        for track in tracks:
            entry = index.find(track)
            if entry is None:
                missing.append(track)
                self.stats.missing.append((name, str(track)))
                continue
            wanted_ids.add(entry.db_id)
            if entry.db_id in current:
                continue
            if not self.cfg.dry_run and target is not None:
                _add_track(target, entry.track, target_name)
            current[entry.db_id] = entry.track
            added += 1

        self.stats.playlists += 1
        self.stats.added += added
        removed = self._remove_extra(current, wanted_ids, target_name)

        self.log(f"  ~ {name}: {len(tracks)} en TIDAL, {added} anadidas, "
                 f"{removed} quitadas, {len(missing)} sin encontrar")
        if missing and self.cfg.get("itunes_missing_playlist"):
            self._publish_missing(name, missing)

    @staticmethod
    def _playlist_contents(target: Any) -> dict[int, Any]:
        """Lo que ya tiene la playlist de iTunes, por id de biblioteca."""
        if target is None:
            return {}
        out: dict[int, Any] = {}
        for track in _com_items(target.Tracks):
            try:
                out[int(track.TrackDatabaseID)] = track
            except Exception:  # noqa: BLE001
                continue
        return out

    def _remove_extra(self, current: dict[int, Any], wanted: set[int],
                      target_name: str) -> int:
        """Quita de iTunes lo que ya no esta en la playlist de TIDAL."""
        if not self.cfg.get("itunes_remove_extra"):
            return 0
        extra = [db_id for db_id in current if db_id not in wanted]
        for db_id in extra:
            if self.cfg.dry_run:
                continue
            try:
                current[db_id].Delete()
            except Exception as exc:  # noqa: BLE001
                self.log(f"    no se pudo quitar una cancion de '{target_name}': {exc}")
        self.stats.removed += len(extra)
        return len(extra)

    # -------------------------------------------------------------- faltantes
    def _publish_missing(self, source_name: str, missing: list[Track]) -> None:
        """Deja en TIDAL una playlist con lo que no esta en la biblioteca."""
        name = f"{source_name} - Faltantes en iTunes"
        existing = None
        for raw in self._tidal_lists():
            if normalize((raw.get("attributes") or {}).get("name") or "") \
                    == normalize(name):
                existing = raw
                break

        if existing is None:
            created = self.tidal.create_playlist(
                name, "Canciones de esta playlist que no estan en iTunes")
            playlist_id = created.get("id")
            if playlist_id and playlist_id != "dry-run":
                self._tidal_lists().append(created)
            self.log(f"  + playlist en TIDAL: {name}")
        else:
            playlist_id = existing.get("id")

        if not playlist_id or playlist_id == "dry-run":
            return
        already = {t.id for t in self.tidal.playlist_tracks(str(playlist_id))}
        nuevas = [t.id for t in missing if t.id not in already]
        if nuevas:
            self.tidal.add_to_playlist(str(playlist_id), nuevas)
            self.log(f"    {len(nuevas)} anadidas a '{name}'")


def _add_track(playlist: Any, com_track: Any, target_name: str) -> None:
    try:
        playlist.AddTrack(com_track)
    except Exception as exc:  # noqa: BLE001
        raise ITunesError(
            f"iTunes rechazo anadir una cancion a '{target_name}': {exc}") from exc


def _is_missing_list(name: str) -> bool:
    """Las playlists que genera esta misma app no se vuelven a sincronizar."""
    return name.strip().lower().endswith("- faltantes en itunes")
