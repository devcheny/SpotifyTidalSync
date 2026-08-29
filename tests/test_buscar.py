"""Prueba de --buscar con una biblioteca y un TIDAL de mentira."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stsync import itunes as mod
from stsync.config import Config
from stsync.model import Track


class Com:
    def __init__(self, name, artist, db_id, duration=227):
        self.Name = name
        self.Artist = artist
        self.TrackDatabaseID = db_id
        self.Duration = duration


class Coleccion:
    def __init__(self, items):
        self.items = items

    @property
    def Count(self):
        return len(self.items)

    def Item(self, i):
        return self.items[i - 1]


BIBLIOTECA = [
    # El acento se perdio al etiquetar: asi es como lo ve el programa.
    Com("Hay Que Venir Al Sur", "Raffaella Carr?", 1, 227),
    Com("Otra Cosa", "Alguien", 2, 180),
]


class LibreriaFalsa:
    def __init__(self, log=None):
        self.log = log or (lambda m: None)
        self.app = type("App", (), {})()
        self.app.LibraryPlaylist = type("PL", (), {})()
        self.app.LibraryPlaylist.Tracks = Coleccion(BIBLIOTECA)

    def connect(self):
        self.log("  iTunes conectado (falso)")

    def close(self):
        pass


class TidalFalso:
    def my_playlists(self):
        return [{"id": "1", "attributes": {"name": "Animacion Old"}},
                {"id": "2", "attributes": {"name": "Animacion Old - Faltantes en iTunes"}}]

    def playlist_tracks(self, playlist_id):
        if str(playlist_id) != "1":
            raise AssertionError("no debe mirar dentro de la lista de faltantes")
        return [Track(service="tidal", id="t1", title="Hay que venir al Sur",
                      artist="Raffaella Carr\u00e0", artists=("Raffaella Carr\u00e0",),
                      duration_ms=225000)]


mod.ITunesLibrary = LibreriaFalsa
lineas = []
mod.inspect_track(Config(), TidalFalso(), "hay que venir", lineas.append)
print("\n".join(lineas))

texto = "\n".join(lineas)
assert "Raffaella Carr?" in texto, "deberia ensenar el nombre tal cual"
assert "OJO" in texto, "deberia avisar de la etiqueta rota"
assert "CASA con" in texto, "con el arreglo del acento perdido deberia casar"
print()
print("BUSCAR OK")
