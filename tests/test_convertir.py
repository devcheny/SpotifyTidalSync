"""Prueba del convertidor FLAC -> ALAC con un ffmpeg simulado."""
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stsync.config import Config
from stsync.convert import DONE_DIR, ConvertError, FlacConverter

AQUI = Path(__file__).parent
FFMPEG = str(AQUI / "ffmpeg-falso.bat")
RAIZ = AQUI / "prueba-flac"


def montar() -> Path:
    """Reproduce lo que hace iTunes: los FLAC caen en 'No anadido/<fecha>'."""
    if RAIZ.exists():
        shutil.rmtree(RAIZ)
    folder = RAIZ / "Anadir automaticamente a iTunes"
    rechazados = folder / "No anadido"
    (rechazados / "2026-08-28 16.20.32").mkdir(parents=True)
    (rechazados / "2026-08-27 10.00.00").mkdir(parents=True)

    (rechazados / "2026-08-28 16.20.32" / "Xiyo - Do You Remember.flac").write_bytes(b"flac1")
    (rechazados / "2026-08-27 10.00.00" / "Xiyo - Do You Remember.flac").write_bytes(b"flac2")
    (rechazados / "2026-08-27 10.00.00" / "Otra cancion.flac").write_bytes(b"flac3")
    # Un WAV y un AIFF tambien se convierten; el MP3 no, que ya perdio calidad.
    (folder / "Grabacion.wav").write_bytes(b"wav")
    (folder / "Vieja.aiff").write_bytes(b"aiff")
    # Ya convertido antes: no se debe machacar.
    (folder / "Xiyo - Do You Remember.m4a").write_bytes(b"ya estaba")
    # Otros formatos: no se tocan.
    (folder / "algo.mp3").write_bytes(b"mp3")
    return folder


def config(folder: Path, **extra) -> Config:
    cfg = Config(dict(Config().data))
    cfg.set("flac_folder", str(folder))
    cfg.set("ffmpeg_path", FFMPEG)
    for key, value in extra.items():
        cfg.set(key, value)
    return cfg


def correr(nombre, folder, cfg):
    lineas = []
    stats = FlacConverter(cfg, lineas.append).run()
    print(f"===== {nombre} =====")
    print("\n".join(lineas))
    print("->", stats.summary())
    return stats


def contenido(folder: Path) -> list[str]:
    return sorted(str(p.relative_to(folder)).replace("\\", "/")
                  for p in folder.rglob("*"))


# --- 1. Ejecucion normal ----------------------------------------------------
folder = montar()
stats = correr("normal", folder, config(folder))
print("queda:", contenido(folder), "\n")
assert stats.converted == 5, stats.converted
assert not stats.failed, stats.failed
hay = contenido(folder)
assert "Xiyo - Do You Remember.m4a" in hay          # el de antes, intacto
assert "Xiyo - Do You Remember (2).m4a" in hay      # no lo machaca
assert "Xiyo - Do You Remember (3).m4a" in hay
assert "Otra cancion.m4a" in hay
assert "algo.mp3" in hay                            # no se toca
assert not [p for p in hay if p.endswith(".flac")], "quedaron FLAC sin borrar"
assert (folder / "Xiyo - Do You Remember.m4a").read_bytes() == b"ya estaba"
assert "No anadido" not in hay, "las carpetas vacias deberian irse"

# --- 2. Sin borrar el original ---------------------------------------------
folder = montar()
stats = correr("mover en vez de borrar", folder, config(folder, flac_delete_source=False))
hay = contenido(folder)
print("queda:", hay, "\n")
guardados = [p for p in hay if p.startswith(DONE_DIR + "/")]
assert len(guardados) == 5, guardados
assert DONE_DIR + "/Xiyo - Do You Remember (2).flac" in hay, hay
# Cada uno con su extension: un WAV archivado como .flac no lo abre nadie.
assert DONE_DIR + "/Grabacion.wav" in hay, hay
assert DONE_DIR + "/Vieja.aiff" in hay, hay

