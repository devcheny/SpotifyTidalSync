"""Portadas fuera de norma en los .m4a, con iTunes y ffmpeg de mentira."""
import shutil
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
from stsync import artwork as mod
from stsync import itunes as itunes_mod
from stsync.artwork import check_artwork
from stsync.config import Config
from stsync.convert import args_caratula
from stsync.normalize import downsample_library, refresh_info
from dobles import Biblioteca as LibreriaFalsa, Cancion as Com

BANCO = AQUI / "prueba-caratulas"
FFMPEG = str(AQUI / "ffmpeg-falso.bat")


def montar():
    """Ficheros cuyo nombre le dice al ffprobe falso que portada llevan."""
    if BANCO.exists():
        shutil.rmtree(BANCO)
    BANCO.mkdir(parents=True)
    nombres = [
        "con-png.m4a",        # portada en PNG -> hay que arreglarla
        "otra-png.m4a",       # idem
        "con-jpg.m4a",        # ya viene en JPEG -> no se toca
        "sin-portada.m4a",    # no lleva ninguna
        "con-png.flac",       # un FLAC con PNG esta en su derecho
        "con-png.mp3",        # y un MP3 tambien
    ]
    for nombre in nombres:
        (BANCO / nombre).write_bytes(b"original " + nombre.encode())
    LibreriaFalsa.canciones = [Com(BANCO / n) for n in nombres]
    LibreriaFalsa.canciones.append(Com(BANCO / "no-existe.m4a"))
    return LibreriaFalsa.canciones


def config(**extra):
    cfg = Config(dict(Config().data))
    cfg.set("ffmpeg_path", FFMPEG)
    for clave, valor in extra.items():
        cfg.set(clave, valor)
    return cfg


# Las cuatro pasadas recorren la biblioteca por el mismo sitio, asi que con
# parchear ahi vale para todas.
itunes_mod.ITunesLibrary = LibreriaFalsa

# --- 1. que args_caratula decide, que es el nucleo del arreglo --------------
assert args_caratula("") == ["-vn"], args_caratula("")
assert "-c:v" in args_caratula("mjpeg") and "copy" in args_caratula("mjpeg")
png = args_caratula("png")
assert "mjpeg" in png and "copy" not in png, png
assert "-frames:v" in png, "una portada es una imagen, no un video"
# Sin saber que trae, se recodifica: es lo unico seguro.
assert "mjpeg" in args_caratula("desconocida")
print("1. args_caratula: copia el JPEG, recodifica lo demas")

# --- 2. simulacion: cuenta y no toca nada -----------------------------------
montar()
antes = {f.name: f.read_bytes() for f in BANCO.iterdir()}
lineas = []
stats = check_artwork(config(dry_run=True), lineas.append)
print("\n".join(lineas))
print()
assert stats.revisadas == 4, stats.revisadas        # solo los .m4a que existen
assert stats.malas == 2, stats.malas
assert stats.correctas == 1, stats.correctas
assert stats.sin_caratula == 1, stats.sin_caratula
assert stats.arregladas == 0, "en simulacion no se toca nada"
assert {f.name: f.read_bytes() for f in BANCO.iterdir()} == antes, "ha tocado algo"
assert stats.formatos == {"png": 2, "mjpeg": 1}, stats.formatos
print("2. simulacion: encuentra las dos PNG y no reescribe nada")

# --- 3. de verdad: reescribe solo las malas ---------------------------------
canciones = montar()
stats = check_artwork(config(), lambda m: None)
print("3.", stats.summary())
assert stats.arregladas == 2, stats.arregladas
assert (BANCO / "con-png.m4a").read_bytes() != b"original con-png.m4a"
assert (BANCO / "con-jpg.m4a").read_bytes() == b"original con-jpg.m4a", \
    "la que ya estaba bien no se toca"
assert (BANCO / "con-png.flac").read_bytes() == b"original con-png.flac", \
    "un FLAC con PNG no tiene ningun problema"
# El audio se copia, que es lo que hace que esto sea rapido y sin perdida
orden = (AQUI / "ultimo-comando.txt").read_text(encoding="utf-8").splitlines()
assert "-c:a" in orden and orden[orden.index("-c:a") + 1] == "copy", orden
assert "-af" not in orden, "aqui no se normaliza nada"
assert "+faststart" in orden, orden
# Y iTunes tiene que releerlas, que si no se queda con el tamano de antes
arregladas = [c for c in canciones if "png.m4a" in c.Location]
assert all(c.refrescada for c in arregladas), "iTunes no las ha releido"
print("   audio copiado tal cual, y iTunes releyendo las dos")

