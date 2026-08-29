"""Completar en iTunes los artistas que faltan, tomandolos de TIDAL."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stsync import itunes as mod
from stsync.config import Config
from stsync.itunes import LibraryIndex, complete_artists
from stsync.model import Track


class Com:
    """Lo poco que se le pide a una cancion de iTunes, con Artist escribible."""

    def __init__(self, name, artist, db_id, duration=200):
        self.Name = name
        self.Artist = artist
        self.TrackDatabaseID = db_id
        self.Duration = duration
        self.solo_lectura = False

    def __setattr__(self, campo, valor):
        if campo == "Artist" and getattr(self, "solo_lectura", False):
            raise OSError("el fichero es de solo lectura")
        object.__setattr__(self, campo, valor)


BIBLIOTECA = [
    Com("La Fama", "ROSALIA", 1),                    # le falta el segundo
    Com("Despecha", "", 2),                          # sin artista
    Com("Otra Cancion", "ROSALIA; The Weeknd", 3),   # ya esta completa
    Com("Cancion Ajena", "Otro Artista", 4),         # artista distinto
    Com("Con Candado", "ROSALIA", 5),                # no se deja escribir
]
BIBLIOTECA[-1].solo_lectura = True


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


def pista(titulo, artistas):
    return Track(service="tidal", id=titulo, title=titulo,
                 artist=artistas[0], artists=tuple(artistas), duration_ms=200000)


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
            pista("La Fama", ["ROSALIA"]),   # TIDAL tampoco sabe mas: se salta
        ]


mod.ITunesLibrary = LibreriaFalsa


def correr(**ajustes):
    for com, original in zip(BIBLIOTECA, ORIGINALES):
        object.__setattr__(com, "Artist", original)
    cfg = Config(dict(Config().data))
    for clave, valor in ajustes.items():
        cfg.set(clave, valor)
    lineas = []
    stats = complete_artists(cfg, TidalFalso(), lineas.append)
    return stats, lineas


ORIGINALES = [c.Artist for c in BIBLIOTECA]

# --- 1. de verdad -----------------------------------------------------------
stats, lineas = correr()
print("\n".join(lineas))
print()
assert BIBLIOTECA[0].Artist == "ROSALIA, The Weeknd", BIBLIOTECA[0].Artist
assert BIBLIOTECA[1].Artist == "ROSALIA, Chanel", BIBLIOTECA[1].Artist
assert BIBLIOTECA[2].Artist == "ROSALIA; The Weeknd", "ya estaba: no se toca"
assert BIBLIOTECA[3].Artist == "Otro Artista", "un artista distinto NO se pisa"
assert BIBLIOTECA[4].Artist == "ROSALIA", "si no se puede escribir, se queda"
assert stats.completed == 2, stats.completed
assert stats.already == 1, stats.already
assert len(stats.failed) == 1, stats.failed
print("1. completa lo que falta y respeta lo demas")

# --- 2. simulacion ----------------------------------------------------------
stats, _ = correr(dry_run=True)
assert BIBLIOTECA[0].Artist == "ROSALIA", "la simulacion no escribe"
# Son 3 y no 2 a proposito: sin escribir no hay forma de saber que una de
# ellas es de solo lectura, asi que cuenta las que intentaria.
assert stats.completed == 3, stats.completed
assert not stats.failed, "en simulacion no puede fallar ninguna escritura"
print("2. la simulacion dice que haria sin tocar nada")

# --- 3. solo las playlists elegidas -----------------------------------------
stats, _ = correr(itunes_playlists=["Repetida"])
assert BIBLIOTECA[0].Artist == "ROSALIA, The Weeknd"
assert BIBLIOTECA[1].Artist == "", "esa cancion no esta en la lista elegida"
print("3. respeta las playlists elegidas")

print()
print("COMPLETAR OK")
