"""Llevar las playlists de iTunes a Spotify y TIDAL, y traerlas de vuelta.

Todo simulado: ni iTunes, ni ffprobe, ni red.
"""
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
from stsync import publish as mod
from stsync.config import Config
from stsync.itunes import LibraryIndex
from stsync.model import Track
from stsync.publish import publish_playlists


class Com:
    def __init__(self, name, artist, db_id, location=""):
        self.Name = name
        self.Artist = artist
        self.TrackDatabaseID = db_id
        self.Duration = 200
        self.Location = location


class Coleccion:
    def __init__(self, items):
        self.items = items

    @property
    def Count(self):
        return len(self.items)

    def Item(self, i):
        return self.items[i - 1]


class PlaylistCom:
    def __init__(self, nombre, canciones, smart=False):
        self.Name = nombre
        self.Smart = smart
        self._canciones = list(canciones)

    @property
    def Tracks(self):
        return Coleccion(self._canciones)

    def AddTrack(self, com_track):
        if self.Smart:
            raise RuntimeError("una lista inteligente no admite canciones")
        self._canciones.append(com_track)


# Lo que hay en la biblioteca de iTunes. "Bohemian Rhapsody" la tienes pero no
# esta en ninguna playlist: es la que deberia entrar al traer de Spotify.
FAMA = Com("La Fama", "ROSALIA", 1)
RARA = Com("Cancion Rara", "Nadie", 2)
AYER = Com("Yesterday", "The Beatles", 3)
REINA = Com("Bohemian Rhapsody", "Queen", 4)
BIBLIOTECA = [FAMA, RARA, AYER, REINA]

LISTAS: list = []


def _listas_nuevas(smart_otra=False):
    """Playlists recien hechas: AddTrack las modifica y no deben arrastrarse."""
    return [
        PlaylistCom("Fiesta", [FAMA, RARA]),
        PlaylistCom("Otra Lista", [AYER], smart=smart_otra),
    ]


class LibreriaFalsa:
    def __init__(self, log=None):
        self.log = log or (lambda m: None)

    def connect(self):
        self.log("  iTunes conectado (falso)")

    def close(self):
        pass

    def user_playlists(self):
        return LISTAS

    @staticmethod
    def is_writable(playlist):
        return not bool(getattr(playlist, "Smart", False))

    def index(self):
        index = LibraryIndex()
        for com in BIBLIOTECA:
            index.add(com)
        self.log(f"  biblioteca falsa: {index.size} canciones")
        return index


class ClienteFalso:
    """Hace de SpotifyClient o de TidalClient, segun se le diga."""

    def __init__(self, servicio, catalogo_texto=True):
        self.servicio = servicio
        self.catalogo_texto = catalogo_texto     # TIDAL no busca por texto
        self.listas: list = []
        self.creadas: list = []
        self.anadidas: dict = {}
        self.quitadas: dict = {}
        self.contenido: dict = {}

    def my_playlists(self):
        return self.listas

    def create_playlist(self, nombre, descripcion="", publica=False):
        self.creadas.append((nombre, publica))
        ident = f"pl{len(self.creadas)}"
        cruda = ({"id": ident, "attributes": {"name": nombre}}
                 if self.servicio == "tidal" else {"id": ident, "name": nombre})
        self.listas.append(cruda)
        self.contenido[ident] = []
        return cruda

    def playlist_tracks(self, playlist_id):
        return self.contenido.get(str(playlist_id), [])

    def add_to_playlist(self, playlist_id, ids):
        self.anadidas.setdefault(str(playlist_id), []).extend(ids)
        self.contenido.setdefault(str(playlist_id), []).extend(
            Track(service=self.servicio, id=i, title=i, artist="x") for i in ids)

    def remove_from_playlist(self, playlist_id, ids):
        self.quitadas.setdefault(str(playlist_id), []).extend(ids)
        fuera = set(ids)
        self.contenido[str(playlist_id)] = [
            t for t in self.contenido.get(str(playlist_id), []) if t.id not in fuera]

    def sembrar(self, nombre, tracks):
        """Una lista que ya existia en el servicio, con estas canciones."""
        cruda = self.create_playlist(nombre)
        self.contenido[cruda["id"]] = list(tracks)
        return cruda

    def find_by_isrc(self, isrc):
        return Track(service=self.servicio, id=f"por-isrc-{isrc}",
                     title="La Fama", artist="ROSALIA") if isrc else None

    def find_by_text(self, titulo, artista):
        if not self.catalogo_texto or titulo == "Cancion Rara":
            return None
        return Track(service=self.servicio, id=f"por-texto-{titulo}",
                     title=titulo, artist=artista)


