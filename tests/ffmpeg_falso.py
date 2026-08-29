"""ffmpeg de mentira: crea el fichero de salida y devuelve 0.

Deja los argumentos recibidos en ultimo-comando.txt para poder comprobarlos.
Con FALLA_CARATULA=1 imita a un ffmpeg que rechaza copiar la portada, para
comprobar el reintento sin ella. Con FALLA_TODO=1 falla siempre.
"""
import os
import sys
from pathlib import Path

args = sys.argv[1:]
salida = args[-1]

registro = Path(__file__).with_name("ultimo-comando.txt")
registro.write_text("\n".join(args), encoding="utf-8")

if os.environ.get("FALLA_TODO"):
    print("Error de prueba: no se pudo convertir", file=sys.stderr)
    sys.exit(1)

if os.environ.get("FALLA_CARATULA") and "attached_pic" in args:
    print("Could not write header: attached_pic no soportado", file=sys.stderr)
    sys.exit(1)

# Un ALAC de calidad CD ocupa bastante menos que el FLAC de 24/192 de origen.
tamano = 400 if "44100" in args else 4000
with open(salida, "wb") as handle:
    handle.write(b"A" * tamano)
sys.exit(0)