# --- 3. ffmpeg rechaza la caratula -> reintento sin ella --------------------
folder = montar()
os.environ["FALLA_CARATULA"] = "1"
stats = correr("caratula rechazada", folder, config(folder))
del os.environ["FALLA_CARATULA"]
print()
assert stats.converted == 5, stats.converted
assert not stats.failed, stats.failed

# --- 4. ffmpeg falla del todo ----------------------------------------------
folder = montar()
os.environ["FALLA_TODO"] = "1"
stats = correr("ffmpeg falla", folder, config(folder))
del os.environ["FALLA_TODO"]
hay = contenido(folder)
print("queda:", hay, "\n")
assert stats.converted == 0, stats.converted
assert len(stats.failed) == 5, stats.failed
assert len([p for p in hay if p.endswith(".flac")]) == 3, "no debe borrar si fallo"
assert len([p for p in hay if p.endswith(".m4a")]) == 1, "no debe dejar restos"

# --- 5. Simulacion ----------------------------------------------------------
folder = montar()
antes = contenido(folder)
stats = correr("simulacion", folder, config(folder, dry_run=True))
print()
assert contenido(folder) == antes, "la simulacion no debe tocar nada"
assert stats.converted == 5, stats.converted

# --- 6. Sin ffmpeg y carpeta inexistente ------------------------------------
for nombre, cfg in (("sin ffmpeg", config(folder, ffmpeg_path=str(AQUI / "no-existe.exe"))),
                    ("carpeta mala", config(Path(AQUI / "no-hay-nada")))):
    try:
        FlacConverter(cfg, lambda m: None).run()
        raise SystemExit(f"ERROR: {nombre} deberia haber fallado")
    except ConvertError as exc:
        print(f"{nombre}: {exc}")

# --- 7. Completar las etiquetas que le falten al FLAC -----------------------
def convertir_uno(nombre_fichero, tags=None, **ajustes):
    """Convierte un solo FLAC y devuelve los argumentos que recibio ffmpeg."""
    if RAIZ.exists():
        shutil.rmtree(RAIZ)
    carpeta = RAIZ / "Anadir automaticamente a iTunes"
    carpeta.mkdir(parents=True)
    (carpeta / nombre_fichero).write_bytes(b"flac")

    anterior = os.environ.get("TAGS_FALSOS")
    if tags is None:
        os.environ.pop("TAGS_FALSOS", None)
    else:
        os.environ["TAGS_FALSOS"] = json.dumps(tags)
    try:
        FlacConverter(config(carpeta, **ajustes), lambda m: None).run()
    finally:
        os.environ.pop("TAGS_FALSOS", None)
        if anterior is not None:
            os.environ["TAGS_FALSOS"] = anterior
    return (AQUI / "ultimo-comando.txt").read_text(encoding="utf-8").splitlines()


def metadatos(args):
    return dict(a.split("=", 1) for i, a in enumerate(args)
                if i and args[i - 1] == "-metadata")


print("===== etiquetas que faltan =====")
args = convertir_uno("Xiyo - Do You Remember.flac")
puestas = metadatos(args)
print("  sin ninguna etiqueta   ->", puestas)
assert puestas == {"artist": "Xiyo", "title": "Do You Remember"}, puestas

# Las que ya trae se copian tal cual (para que no las tire el contenedor), y
# no se las pisa con lo que diga el nombre del fichero.
args = convertir_uno("Xiyo - Do You Remember.flac",
                     tags={"ARTIST": "Xiyo", "TITLE": "Do You Remember"})
print("  con las suyas          ->", metadatos(args), "(copiadas del original)")
assert metadatos(args) == {"artist": "Xiyo", "title": "Do You Remember"}, \
    metadatos(args)

args = convertir_uno("Xiyo - Do You Remember.flac", tags={"ARTIST": "Otro Nombre"})
print("  solo le falta el titulo->", metadatos(args))
assert metadatos(args) == {"artist": "Otro Nombre",
                           "title": "Do You Remember"}, metadatos(args)