def config(**extra):
    cfg = Config(dict(Config().data))
    cfg.set("publish_playlists", ["Fiesta", "Otra Lista"])
    for clave, valor in extra.items():
        cfg.set(clave, valor)
    return cfg


class TokensFalsos:
    def has(self, servicio):
        return True


mod.ITunesLibrary = LibreriaFalsa
mod._buscar_ffprobe = lambda ffmpeg: None      # sin ffprobe: no hay ISRC
mod.find_ffmpeg = lambda configurado: "ffmpeg"

spotify = tidal = None


def preparar(smart_otra=False):
    """Clientes y listas nuevos: lo apuntado antes falsearia la prueba."""
    global spotify, tidal, LISTAS
    from stsync.paths import state_file
    state_file().unlink(missing_ok=True)
    LISTAS = _listas_nuevas(smart_otra)
    spotify = ClienteFalso("spotify", catalogo_texto=True)
    tidal = ClienteFalso("tidal", catalogo_texto=False)
    mod.SpotifyClient = lambda cfg, tokens, log: spotify
    mod.TidalClient = lambda cfg, tokens, log: tidal


# --- 1. a Spotify, que si busca por texto -----------------------------------
preparar()
lineas = []
stats = publish_playlists(config(publish_to_spotify=True), TokensFalsos(),
                          lineas.append)
print("\n".join(lineas))
print()
assert stats.playlists == 2, stats.playlists
assert stats.creadas == 2, stats.creadas
assert stats.anadidas == 2, stats.anadidas   # La Fama y Yesterday
assert [n for n, _ in spotify.creadas] == ["iTunes - Fiesta", "iTunes - Otra Lista"], spotify.creadas
assert all(not publica for _, publica in spotify.creadas), "nadie pidio publicas"
# La que no esta en el catalogo se apunta, no se inventa
assert any("Cancion Rara" in c for _, c, _ in stats.sin_equivalencia), \
    stats.sin_equivalencia
print("1. crea las listas en Spotify y apunta lo que no encuentra")

# --- 2. las marcadas salen publicas -----------------------------------------
preparar()
publish_playlists(config(publish_to_spotify=True, publish_public=["Fiesta"]),
                  TokensFalsos(), lambda m: None)
print("2. creadas:", spotify.creadas)
assert ("iTunes - Fiesta", True) in spotify.creadas, spotify.creadas
assert ("iTunes - Otra Lista", False) in spotify.creadas, spotify.creadas

# --- 3. TIDAL sin ISRC no puede enlazar nada --------------------------------
preparar()
stats = publish_playlists(config(publish_to_tidal=True, publish_to_spotify=False), TokensFalsos(),
                          lambda m: None)
print("3. a TIDAL sin ISRC -> anadidas:", stats.anadidas,
      "| sin equivalencia:", len(stats.sin_equivalencia))
assert stats.anadidas == 0, stats.anadidas
assert all("sin ISRC" in motivo for _, _, motivo in stats.sin_equivalencia), \
    stats.sin_equivalencia

# --- 4. con ISRC en el fichero, TIDAL si las encuentra ----------------------
preparar()
original = mod._isrc_de          # se guarda: borrarla dejaria el modulo cojo
mod._isrc_de = lambda com, ffprobe: "ES1234567890"
try:
    stats = publish_playlists(config(publish_to_tidal=True, publish_to_spotify=False), TokensFalsos(),
                              lambda m: None)
finally:
    mod._isrc_de = original
print("4. a TIDAL con ISRC -> anadidas:", stats.anadidas)
assert stats.anadidas > 0, stats.anadidas

# --- 5. no repite lo que ya esta en la lista --------------------------------
preparar()
cfg = config(publish_to_spotify=True)
publish_playlists(cfg, TokensFalsos(), lambda m: None)
stats = publish_playlists(cfg, TokensFalsos(), lambda m: None)
print("5. segunda pasada -> anadidas:", stats.anadidas)
assert stats.anadidas == 0, "ya estaban todas"
assert stats.creadas == 0, "la lista ya existia"

# --- 6. sin destino ni playlists elegidas -----------------------------------
from stsync.http import ApiError
try:
    publish_playlists(config(publish_to_spotify=False), TokensFalsos(),
                      lambda m: None)
    raise SystemExit("ERROR: sin destino deberia avisar")
except ApiError as exc:
    print("6. sin destino:", exc)

preparar()
stats = publish_playlists(config(publish_to_spotify=True, publish_playlists=[]),
                          TokensFalsos(), lambda m: None)
assert stats.playlists == 0, "sin marcar ninguna, no se publica nada"
print("   sin marcar ninguna: no hace nada")


