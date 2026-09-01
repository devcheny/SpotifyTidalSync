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

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Callable, Iterator

from .config import Config
from .http import ApiError
from .model import (DURATION_TOLERANCE_S, Track, mmss, normalize,
                    same_length)
from .tidal import TidalClient

# Cada lectura de un campo cruza la frontera COM: avisamos de vez en cuando.
INDEX_PROGRESS_EVERY = 1000


class ITunesError(RuntimeError):
    """iTunes no esta disponible o ha rechazado una operacion."""


def _reglas(cfg: Config | None) -> tuple[bool, float]:
    """Como de estricto se compara: (mirar la duracion, margen en segundos)."""
    if cfg is None:
        return True, DURATION_TOLERANCE_S
    return (bool(cfg.get("match_check_duration", True)),
            float(cfg.get("match_duration_tolerance", DURATION_TOLERANCE_S)))


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

    def user_playlists(self) -> list[Any]:
        """Las playlists que has hecho tu, sin las del sistema (Musica,
        Peliculas, Anadidos recientemente...).

        Las inteligentes entran: no se pueden modificar, pero si leer, que es
        lo unico que hace falta para copiarlas fuera.
        """
        out = []
        for item in _com_items(self.app.LibrarySource.Playlists):
            try:
                if int(item.Kind) != 2 or int(item.SpecialKind) != 0:
                    continue
                if item.Name:
                    out.append(item)
            except Exception:  # noqa: BLE001 - entrada ilegible, se ignora
                continue
        return out

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
    def index(self, cfg: Config | None = None) -> "LibraryIndex":
        index = LibraryIndex(*_reglas(cfg))
        tracks = self.app.LibraryPlaylist.Tracks
        total = tracks.Count
        self.log(f"  leyendo la biblioteca de iTunes ({total} canciones)...")
        for position, track in enumerate(_com_items(tracks), 1):
            index.add(track)
            if position % INDEX_PROGRESS_EVERY == 0:
                self.log(f"    {position}/{total}...")
        return index


def recorrer_biblioteca(log: Callable[[str], None],
                        parar: Callable[[], bool],
                        cada: Callable[[Any], None],
                        paso: int = 250,
                        avisar: Callable[[int, int], None] | None = None) -> int:
    """Recorre la biblioteca de iTunes llamando a `cada(cancion)`.

    Lo hacen igual las cuatro pasadas que la repasan entera (volumen, calidad,
    caratulas y releer datos), asi que la conexion, el conteo, el aviso de
    progreso cada tantas y el cierre pase lo que pase viven aqui.

    Devuelve cuantas se han llegado a mirar, que no tiene por que ser el total
    si se ha parado a medias.
    """
    library = ITunesLibrary(log)
    library.connect()
    vistas = 0
    try:
        canciones = list(_com_items(library.app.LibraryPlaylist.Tracks))
        total = len(canciones)
        log(f"  {total} canciones en la biblioteca")
        for numero, track in enumerate(canciones, 1):
            if parar():
                log("  detenido por el usuario")
                break
            if numero % paso == 0:
                if avisar is not None:
                    avisar(numero, total)
                else:
                    log(f"    {numero}/{total}...")
            vistas = numero
            cada(track)
    finally:
        library.close()
    return vistas


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
    artist: str                 # tal y como lo escribe iTunes, para el informe
    title: str                  # idem, para poder ensenar la errata
    titles: tuple[str, ...]     # sus variantes normalizadas
    tokens: frozenset[str]      # palabras del artista, ya normalizadas
    duration_s: float
    unknown_artist: bool        # vacio o "Varios Artistas": no dice nada
    broken_artist: bool         # trae "?" o el rombo de un acento perdido


