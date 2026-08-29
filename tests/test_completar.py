"""Completar en iTunes los datos que faltan (artistas y año), desde TIDAL."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stsync import itunes as mod
from stsync.config import Config
from stsync.itunes import LibraryIndex, complete_tags
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

    def index(self):
        indice = LibraryIndex()
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

print()
print("COMPLETAR OK")
