"""Prueba del motor TIDAL -> iTunes con dobles de iTunes y de la API."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stsync import itunes as itunes_mod
from stsync.config import Config
from stsync.itunes import ITunesSync, LibraryIndex
from stsync.model import Track


# --------------------------------------------------------------- dobles COM
class FakeTrackCom:
    def __init__(self, name, artist, db_id, duration=200):
        self.Name = name
        self.Artist = artist
        self.TrackDatabaseID = db_id
        self.Duration = duration
        self.deleted = False

    def Delete(self):
        self.deleted = True


class FakeCollection:
    def __init__(self, items):
        self.items = items

    @property
    def Count(self):
        return len(self.items)

    def Item(self, i):
        return self.items[i - 1]


class FakePlaylist:
    def __init__(self, name, tracks=None, smart=False):
        self.Name = name
        self.Smart = smart
        self._tracks = list(tracks or [])

    @property
    def Tracks(self):
        return FakeCollection(self._tracks)

    def AddTrack(self, track):
        self._tracks.append(track)


class FakeLibrary:
    """Sustituye a ITunesLibrary: misma superficie, sin COM."""
    created = []

    def __init__(self, log=None):
        self.log = log or (lambda m: None)
        self.playlists = {
            "TIDAL - Ya existente": FakePlaylist(
                "TIDAL - Ya existente",
                [LIBRARY[0], FakeTrackCom("Sobrante", "Nadie", 99)]),
            "TIDAL - Lista inteligente": FakePlaylist(
                "TIDAL - Lista inteligente", [], smart=True),
        }

    def connect(self):
        self.log("  iTunes conectado (falso)")

    def close(self):
        pass

    def playlist(self, name):
        return self.playlists.get(name)

    def create_playlist(self, name):
        pl = FakePlaylist(name)
        self.playlists[name] = pl
        FakeLibrary.created.append(name)
        return pl

    @staticmethod
    def is_writable(playlist):
        return not playlist.Smart

    def index(self):
        index = LibraryIndex()
        for com in LIBRARY:
            index.add(com)
        return index


LIBRARY = [
    FakeTrackCom("Despechá", "ROSALÍA", 1, 155),
    FakeTrackCom("La Fama", "ROSALÍA; The Weeknd", 2, 188),
    FakeTrackCom("Yesterday", "The Beatles", 4, 125),
]


# -------------------------------------------------------------- doble TIDAL
class FakeTidal:
    def __init__(self):
        self.added = {}
        self.removed = {}
        self.created = []
        self.lists = [
            {"id": "1", "attributes": {"name": "Ya existente"}},
            {"id": "2", "attributes": {"name": "Nueva"}},
            {"id": "3", "attributes": {"name": "Vacia"}},
            {"id": "4", "attributes": {"name": "Lista inteligente"}},
        ]
        self.tracks = {
            "1": [t("Despecha", ["Rosalia"]), t("No la tengo", ["Alguien"])],
            "2": [t("La Fama", ["ROSALÍA", "The Weeknd"]), t("Yesterday", ["The Beatles"])],
            "3": [],
            "4": [t("Despecha", ["Rosalia"])],
        }

    def my_playlists(self):
        return self.lists

    def playlist_tracks(self, playlist_id):
        return self.tracks.get(str(playlist_id), [])

    def create_playlist(self, name, description=""):
        self.created.append(name)
        new_id = str(100 + len(self.created))
        self.tracks[new_id] = []
        return {"id": new_id, "attributes": {"name": name}}

    def add_to_playlist(self, playlist_id, ids):
        self.added.setdefault(str(playlist_id), []).extend(ids)
        self.tracks.setdefault(str(playlist_id), []).extend(
            t(i, ["x"], track_id=i) for i in ids)

    def remove_from_playlist(self, playlist_id, ids):
        self.removed.setdefault(str(playlist_id), []).extend(ids)
        self.tracks[str(playlist_id)] = [
            x for x in self.tracks.get(str(playlist_id), []) if x.id not in ids]


def t(title, artists, track_id=None):
    return Track(service="tidal", id=track_id or title.lower().replace(" ", "-"),
                 title=title, artist=artists[0] if artists else "",
                 artists=tuple(artists))


# ------------------------------------------------------------------ escenarios
def run(name, **overrides):
    itunes_mod.ITunesLibrary = FakeLibrary
    FakeLibrary.created = []
    cfg = Config(dict(Config().data))
    cfg.set("itunes_playlist_prefix", "TIDAL - ")
    for key, value in overrides.items():
        cfg.set(key, value)

    tidal = FakeTidal()
    lines = []
    engine = ITunesSync(cfg, tidal, lines.append)
    stats = engine.run()

    print(f"===== {name} =====")
    print("\n".join(lines))
    print(f"-> {stats.summary()}")
    return stats, tidal, engine


# 1. Ejecucion normal
stats, tidal, _ = run("normal")
lib = itunes_mod.ITunesLibrary(lambda m: None)
# Vacia se omite y la lista inteligente se rechaza: quedan 2 procesadas.
assert stats.playlists == 2, stats.playlists
assert stats.created == 1, stats.created              # solo "Nueva"
assert stats.added == 2, stats.added                  # las 2 de "Nueva"
faltan = [(pl, cancion) for pl, cancion, _motivo in stats.missing]
assert ("Ya existente", "Alguien - No la tengo") in faltan, stats.missing
print("   motivo:", stats.missing[0][2])
print("   creadas en iTunes:", FakeLibrary.created)
print()

# 2. Simulacion: no debe crear nada en iTunes
stats, tidal, _ = run("simulacion", dry_run=True)
assert FakeLibrary.created == [], FakeLibrary.created
print()

# 3. Solo una playlist elegida
stats, tidal, _ = run("solo una", itunes_playlists=["Nueva"])
assert stats.playlists == 1, stats.playlists
print()

# 4. Espejo: quita de iTunes lo que ya no esta en TIDAL
stats, tidal, _ = run("espejo", itunes_remove_extra=True)
assert stats.removed == 1, stats.removed
print()

# 5. Playlist de faltantes en TIDAL
stats, tidal, _ = run("faltantes", itunes_missing_playlist=True)
assert tidal.created == ["Ya existente - Faltantes en iTunes"], tidal.created
assert tidal.added == {"101": ["no-la-tengo"]}, tidal.added
print()

# 6. La lista de faltantes suelta lo que ya no falta
itunes_mod.ITunesLibrary = FakeLibrary
cfg = Config(dict(Config().data))
cfg.set("itunes_playlist_prefix", "TIDAL - ")
cfg.set("itunes_missing_playlist", True)
cfg.set("itunes_playlists", ["Nueva"])      # sus 2 canciones si estan en iTunes

tidal = FakeTidal()
# Ya existia la lista de faltantes, con una que entonces no estaba y ahora si.
tidal.lists.append({"id": "9", "attributes": {"name": "Nueva - Faltantes en iTunes"}})
tidal.tracks["9"] = [t("La Fama", ["ROSALIA"], track_id="ya-la-tengo")]

lineas = []
stats = ITunesSync(cfg, tidal, lineas.append).run()
print("===== faltantes al dia =====")
print("\n".join(lineas))
print("-> quitadas de la lista:", tidal.removed)
assert tidal.removed == {"9": ["ya-la-tengo"]}, tidal.removed
assert tidal.tracks["9"] == [], tidal.tracks["9"]
assert not tidal.added.get("9"), tidal.added
print()

print("TODOS LOS ESCENARIOS OK")