class LibraryIndex:
    """Busca una cancion de TIDAL dentro de la biblioteca de iTunes.

    iTunes no expone el ISRC, asi que la unica via es el texto: se comparan
    titulo y artista normalizados (sin acentos, sin "feat.", sin "Remastered")
    y, si varias candidatas comparten titulo, se desempata por duracion.

    La misma cancion se escribe de muchas maneras, asi que cada una se indexa
    por varias formas de su titulo (ver _title_variants) y los recopilatorios,
    que suelen venir con "Varios Artistas", se tratan como artista desconocido.
    """

    def __init__(self, check_duration: bool = True,
                 tolerance: float = DURATION_TOLERANCE_S) -> None:
        self._by_pair: dict[tuple[str, str], list[_Entry]] = {}
        self._by_title: dict[str, list[_Entry]] = {}
        self._by_artist: dict[str, list[_Entry]] = {}
        self.check_duration = check_duration
        self.tolerance = tolerance
        self.size = 0

    def add(self, com_track: Any) -> None:
        try:
            name = com_track.Name or ""
            artist = com_track.Artist or ""
            db_id = int(com_track.TrackDatabaseID)
            duration = float(com_track.Duration or 0)
        except Exception:  # noqa: BLE001 - cancion ilegible (fichero perdido)
            return
        variants = _title_variants(name)
        if not variants:
            return

        tokens = _tokens(artist)
        entry = _Entry(com_track, db_id, artist, name, tuple(variants), tokens,
                       duration, _is_unknown(tokens), _is_broken(artist))
        self._by_pair.setdefault((variants[0], normalize(artist)), []).append(entry)
        for variant in variants:
            self._by_title.setdefault(variant, []).append(entry)
        for token in tokens:
            self._by_artist.setdefault(token, []).append(entry)
        self.size += 1

    def find(self, track: Track) -> _Entry | None:
        """La cancion de tu biblioteca que es esa, o None.

        Lo que devuelve el emparejamiento por texto pasa ademas por la
        duracion: dos canciones pueden llamarse igual y no ser la misma
        grabacion (el directo, el radio edit, la version de otro).
        """
        entry = self._match(track)
        if entry is None or not self.check_duration:
            return entry
        return entry if self._fits(entry, track) else None

    def _fits(self, entry: _Entry, track: Track) -> bool:
        return same_length(entry.duration_s, track.duration_ms / 1000.0,
                           self.tolerance)

    def _match(self, track: Track) -> _Entry | None:
        variants = _title_variants(track.title)
        if not variants:
            return None

        # 1. titulo + artista exactos, con todos los interpretes o solo con el
        #    principal: iTunes guarda unas veces uno y otras veces lo otro.
        for artist in (track.credit, track.artist):
            found = self._by_pair.get((variants[0], normalize(artist)))
            if found:
                # Del mismo artista puedes tener el disco y el directo: si hay
                # varias, manda la duracion en vez del orden de la biblioteca.
                return _closest_duration(found, track.duration_ms, self.tolerance)

        wanted = _tokens(track.credit)
        candidates = self._candidates(variants)

        # 2. mismo titulo y reparto compatible (uno contiene al otro).
        compatible = [e for e in candidates if _compatible(wanted, e.tokens)]
        if len(compatible) == 1:
            return compatible[0]
        if compatible:
            return _closest_duration(compatible, track.duration_ms,
                                     self.tolerance)

        # 3. la etiqueta de iTunes perdio el acento y dejo "Carr?": el nombre
        #    queda cortado, asi que solo se puede comparar por como empieza.
        rotos = [e for e in candidates
                 if e.broken_artist and _starts_like(wanted, e.tokens)]
        if len(rotos) == 1:
            return rotos[0]

        # 4. sin nada que comparar (recopilatorio sin artista, o TIDAL no lo
        #    dio): vale si no cabe duda, o si la duracion lo confirma.
        dudosos = [e for e in candidates if e.unknown_artist or not wanted]
        if dudosos:
            if len(dudosos) == 1 and not track.duration_ms:
                return dudosos[0]
            elegido = _same_duration(dudosos, track.duration_ms,
                                     self.tolerance)
            if elegido is not None:
                return elegido

        # 5. ultimo recurso: mismo artista y titulo casi igual. Cubre las
        #    erratas de una letra ("Hay Quel Venir al Sur"), que si no dejan
        #    la cancion fuera aunque la tengas.
        return self._almost(variants[0], wanted)

    def explain(self, track: Track) -> str:
        """Por que no se encontro, para que el informe lo diga."""
        # Lo primero, el caso que mas despista: la tienes, se llama igual, y
        # aun asi se ha descartado porque no dura lo mismo.
        rechazada = self._match(track) if self.check_duration else None
        if rechazada is not None and not self._fits(rechazada, track):
            return (f"la tuya ({rechazada.artist} - {rechazada.title}) dura "
                    f"{mmss(rechazada.duration_s)} y esta "
                    f"{mmss(track.duration_ms / 1000.0)}: parece otra version")

        candidates = self._candidates(_title_variants(track.title))
        if candidates:
            artistas = sorted({e.artist or "(sin artista)" for e in candidates})
            return ("en iTunes ese titulo esta a nombre de: "
                    + ", ".join(artistas[:3]))

        # Ningun titulo coincide: se ensena lo que hay de ese artista, que es
        # donde se ve de un vistazo una errata en el nombre de la cancion.
        suyas = sorted({e.title for e in self._artist_entries(_tokens(track.credit))})
        if suyas:
            return ("con ese titulo no, pero de ese artista tienes: "
                    + ", ".join(repr(t) for t in suyas[:3]))
        return "no esta en la biblioteca"

    def _candidates(self, variants: list[str]) -> list[_Entry]:
        seen: dict[int, _Entry] = {}
        for variant in variants:
            for entry in self._by_title.get(variant) or []:
                seen.setdefault(id(entry), entry)
        return list(seen.values())

    def _artist_entries(self, wanted: frozenset[str]) -> list[_Entry]:
        """Lo que hay en la biblioteca de ese artista."""
        if not wanted:
            return []
        seen: dict[int, _Entry] = {}
        for token in wanted:
            for entry in self._by_artist.get(token) or []:
                # El "Carr?" de una etiqueta rota tampoco casa aqui palabra a
                # palabra, asi que se admite igual que en la busqueda normal.
                if (_compatible(wanted, entry.tokens)
                        or (entry.broken_artist
                            and _starts_like(wanted, entry.tokens))):
                    seen.setdefault(id(entry), entry)
        return list(seen.values())

    def _almost(self, title: str, wanted: frozenset[str]) -> _Entry | None:
        """Titulo casi igual, pero solo dentro de las canciones de ese artista.

        Con el artista atado y una sola candidata parecida, una letra de
        diferencia es una errata; con dos candidatas no se adivina.
        """
        if len(title) < FUZZY_MIN_LEN:
            return None
        cercanos = [e for e in self._artist_entries(wanted)
                    if any(_almost_equal(title, t) for t in e.titles)]
        return cercanos[0] if len(cercanos) == 1 else None


