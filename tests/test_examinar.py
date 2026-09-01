"""El informe de un fichero suelto, que es lo que se mira cuando algo falla.

Los .m4a se montan a mano aqui: un MP4 de verdad no hace falta para comprobar
que se leen bien los bloques, que es lo que dice si el indice va al principio y
si el fichero llega entero.
"""
import os
import shutil
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
from stsync.config import Config
from stsync.convert import ConvertError, _estructura_mp4, _etiquetas, informe_fichero

BANCO = AQUI / "prueba-examinar"
FFMPEG = str(AQUI / "ffmpeg-falso.bat")


def bloque(tipo: str, datos: bytes) -> bytes:
    return (len(datos) + 8).to_bytes(4, "big") + tipo.encode("latin-1") + datos


def montar():
    if BANCO.exists():
        shutil.rmtree(BANCO)
    BANCO.mkdir(parents=True)


def config(**extra):
    cfg = Config(dict(Config().data))
    cfg.set("ffmpeg_path", FFMPEG)
    for clave, valor in extra.items():
        cfg.set(clave, valor)
    return cfg


montar()

# --- 1. el indice al principio y el fichero entero --------------------------
bueno = BANCO / "con-jpg.m4a"
bueno.write_bytes(bloque("ftyp", b"M4A ") + bloque("moov", b"x" * 200)
                  + bloque("mdat", b"y" * 500))
lineas = _estructura_mp4(bueno)
print("1.", lineas)
assert any("ftyp moov mdat" in l for l in lineas), lineas
assert any("va al principio: si" in l for l in lineas), lineas
assert not any("CORTADO" in l for l in lineas), lineas

# --- 2. el indice al final: se dice, porque distingue de donde salio --------
tarde = BANCO / "moov-al-final.m4a"
tarde.write_bytes(bloque("ftyp", b"M4A ") + bloque("mdat", b"y" * 500)
                  + bloque("moov", b"x" * 200))
lineas = _estructura_mp4(tarde)
print("2.", lineas)
assert any("NO, va al final" in l for l in lineas), lineas

# --- 3. un fichero cortado se pilla sin decodificar nada --------------------
entero = bloque("ftyp", b"M4A ") + bloque("moov", b"x" * 200) \
    + bloque("mdat", b"y" * 5000)
cortado = BANCO / "a-medias.m4a"
cortado.write_bytes(entero[:900])          # el mdat dice medir mucho mas
lineas = _estructura_mp4(cortado)
print("3.", lineas)
assert any("CORTADO" in l for l in lineas), lineas

# --- 4. lo que no es MP4 no se mira por bloques -----------------------------
assert _estructura_mp4(BANCO / "algo.flac") == [], "un FLAC no tiene bloques MP4"
print("4. un FLAC no pasa por ahi")

# --- 5. una etiqueta larguisima se corta, pero se dice cuanto mide ----------
salida = _etiquetas({"comment": "L" * 500, "title": "Corta"}, "  ")
print("5.", salida[0][:60], "...")
assert "[500 caracteres]" in salida[0], salida
assert len(salida[0]) < 130, "no se ha cortado"
assert salida[1].strip() == "title            = Corta", salida

# --- 6. el informe entero, con el ffmpeg y el ffprobe de mentira ------------
os.environ["TAGS_FALSOS"] = '{"title": "Meneando la cintura", "artist": "Kaoma"}'
try:
    texto = informe_fichero(config(), bueno)
finally:
    del os.environ["TAGS_FALSOS"]
print()
print(texto)
print()
assert "== con-jpg.m4a ==" in texto
assert "Stream 0  audio  alac" in texto, "no ha listado el stream de audio"
assert "44100 Hz, 2 canales (stereo), s16p, 16 bits" in texto, texto
assert "Duracion: 3:12" in texto, "no ha leido el formato"
assert "Meneando la cintura" in texto, "no ha sacado las etiquetas"
assert "va al principio: si" in texto
assert "sin errores" in texto, "deberia haber decodificado sin quejas"

# --- 7. si el audio no decodifica, el informe lo dice ----------------------
os.environ["FALLA_TODO"] = "1"
try:
    texto = informe_fichero(config(), bueno)
finally:
    del os.environ["FALLA_TODO"]
print("7.", [l for l in texto.splitlines() if "error" in l.lower()])
assert "errores al decodificar" in texto, texto[-400:]
assert "no se pudo convertir" in texto, "deberia traer la queja de ffmpeg"

# --- 8. un fichero que no existe se dice claro ------------------------------
try:
    informe_fichero(config(), BANCO / "fantasma.m4a")
    raise SystemExit("ERROR: deberia haber avisado")
except ConvertError as exc:
    print("8.", exc)

shutil.rmtree(BANCO, ignore_errors=True)
print()
print("EXAMINAR OK")
