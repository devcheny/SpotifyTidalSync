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

# La pasada de medicion no convierte nada: suelta el JSON por stderr, como el
# ffmpeg de verdad, y se acaba ahi. MEDIDA_FALSA dice que volumen tiene.
if any("print_format=json" in a for a in args):
    Path(__file__).with_name("ultima-medicion.txt").write_text(
        "\n".join(args), encoding="utf-8")
    if os.environ.get("MEDICION_ROTA"):
        print("no se ha podido medir", file=sys.stderr)
        sys.exit(1)
    # El volumen sale del nombre: "-ok" ya esta en su sitio, el resto no.
    entrada = args[args.index("-i") + 1] if "-i" in args else ""
    nivel = "-9.0" if "-ok" in os.path.basename(entrada).lower() \
        else os.environ.get("MEDIDA_FALSA", "-16.55")
    print("[Parsed_loudnorm_0 @ 000] \n{", file=sys.stderr)
    print(f'  "input_i" : "{nivel}",', file=sys.stderr)
    print('  "input_tp" : "-2.06",', file=sys.stderr)
    print('  "input_lra" : "6.20",', file=sys.stderr)
    print('  "input_thresh" : "-27.15",', file=sys.stderr)
    print('  "target_offset" : "0.29"', file=sys.stderr)
    print("}", file=sys.stderr)
    sys.exit(0)

registro = Path(__file__).with_name("ultimo-comando.txt")
registro.write_text("\n".join(args), encoding="utf-8")

if os.environ.get("FALLA_TODO"):
    print("Error de prueba: no se pudo convertir", file=sys.stderr)
    sys.exit(1)

# "-f null -" solo decodifica para ver si el fichero esta sano: no escribe
# nada, y menos un fichero llamado "-".
if "null" in args:
    sys.exit(0)

if os.environ.get("SIN_ESPACIO"):
    print("[out#0/ipod @ 0] Error closing file: No space left on device",
          file=sys.stderr)
    sys.exit(1)

if os.environ.get("FALLA_CARATULA") and "attached_pic" in args:
    print("Could not write header: attached_pic no soportado", file=sys.stderr)
    sys.exit(1)

def caja(tipo, cuerpo):
    return (len(cuerpo) + 8).to_bytes(4, "big") + tipo + cuerpo


def m4a_falso(rate, con_audio=True, segundos=180):
    """Un .m4a con la forma justa para que lo lean nuestros comprobadores.

    No suena, pero tiene los bloques que se miran antes de dar una conversion
    por buena: el mvhd con la duracion y la entrada de audio con su frecuencia
    en el campo de 16.16 bits. Con con_audio=False sale la cancion sin pista,
    que es justo el desastre del que hay que protegerse.
    """
    mvhd = caja(b"mvhd", bytes(12) + (1000).to_bytes(4, "big")
                + (segundos * 1000).to_bytes(4, "big") + bytes(80))
    entrada = bytes(8) + bytes(8) + bytes(8) + (rate << 16).to_bytes(4, "big")
    stsd = caja(b"stsd", bytes(8) + (caja(b"alac", entrada) if con_audio
                                     else caja(b"mjpg", entrada)))
    trak = caja(b"trak", caja(b"mdia", caja(b"minf", caja(b"stbl", stsd))))
    return (caja(b"ftyp", b"M4A isomiso2") + caja(b"moov", mvhd + trak)
            + caja(b"mdat", b"A" * 400))


# Un ALAC de calidad CD ocupa bastante menos que el FLAC de 24/192 de origen.
tamano = 400 if "44100" in args else 4000
if str(salida).lower().endswith((".m4a", ".mp4", ".m4b")):
    frecuencia = int(args[args.index("-ar") + 1]) if "-ar" in args else 44100
    datos = m4a_falso(frecuencia, con_audio=not os.environ.get("SIN_AUDIO"))
else:
    datos = b"A" * tamano
with open(salida, "wb") as handle:
    handle.write(datos)
sys.exit(0)