def _tokens(artist: str) -> frozenset[str]:
    return frozenset(normalize(artist).split())


def _compatible(a: frozenset[str], b: frozenset[str]) -> bool:
    """True si un reparto de artistas esta contenido en el otro."""
    return bool(a and b) and (a <= b or b <= a)


# Un recopilatorio no dice quien canta: no sirve para descartar ni para elegir.
_GENERIC_ARTISTS = [frozenset(normalize(name).split()) for name in (
    "Varios Artistas", "Various Artists", "VA", "VV AA", "Artistas Varios",
    "Compilation", "Recopilatorio", "Unknown Artist", "Artista Desconocido",
    "Banda Sonora", "Soundtrack", "Original Soundtrack",
)]


def _is_unknown(tokens: frozenset[str]) -> bool:
    return not tokens or tokens in _GENERIC_ARTISTS


# Un tag mal codificado deja el acento en "?" o en el rombo de sustitucion.
# chr(0xFFFD) en vez del caracter suelto: asi el fichero es ASCII puro y no
# depende de con que codificacion se copie.
_BROKEN_CHARS = ("?", chr(0xFFFD))


def _is_broken(text: str) -> bool:
    return any(char in text for char in _BROKEN_CHARS)


def _starts_like(wanted: frozenset[str], broken: frozenset[str]) -> bool:
    """Cada palabra rota tiene que ser el principio de una del otro lado.

    Se usa solo con etiquetas corruptas y con el titulo ya coincidiendo: sin
    esas dos condiciones seria demasiado alegre ("carr" vale para Carreras).
    """
    if not wanted or not broken:
        return False
    return all(any(w.startswith(b) for w in wanted) for b in broken)


# "Cancion (A Far L'Amore Comincia Tu)" -> tambien por "Cancion".
_BRACKETS = re.compile(r"[\(\[][^)\]]*[\)\]]")
# Solo se corta tras el guion si lo que sigue es una coletilla de edicion.
_DASH = re.compile(r"\s+[-–—]\s+")
_EDITION = re.compile(
    r"remaster|version|edicion|edit|live|directo|vivo|mix|mono|stereo|"
    r"deluxe|bonus|radio|single|album|instrumental|karaoke", re.IGNORECASE)