# --- 4. con la casilla puesta, la portada se quita --------------------------
montar()
check_artwork(config(artwork_remove=True), lambda m: None)
orden = (AQUI / "ultimo-comando.txt").read_text(encoding="utf-8").splitlines()
assert "-vn" in orden, orden
assert "mjpeg" not in orden, orden
print("4. quitar la portada en vez de convertirla")

# --- 5. si ffmpeg falla, el fichero de siempre sigue entero -----------------
import os
montar()
os.environ["FALLA_TODO"] = "1"
try:
    stats = check_artwork(config(), lambda m: None)
finally:
    del os.environ["FALLA_TODO"]
print("5. con ffmpeg roto ->", stats.summary())
assert stats.arregladas == 0, stats.arregladas
assert len(stats.fallidas) == 2, stats.fallidas
assert (BANCO / "con-png.m4a").read_bytes() == b"original con-png.m4a", \
    "el original tiene que quedar intacto"
assert not list(BANCO.glob(".*")), "no puede quedar ningun temporal"


# ===========================================================================
# Releer los datos en iTunes
# ===========================================================================
# --- 6. solo se releen las que declaran mas de lo que cabe en un CD ---------
LibreriaFalsa.canciones = [
    Com(BANCO / "con-jpg.m4a", bitrate=9216, rate=192000),   # dato viejo
    Com(BANCO / "sin-portada.m4a", bitrate=1411, rate=44100),  # ya esta bien
    Com(BANCO / "con-png.m4a", bitrate=900, rate=48000),     # 48 kHz: tambien
    Com(BANCO / "no-existe.m4a", bitrate=9216, rate=192000),  # sin fichero
]
stats = refresh_info(config(), lambda m: None)
print("6.", stats.summary())
assert stats.miradas == 2, stats.miradas
assert stats.refrescadas == 2, stats.refrescadas
assert LibreriaFalsa.canciones[0].refrescada
assert not LibreriaFalsa.canciones[1].refrescada, "esa ya declaraba lo correcto"
assert LibreriaFalsa.canciones[2].refrescada

# --- 7. si iTunes se niega, se apunta y se sigue ----------------------------
LibreriaFalsa.canciones = [
    Com(BANCO / "con-jpg.m4a", bitrate=9216, refresco_falla=True),
    Com(BANCO / "con-png.m4a", bitrate=9216),
]
stats = refresh_info(config(), lambda m: None)
print("7. con iTunes ocupado ->", stats.summary())
assert stats.refrescadas == 1, stats.refrescadas
assert len(stats.fallidas) == 1, stats.fallidas

# --- 8. en simulacion se cuentan pero no se tocan ---------------------------
LibreriaFalsa.canciones = [Com(BANCO / "con-jpg.m4a", bitrate=9216)]
stats = refresh_info(config(dry_run=True), lambda m: None)
assert stats.miradas == 1 and stats.refrescadas == 0, stats.summary()
assert not LibreriaFalsa.canciones[0].refrescada
print("8. la simulacion solo cuenta")


# ===========================================================================
# Bajar a calidad CD lo que se quedo por encima
# ===========================================================================
# Un .m4a de 24/192 no puede declarar su frecuencia: ese campo de la cabecera
# solo llega a 65535 Hz, asi que se queda a cero y hay programas que se
# cierran al leerlo.
# --- 9. solo se tocan las que estan por encima ------------------------------
montar()
(BANCO / "hires-con-jpg.m4a").write_bytes(b"original hires")
LibreriaFalsa.canciones = [
    Com(BANCO / "hires-con-jpg.m4a"),      # el ffprobe falso la da a 192 kHz
    Com(BANCO / "con-jpg.m4a"),            # ya esta a 44,1
    Com(BANCO / "con-png.mp3"),            # con perdida: no se toca
    Com(BANCO / "no-existe.m4a"),
]
lineas = []
stats = downsample_library(config(), lineas.append)
print()
print("\n".join(lineas))
print()
assert stats.altas == 1, stats.altas
assert stats.bajadas == 1, stats.bajadas
assert stats.revisadas == 2, stats.revisadas    # el mp3 ni se cuenta
assert (BANCO / "hires-con-jpg.m4a").read_bytes() != b"original hires"
assert (BANCO / "con-jpg.m4a").read_bytes() == b"original con-jpg.m4a", \
    "esa ya estaba a 44,1 y no habia que tocarla"
