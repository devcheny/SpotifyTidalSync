"""ffprobe de mentira.

Devuelve lo que se le pida, y se lo inventa a partir del nombre del fichero:

- `-show_streams`: el stream de audio. Si el nombre lleva "hires" es de 24 bits
  y 192 kHz, y si no, calidad CD. El codec sale de la extension.
- `-show_streams -select_streams v:0`: la portada, que es donde va. "png" o
  "jpg" en el nombre dan ese formato; sin ninguno de los dos, no lleva.
- `-show_format`: las etiquetas que le diga TAGS_FALSOS, o TAGS_SALIDA si el
  fichero es el que acaba de escribir el ffmpeg de mentira.

Los dos primeros se pueden pedir a la vez, como hace el informe de un fichero.
"""
import json
import os
import sys

def recien_escrito():
    """El fichero que escribio el ultimo ffmpeg, tal cual se lo pasaron."""
    registro = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "ultimo-comando.txt")
    try:
        with open(registro, encoding="utf-8") as handle:
            lineas = handle.read().splitlines()
    except OSError:
        return ""
    return lineas[-1] if lineas else ""


fichero = sys.argv[-1]
nombre = os.path.basename(fichero).lower()
salida = {}

if "-show_streams" in sys.argv:
    if "v:0" in sys.argv:
        if "png" in nombre:
            salida["streams"] = [{"codec_name": "png"}]
        elif "jpg" in nombre or "jpeg" in nombre:
            salida["streams"] = [{"codec_name": "mjpeg"}]
        else:
            salida["streams"] = []
    else:
        extension = os.path.splitext(nombre)[1]
        codec = {".m4a": "alac", ".flac": "flac", ".mp3": "mp3",
                 ".wma": "wmav2", ".wav": "pcm_s16le"}.get(extension, "desconocido")
        if "aac" in nombre:
            codec = "aac"
        hires = "hires" in nombre
        salida["streams"] = [{
            "index": 0,
            "codec_type": "audio",
            "codec_name": codec,
            "codec_tag_string": "alac" if codec == "alac" else "?",
            "channels": 2,
            "channel_layout": "stereo",
            "sample_rate": "192000" if hires else "44100",
            "bits_per_raw_sample": "24" if hires else "16",
            "sample_fmt": "s32p" if hires else "s16p",
            "start_time": "0.000000",
        }]

if "-show_format" in sys.argv or "-show_streams" not in sys.argv:
    # TAGS_SALIDA, si esta, es lo que trae el fichero que ffmpeg acaba de
    # escribir: asi se puede comprobar que se detectan las etiquetas que se han
    # quedado por el camino. Cual es se sabe con exactitud, sin adivinarlo por
    # el nombre: es el ultimo argumento del ultimo ffmpeg que se lanzo.
    cual = "TAGS_SALIDA" if (os.environ.get("TAGS_SALIDA")
                             and fichero == recien_escrito()) else "TAGS_FALSOS"
    etiquetas = json.loads(os.environ.get(cual) or "{}")
    salida["format"] = {
        "filename": fichero,
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "duration": "192.000000",
        "bit_rate": "1411000",
        "tags": etiquetas,
    }

json.dump(salida, sys.stdout)
