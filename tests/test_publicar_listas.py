"""Llevar las playlists de iTunes a Spotify y TIDAL, con todo simulado."""
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
from stsync import publish as mod
from stsync.config import Config
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
    def __init__(self, nombre, canciones):
        self.Name = nombre
        self._canciones = canciones

    @property
    def Tracks(self):
        return Coleccion(self._canciones)


LISTAS = [
    PlaylistCom("Fiesta", [Com("La Fama", "ROSALIA", 1),
                           Com("Cancion Rara", "Nadie", 2)]),
    PlaylistCom("Otra Lista", [Com("Yesterday", "The Beatles", 3)]),
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


class ClienteFalso:
    """Hace de SpotifyClient o de TidalClient, segun se le diga."""

    def __init__(self, servicio, catalogo_texto=True):
        self.servicio = servicio
        self.catalogo_texto = catalogo_texto     # TIDAL no busca por texto
        self.listas: list = []
        self.creadas: list = []
        self.anadidas: dict = {}
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


def preparar():
    """Clientes nuevos y sin recuerdos: lo apuntado antes falsearia la prueba."""
    global spotify, tidal
    from stsync.paths import state_file
    state_file().unlink(missing_ok=True)
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
assert [n for n, _ in spotify.creadas] == ["Fiesta", "Otra Lista"], spotify.creadas
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
assert ("Fiesta", True) in spotify.creadas, spotify.creadas
assert ("Otra Lista", False) in spotify.creadas, spotify.creadas

# --- 3. TIDAL sin ISRC no puede enlazar nada --------------------------------
preparar()
stats = publish_playlists(config(publish_to_tidal=True), TokensFalsos(),
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
    stats = publish_playlists(config(publish_to_tidal=True), TokensFalsos(),
                              lambda m: None)
finally:
    mod._isrc_de = original
print("4. a TIDAL con ISRC -> anadidas:", stats.anadidas)
assert stats.anadidas > 0, stats.anadidas

# --- 5. no repite lo que ya esta en la lista --------------------------------
preparar()
cfg = config(publish_to_spotify=True)
publish_playlists(cfg, TokensFalsos(), lambda m: None)
primera = dict(spotify.anadidas)
stats = publish_playlists(cfg, TokensFalsos(), lambda m: None)
print("5. segunda pasada -> anadidas:", stats.anadidas)
assert stats.anadidas == 0, "ya estaban todas"
assert stats.creadas == 0, "la lista ya existia"

# --- 6. sin destino ni playlists elegidas -----------------------------------
from stsync.http import ApiError
try:
    publish_playlists(config(), TokensFalsos(), lambda m: None)
    raise SystemExit("ERROR: sin destino deberia avisar")
except ApiError as exc:
    print("6. sin destino:", exc)

preparar()
stats = publish_playlists(config(publish_to_spotify=True, publish_playlists=[]),
                          TokensFalsos(), lambda m: None)
assert stats.playlists == 0, "sin marcar ninguna, no se publica nada"
print("   sin marcar ninguna: no hace nada")

print()
print("PUBLICAR LISTAS OK")