# ===========================================================================
# El sentido de vuelta: de Spotify a iTunes
# ===========================================================================
def en_spotify(*pares):
    return [Track(service="spotify", id=ident, title=titulo, artist=artista)
            for ident, titulo, artista in pares]


ANADIDAS_EN_SPOTIFY = (
    ("s1", "La Fama", "ROSALIA"),            # ya esta en la lista de iTunes
    ("s4", "Bohemian Rhapsody", "Queen"),    # la tienes, pero no en esa lista
    ("s9", "Cancion Inexistente", "Fulano"), # no la tienes: te falta
)

# --- 7. traer mete en iTunes lo que tengas y apunta lo que no ---------------
preparar()
spotify.sembrar("iTunes - Fiesta", en_spotify(*ANADIDAS_EN_SPOTIFY))
lineas = []
stats = publish_playlists(
    config(publish_to_spotify=True, publish_playlists=[],
           publish_import=["Fiesta"]), TokensFalsos(), lineas.append)
print("\n".join(lineas))
print()
assert stats.traidas == 1, stats.traidas          # solo Bohemian Rhapsody
assert stats.faltantes == 1, stats.faltantes      # solo la que no tienes
fiesta = LISTAS[0]
assert [c.Name for c in fiesta._canciones] == ["La Fama", "Cancion Rara",
                                               "Bohemian Rhapsody"], \
    [c.Name for c in fiesta._canciones]
faltantes = [n for n, _ in spotify.creadas
             if n == "iTunes - Fiesta - Faltantes en iTunes"]
assert faltantes, spotify.creadas
ident = next(c["id"] for c in spotify.listas
             if c["name"] == "iTunes - Fiesta - Faltantes en iTunes")
assert spotify.anadidas[ident] == ["s9"], spotify.anadidas
assert all(not publica for n, publica in spotify.creadas
           if "Faltantes" in n), "la lista de faltantes nunca es publica"
print("7. trae de Spotify lo que ya tienes y apunta el resto")

# --- 8. la lista de faltantes se limpia cuando dejan de faltar --------------
# Segunda pasada con la cancion ya "conseguida": sale de la lista sola.
BIBLIOTECA.append(Com("Cancion Inexistente", "Fulano", 9))
try:
    stats = publish_playlists(
        config(publish_to_spotify=True, publish_playlists=[],
               publish_import=["Fiesta"]), TokensFalsos(), lambda m: None)
finally:
    BIBLIOTECA.pop()
print("8. segunda pasada -> quitadas de faltantes:", spotify.quitadas.get(ident))
assert stats.faltantes == 0, stats.faltantes
assert spotify.quitadas.get(ident) == ["s9"], spotify.quitadas
assert stats.traidas == 1, stats.traidas          # ahora entra la que faltaba

# --- 9. una lista inteligente no admite canciones, pero si se apunta --------
preparar(smart_otra=True)
spotify.sembrar("iTunes - Otra Lista",
                en_spotify(("s9", "Cancion Inexistente", "Fulano"),
                           ("s4", "Bohemian Rhapsody", "Queen")))
stats = publish_playlists(
    config(publish_to_spotify=True, publish_playlists=[],
           publish_import=["Otra Lista"]), TokensFalsos(), lambda m: None)
print("9. inteligente -> traidas:", stats.traidas,
      "| faltantes:", stats.faltantes)
assert stats.traidas == 0, "a una lista inteligente no se le anade nada"
assert stats.faltantes == 1, stats.faltantes
assert [c.Name for c in LISTAS[1]._canciones] == ["Yesterday"], "no se toco"

# --- 10. sin marcar Spotify no se puede traer -------------------------------
preparar()
lineas = []
stats = publish_playlists(
    config(publish_to_tidal=True, publish_to_spotify=False,
           publish_playlists=[], publish_import=["Fiesta"]),
    TokensFalsos(), lineas.append)
assert stats.traidas == 0, stats.traidas
assert any("no se trae nada" in l for l in lineas), lineas
print("10. sin Spotify entre los destinos avisa y no trae")

# --- 11. la lista de faltantes no viaja a TIDAL -----------------------------
from stsync.sync import SyncEngine, _pl_key

motor = SyncEngine(Config())
for nombre, permitida in (("iTunes - Fiesta - Faltantes en iTunes", False),
                          ("Animacion Old - Faltantes en iTunes", False),
                          ("iTunes - Fiesta", True),
                          ("Fiesta", True)):
    assert motor._playlist_allowed(_pl_key(nombre), nombre) is permitida, nombre
print("11. las listas de faltantes se quedan fuera de la sincronizacion")

print()
print("PUBLICAR LISTAS OK")
