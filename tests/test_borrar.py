"""El DELETE de una playlist de TIDAL exige el meta de cada entrada."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stsync.config import Config
from stsync.store import TokenStore
from stsync.tidal import TidalClient


def cliente(items):
    """TidalClient con la lectura y la escritura simuladas."""
    c = TidalClient(Config(), TokenStore(), lambda m: registro.append(m))
    c._paginate = lambda path, params: iter([{"data": items}])
    c._write = lambda method, path, body, params=None: escrituras.append(
        (method, path, body))
    return c


# --- 1. meta con itemId: se manda solo eso -----------------------------------
registro, escrituras = [], []
cliente([
    {"id": "111", "type": "tracks", "meta": {"itemId": "aaa", "addedAt": "2026"}},
    {"id": "222", "type": "tracks", "meta": {"itemId": "bbb", "addedAt": "2026"}},
]).remove_from_playlist("PL", ["111"])
print("1. envio:", escrituras)
metodo, ruta, cuerpo = escrituras[0]
assert metodo == "DELETE", metodo
assert cuerpo == {"data": [{"id": "111", "type": "tracks",
                            "meta": {"itemId": "aaa"}}]}, cuerpo
assert "addedAt" not in str(cuerpo), "solo hace falta el identificador"

# --- 2. la misma cancion repetida: hay que quitar las dos entradas -----------
registro, escrituras = [], []
cliente([
    {"id": "111", "type": "tracks", "meta": {"itemId": "aaa"}},
    {"id": "111", "type": "tracks", "meta": {"itemId": "ccc"}},
]).remove_from_playlist("PL", ["111"])
metas = [d["meta"]["itemId"] for d in escrituras[0][2]["data"]]
print("2. entradas repetidas:", metas)
assert metas == ["aaa", "ccc"], metas

# --- 3. meta con otro nombre: se devuelve tal y como llego -------------------
registro, escrituras = [], []
cliente([{"id": "111", "type": "tracks",
          "meta": {"playlistItemUuid": "zzz"}}]).remove_from_playlist("PL", ["111"])
print("3. meta desconocido:", escrituras[0][2])
assert escrituras[0][2]["data"][0]["meta"] == {"playlistItemUuid": "zzz"}

# --- 4. la cancion ya no esta: ni se intenta ---------------------------------
registro, escrituras = [], []
cliente([{"id": "999", "type": "tracks",
          "meta": {"itemId": "aaa"}}]).remove_from_playlist("PL", ["111"])
print("4. sin escrituras:", escrituras, "| aviso:", registro)
assert not escrituras, escrituras
assert any("ya no estaban" in m for m in registro), registro

print()
print("BORRADO OK")