# "01 - Cancion", "01. Cancion" y "01 Cancion Con Mas Palabras".
_TRACK_NUMBER = re.compile(r"^\s*\d{1,2}\s*[-._)]\s*|^\s*\d{1,2}\s+(?=\S+\s+\S)")


def _title_variants(title: str) -> list[str]:
    """Formas en que la misma cancion aparece escrita. La primera es la buena."""
    base = normalize(title)
    if not base:
        return []
    out = [base]
    for candidate in (_BRACKETS.sub(" ", title), _strip_edition(title),
                      _TRACK_NUMBER.sub("", title)):
        value = normalize(candidate)
        if value and value not in out:
            out.append(value)
    return out


def _strip_edition(title: str) -> str:
    """Quita el "- Remasterizado 2016" que TIDAL cuelga de muchos titulos."""
    parts = _DASH.split(title)
    if len(parts) > 1 and _EDITION.search(parts[-1]):
        return " - ".join(parts[:-1])
    return title


# Solo se admite una errata en titulos con cuerpo: en "Amor" contra "Amar" una
# letra lo cambia todo, en un titulo largo casi siempre es un dedazo.
FUZZY_MIN_LEN = 10
FUZZY_RATIO = 0.92


def _almost_equal(a: str, b: str) -> bool:
    if abs(len(a) - len(b)) > 3:
        return False
    # Los numeros nunca son un dedazo: "Parte 1" y "Parte 2" son distintas,
    # y sin esto se parecen demasiado para lo que mide SequenceMatcher.
    if _digits(a) != _digits(b):
        return False
    return SequenceMatcher(None, a, b).ratio() >= FUZZY_RATIO


def _digits(text: str) -> str:
    return "".join(char for char in text if char.isdigit())


def _closest_duration(entries: list[_Entry], duration_ms: int,
                      tolerance: float = DURATION_TOLERANCE_S) -> _Entry:
    if not duration_ms:
        return entries[0]
    wanted = duration_ms / 1000.0
    best = min(entries, key=lambda e: abs(e.duration_s - wanted))
    return best if abs(best.duration_s - wanted) <= tolerance else entries[0]


def _same_duration(entries: list[_Entry], duration_ms: int,
                   tolerance: float = DURATION_TOLERANCE_S) -> _Entry | None:
    """Como _closest_duration, pero sin artista que valga no se arriesga."""
    if not duration_ms:
        return None
    wanted = duration_ms / 1000.0
    best = min(entries, key=lambda e: abs(e.duration_s - wanted))
    return best if abs(best.duration_s - wanted) <= tolerance else None


# --------------------------------------------------------------------------
# Motor TIDAL -> iTunes
# --------------------------------------------------------------------------
@dataclass
class ITunesStats:
    playlists: int = 0
    created: int = 0
    added: int = 0
    removed: int = 0
    missing: list[tuple[str, str, str]] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        texto = (
            f"iTunes: {self.playlists} playlists | {self.created} creadas | "
            f"{self.added} canciones anadidas | {self.removed} quitadas | "
            f"{len(self.missing)} no estan en la biblioteca"
        )
        if self.failed:
            texto += f" | {len(self.failed)} playlists con error"
        return texto


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
            index = library.index(self.cfg)
            self.log(f"  indice listo: {index.size} canciones utilizables")
            for raw in selected:
                if self.should_stop():
                    self.log("  detenido por el usuario")
                    break
                name = (raw.get("attributes") or {}).get("name") or ""
                try:
                    self._sync_playlist(library, index, raw, name)
                except (ITunesError, ApiError) as exc:
                    # Que una playlist se tuerza no debe dejar sin hacer el resto.
                    self.log(f"  ! {name}: {exc}")
                    self.stats.failed.append((name, str(exc)))
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

        # Lo que salio de iTunes no tiene que volver: si no, una lista tuya
        # acabaria en TIDAL como "iTunes - Fiesta" y de vuelta aqui como
        # "TIDAL - iTunes - Fiesta".
        propio = normalize(str(self.cfg.get("publish_prefix", "")))

        out: list[dict[str, Any]] = []
        for raw in self._tidal_lists():
            name = (raw.get("attributes") or {}).get("name") or ""
            if not name or _is_missing_list(name):
                continue
            if propio and normalize(name).startswith(propio):
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
                self.stats.missing.append((name, str(track), index.explain(track)))
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
        # Tambien con la lista vacia: hay que sacar de ahi lo que ya no falta.
        if self.cfg.get("itunes_missing_playlist"):
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
        """Mantiene en TIDAL la lista de lo que no esta en la biblioteca.

        Se pone al dia en los dos sentidos: lo que ya has conseguido (o has
        reetiquetado en iTunes para que casase) sale de la lista, para que sea
        siempre lo que te falta AHORA y no un historico.
        """
        name = f"{source_name} - Faltantes en iTunes"
        existing = None
        for raw in self._tidal_lists():
            if normalize((raw.get("attributes") or {}).get("name") or "") \
                    == normalize(name):
                existing = raw
                break

        if existing is None:
            if not missing:
                return          # nada que publicar: no se crea una lista vacia
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
        faltan = {t.id for t in missing}

        nuevas = sorted(faltan - already)
        if nuevas:
            self.tidal.add_to_playlist(str(playlist_id), nuevas)
            self.log(f"    {len(nuevas)} anadidas a '{name}'")

        resueltas = sorted(already - faltan)
        if resueltas:
            try:
                self.tidal.remove_from_playlist(str(playlist_id), resueltas)
                self.log(f"    {len(resueltas)} ya no faltan, fuera de '{name}'")
            except ApiError as exc:
                # Limpiar la lista es lo accesorio: el volcado ya esta hecho.
                self.log(f"    no se pudo limpiar '{name}': {exc}")


