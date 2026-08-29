"""Comprueba el parseo de TIDAL con artistas incluidos y su plan B."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stsync.config import Config
from stsync.http import ApiError
from stsync.store import TokenStore
from stsync.tidal import TidalClient

PAGE_CON_ARTISTAS = {
    "data": [],
    "included": [
        {"type": "tracks", "id": "111",
         "attributes": {"title": "La Fama", "isrc": "ES1234567890",
                        "duration": "PT3M8S"},
         "relationships": {"artists": {"data": [{"id": "a1", "type": "artists"},
                                                {"id": "a2", "type": "artists"}]},
                           "albums": {"data": [{"id": "al1", "type": "albums"}]}}},
        {"type": "artists", "id": "a1", "attributes": {"name": "ROSALÍA"}},
        {"type": "artists", "id": "a2", "attributes": {"name": "The Weeknd"}},
        # El año no esta en la pista: viene de la fecha del album.
        {"type": "albums", "id": "al1",
         "attributes": {"title": "MOTOMAMI", "releaseDate": "2022-03-18"}},
    ],
}

# Formato antiguo: el nombre viaja en el meta de la relacion, sin includes.
PAGE_CON_META = {
    "data": [],
    "included": [
        {"type": "tracks", "id": "222",
         "attributes": {"title": "Yesterday", "isrc": "GB0000000001",
                        "duration": 125},
         "relationships": {"artists": {"data": [
             {"id": "b1", "type": "artists", "meta": {"name": "The Beatles"}}]}}},
    ],
}


def cliente(paginas, fallar_con_include=False):
    client = TidalClient(Config(), TokenStore(), lambda m: print("   log:", m))
    intentos = []

    def fake_paginate(path, params):
        intentos.append(params.get("include"))
        if fallar_con_include and "items.artists" in (params.get("include") or ""):
            raise ApiError("include no soportado", status=400)
        yield from paginas

    client._paginate = fake_paginate
    return client, intentos


print("--- caso 1: TIDAL devuelve los artistas incluidos ---")
client, intentos = cliente([PAGE_CON_ARTISTAS])
tracks = client._collect("/playlists/1/relationships/items")
t = tracks[0]
print(f"   {t} | artistas={t.artists} | isrc={t.isrc} | dur={t.duration_ms}ms")
assert t.artist == "ROSALÍA", t.artist
assert t.artists == ("ROSALÍA", "The Weeknd"), t.artists
assert t.credit == "ROSALÍA, The Weeknd", t.credit
assert t.duration_ms == 188000, t.duration_ms
assert t.year == 2022, t.year
assert intentos == ["items.artists,items.albums"], intentos

print("--- caso 2: el nombre viene en el meta, como antes ---")
client, intentos = cliente([PAGE_CON_META])
t = client._collect("/x")[0]
print(f"   {t} | artistas={t.artists}")
assert t.artist == "The Beatles", t.artist
assert t.duration_ms == 125000, t.duration_ms

print("--- caso 3: TIDAL rechaza include=items.artists -> plan B ---")
client, intentos = cliente([PAGE_CON_META], fallar_con_include=True)
t = client._collect("/x")[0]
print(f"   {t} | intentos={intentos}")
assert intentos == ["items.artists,items.albums", "items.artists", "items"], intentos
assert t.artist == "The Beatles", t.artist
assert t.year == 0, "sin album incluido no hay año que valga"

print("--- caso 4: si tambien falla el basico, el error sube ---")
client, intentos = cliente([PAGE_CON_META])

def siempre_falla(path, params):
    intentos.append(params.get("include"))
    raise ApiError("caida", status=500)
    yield  # pragma: no cover

client._paginate = siempre_falla
try:
    client._collect("/x")
    raise SystemExit("ERROR: deberia haber lanzado ApiError")
except ApiError as exc:
    print(f"   ApiError propagado correctamente: {exc}")
assert intentos == ["items.artists,items.albums", "items.artists", "items"], intentos

print()
print("TIDAL OK")
