"""Prueba de la actualizacion desde GitHub, sin salir a internet."""
import io
import shutil
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stsync import updates
from stsync.updates import Release, UpdateError

AQUI = Path(__file__).parent
BANCO = AQUI / "prueba-updates"


class Respuesta:
    def __init__(self, codigo=200, datos=None, contenido=b""):
        self.status_code = codigo
        self._datos = datos or {}
        self.content = contenido

    def json(self):
        return self._datos

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.RequestException(f"HTTP {self.status_code}")


def responder(**por_url):
    """Sustituye requests.get: devuelve lo pactado segun lo que pida."""
    def falso(url, **kwargs):
        for trozo, respuesta in por_url.items():
            if trozo in url:
                return respuesta
        raise AssertionError(f"peticion inesperada: {url}")
    updates.requests.get = falso


def zip_con(ficheros: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for nombre, contenido in ficheros.items():
            zf.writestr(f"DevCheny-SpotifyTidalSync-abc123/{nombre}", contenido)
    return buffer.getvalue()


# --- 1. comparacion de versiones --------------------------------------------
casos = [
    ("v1.1.0", "1.0.0", True),
    ("1.0.1", "1.0.0", True),
    ("v1.0.0", "1.0.0", False),
    ("v0.9.9", "1.0.0", False),
    ("v1.2.0-beta", "1.1.9", True),
    ("v2", "1.9.9", True),
]
for etiqueta, actual, esperado in casos:
    hay = updates.hay_novedad(Release(etiqueta, "u", ""), actual)
    print(f"   {etiqueta} sobre {actual}: {'nueva' if hay else 'no'}")
    assert hay == esperado, (etiqueta, actual, hay)
print("1. versiones OK")

# --- 2. respuestas de GitHub -------------------------------------------------
responder(**{"releases/latest": Respuesta(200, {
    "tag_name": "v1.4.0", "zipball_url": "https://x/zip", "body": "notas"})})
hay, release = updates.check("DevCheny/SpotifyTidalSync")
print(f"2. ultima: {release.version} | novedad: {hay} | notas: {release.notes!r}")
assert release.version == "v1.4.0"

for codigo, texto in ((404, "no existe"), (403, "exceso"), (500, "respondido")):
    responder(**{"releases/latest": Respuesta(codigo)})
    try:
        updates.check("DevCheny/SpotifyTidalSync")
        raise SystemExit(f"ERROR: {codigo} deberia fallar")
    except UpdateError as exc:
        assert texto in str(exc), (codigo, exc)
        print(f"   {codigo}: {exc}")

try:
    updates.check("sin-barra")
    raise SystemExit("ERROR: deberia exigir usuario/proyecto")
except UpdateError as exc:
    print("   repo mal escrito:", exc)

# --- 3. aplicar la actualizacion --------------------------------------------
def preparar() -> Path:
    if BANCO.exists():
        shutil.rmtree(BANCO)
    (BANCO / "stsync").mkdir(parents=True)
    (BANCO / ".venv" / "Scripts").mkdir(parents=True)
    (BANCO / "main.py").write_text("viejo", encoding="utf-8")
    (BANCO / "stsync" / "gui.py").write_text("viejo", encoding="utf-8")
    (BANCO / ".venv" / "Scripts" / "python.exe").write_text("no tocar", encoding="utf-8")
    (BANCO / "requirements.txt").write_text("requests>=2.31\n", encoding="utf-8")
    return BANCO


lineas = []
destino = preparar()
updates._instalar_dependencias = lambda carpeta, log: log("  (dependencias omitidas)")
responder(**{"/zip": Respuesta(200, contenido=zip_con({
    "main.py": "print('nuevo')\n",
    "stsync/gui.py": "nuevo\n",
    "stsync/nuevo.py": "nuevo\n",
    ".venv/Scripts/python.exe": "INTRUSO",
}))})
updates.apply_release(Release("v1.4.0", "https://x/zip", ""), lineas.append, destino)
print("3.", " / ".join(l.strip() for l in lineas))
assert (destino / "main.py").read_text(encoding="utf-8") == "print('nuevo')\n"
assert (destino / "stsync" / "nuevo.py").is_file(), "deberia traer los ficheros nuevos"
assert (destino / ".venv" / "Scripts" / "python.exe").read_text(encoding="utf-8") \
    == "no tocar", "el entorno virtual no se toca NUNCA"
print("   el .venv sigue intacto")

# --- 4. una descarga que no vale no debe tocar nada -------------------------
for descripcion, contenido in (
    ("zip sin main.py", zip_con({"leeme.txt": "nada"})),
    ("main.py roto", zip_con({"main.py": "def (:\n", "stsync/x.py": ""})),
    ("no es un zip", b"esto no es un zip"),
):
    destino = preparar()
    responder(**{"/zip": Respuesta(200, contenido=contenido)})
    try:
        updates.apply_release(Release("v9", "https://x/zip", ""), lambda m: None, destino)
        raise SystemExit(f"ERROR: '{descripcion}' deberia fallar")
    except UpdateError as exc:
        assert (destino / "main.py").read_text(encoding="utf-8") == "viejo", descripcion
        print(f"4. {descripcion}: rechazado y sin tocar nada")

shutil.rmtree(BANCO)
print()
print("ACTUALIZAR OK")