@dataclass
class FixStats:
    artists: int = 0
    years: int = 0
    already: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def completed(self) -> int:
        return self.artists + self.years

    def summary(self) -> str:
        texto = (f"Datos: {self.artists} artistas y {self.years} años "
                 f"completados | {self.already} ya estaban bien")
        if self.failed:
            texto += f" | {len(self.failed)} no se pudieron cambiar"
        return texto


def complete_tags(cfg: Config, tidal: TidalClient,
                  log: Callable[[str], None],
                  should_stop: Callable[[], bool] | None = None) -> FixStats:
    """Rellena en iTunes los datos que faltan, tomandolos de TIDAL.

    Dos cosas, y solo cuando en iTunes falta la informacion:

    - Artistas: si tiene "ROSALIA" y en TIDAL son "ROSALIA, The Weeknd", pasa a
      tenerlos los dos. Nunca cambia un artista por otro ni quita ninguno; si
      lo que hay en iTunes no esta contenido en lo de TIDAL, se deja como esta.
    - Año: solo si la cancion no tiene ninguno. Un año ya puesto no se pisa,
      porque puede ser el de la edicion que tu tienes y no el de TIDAL.
    """
    parar = should_stop or (lambda: False)
    stats = FixStats()
    library = ITunesLibrary(log)
    library.connect()
    try:
        index = library.index(cfg)
        log(f"  indice listo: {index.size} canciones utilizables")
        sync = ITunesSync(cfg, tidal, log, parar)
        listas = sync._select_playlists(None)
        vistas: set[int] = set()

        for raw in listas:
            if parar():
                log("  detenido por el usuario")
                break
            nombre = (raw.get("attributes") or {}).get("name") or ""
            if not raw.get("id"):
                continue
            for track in tidal.playlist_tracks(str(raw["id"])):
                if parar():
                    break
                if len(track.artists) < 2 and not track.year:
                    continue        # TIDAL tampoco sabe mas de lo que hay
                entry = index.find(track)
                if entry is None or entry.db_id in vistas:
                    continue
                vistas.add(entry.db_id)
                _completar_uno(entry, track, nombre, cfg, log, stats)
    finally:
        library.close()
    log(f"  {stats.summary()}")
    return stats


