"""ffprobe de mentira.

Con -show_format devuelve las etiquetas que le diga TAGS_FALSOS.
Con -show_streams se inventa el stream de audio a partir del nombre del
fichero: si lleva "hires" es de 24 bits y 192 kHz, y si no, calidad CD. El
codec sale de la extension.
"""
import json
import os
import sys

fichero = sys.argv[-1]
nombre = os.path.basename(fichero).lower()

if "-show_streams" in sys.argv:
    extension = os.path.splitext(nombre)[1]
    codec = {".m4a": "alac", ".flac": "flac", ".mp3": "mp3",
             ".wma": "wmav2", ".wav": "pcm_s16le"}.get(extension, "desconocido")
    if "aac" in nombre:
        codec = "aac"
    hires = "hires" in nombre
    json.dump({"streams": [{
        "codec_name": codec,
        "sample_rate": "192000" if hires else "44100",
        "bits_per_raw_sample": "24" if hires else "16",
        "sample_fmt": "s32p" if hires else "s16p",
    }]}, sys.stdout)
else:
    etiquetas = json.loads(os.environ.get("TAGS_FALSOS") or "{}")
    json.dump({"format": {"filename": fichero, "tags": etiquetas}}, sys.stdout)