orden = (AQUI / "ultimo-comando.txt").read_text(encoding="utf-8").splitlines()
assert "-ar" in orden and orden[orden.index("-ar") + 1] == "48000", orden
assert "-sample_fmt" not in orden, "el techo de 24/48 conserva los bits"
assert "-af" not in orden, "aqui no se normaliza: solo cambia la calidad"
assert LibreriaFalsa.canciones[0].refrescada, "iTunes tiene que releerla"
print("9. baja solo la de 192 kHz al techo, y sin tocar el volumen")

# El mismo trabajo con el otro techo llega hasta la calidad CD.
montar()
(BANCO / "hires-con-jpg.m4a").write_bytes(b"original hires")
LibreriaFalsa.canciones = [Com(BANCO / "hires-con-jpg.m4a")]
downsample_library(config(quality_target="cd"), lambda m: None)
orden = (AQUI / "ultimo-comando.txt").read_text(encoding="utf-8").splitlines()
assert orden[orden.index("-ar") + 1] == "44100", orden
assert "-sample_fmt" in orden, "calidad CD si baja los bits"
print("   y con el techo en calidad CD, a 44100 y 16 bits")

# --- 10. en simulacion se cuentan y no se toca nada -------------------------
montar()
(BANCO / "hires-con-jpg.m4a").write_bytes(b"original hires")
LibreriaFalsa.canciones = [Com(BANCO / "hires-con-jpg.m4a")]
stats = downsample_library(config(dry_run=True), lambda m: None)
assert stats.altas == 1 and stats.bajadas == 0, stats.summary()
assert (BANCO / "hires-con-jpg.m4a").read_bytes() == b"original hires"
print("10. la simulacion solo cuenta")

# --- 11. si ffmpeg falla, la cancion de siempre sigue entera ----------------
os.environ["FALLA_TODO"] = "1"
try:
    stats = downsample_library(config(), lambda m: None)
finally:
    del os.environ["FALLA_TODO"]
print("11. con ffmpeg roto ->", stats.summary())
assert stats.bajadas == 0 and len(stats.fallidas) == 1, stats.summary()
assert (BANCO / "hires-con-jpg.m4a").read_bytes() == b"original hires"
assert not list(BANCO.glob(".*")), "no puede quedar ningun temporal"


# ===========================================================================
# Arreglar una sola cancion, para probar antes de soltarlo en 7000
# ===========================================================================
from stsync.artwork import fix_one_file

# --- 12. una que necesita las dos cosas -------------------------------------
montar()
mala = BANCO / "hires-con-png.m4a"     # 192 kHz Y portada PNG
mala.write_bytes(b"original mala")
texto = fix_one_file(config(), mala, lambda m: None)
print("12. lo que dice que hace:")
for linea in texto.splitlines():
    if linea.startswith("  - "):
        print("   ", linea.strip())
assert "bajar la calidad" in texto, texto
assert "portada de png a JPEG" in texto, texto
assert "ANTES" in texto and "DESPUES" in texto, "falta el antes y el despues"
assert mala.read_bytes() != b"original mala", "no la ha tocado"

# --- 13. una que ya esta bien: se dice y no se toca -------------------------
buena = BANCO / "con-jpg.m4a"
texto = fix_one_file(config(), buena, lambda m: None)
print("13.", [l.strip() for l in texto.splitlines() if l.startswith("  - ")])
assert "ya esta bien" in texto, texto
assert buena.read_bytes() == b"original con-jpg.m4a", "la ha tocado"

# --- 14. en simulacion dice lo que haria y no toca nada ---------------------
montar()
mala = BANCO / "hires-con-png.m4a"
mala.write_bytes(b"original mala")
texto = fix_one_file(config(dry_run=True), mala, lambda m: None)
assert "simulacion" in texto, texto
assert "DESPUES" not in texto, "no hay despues si no se ha hecho nada"
assert mala.read_bytes() == b"original mala", "la ha tocado en simulacion"
print("14. la simulacion dice lo que haria y no toca nada")

shutil.rmtree(BANCO, ignore_errors=True)
print()
print("CARATULAS OK")