def _completar_uno(entry: _Entry, track: Track, playlist: str, cfg: Config,
                   log: Callable[[str], None], stats: FixStats) -> None:
    cambios: list[tuple[str, Any, str]] = []   # (campo, valor, como contarlo)

    # -- artistas: solo se anaden los que faltan ----------------------------
    completo = track.credit
    actuales, todos = entry.tokens, _tokens(completo)
    if completo and actuales != todos and (not actuales or actuales <= todos):
        cambios.append(("Artist", completo,
                        f"artista  {entry.artist!r} -> {completo!r}"))

    # -- año: solo si no tiene ninguno --------------------------------------
    if track.year and not _year_of(entry.track):
        cambios.append(("Year", track.year, f"año      (vacio) -> {track.year}"))

    if not cambios:
        stats.already += 1
        return

    log(f"  ~ [{playlist}] {entry.title}")
    for _campo, _valor, descripcion in cambios:
        log(f"      {descripcion}")

    for campo, valor, _descripcion in cambios:
        if cfg.dry_run:
            _apuntar(stats, campo)
            continue
        try:
            setattr(entry.track, campo, valor)
        except Exception as exc:  # noqa: BLE001 - solo lectura, en la nube...
            log(f"      no se pudo cambiar {campo}: {exc}")
            stats.failed.append((entry.title, str(exc)))
            continue
        _apuntar(stats, campo)


def _apuntar(stats: FixStats, campo: str) -> None:
    if campo == "Artist":
        stats.artists += 1
    else:
        stats.years += 1


def _year_of(com_track: Any) -> int:
    """El año que tiene iTunes, 0 si no tiene ninguno."""
    try:
        return int(com_track.Year or 0)
    except Exception:  # noqa: BLE001 - la cancion no expone el campo
        return 0


def inspect_track(cfg: Config, tidal: TidalClient, query: str,
            log: Callable[[str], None]) -> None:
    """Ensena que ve el programa de una cancion, en iTunes y en TIDAL.

    Imprime los nombres tal cual (con repr, para que se vean los acentos
    perdidos y los espacios raros) y su forma normalizada, que es con la que
    se compara. Sirve para saber por que algo no casa sin tener que adivinar.
    """
    wanted = normalize(query)
    if not wanted:
        log("Dime un trozo del titulo que buscar.")
        return

    library = ITunesLibrary(log)
    library.connect()
    try:
        index = LibraryIndex(*_reglas(cfg))
        en_itunes: list[Any] = []
        for com in _com_items(library.app.LibraryPlaylist.Tracks):
            index.add(com)
            try:
                if wanted in normalize(com.Name or ""):
                    en_itunes.append(com)
            except Exception:  # noqa: BLE001
                continue

        log("")
        log(f"== En iTunes, titulos que contienen '{query}' ==")
        if not en_itunes:
            log("  nada. Ojo: se busca en el campo Nombre, no en el fichero.")
        for com in en_itunes:
            nombre, artista = com.Name or "", com.Artist or ""
            log(f"  {nombre!r}")
            log(f"      artista : {artista!r}")
            log(f"      compara : {normalize(nombre)!r} | {normalize(artista)!r}")
            log(f"      duracion: {float(com.Duration or 0):.0f}s")
            if _is_broken(artista) or _is_broken(nombre):
                log("      OJO: hay un '?' o un rombo donde deberia ir un acento; "
                    "la etiqueta esta mal codificada en iTunes.")

        log("")
        log(f"== En tus playlists de TIDAL ==")
        alguna = False
        for raw in tidal.my_playlists():
            nombre_lista = (raw.get("attributes") or {}).get("name") or ""
            if not raw.get("id") or _is_missing_list(nombre_lista):
                continue
            for track in tidal.playlist_tracks(str(raw["id"])):
                if wanted not in normalize(track.title):
                    continue
                alguna = True
                log(f"  [{nombre_lista}] {track.title!r} de {track.credit!r}")
                log(f"      compara : {normalize(track.title)!r} | "
                    f"{normalize(track.credit)!r}")
                log(f"      duracion: {track.duration_ms / 1000:.0f}s")
                hallado = index.find(track)
                if hallado is not None:
                    log(f"      CASA con {hallado.track.Name!r} de "
                        f"{hallado.artist!r}")
                else:
                    log(f"      NO CASA: {index.explain(track)}")
        if not alguna:
            log("  no aparece en ninguna playlist tuya de TIDAL")
    finally:
        library.close()


def _add_track(playlist: Any, com_track: Any, target_name: str) -> None:
    try:
        playlist.AddTrack(com_track)
    except Exception as exc:  # noqa: BLE001
        raise ITunesError(
            f"iTunes rechazo anadir una cancion a '{target_name}': {exc}") from exc


def _is_missing_list(name: str) -> bool:
    """Las playlists que genera esta misma app no se vuelven a sincronizar."""
    return name.strip().lower().endswith("- faltantes en itunes")
