"""Completar en iTunes los datos que faltan (artistas y año), desde TIDAL."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stsync import itunes as mod
from stsync.config import Config
from stsync.itunes import (LibraryIndex, _reglas, complete_artists_by_isrc,
                           complete_tags)
from stsync.model import Track


class Com:
    """Lo poco que se le pide a una cancion de iTunes, con campos escribibles."""

    def __init__(self, name, artist, db_id, year=0, duration=200):
        self.Name = name
        self.Artist = artist
        self.Year = year
        self.TrackDatabaseID = db_id
        self.Duration = duration
        self.solo_lectura = False

    def __setattr__(self, campo, valor):
        if campo in ("Artist", "Year") and getattr(self, "solo_lectura", False):
            raise OSError("el fichero es de solo lectura")
        object.__setattr__(self, campo, valor)


#                  titulo            artista en iTunes      id  año
BIBLIOTECA = [
    Com("La Fama", "ROSALIA", 1, 0),                   # falta artista y año
    Com("Despecha", "", 2, 2000),                      # sin artista, con año
    Com("Otra Cancion", "ROSALIA; The Weeknd", 3, 2022),   # ya esta completa
    Com("Cancion Ajena", "Otro Artista", 4, 0),        # otro artista, sin año
    Com("Con Candado", "ROSALIA", 5, 0),               # no se deja escribir
]
BIBLIOTECA[-1].solo_lectura = True
ORIGINALES = [(c.Artist, c.Year) for c in BIBLIOTECA]


class LibreriaFalsa:
    def __init__(self, log=None):
        self.log = log or (lambda m: None)

    def connect(self):
        self.log("  iTunes conectado (falso)")

    def close(self):
        pass

    def index(self, cfg=None):
        indice = LibraryIndex(*_reglas(cfg))
        for com in BIBLIOTECA:
            indice.add(com)
        return indice


def pista(titulo, artistas, year=2022):
    return Track(service="tidal", id=titulo, title=titulo,
                 artist=artistas[0], artists=tuple(artistas),
                 duration_ms=200000, year=year)


class TidalFalso:
    def my_playlists(self):
        # La misma cancion en dos listas: no debe tocarse dos veces.
        return [{"id": "1", "attributes": {"name": "Fiesta"}},
                {"id": "2", "attributes": {"name": "Repetida"}}]

    def playlist_tracks(self, playlist_id):
        if str(playlist_id) == "2":
            return [pista("La Fama", ["ROSALIA", "The Weeknd"])]
        return [
            pista("La Fama", ["ROSALIA", "The Weeknd"]),
            pista("Despecha", ["ROSALIA", "Chanel"]),
            pista("Otra Cancion", ["ROSALIA", "The Weeknd"]),
            pista("Cancion Ajena", ["ROSALIA", "The Weeknd"]),
            pista("Con Candado", ["ROSALIA", "The Weeknd"]),
        ]


mod.ITunesLibrary = LibreriaFalsa


def correr(**ajustes):
    for com, (artista, year) in zip(BIBLIOTECA, ORIGINALES):
        object.__setattr__(com, "Artist", artista)
        object.__setattr__(com, "Year", year)
    cfg = Config(dict(Config().data))
    for clave, valor in ajustes.items():
        cfg.set(clave, valor)
    lineas = []
    return complete_tags(cfg, TidalFalso(), lineas.append), lineas


# --- 1. de verdad -----------------------------------------------------------
stats, lineas = correr()
print("\n".join(lineas))
print()
assert BIBLIOTECA[0].Artist == "ROSALIA, The Weeknd", BIBLIOTECA[0].Artist
assert BIBLIOTECA[0].Year == 2022, "le faltaba el año"
assert BIBLIOTECA[1].Artist == "ROSALIA, Chanel", BIBLIOTECA[1].Artist
assert BIBLIOTECA[1].Year == 2000, "el año que ya tenia NO se pisa"
assert BIBLIOTECA[2].Artist == "ROSALIA; The Weeknd", "ya estaba: no se toca"
# Con otro artista, el emparejador ni la da por la misma cancion: no se le
# toca nada, tampoco el año. Es lo suyo: no sabemos si es esa grabacion.
assert BIBLIOTECA[3].Artist == "Otro Artista", "un artista distinto NO se pisa"
assert BIBLIOTECA[3].Year == 0, "y sin identificarla, tampoco se le pone el año"
assert BIBLIOTECA[4].Artist == "ROSALIA", "si no se puede escribir, se queda"
assert stats.artists == 2, stats.artists
assert stats.years == 1, stats.years
assert stats.already == 1, stats.already
assert len(stats.failed) == 2, stats.failed   # artista y año de la bloqueada
print("1. completa lo que falta y respeta lo que ya hay")

# --- 2. simulacion ----------------------------------------------------------
stats, _ = correr(dry_run=True)
assert BIBLIOTECA[0].Artist == "ROSALIA" and BIBLIOTECA[0].Year == 0, \
    "la simulacion no escribe"
# Cuenta tambien la bloqueada: sin escribir no hay forma de saber que falla.
assert (stats.artists, stats.years) == (3, 2), (stats.artists, stats.years)
assert not stats.failed, "en simulacion no puede fallar ninguna escritura"
print("2. la simulacion dice que haria sin tocar nada")

# --- 3. solo las playlists elegidas -----------------------------------------
stats, _ = correr(itunes_playlists=["Repetida"])
assert BIBLIOTECA[0].Artist == "ROSALIA, The Weeknd"
assert BIBLIOTECA[1].Artist == "", "esa cancion no esta en la lista elegida"
print("3. respeta las playlists elegidas")

# --- 4. si TIDAL no sabe el año, no inventa ---------------------------------
class TidalSinAnio(TidalFalso):
    def playlist_tracks(self, playlist_id):
        return [pista("La Fama", ["ROSALIA", "The Weeknd"], year=0)]


for com, (artista, year) in zip(BIBLIOTECA, ORIGINALES):
    object.__setattr__(com, "Artist", artista)
    object.__setattr__(com, "Year", year)
stats = complete_tags(Config(dict(Config().data)), TidalSinAnio(), lambda m: None)
assert BIBLIOTECA[0].Year == 0, "sin dato en TIDAL, el año se queda vacio"
assert stats.years == 0 and stats.artists == 1, (stats.years, stats.artists)
print("4. sin año en TIDAL no se inventa nada")


# ===========================================================================
# Completar los artistas por ISRC
# ===========================================================================
# Un FLAC de tienda suele traer un solo artista aunque la cancion sea de
# varios. El ISRC identifica esa grabacion exacta, asi que la lista que
# devuelve el servicio es la del sello, no una adivinanza.
import os
import shutil
from stsync import itunes as itunes_mod
from stsync.model import Track

BANCO = Path(__file__).resolve().parent / "prueba-isrc"
if BANCO.exists():
    shutil.rmtree(BANCO)
BANCO.mkdir(parents=True)
for nombre in ("sola.m4a", "acompanada.m4a", "sin-isrc.m4a"):
    (BANCO / nombre).write_bytes(b"x")


class ComISRC:
    def __init__(self, nombre, artista):
        self.Location = str(BANCO / nombre)
        self.Kind = 1
        self.Name = nombre
        self.Artist = artista


class ClienteISRC:
    def __init__(self):
        self.buscados = []

    def find_by_isrc(self, isrc):
        self.buscados.append(isrc)
        return Track(service="spotify", id="x", title="La Fama",
                     artist="ROSALIA", artists=("ROSALIA", "The Weeknd"))


CANCIONES = [ComISRC("sola.m4a", "ROSALIA"),
             ComISRC("acompanada.m4a", "ROSALIA; The Weeknd"),
             ComISRC("sin-isrc.m4a", "Otro")]
# Esta pasada recorre la biblioteca entera, no un indice: hace falta el doble
# que imita la coleccion COM.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dobles import Biblioteca

Biblioteca.canciones = CANCIONES
itunes_mod.ITunesLibrary = Biblioteca
itunes_mod.find_ffmpeg = lambda configurado: "ffmpeg"
itunes_mod._buscar_ffprobe = lambda ffmpeg: "ffprobe"
itunes_mod._leer_tags = lambda ffprobe, ruta: (
    {} if "sin-isrc" in str(ruta) else {"isrc": "ESA012345678"})

# Lo ya buscado se recuerda entre ejecuciones: si no se limpia, la segunda
# pasada no preguntaria nada y la prueba no probaria nada.
from stsync.paths import state_file
state_file().unlink(missing_ok=True)

cliente = ClienteISRC()
lineas = []
stats = complete_artists_by_isrc(Config(dict(Config().data)), cliente,
                                 lineas.append)
print()
print("\n".join(lineas))
print()
assert CANCIONES[0].Artist == "ROSALIA, The Weeknd", CANCIONES[0].Artist
assert CANCIONES[1].Artist == "ROSALIA; The Weeknd", "esa ya tenia varios"
assert CANCIONES[2].Artist == "Otro", "sin ISRC no se puede buscar"
assert cliente.buscados == ["ESA012345678"], \
    f"solo se busca la que puede ganar algo: {cliente.buscados}"
assert stats.artists == 1, stats.artists
print("5. completa la que iba sola y deja en paz a las demas")

# --- 5b. lo ya preguntado no se vuelve a preguntar --------------------------
CANCIONES[0].Artist = "ROSALIA"
otro = ClienteISRC()
complete_artists_by_isrc(Config(dict(Config().data)), otro, lambda m: None)
print("5b. segunda pasada -> busquedas:", otro.buscados)
assert otro.buscados == [], "deberia haberlo recordado"
assert CANCIONES[0].Artist == "ROSALIA, The Weeknd", "y aun asi completarla"

# --- 6. en simulacion no se toca iTunes ------------------------------------
CANCIONES[0].Artist = "ROSALIA"
complete_artists_by_isrc(Config(dict(Config().data, dry_run=True)),
                         ClienteISRC(), lambda m: None)
assert CANCIONES[0].Artist == "ROSALIA", "la simulacion ha escrito"
print("6. la simulacion no escribe")

shutil.rmtree(BANCO, ignore_errors=True)
print()
print("COMPLETAR OK")