args = convertir_uno("03 - Xiyo - Do You Remember.flac")
print("  con numero de pista    ->", metadatos(args))
assert metadatos(args) == {"artist": "Xiyo", "title": "Do You Remember",
                           "track": "3"}, metadatos(args)

# --- los que colaboran suelen ir en el titulo, no en el artista -------------
from stsync.convert import sumar_artistas, artistas_del_titulo

print()
print("===== artistas escondidos en el titulo =====")
for artista, titulo, espera in [
    ("Lola Indigo", "EL BACHATON (feat. Lucho RK)", "Lola Indigo; Lucho RK"),
    ("Karol G", "Provenza ft. Maria Becerra, Nicki Nicole",
     "Karol G; Maria Becerra; Nicki Nicole"),
    ("Shakira", "Sessions #53 [with Bizarrap]", "Shakira; Bizarrap"),
    ("ROSALIA; The Weeknd", "La Fama (feat. The Weeknd)", ""),   # ya estaba
    ("Bad Bunny", "Titi Me Pregunto", ""),                       # no hay nadie
    ("Manolo", "Cancion (Remastered 2016)", ""),                 # no confundir
]:
    sale = sumar_artistas(artista, titulo)
    print(f"  {artista:<20} + {titulo:<42} -> {sale or '(sin cambio)'}")
    assert sale == espera, (artista, titulo, sale)

# Y de punta a punta: el .m4a sale con los dos.
args = convertir_uno("Lola Indigo - EL BACHATON.flac",
                     tags={"ARTIST": "Lola Indigo",
                           "TITLE": "EL BACHATON (feat. Lucho RK)"})
print("  al convertir           ->", metadatos(args)["artist"])
assert metadatos(args)["artist"] == "Lola Indigo; Lucho RK", metadatos(args)
assert metadatos(args)["title"] == "EL BACHATON (feat. Lucho RK)",     "el titulo se deja como estaba"

args = convertir_uno("99 Luftballons.flac")
print("  titulo que empieza por numero ->", metadatos(args))
assert metadatos(args) == {"title": "99 Luftballons"}, metadatos(args)

args = convertir_uno("Xiyo - Do You Remember.flac", flac_complete_tags=False)
print("  con el ajuste apagado  ->", metadatos(args))
assert metadatos(args) == {}, metadatos(args)
print()

# --- 8. Normalizar midiendo primero -----------------------------------------
print("===== dos pasadas =====")
args = convertir_uno("Xiyo - Do You Remember.flac")
filtro = args[args.index("-af") + 1]
print("  filtro con medida :", filtro[:64] + "...")
assert "measured_I=-16.55" in filtro, filtro
assert "linear=true" in filtro, filtro

args = convertir_uno("Xiyo - Do You Remember.flac", flac_two_pass=False)
filtro = args[args.index("-af") + 1]
print("  a una pasada      :", filtro)
assert filtro.startswith("loudnorm=I=-9:TP=-1.5:LRA=11,"), filtro

os.environ["MEDICION_ROTA"] = "1"
try:
    args = convertir_uno("Xiyo - Do You Remember.flac")
finally:
    os.environ.pop("MEDICION_ROTA", None)
filtro = args[args.index("-af") + 1]
print("  si la medida falla:", filtro, "(sigue convirtiendo)")
assert filtro.startswith("loudnorm=I=-9:TP=-1.5:LRA=11,"), filtro

# El reencuadre va SIEMPRE detras del normalizador: sin el, el filtro suelta
# su buffer al final y deja un salto en la linea de tiempo que rekordbox no
# sobrevive. Ver filtro_audio.
assert "aresample=44100" in filtro, filtro
assert filtro.index("loudnorm") < filtro.index("aresample"), filtro

args = convertir_uno("Xiyo - Do You Remember.flac", flac_normalize=False)
filtro = args[args.index("-af") + 1]
print("  sin normalizar    :", filtro)
assert "loudnorm" not in filtro, "no se pidio normalizar"
assert filtro.startswith("aresample="), "pero reencuadrar si, siempre"
print()

