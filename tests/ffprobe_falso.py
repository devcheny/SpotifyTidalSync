"""ffprobe de mentira: devuelve las etiquetas que le diga TAGS_FALSOS.

TAGS_FALSOS lleva un JSON con lo que se supone que trae el fichero, por
ejemplo {"ARTIST": "Xiyo"}. Sin esa variable, el fichero no tiene ninguna.
"""
import json
import os
import sys

etiquetas = json.loads(os.environ.get("TAGS_FALSOS") or "{}")
json.dump({"format": {"filename": sys.argv[-1], "tags": etiquetas}}, sys.stdout)
