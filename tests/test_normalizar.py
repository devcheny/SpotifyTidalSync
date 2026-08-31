"""Repaso de la biblioteca de iTunes, con iTunes y ffmpeg de mentira."""
import shutil
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
from stsync import normalize as mod
from stsync.config import Config
from stsync.normalize import normalize_library

BANCO = AQUI / "prueba-biblioteca"
FFMPEG = str(AQUI / "ffmpeg-falso.bat")


class Com:
    """Una cancion de iTunes: solo lo que se le pide aqui."""

    def __init__(self, ruta, kind=1, refresco_falla=False):
        self.Location = str(ruta)
        self.Kind = kind
        self.refrescada = False
        self.refresco_falla = refresco_falla

    def UpdateInfoFromFile(self):
        if self.refresco_falla:
            raise OSError("iTunes esta ocupado")
        self.refrescada = True


class Coleccion:
    def __init__(self, items):
        self.items = items

    @property
    def Count(self):
        return len(self.items)

    def Item(self, i):
        return self.items[i - 1]


class LibreriaFalsa:
    canciones: list = []

    def __init__(self, log=None):
        self.log = log or (lambda m: None)
        self.app = type("App", (), {})()
        self.app.LibraryPlaylist = type("PL", (), {})()
        self.app.LibraryPlaylist.Tracks = Coleccion(LibreriaFalsa.canciones)

    def connect(self):
        self.log("  iTunes conectado (falso)")

    def close(self):
        pass


def montar():
    """Cinco canciones: una baja de volumen, una correcta, una hi-res..."""
    if BANCO.exists():
        shutil.rmtree(BANCO)
    BANCO.mkdir(parents=True)
    ficheros = {
        "baja.m4a": b"original baja",          # volumen bajo -> normalizar
        "cancion-ok.m4a": b"original ok",      # ya esta a -9.0 -> no tocar
        "hires-ok.m4a": b"original hires",     # volumen bien, pero 24/192
        "cancion.mp3": b"original mp3",        # con perdida -> no se toca
    }
    for nombre, contenido in ficheros.items():
        (BANCO / nombre).write_bytes(contenido)

    LibreriaFalsa.canciones = [Com(BANCO / n) for n in ficheros]
    LibreriaFalsa.canciones.append(Com(BANCO / "no-existe.m4a"))
    LibreriaFalsa.canciones.append(Com(BANCO / "baja.m4a", kind=2))  # de la nube
    return LibreriaFalsa.canciones


def config(**extra):
    cfg = Config(dict(Config().data))
    cfg.set("ffmpeg_path", FFMPEG)
    for clave, valor in extra.items():
        cfg.set(clave, valor)
    return cfg


mod.ITunesLibrary = LibreriaFalsa

# --- 1. repaso normal --------------------------------------------------------
montar()
lineas = []
stats = normalize_library(config(), lineas.append)
print("\n".join(lineas))
print()
assert stats.revisadas == 3, stats.revisadas          # las 3 sin perdida
assert stats.normalizadas == 2, stats.normalizadas    # la baja y la hi-res
assert stats.bajadas == 1, stats.bajadas              # solo la hi-res
assert stats.ya_estaban == 1, stats.ya_estaban        # cancion-ok
assert stats.saltadas == 3, stats.saltadas            # mp3, inexistente, nube
assert (BANCO / "baja.m4a").read_bytes() != b"original baja", "deberia reescribirse"
assert (BANCO / "cancion-ok.m4a").read_bytes() == b"original ok", "no se toca"
assert (BANCO / "cancion.mp3").read_bytes() == b"original mp3", "mp3 intacto"
print("1. arregla lo que hace falta y deja lo demas en paz")

# --- 2. la hi-res se baja a calidad CD --------------------------------------
montar()
lineas = []
normalize_library(config(), lineas.append)
orden = (AQUI / "ultimo-comando.txt").read_text(encoding="utf-8").splitlines()
print("2. ultima orden de ffmpeg:", " ".join(orden[-8:]))
assert "-ar" in orden and orden[orden.index("-ar") + 1] == "44100", orden
assert "measured_I" in orden[orden.index("-af") + 1], "usa la medicion"

# --- 3. sin bajar la calidad -------------------------------------------------
montar()
stats = normalize_library(config(flac_cd_quality=False), lambda m: None)
print("3. sin bajar calidad -> normalizadas:", stats.normalizadas,
      "| bajadas:", stats.bajadas)
assert stats.bajadas == 0, stats.bajadas
assert stats.normalizadas == 1, "solo la de volumen bajo"
assert (BANCO / "hires-ok.m4a").read_bytes() == b"original hires", "no se toca"

# --- 4. tambien los formatos con perdida ------------------------------------
montar()
stats = normalize_library(config(library_include_lossy=True), lambda m: None)
print("4. con los MP3 -> revisadas:", stats.revisadas, "| saltadas:", stats.saltadas)
assert stats.revisadas == 4, stats.revisadas
assert (BANCO / "cancion.mp3").read_bytes() != b"original mp3", "ahora si se toca"

# --- 5. simulacion -----------------------------------------------------------
montar()
stats = normalize_library(config(dry_run=True), lambda m: None)
print("5. simulacion -> diria que arregla", stats.normalizadas)
assert stats.normalizadas == 2, stats.normalizadas
assert (BANCO / "baja.m4a").read_bytes() == b"original baja", "no debe escribir"

# --- 6. si ffmpeg falla, el original no se pierde ---------------------------
montar()
import os
os.environ["FALLA_TODO"] = "1"
try:
    stats = normalize_library(config(), lambda m: None)
finally:
    os.environ.pop("FALLA_TODO", None)
print("6. con ffmpeg fallando -> fallidas:", len(stats.fallidas))
assert stats.normalizadas == 0, stats.normalizadas
assert len(stats.fallidas) == 2, stats.fallidas
assert (BANCO / "baja.m4a").read_bytes() == b"original baja", "el original sigue ahi"
assert not list(BANCO.glob(".*normalizando*")), "no debe dejar temporales"

# --- 7. iTunes tiene que releer el fichero cambiado -------------------------
canciones = montar()
stats = normalize_library(config(), lambda m: None)
tocadas = [c for c in canciones if c.refrescada]
print("7. se le dice a iTunes que relea:", len(tocadas), "canciones")
assert len(tocadas) == 2, [c.Location for c in tocadas]
assert stats.sin_refrescar == 0, stats.sin_refrescar

# Y si iTunes no puede releerlo, se dice en vez de callarlo: si no, seguiria
# ensenando los kbps de antes sin que nadie sepa por que.
canciones = montar()
for c in canciones:
    c.refresco_falla = True
lineas = []
stats = normalize_library(config(), lineas.append)
print("   si iTunes no puede:", stats.sin_refrescar, "avisadas")
assert stats.sin_refrescar == 2, stats.sin_refrescar
assert any("no ha releido" in l for l in lineas), lineas
assert "sin releer en iTunes" in stats.summary(), stats.summary()

shutil.rmtree(BANCO)
print()
print("NORMALIZAR BIBLIOTECA OK")