# --- las etiquetas del original se vuelven a escribir a mano -----------------
# Con -map_metadata ffmpeg copia solo lo que sabe traducir a MP4 y tira el
# resto. El ISRC es de los que tira, y es la unica llave para TIDAL.
from stsync.convert import args_metadatos, etiquetas_perdidas
sys.path.insert(0, str(AQUI))
from dobles import m4a_falso as m4a_de_mentira

DEL_FLAC = {"isrc": "ESUM72600399", "barcode": "0600574153173",
            "title": "EL BACHATON", "publisher": "Universal", "encoder": "Lavf"}
args = args_metadatos(DEL_FLAC)
print()
print("etiquetas que se pasan a mano:", " ".join(args[:6]), "...")
assert "isrc=ESUM72600399" in args, args
assert "barcode=0600574153173" in args, args
assert not any(a.startswith("encoder=") for a in args), \
    "el encoder lo pone el contenedor, no se copia"

# Y lo que aun asi se pierda, se dice.
perdidas = etiquetas_perdidas(DEL_FLAC, {"title": "EL BACHATON",
                                         "encoder": "Lavf61"})
print("se han perdido:", perdidas)
assert perdidas == ["barcode", "isrc", "publisher"], perdidas
assert etiquetas_perdidas(DEL_FLAC, DEL_FLAC) == [], "no falta ninguna"

# Las que ffmpeg no coloca se escriben aparte, como atomos libres, y se leen
# de vuelta. Un .m4a de verdad hace falta aqui: mutagen no se conforma con
# bytes sueltos, y precisamente por eso vale la pena que lo haga el.
from stsync.convert import escribir_libres, leer_libres

montar()
real = RAIZ / "con-etiquetas.m4a"
real.write_bytes(m4a_de_mentira(44100))
error = escribir_libres(real, {"ISRC": "ESUM72600399", "BARCODE": "060057"})
print()
print("escribir atomos libres ->", repr(error) or "bien")
if error.startswith("falta el paquete mutagen"):
    print("  (sin mutagen en este equipo: no se puede comprobar)")
else:
    assert error == "", error
    leidas = leer_libres(real)
    print("  leidas de vuelta:", leidas)
    assert leidas.get("isrc") == "ESUM72600399", leidas
    assert leidas.get("barcode") == "060057", leidas
    # Y quien lea las etiquetas tiene que verlas: sin esto, el ISRC recien
    # escrito no serviria para publicar en TIDAL.
    from stsync.convert import _leer_tags
    os.environ["TAGS_FALSOS"] = json.dumps({"title": "Bachaton"})
    try:
        todas = _leer_tags(str(AQUI / "ffprobe-falso.bat"), real)
    finally:
        del os.environ["TAGS_FALSOS"]
    print("  y _leer_tags las suma:", sorted(todas))
    assert todas.get("isrc") == "ESUM72600399", todas
    assert todas.get("title") == "Bachaton", "no pisa las que si ve ffprobe"


# --- sin ffprobe no se empieza siquiera --------------------------------------
# Sin saber como esta grabado el original no se puede decidir si hay que
# bajarlo, y un 24/192 saldria con la frecuencia a cero en la cabecera, que es
# lo que hace que otros programas se cierren al abrirlo.
from stsync import convert as mod

anterior = mod._buscar_ffprobe
mod._buscar_ffprobe = lambda ffmpeg: None
try:
    FlacConverter(config(RAIZ), lambda m: None).run()
    raise SystemExit("ERROR: sin ffprobe deberia negarse a empezar")
except ConvertError as exc:
    print("sin ffprobe:", str(exc)[:52], "...")
    assert "ffprobe" in str(exc), exc
finally:
    mod._buscar_ffprobe = anterior

shutil.rmtree(RAIZ)
print()
print("TODOS LOS ESCENARIOS OK")
