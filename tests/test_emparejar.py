"""Prueba del emparejamiento TIDAL -> biblioteca de iTunes con datos falsos."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stsync.itunes import LibraryIndex
from stsync.model import Track


class FakeCom:
    """Imita lo poco que se le pide a un IITTrack."""
    def __init__(self, name, artist, db_id, duration):
        self.Name = name
        self.Artist = artist
        self.TrackDatabaseID = db_id
        self.Duration = duration


LIBRARY = [
    FakeCom("Despechá", "ROSALÍA", 1, 155),
    FakeCom("La Fama", "ROSALÍA; The Weeknd", 2, 188),
    FakeCom("Bohemian Rhapsody (Remastered 2011)", "Queen", 3, 355),
    FakeCom("Yesterday", "The Beatles", 4, 125),
    FakeCom("Yesterday", "Boyz II Men", 5, 240),
    FakeCom("Blinding Lights", "The Weeknd", 6, 200),
    FakeCom("Titi Me Pregunto", "Bad Bunny", 7, 243),
]


def td(title, artists, duration_s=0):
    return Track(service="tidal", id="x", title=title,
                 artist=artists[0] if artists else "",
                 artists=tuple(artists), duration_ms=int(duration_s * 1000))


CASES = [
    # (descripcion, track de TIDAL, db_id esperado o None)
    ("acentos y mayusculas", td("Despecha", ["Rosalia"]), 1),
    ("varios artistas con coma vs punto y coma",
     td("La Fama", ["ROSALÍA", "The Weeknd"]), 2),
    ("solo el artista principal", td("La Fama", ["ROSALÍA"]), 2),
    ("iTunes trae el sufijo Remastered", td("Bohemian Rhapsody", ["Queen"]), 3),
    ("mismo titulo, artistas distintos", td("Yesterday", ["The Beatles"]), 4),
    ("mismo titulo, el otro artista", td("Yesterday", ["Boyz II Men"]), 5),
    ("titulo compartido y artista desconocido", td("Yesterday", ["Nadie"]), None),
    ("feat. en el titulo de TIDAL",
     td("Blinding Lights (feat. Rosalia)", ["The Weeknd"]), 6),
    ("no esta en la biblioteca", td("Cancion Inventada", ["Alguien"]), None),
    ("sin artista y titulo unico", td("Despecha", []), 1),
    ("sin artista y titulo ambiguo", td("Yesterday", []), None),
    ("interrogacion perdida en el titulo",
     td("Tití Me Preguntó", ["Bad Bunny"]), 7),
]


def main():
    index = LibraryIndex()
    for com in LIBRARY:
        index.add(com)
    print(f"indice: {index.size} canciones\n")

    fallos = 0
    for description, track, expected in CASES:
        entry = index.find(track)
        got = entry.db_id if entry else None
        ok = got == expected
        fallos += not ok
        print(f"{'OK  ' if ok else 'FALLA'} {description}: esperado={expected} obtenido={got}")
    print()
    print("todos correctos" if not fallos else f"{fallos} fallos")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
