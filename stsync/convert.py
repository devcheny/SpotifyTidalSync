"""Conversion a ALAC para la carpeta de auto-anadir de iTunes.

iTunes no sabe leer FLAC: lo que dejas en "Anadir automaticamente a iTunes"
acaba arrinconado en su subcarpeta "No anadido". Y un WAV si lo lee, pero
ocupa el triple y casi no admite etiquetas. Esto recorre esa carpeta entera,
convierte a ALAC todo lo que sea sin perdida y deja el .m4a en la raiz, que es
donde iTunes si lo recoge solo.

Solo se convierte lo que no pierde nada por el camino. Un MP3 pasado a ALAC no
recuperaria la calidad que ya perdio: solo ocuparia mas, asi que se deja.

Viene del flac2alac.bat de siempre y mantiene su normalizacion de volumen
(loudnorm I=-9). Cambia en tres cosas, todas para no perder nada: comprueba
como termino ffmpeg en vez de mirar si el fichero destino existe (iTunes se lo
lleva en cuanto aparece), no machaca un .m4a que ya estuviera ahi, y conserva
la caratula si el fichero la trae.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import Config

# Misma normalizacion que el .bat original y que NormalizeLibrary.ps1: deja la
# musica bastante alta. I es el volumen al que se quiere llegar (LUFS), TP el
# techo de pico y LRA cuanto margen se deja entre lo bajo y lo alto.
LOUDNORM = "loudnorm=I=-9:TP=-1.5:LRA=11"

# De la medicion previa salen estos cinco datos, que es lo que convierte a
# loudnorm en preciso: sin ellos trabaja sobre la marcha y se queda cerca.
MEDIDAS = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")

# Calidad CD: 16 bits a 44,1 kHz, o sea los 1411 kbps de toda la vida. El
# codificador ALAC trabaja con muestras planares, de ahi la "p" de s16p.
CD_RATE = 44100
CD_FORMAT = "s16p"

# Hasta donde se graba, como techo. Ninguno sube nada: una cancion que ya venga
# por debajo se queda igual.
#
#   cd   1411 kbps, lo que ocupa menos y suena a disco de toda la vida.
#   48k  2304 kbps, la mitad justa de un 24/192 y el maximo que un .m4a puede
#        declarar. El equilibrio entre lo que se oye y lo que ocupa.
OBJETIVOS = {
    "cd": (CD_RATE, 16),
    "48k": (48000, 24),
}
OBJETIVOS_NOMBRE = {
    "cd": "calidad CD, 16 bits / 44,1 kHz",
    "48k": "24 bits / 48 kHz",
}
POR_DEFECTO = "48k"

# Como se llama el formato de muestra en cada codificador, para 16 y para 24
# bits. El ALAC trabaja con muestras planares, de ahi la "p". Los que no
# estan aqui no admiten que se les diga: a un PCM se lo fija su propio
# nombre, y a uno con perdida no le pinta nada.
FORMATO_MUESTRA = {
    "alac": ("s16p", "s32p"),
    "flac": ("s16", "s32"),
}

# Donde guarda un MP4 lo que no es estandar. Es el "mean" que usa iTunes, asi
# que es donde lo buscan los demas programas.
PREFIJO_LIBRE = "----:com.apple.iTunes:"

# Etiquetas que pone el contenedor, no la cancion: ni se copian ni se echan de
# menos si no llegan al destino.
ETIQUETAS_DEL_CONTENEDOR = {
    "encoder", "major_brand", "minor_version", "compatible_brands",
    "handler_name", "vendor_id", "language",
}

# Portadas que un .m4a admite tal cual. Lo demas (PNG, sobre todo) hay
# que recodificarlo: ver args_caratula.
ART_JPEG = {"mjpeg", "jpeg"}

# Cajas de un MP4 que contienen otras. Hace falta para llegar hasta la
# descripcion del audio, que vive en moov/trak/mdia/minf/stbl/stsd.
ANIDAN = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"stsd"}
# El indice de una cancion no llega a esto ni de lejos; el tope es por si
# el fichero esta roto y dice medir un disparate.
MAX_MOOV = 8 * 1024 * 1024
# Los contenedores MP4, que son los que tienen el problema del campo de
# 16.16 bits (ver args_calidad) y los unicos que exigen la portada en JPEG.
CONTENEDOR_MP4 = (".m4a", ".mp4", ".m4b")
# La frecuencia alta mas grande que cabe en ese campo. Por encima, el
# fichero no puede declarar la suya.
MAX_M4A_RATE = 48000

# Lo que se le pega al nombre mientras se copia, para que iTunes no lo
# reconozca como musica y no se lo lleve a medias. La conversion en si se hace
# en otra carpeta (ver FlacConverter._taller).
EN_OBRAS = ".tmp"

# Un fichero recien escrito, o uno que iTunes esta mirando, puede estar
# ocupado un rato: ver _insistiendo.
INTENTOS_FICHERO = 5
ESPERA_FICHERO = 0.4

# Lo que se convierte a ALAC: formatos sin perdida que iTunes o no lee, o lee
# ocupando de mas. Los de con perdida (mp3, ogg, opus, wma) no entran: pasarlos
# a ALAC no devuelve la calidad que ya perdieron y encima ocuparian el triple.
CONVERTIBLES = (".flac", ".wav", ".aif", ".aiff", ".ape", ".wv")

# Carpeta donde van los originales cuando se pide no borrarlos.
DONE_DIR = "_convertidos"

# Una cancion larga con loudnorm puede tardar; aun asi no debe colgarse.
TIMEOUT_S = 1800

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ConvertError(RuntimeError):
    """No se puede convertir: falta ffmpeg o la carpeta no sirve."""


def es_mp4(ruta: Path) -> bool:
    """Si el fichero es un contenedor MP4, aunque este a medio traer.

    Al copiar entre unidades, el destino se llama un rato "cancion.m4a.tmp"
    para que iTunes no lo mire hasta que este entero. Sigue siendo un MP4.
    """
    nombre = ruta.name.lower()
    if nombre.endswith(EN_OBRAS):
        nombre = nombre[:-len(EN_OBRAS)]
    return nombre.endswith(CONTENEDOR_MP4)


def _traer(origen: Path, destino: Path) -> str:
    """Deja el fichero terminado en su sitio. "" si va bien.

    En el mismo volumen es un cambio de nombre y es instantaneo, asi que iTunes
    lo ve entero o no lo ve. Si el taller cayo en otra unidad hay que copiar, y
    entonces se copia a un nombre que iTunes no mira y se renombra al final,
    para que tampoco vea nunca una copia a medias.
    """
    try:
        os.replace(origen, destino)
        return ""
    except OSError:
        pass
    puente = destino.with_name(destino.name + EN_OBRAS)
    try:
        shutil.copy2(origen, puente)
        os.replace(puente, destino)
    except OSError as exc:
        _quitar(puente)
        return str(exc)
    _quitar(origen)
    return ""


def _insistiendo(accion: Callable[[], Any]) -> tuple[Any, str]:
    """Repite una operacion sobre ficheros que Windows puede negar un rato.

    Devuelve (lo que saliera, motivo del ultimo fallo). Pasa constantemente:
    iTunes se queda el fichero mientras lo mira, y el antivirus y el indexador
    abren un instante todo lo recien escrito. A la primera falla y a la tercera
    no, asi que insistir un poco ahorra la mitad de los errores.
    """
    ultimo = ""
    for intento in range(INTENTOS_FICHERO):
        try:
            return accion(), ""
        except OSError as exc:
            ultimo = str(exc)
            time.sleep(ESPERA_FICHERO * (intento + 1))
    return None, ultimo


def _quitar(ruta: Path) -> None:
    """Borra un fichero a medias sin montar un drama si esta bloqueado."""
    try:
        ruta.unlink(missing_ok=True)
    except OSError:
        pass


# --------------------------------------------------------------------------
# ffmpeg
# --------------------------------------------------------------------------
def find_ffmpeg(configured: str = "") -> str | None:
    """Busca ffmpeg: primero el configurado, luego el PATH, luego lo tipico."""
    if configured:
        path = Path(configured)
        if path.is_file():
            return str(path)
        # Si apuntan a la carpeta, se completa con el ejecutable.
        if path.is_dir() and (path / "ffmpeg.exe").is_file():
            return str(path / "ffmpeg.exe")
        return None

    found = shutil.which("ffmpeg")
    if found:
        return found

    for candidate in (
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe"),
    ):
        if Path(candidate).is_file():
            return candidate
    return None


def diagnose(cfg: Config) -> tuple[bool, str]:
    """Estado de ffmpeg y de la carpeta, para enseñarlo en la interfaz."""
    ffmpeg = find_ffmpeg(str(cfg.get("ffmpeg_path", "")))
    if not ffmpeg:
        return False, ("No se encuentra ffmpeg. Instalalo (winget install "
                       "Gyan.FFmpeg) o pon su ruta en el ajuste de abajo.")
    folder = Path(str(cfg.get("flac_folder", "")))
    if not folder.is_dir():
        return False, f"ffmpeg listo, pero la carpeta no existe: {folder}"
    return True, f"ffmpeg listo: {ffmpeg}"


# --------------------------------------------------------------------------
# Conversion
# --------------------------------------------------------------------------
@dataclass
class ConvertStats:
    converted: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)
    cleaned_dirs: int = 0

    def summary(self) -> str:
        return (f"A ALAC: {self.converted} convertidos | "
                f"{len(self.failed)} con error | "
                f"{self.cleaned_dirs} carpetas vacias eliminadas")


class FlacConverter:
    def __init__(self, cfg: Config, log: Callable[[str], None] | None = None,
                 should_stop: Callable[[], bool] | None = None) -> None:
        self.cfg = cfg
        self.log = log or (lambda msg: print(msg))
        self.should_stop = should_stop or (lambda: False)
        self.stats = ConvertStats()
        self._obrador: Path | None = None

    # ---------------------------------------------------------------- publico
    def run(self) -> ConvertStats:
        self.log("")
        self.log("== Convertir a ALAC ==")
        if self.cfg.dry_run:
            self.log("  (simulacion: no se convierte ni se borra nada)")

        ffmpeg = find_ffmpeg(str(self.cfg.get("ffmpeg_path", "")))
        if not ffmpeg:
            raise ConvertError(
                "No se encuentra ffmpeg. Instalalo con 'winget install Gyan.FFmpeg' "
                "o indica su ruta en la pestana Convertir a ALAC.")
        # Sin ffprobe no se sabe como esta grabado el original, y sin saberlo no
        # se puede decidir si hay que bajarlo: un 24/192 saldria tal cual, con
        # la frecuencia a cero en la cabecera. Mejor no empezar.
        if not _buscar_ffprobe(ffmpeg):
            raise ConvertError(
                "No se encuentra ffprobe, que viene junto a ffmpeg. Hace falta "
                "para saber como esta grabada cada cancion: sin el no se puede "
                "decidir si hay que bajarle la calidad, y saldrian ficheros que "
                "otros programas no saben abrir.")

        folder = Path(str(self.cfg.get("flac_folder", "")))
        if not folder.is_dir():
            raise ConvertError(f"La carpeta no existe: {folder}")

        sources = self._find_sources(folder)
        if not sources:
            self.log(f"  no hay nada que convertir en {folder}")
            return self.stats
        self.log(f"  {len(sources)} ficheros que convertir en {folder}")

        for i, source in enumerate(sources, 1):
            if self.should_stop():
                self.log("  detenido por el usuario")
                break
            self.log(f"  [{i}/{len(sources)}] {source.name}")
            self._convert_one(ffmpeg, source, folder)

        self._clean_empty_dirs(folder)
        self._recoger_taller()
        return self.stats

    # ----------------------------------------------------------------- buscar
    def _find_sources(self, folder: Path) -> list[Path]:
        """Lo convertible de la carpeta y sus subcarpetas, menos lo ya hecho."""
        done = folder / DONE_DIR
        out = []
        for path in sorted(folder.rglob("*")):
            if path.suffix.lower() not in CONVERTIBLES or not path.is_file():
                continue
            if done in path.parents:
                continue
            out.append(path)
        return out

    # -------------------------------------------------------------- convertir
    def _convert_one(self, ffmpeg: str, source: Path, folder: Path,
                     retirar: bool = True) -> Path | None:
        """Convierte una cancion y devuelve el .m4a que ha salido, o None.

        Con retirar=False el original se queda donde esta. Lo usa el arreglo de
        un fichero suelto, que es para probar: ahi borrar o mover el original
        seria una sorpresa desagradable.
        """
        target = _free_name(folder, source.stem)
        if self.cfg.dry_run:
            self.log(f"      [simulacion] -> {target.name}")
            self.stats.converted += 1
            return None

        # Se trabaja FUERA de la carpeta de auto-anadir. Esa carpeta la vigila
        # iTunes y todo lo que aparece ahi lo abre para mirarlo: con el fichero
        # de trabajo dentro, ni se le pueden escribir las etiquetas ni se puede
        # comprobar como ha salido. Cambiarle la extension no basta, iTunes lo
        # toca igual. Solo se trae ya terminado.
        temporal = self._taller() / target.name
        keep_art = bool(self.cfg.get("flac_keep_artwork", True))
        medida = None
        if self.cfg.get("flac_normalize", True) and \
                self.cfg.get("flac_two_pass", True):
            medida = medir_volumen(ffmpeg, source, self.log)
        result = self._run_ffmpeg(ffmpeg, source, temporal, keep_art, medida)
        if result.returncode != 0 and keep_art:
            # La causa mas comun de fallo es una caratula que no entra en el
            # .m4a, asi que antes de darlo por perdido se prueba sin ella.
            self.log("      fallo el primer intento, reintento sin la caratula")
            _quitar(temporal)
            result = self._run_ffmpeg(ffmpeg, source, temporal, keep_art=False)

        if result.returncode != 0:
            detail = (result.stderr or "").strip().splitlines()
            self.stats.failed.append((source.name, detail[-1] if detail else "error"))
            self.log(f"      ERROR: {detail[-1] if detail else 'ffmpeg fallo'}")
            _quitar(temporal)
            return None

        # ffmpeg puede terminar diciendo que todo ha ido bien y dejar un .m4a
        # que iTunes acepta pero otros programas no. Se mira antes de dar la
        # conversion por buena: asi el original no se borra por las buenas.
        malo = comprobar_salida(temporal, source)
        if malo:
            self.stats.failed.append((source.name, malo))
            self.log(f"      ERROR: {malo}")
            _quitar(temporal)
            return None

        avisos = completar_etiquetas(ffmpeg, source, temporal)

        fallo = _traer(temporal, target)
        if fallo:
            self.stats.failed.append(
                (source.name, f"no se pudo dejar el .m4a en su sitio: {fallo}"))
            self.log(f"      ERROR: {fallo}")
            _quitar(temporal)
            return None

        self.stats.converted += 1
        self.log(f"      -> {target.name}{_size_change(source, target)}")
        for linea in avisos:
            self.log(f"      {linea}")
        if retirar:
            self._retire(source, folder)
        return target

    def _taller(self) -> Path:
        """Carpeta de trabajo, fuera de donde mira iTunes. Se crea una por pasada."""
        if self._obrador is None:
            self._obrador = Path(tempfile.mkdtemp(prefix="stsync-alac-"))
        return self._obrador

    def _recoger_taller(self) -> None:
        if self._obrador is not None:
            shutil.rmtree(self._obrador, ignore_errors=True)
            self._obrador = None

    def _run_ffmpeg(self, ffmpeg: str, source: Path, target: Path,
                    keep_art: bool, medida: dict[str, str] | None = None
                    ) -> subprocess.CompletedProcess[str]:
        command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                   "-i", str(source), "-map", "0:a:0"]
        if keep_art:
            ffprobe = _buscar_ffprobe(ffmpeg)
            # Sin ffprobe no se sabe que trae, y ante la duda se recodifica:
            # es lo unico que garantiza un .m4a que abra en cualquier sitio.
            command += args_caratula(caratula_de(ffprobe, source) if ffprobe
                                     else "desconocida")
        else:
            command += ["-vn"]
        # Primero las del original (que -map_metadata solo copia a medias) y
        # encima las que haya que deducir del nombre del fichero.
        ffprobe_tags = _buscar_ffprobe(ffmpeg)
        command += args_metadatos(_leer_tags(ffprobe_tags, source)
                                  if ffprobe_tags else {})
        for campo, valor in self._tags_que_faltan(ffmpeg, source).items():
            command += ["-metadata", f"{campo}={valor}"]
        command += ["-c:a", "alac"]
        # Un FLAC de 24 bits y 192 kHz sale a 9216 kbps y ocupa una barbaridad;
        # a 16/44,1 son los 1411 kbps de un CD. Y aunque no quieras bajarlo,
        # por encima de 48 kHz un .m4a no puede ni declarar su frecuencia.
        ffprobe = _buscar_ffprobe(ffmpeg)
        audio = leer_audio(ffprobe, source) if ffprobe else {}
        extra, motivo = args_calidad(
            int(audio.get("rate", 0)), int(audio.get("bits", 0)),
            str(self.cfg.get("quality_target", POR_DEFECTO)), target, "alac")
        if motivo:
            self.log(f"      {motivo}")
        command += extra
        cadena = filtro_audio(medida, _rate_de(extra),
                              bool(self.cfg.get("flac_normalize", True)))
        if cadena:
            command += ["-af", cadena]
        if es_mp4(target):
            # Explicito, para no depender de que la extension del sitio donde
            # se este trabajando le diga a ffmpeg que contenedor queremos.
            command += ["-f", "ipod"]
        command += ["-movflags", "+faststart", str(target)]

        try:
            return subprocess.run(command, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=TIMEOUT_S, creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(command, 1, "", "ffmpeg tardo demasiado")

    def _tags_que_faltan(self, ffmpeg: str, source: Path) -> dict[str, str]:
        """Lo que el FLAC no trae y se puede deducir de su nombre.

        Un FLAC sin etiquetas entra en iTunes como "Artista desconocido" y ya
        no hay quien lo empareje con nada. Como esos ficheros suelen llamarse
        "Artista - Titulo", de ahi sale lo justo para que quede identificado.

        Solo se rellenan los huecos: lo que el fichero ya trae manda siempre.
        """
        if not self.cfg.get("flac_complete_tags", True):
            return {}
        ffprobe = _buscar_ffprobe(ffmpeg)
        if not ffprobe:
            return {}          # sin ffprobe no se sabe que falta: mejor no tocar

        tiene = _leer_tags(ffprobe, source)
        artista, titulo, pista = _partir_nombre(source.stem)
        faltan: dict[str, str] = {}
        if artista and not tiene.get("artist"):
            faltan["artist"] = artista
        if titulo and not tiene.get("title"):
            faltan["title"] = titulo
        if pista and not tiene.get("track"):
            faltan["track"] = pista

        # Los que colaboran suelen ir escondidos en el titulo y no en el
        # artista, asi que la cancion entra en iTunes a nombre de uno solo.
        completo = sumar_artistas(faltan.get("artist") or tiene.get("artist", ""),
                                  faltan.get("title") or tiene.get("title", ""))
        if completo:
            faltan["artist"] = completo

        if faltan:
            self.log(f"      completando: {', '.join(sorted(faltan))}")
        return faltan

    def _retire(self, source: Path, folder: Path) -> None:
        """Sacar el original de la carpeta: si se queda, la proxima vez se
        convertiria otra vez y saldria un duplicado.

        Con paciencia, porque iTunes tiene el FLAC abierto un rato mientras
        decide que hacer con el (no sabe leerlo, asi que acaba mandandolo a
        "No anadido") y Windows no deja tocarlo hasta que lo suelta.
        """
        borrar = bool(self.cfg.get("flac_delete_source", True))
        if borrar:
            _, fallo = _insistiendo(source.unlink)
            if not fallo:
                return
            self.log(f"      no se pudo borrar el FLAC: {fallo}")
            self.log("      se intenta apartarlo, que si se queda ahi la "
                     "proxima pasada lo convertiria otra vez")

        done = folder / DONE_DIR
        # Con su extension de siempre: un WAV archivado como .flac no hay
        # programa que lo abra.
        destino = _free_name(done, source.stem, source.suffix)
        _, fallo = _insistiendo(lambda: (done.mkdir(exist_ok=True),
                                         source.replace(destino)))
        if fallo:
            self.log(f"      no se pudo mover el FLAC a {DONE_DIR}: {fallo}")
            if borrar:
                self.stats.failed.append(
                    (source.name, "convertido, pero el original sigue en la "
                                  "carpeta: la proxima pasada lo duplicaria"))

    # --------------------------------------------------------------- limpieza
    def _clean_empty_dirs(self, folder: Path) -> None:
        """Las carpetas de 'No anadido' quedan vacias tras llevarse los FLAC."""
        if self.cfg.dry_run:
            return
        done = folder / DONE_DIR
        for path in sorted(folder.rglob("*"), key=lambda p: len(p.parts),
                           reverse=True):
            if not path.is_dir() or path == done or done in path.parents:
                continue
            try:
                path.rmdir()          # falla sola si no esta vacia
                self.stats.cleaned_dirs += 1
            except OSError:
                pass


def medir_volumen(ffmpeg: str, source: Path,
                  log: Callable[[str], None] | None = None) -> dict[str, str] | None:
    """Analiza el fichero y devuelve lo que loudnorm necesita saber de el.

    Es la primera de las dos pasadas: aqui no se convierte nada, solo se mide.
    Devuelve None si la medicion no sale, y entonces se normaliza a la antigua.
    """
    orden = [ffmpeg, "-hide_banner", "-nostats", "-i", str(source),
             "-af", f"{LOUDNORM}:print_format=json", "-f", "null", "-"]
    try:
        salida = subprocess.run(orden, capture_output=True, text=True,
                                encoding="utf-8", errors="replace",
                                timeout=TIMEOUT_S, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None

    # El JSON sale por stderr, al final de todo lo que ffmpeg va contando.
    texto = salida.stderr or ""
    abre, cierra = texto.rfind("{"), texto.rfind("}")
    if abre < 0 or cierra < abre:
        return None
    try:
        datos = json.loads(texto[abre:cierra + 1])
    except ValueError:
        return None

    medida = {clave: str(datos[clave]) for clave in MEDIDAS if clave in datos}
    if len(medida) != len(MEDIDAS):
        return None
    if log:
        log(f"      medido: {medida['input_i']} LUFS")
    return medida


def filtro_audio(medida: dict[str, str] | None, rate: int,
                 normalizar: bool = True) -> str:
    """La cadena de filtros del audio: normalizar y **reencuadrar**.

    Lo segundo no es un adorno. Un filtro como loudnorm guarda audio por
    delante para mirarlo antes de decidir, y al terminar lo suelta de golpe:
    el multiplexor ve un salto en los tiempos y lo apunta como si ahi hubiera
    un fotograma larguisimo. El fichero decodifica sin quejarse y dura lo que
    tiene que durar, pero su tabla de tiempos declara fotogramas de 7666
    muestras cuando el ALAC no puede pasar de 4096, y quien la recorra para
    dibujar la onda se encuentra un hueco: rekordbox se cierra ahi.

    `aresample` al final vuelve a partir el audio en fotogramas iguales y con
    los tiempos seguidos. Se pone siempre que se recodifique, aunque no se
    normalice: cuesta nada y quita toda una familia de sorpresas.
    """
    partes = []
    if normalizar:
        partes.append(loudnorm_con(medida))
    if rate:
        partes.append(f"aresample={rate}:async=1:first_pts=0")
    return ",".join(partes)


def loudnorm_con(medida: dict[str, str] | None) -> str:
    """El filtro de normalizacion, afinado con la medicion si la hay."""
    if not medida:
        return LOUDNORM
    return (f"{LOUDNORM}"
            f":measured_I={medida['input_i']}"
            f":measured_TP={medida['input_tp']}"
            f":measured_LRA={medida['input_lra']}"
            f":measured_thresh={medida['input_thresh']}"
            f":offset={medida['target_offset']}"
            f":linear=true")


def volumen_actual(medida: dict[str, str] | None) -> float | None:
    """Los LUFS que tiene ahora mismo, para saber si hace falta tocarlo."""
    try:
        return float((medida or {})["input_i"])
    except (KeyError, TypeError, ValueError):
        return None


def _buscar_ffprobe(ffmpeg: str) -> str | None:
    """ffprobe vive al lado de ffmpeg y se llama igual cambiando el nombre."""
    hermano = Path(ffmpeg).with_name(Path(ffmpeg).name.replace("ffmpeg", "ffprobe"))
    if hermano.is_file():
        return str(hermano)
    return shutil.which("ffprobe")


def leer_audio(ffprobe: str, source: Path) -> dict[str, Any]:
    """Como esta grabada la cancion: codec, frecuencia y bits por muestra.

    Hace falta para saber si merece la pena bajarla a calidad CD y para volver
    a guardarla en su mismo formato.
    """
    orden = [ffprobe, "-v", "quiet", "-print_format", "json", "-show_streams",
             "-select_streams", "a:0", str(source)]
    try:
        salida = subprocess.run(orden, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=60,
                                creationflags=NO_WINDOW)
        streams = json.loads(salida.stdout or "{}").get("streams") or []
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {}
    if not streams:
        return {}

    stream = streams[0]
    bits = stream.get("bits_per_raw_sample") or stream.get("bits_per_sample")
    return {
        "codec": str(stream.get("codec_name") or ""),
        "rate": _entero(stream.get("sample_rate")),
        # Un s16p sin mas dato es de 16 bits; si no se sabe, se deja en 0.
        "bits": _entero(bits) or (16 if "s16" in str(stream.get("sample_fmt")) else 0),
    }


def _entero(valor: Any) -> int:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return 0


def informe_fichero(cfg: Config, source: Path) -> str:
    """Todo lo que se puede saber de un fichero, para comparar dos.

    Cuando un reproductor se cierra al abrir una cancion y no dice por que, la
    unica via es poner al lado una que si funcione y ver en que se diferencian.
    Esto lo cuenta entero: contenedor, streams, etiquetas, si el indice va al
    principio, si el fichero esta completo y si el audio se decodifica sin
    errores de cabo a rabo.
    """
    ffmpeg = find_ffmpeg(str(cfg.get("ffmpeg_path", "")))
    if not ffmpeg:
        raise ConvertError("No se encuentra ffmpeg.")
    ffprobe = _buscar_ffprobe(ffmpeg)
    if not ffprobe:
        raise ConvertError("No se encuentra ffprobe, que viene con ffmpeg.")
    if not source.is_file():
        raise ConvertError(f"No existe el fichero: {source}")

    lineas = [f"== {source.name} ==", f"  Carpeta: {source.parent}"]
    info = source.stat()
    fecha = dt.datetime.fromtimestamp(info.st_mtime)
    lineas.append(f"  Tamano: {tamano_legible(info.st_size)}   "
                  f"Modificado: {fecha:%Y-%m-%d %H:%M}")

    datos = _sondear(ffprobe, source)
    formato = datos.get("format") or {}
    streams = datos.get("streams") or []
    if not formato and not streams:
        lineas.append("")
        lineas.append("  ffprobe no ha sabido leerlo: el fichero esta roto o no "
                      "es lo que dice su extension.")
        return "\n".join(lineas)

    lineas.append("")
    duracion = _numero(formato.get("duration"))
    lineas.append(f"  Contenedor: {formato.get('format_name', '?')}   "
                  f"Duracion: {int(duracion) // 60}:{int(duracion) % 60:02d}   "
                  f"Bitrate: {_entero(formato.get('bit_rate')) // 1000} kbps")

    lineas += _estructura_mp4(source)
    lineas += _cabecera_audio(source)
    saltos = fotogramas_imposibles(source)
    if saltos:
        lineas.append(f"  OJO: {saltos}")

    for stream in streams:
        lineas.append("")
        indice = stream.get("index", "?")
        tipo = stream.get("codec_type", "?")
        lineas.append(f"  Stream {indice}  {tipo}  {stream.get('codec_name', '?')}"
                      f"  (tag {stream.get('codec_tag_string', '?')})")
        if tipo == "audio":
            lineas.append(f"    {stream.get('sample_rate', '?')} Hz, "
                          f"{stream.get('channels', '?')} canales "
                          f"({stream.get('channel_layout', '?')}), "
                          f"{stream.get('sample_fmt', '?')}, "
                          f"{stream.get('bits_per_raw_sample') or '?'} bits")
            if _entero(stream.get("sample_rate")) > 48000:
                lineas.append(
                    "    OJO: por encima de 48 kHz. Mira mas arriba lo que "
                    "declara la cabecera: no siempre es esto.")
            inicio = _numero(stream.get("start_time"))
            if inicio:
                lineas.append(f"    OJO: no empieza en cero, sino en {inicio}s "
                              "(el fichero lleva una lista de edicion)")
        elif tipo == "video":
            adjunta = (stream.get("disposition") or {}).get("attached_pic")
            lineas.append(f"    {stream.get('width', '?')}x{stream.get('height', '?')}"
                          f", marcada como portada: {'si' if adjunta else 'NO'}")
        lineas += _etiquetas(stream.get("tags") or {}, "    ")

    lineas.append("")
    lineas.append("  Etiquetas del fichero:")
    lineas += _etiquetas(formato.get("tags") or {}, "    ") or ["    (ninguna)"]

    lineas.append("")
    lineas.append("  Decodificando entero para ver si da errores...")
    lineas += _decodificar(ffmpeg, source)
    return "\n".join(lineas)


def _sondear(ffprobe: str, source: Path) -> dict[str, Any]:
    orden = [ffprobe, "-v", "quiet", "-print_format", "json", "-show_format",
             "-show_streams", str(source)]
    try:
        salida = subprocess.run(orden, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=120,
                                creationflags=NO_WINDOW)
        return json.loads(salida.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {}


def _etiquetas(tags: dict[str, Any], sangria: str) -> list[str]:
    """Las etiquetas, cortando las larguisimas pero diciendo cuanto miden.

    Una letra entera o un cuesheet metidos en una etiqueta son de lo que mas
    despista a un reproductor, y no se ven si no se miran a proposito.
    """
    fuera = []
    for clave, valor in sorted(tags.items()):
        texto = str(valor).replace("\n", " ")
        if len(texto) > 90:
            fuera.append(f"{sangria}{clave:<16} = [{len(texto)} caracteres] "
                         f"{texto[:70]}...")
        else:
            fuera.append(f"{sangria}{clave:<16} = {texto}")
    return fuera


def args_calidad(rate: int, bits: int, objetivo: str, destino: Path,
                 codec: str = "alac") -> tuple[list[str], str]:
    """Con que calidad grabar, y si eso supone bajarla.

    Devuelve (argumentos para ffmpeg, motivo). El **motivo vacio significa que
    la cancion ya estaba bien**, no que no haya argumentos: los argumentos van
    siempre, y ese es justo el asunto.

    Porque `loudnorm` trabaja por dentro a 192 kHz y **saca a esa frecuencia
    lo que le entre**. Si no se le fija la salida al codificador, un FLAC de
    44,1 kHz acaba siendo un ALAC de 192 kHz que la cabecera de un .m4a no
    puede declarar (ver comprobar_salida), y ese fichero cierra rekordbox al
    analizarlo. Asi se estropearon las canciones. Por eso el -ar va siempre,
    aunque no haya nada que bajar.

    El techo es un techo, nunca un objetivo: lo que venga por debajo se queda
    como esta. Subirlo no anadiria nada que no estuviera ya y ocuparia mas.
    """
    techo_rate, techo_bits = OBJETIVOS.get(objetivo, OBJETIVOS[POR_DEFECTO])
    if es_mp4(destino):
        # Un .m4a declara su frecuencia en un campo de 16.16 bits, o sea hasta
        # 65535 Hz. Por encima ffmpeg lo deja a cero, y eso no es una
        # preferencia: es un fichero que otros programas no saben abrir.
        techo_rate = min(techo_rate, MAX_M4A_RATE)

    final_rate = min(rate, techo_rate) if rate else techo_rate
    final_bits = min(bits, techo_bits) if bits else techo_bits

    args = ["-ar", str(final_rate)]
    formato = formato_de(codec, final_bits)
    if formato:
        args += ["-sample_fmt", formato]

    cambios = []
    if rate and rate > techo_rate:
        cambios.append(f"{rate} -> {final_rate} Hz")
    if bits and bits > techo_bits:
        cambios.append(f"{bits} -> {final_bits} bits")
    if not cambios:
        return args, ""
    return args, f"{', '.join(cambios)} ({OBJETIVOS_NOMBRE[objetivo]})"


def args_metadatos(tags: dict[str, str]) -> list[str]:
    """Vuelve a escribir a mano las etiquetas del original.

    Con "-map_metadata 0" ffmpeg copia las que sabe traducir al contenedor de
    destino y **tira el resto sin decir nada**. En un FLAC de una tienda eso se
    lleva por delante el ISRC, el codigo de barras, el sello... y el ISRC no es
    un adorno: es la unica llave con la que se puede emparejar una cancion en
    TIDAL, porque su API no busca por texto.

    Pasarlas otra vez en minusculas da dos oportunidades: las que ffmpeg tiene
    en su tabla se colocan en su sitio, y del resto se encarga (o no) el
    contenedor. Lo que aun asi se pierda lo dira etiquetas_perdidas, que para
    eso esta.
    """
    fuera = []
    for clave, valor in sorted(tags.items()):
        if clave in ETIQUETAS_DEL_CONTENEDOR or not valor:
            continue
        fuera += ["-metadata", f"{clave}={valor}"]
    return fuera


def etiquetas_perdidas(antes: dict[str, str],
                       despues: dict[str, str]) -> list[str]:
    """Las etiquetas del original que no han llegado al fichero nuevo."""
    llegadas = {k.lower() for k in despues}
    return sorted(k for k in antes
                  if k.lower() not in llegadas
                  and k not in ETIQUETAS_DEL_CONTENEDOR)


def completar_etiquetas(ffmpeg: str, origen: Path, destino: Path) -> list[str]:
    """Le pone al fichero nuevo las etiquetas que ffmpeg no supo colocar.

    ffmpeg solo escribe en un .m4a las etiquetas de su tabla, y tira el resto
    aunque se las pases a mano: el ISRC, el codigo de barras, el sello... Aqui
    se vuelven a poner, y lo que aun asi no entre se dice en voz alta. No se
    aborta por esto -la musica esta entera, que es lo que importa-, pero
    tampoco se calla: enterarse un mes despues, con la biblioteca ya
    convertida, no vale de nada.
    """
    ffprobe = _buscar_ffprobe(ffmpeg)
    if not ffprobe:
        return []
    del_origen = _leer_tags(ffprobe, origen)
    faltan = etiquetas_perdidas(del_origen, _leer_tags(ffprobe, destino))
    if not faltan:
        return []

    problema = ""
    if es_mp4(destino):
        problema = escribir_libres(destino,
                                   {k: del_origen[k] for k in faltan})
        if not problema:
            # Se leen del propio fichero, no del informe de ffprobe: quien las
            # ha escrito es quien sabe si estan.
            puestas = leer_libres(destino)
            faltan = [k for k in faltan if k not in puestas]
            if not faltan:
                return []

    aviso = [f"OJO: el {destino.suffix} no se ha quedado con estas etiquetas: "
             + ", ".join(faltan)]
    if problema:
        aviso.append(f"     no se han podido escribir aparte: {problema}")
    if "isrc" in faltan:
        aviso.append("     el ISRC es el codigo con el que se empareja una "
                     "cancion en TIDAL: sin el, esa no se puede publicar alli")
    return aviso


def escribir_libres(destino: Path, tags: dict[str, str]) -> str:
    """Mete esas etiquetas en el .m4a como atomos libres. "" si va bien.

    Un MP4 guarda lo que no es estandar en atomos "----", con un `mean` que
    dice de quien son; `com.apple.iTunes` es el que usa iTunes y el que miran
    los demas programas. Se hace con mutagen y no a mano porque agrandar el
    indice de un MP4 obliga a recolocar los desplazamientos de cada trozo de
    audio, y esa es justo la clase de cosa que no conviene escribir uno mismo.
    """
    if not tags:
        return ""
    try:
        from mutagen.mp4 import MP4, MP4FreeForm  # type: ignore[import-untyped]
    except ImportError:
        return ("falta el paquete mutagen; vuelve a ejecutar instalar.bat en "
                "este equipo")
    try:
        fichero = MP4(destino)
        for clave, valor in tags.items():
            fichero[f"{PREFIJO_LIBRE}{clave.upper()}"] = MP4FreeForm(
                str(valor).encode("utf-8"))
        fichero.save()
    except Exception as exc:  # noqa: BLE001 - mutagen lanza lo suyo
        return str(exc)
    return ""


def leer_libres(source: Path) -> dict[str, str]:
    """Las etiquetas que un .m4a guarda en atomos libres, en minusculas."""
    if not es_mp4(source):
        return {}
    try:
        from mutagen.mp4 import MP4  # type: ignore[import-untyped]
        fichero = MP4(source)
    except Exception:  # noqa: BLE001 - sin mutagen, o fichero ilegible
        return {}
    fuera = {}
    for clave, valor in (fichero.tags or {}).items():
        if not clave.startswith(PREFIJO_LIBRE) or not valor:
            continue
        try:
            fuera[clave[len(PREFIJO_LIBRE):].lower()] = \
                bytes(valor[0]).decode("utf-8", "replace").strip()
        except Exception:  # noqa: BLE001 - atomo raro, se ignora
            continue
    return fuera


def _rate_de(args: list[str]) -> int:
    """La frecuencia que lleva una lista de argumentos de ffmpeg, o 0."""
    try:
        return int(args[args.index("-ar") + 1])
    except (ValueError, IndexError):
        return 0


def formato_de(codec: str, bits: int) -> str:
    """Como llama cada codificador al formato de muestra que queremos.

    Vacio para los que no admiten que se les diga: a un PCM se lo fija ya su
    propio nombre, y a uno con perdida no le pinta nada.
    """
    par = FORMATO_MUESTRA.get(codec)
    return "" if par is None else par[0 if bits <= 16 else 1]


def comprobar_salida(salida: Path, original: Path | None = None) -> str:
    """Motivo por el que lo recien escrito NO puede sustituir al original.

    Esto es lo ultimo que se mira antes de machacar una cancion, asi que peca
    de desconfiado a proposito. Que ffmpeg termine diciendo que todo ha ido
    bien **no basta**: ya paso una vez que se dio por buena una conversion que
    habia salido sin pista de audio, y al sustituir se perdio el original.

    Se comprueban tres cosas, de la mas grave a la menos:

    1. Que siga habiendo audio. Un .m4a con la portada y nada mas pesa cuatro
       cientos de kilobytes y no da ningun error al abrirlo.
    2. Que la cabecera declare su frecuencia. A cero, hay programas que se
       cierran al leerla (ver args_calidad).
    3. Que dure mas o menos lo mismo que el original. Bajar la calidad cambia
       lo que ocupa, nunca lo que dura.
    """
    if not es_mp4(salida):
        return ""
    if not salida.is_file() or not salida.stat().st_size:
        return "no se ha llegado a escribir"

    cajas, fallo = _insistiendo(lambda: _leer_moov(salida))
    if fallo:
        return f"no se ha podido volver a leer: {fallo}"
    if cajas is None:
        return "no tiene indice (moov): no es un MP4 valido"
    if not (_buscar_caja(cajas, 0, len(cajas), b"alac")
            or _buscar_caja(cajas, 0, len(cajas), b"mp4a")):
        return ("ha salido SIN pista de audio, solo con la portada: se "
                "descarta antes de que sustituya a la buena")

    declarada = frecuencia_declarada(salida)
    if declarada == 0:
        return ("ha salido con la frecuencia a cero en la cabecera, que es lo "
                "que hace que otros programas se cierren al abrirlo")

    saltos = fotogramas_imposibles(salida)
    if saltos:
        return saltos

    if original is not None and es_mp4(original):
        antes, ahora = duracion_mp4(original), duracion_mp4(salida)
        # Un margen de dos segundos cubre el redondeo de los fotogramas.
        if antes and ahora and abs(antes - ahora) > max(2.0, antes * 0.02):
            return (f"dura {ahora:.0f}s y el original {antes:.0f}s: algo se ha "
                    "quedado por el camino")
    return ""


def fotogramas_imposibles(source: Path) -> str:
    """Avisa si la tabla de tiempos declara fotogramas mas largos de lo posible.

    Un ALAC dice en su cookie cuantas muestras mide un fotograma (4096, casi
    siempre) y ninguno puede medir mas. Cuando la tabla `stts` declara uno de
    7666, lo que hay ahi no es un fotograma gigante sino **un salto en la linea
    de tiempo**: al fichero le falta un trozo de reloj.

    ffmpeg lo decodifica sin quejarse, porque cada fotograma por separado esta
    bien, y la duracion total sigue cuadrando. Pero un programa que recorra la
    tabla para dibujar la onda se encuentra con un hueco donde no deberia
    haberlo, y rekordbox se cierra al analizarla.

    Sale de normalizar sin reencuadrar despues: el filtro suelta su buffer al
    final y el multiplexor apunta el salto como si fuera un fotograma largo.
    """
    if not es_mp4(source):
        return ""
    try:
        datos = _leer_moov(source)
    except OSError:
        return ""
    if datos is None:
        return ""

    caja = _buscar_caja(datos, 0, len(datos), b"alac")
    if caja is None:
        return ""           # solo se sabe el tope de un fotograma en ALAC
    cuerpo = datos[caja[0]:caja[1]]
    hueco = cuerpo[28:].find(b"alac")
    if hueco < 0 or len(cuerpo) < 28 + hueco + 12:
        return ""
    tope = int.from_bytes(cuerpo[28 + hueco + 8:28 + hueco + 12], "big")
    if not tope:
        return ""

    caja = _buscar_caja(datos, 0, len(datos), b"stts")
    if caja is None:
        return ""
    tabla = datos[caja[0]:caja[1]]
    cuantas = int.from_bytes(tabla[4:8], "big")
    peores = []
    for i in range(min(cuantas, 4096)):
        trozo = tabla[8 + i * 8:16 + i * 8]
        if len(trozo) < 8:
            break
        cuenta = int.from_bytes(trozo[:4], "big")
        dura = int.from_bytes(trozo[4:], "big")
        if dura > tope:
            peores.append((cuenta, dura))
    if not peores:
        return ""
    detalle = ", ".join(f"{c}x{d}" for c, d in peores[:3])
    return (f"tiene saltos en la linea de tiempo: declara fotogramas de "
            f"{detalle} muestras cuando el maximo del codec es {tope}")


def duracion_mp4(source: Path) -> float:
    """Lo que dura, sacado de la cabecera del propio fichero (sin ffprobe)."""
    try:
        datos = _leer_moov(source)
    except OSError:
        return 0.0
    if datos is None:
        return 0.0
    caja = _buscar_caja(datos, 0, len(datos), b"mvhd")
    if caja is None:
        return 0.0
    cuerpo = datos[caja[0]:caja[1]]
    try:
        version = cuerpo[0]
        if version == 1:
            escala = int.from_bytes(cuerpo[20:24], "big")
            cuanto = int.from_bytes(cuerpo[24:32], "big")
        else:
            escala = int.from_bytes(cuerpo[12:16], "big")
            cuanto = int.from_bytes(cuerpo[16:20], "big")
    except IndexError:
        return 0.0
    return cuanto / escala if escala else 0.0


def frecuencia_declarada(source: Path) -> int | None:
    """La frecuencia que dice la cabecera, o None si no se ha podido leer."""
    try:
        datos = _leer_moov(source)
    except OSError:
        return None
    if datos is None:
        return None
    caja = _buscar_caja(datos, 0, len(datos), b"alac") \
        or _buscar_caja(datos, 0, len(datos), b"mp4a")
    if caja is None:
        return None
    cuerpo = datos[caja[0]:caja[1]]
    if len(cuerpo) < 28:
        return None
    return int.from_bytes(cuerpo[24:28], "big") >> 16


def _cabecera_audio(source: Path) -> list[str]:
    """La frecuencia que DECLARA la cabecera, que no siempre es la de verdad.

    En un MP4 la frecuencia va en la tabla de descripcion como un numero de
    16.16 bits: la parte entera son 16 bits, o sea hasta 65535 Hz. Un 192000
    no cabe, asi que ffmpeg deja ese campo **a cero** y apunta la frecuencia
    buena en la cookie del codec.

    ffprobe lee la cookie y dice 192000 tan tranquilo, asi que por ahi no se
    ve nada raro. Pero un programa que se fie de la cabecera se encuentra un
    cero, y no todos lo sobreviven: rekordbox se cierra al analizar el fichero
    sin decir por que. A 44,1 kHz el campo cabe y no pasa nada.
    """
    if not es_mp4(source):
        return []
    declarada = frecuencia_declarada(source)
    if declarada is None:
        return ["  No se ha podido leer la cabecera de audio: o no es un MP4 "
                "valido, o esta cortado, o no lleva audio reconocible."]

    fuera = [f"  Frecuencia declarada en la cabecera: {declarada} Hz"]
    if declarada == 0:
        fuera.append("  OJO: la cabecera dice 0 Hz. Ese campo solo llega a "
                     "65535, asi que una cancion de 192 kHz no cabe y se "
                     "queda a cero. Hay programas que se cierran al leerlo "
                     "(rekordbox, sin ir mas lejos, al analizarla). Se "
                     "arregla bajandola a calidad CD.")
    return fuera


def _leer_moov(source: Path) -> bytes | None:
    """El bloque moov, este al principio o al final del fichero."""
    total = source.stat().st_size
    with source.open("rb") as mano:
        posicion = 0
        while posicion < total:
            mano.seek(posicion)
            cabecera = mano.read(8)
            if len(cabecera) < 8:
                return None
            tamano = int.from_bytes(cabecera[:4], "big")
            tipo = cabecera[4:8]
            cuerpo = posicion + 8
            if tamano == 1:
                tamano = int.from_bytes(mano.read(8), "big")
                cuerpo = posicion + 16
            elif tamano == 0:
                tamano = total - posicion
            if tamano < 8:
                return None
            if tipo == b"moov":
                mano.seek(cuerpo)
                return mano.read(min(tamano, MAX_MOOV))
            posicion += tamano
    return None


def _buscar_caja(datos: bytes, inicio: int, fin: int,
                 quiero: bytes) -> tuple[int, int] | None:
    """(principio, final) del cuerpo de esa caja, buscando en las que anidan."""
    posicion = inicio
    while posicion + 8 <= fin:
        tamano = int.from_bytes(datos[posicion:posicion + 4], "big")
        tipo = datos[posicion + 4:posicion + 8]
        cuerpo = posicion + 8
        if tamano == 1:
            tamano = int.from_bytes(datos[posicion + 8:posicion + 16], "big")
            cuerpo = posicion + 16
        elif tamano == 0:
            tamano = fin - posicion
        if tamano < 8:
            return None
        if tipo == quiero:
            return cuerpo, posicion + tamano
        if tipo in ANIDAN:
            # La tabla de descripciones lleva delante un contador de 8 bytes.
            salto = 8 if tipo == b"stsd" else 0
            dentro = _buscar_caja(datos, cuerpo + salto, posicion + tamano, quiero)
            if dentro:
                return dentro
        posicion += tamano
    return None


def _estructura_mp4(source: Path) -> list[str]:
    """Los bloques de un .m4a: donde esta el indice y si el fichero llega entero."""
    if not es_mp4(source):
        return []
    bloques: list[tuple[str, int]] = []
    truncado = False
    try:
        total = source.stat().st_size
        with source.open("rb") as mano:
            posicion = 0
            while posicion < total:
                cabecera = mano.read(8)
                if len(cabecera) < 8:
                    truncado = True
                    break
                tamano = int.from_bytes(cabecera[:4], "big")
                tipo = cabecera[4:8].decode("latin-1", "replace")
                if tamano == 1:                     # tamano de 64 bits
                    tamano = int.from_bytes(mano.read(8), "big")
                elif tamano == 0:                   # hasta el final
                    tamano = total - posicion
                if tamano < 8:
                    truncado = True
                    break
                bloques.append((tipo, tamano))
                if posicion + tamano > total:
                    truncado = True
                    break
                posicion += tamano
                mano.seek(posicion)
    except OSError as exc:
        return [f"  No se ha podido mirar la estructura: {exc}"]

    if not bloques:
        return ["  No parece un MP4: no tiene bloques reconocibles."]
    nombres = [tipo for tipo, _ in bloques]
    fuera = [f"  Bloques: {' '.join(nombres)}"]
    if "moov" in nombres and "mdat" in nombres:
        primero = nombres.index("moov") < nombres.index("mdat")
        fuera.append(f"  El indice (moov) va al principio: "
                     f"{'si' if primero else 'NO, va al final'}")
    if truncado:
        fuera.append("  OJO: el fichero esta CORTADO. El ultimo bloque dice "
                     "medir mas de lo que hay.")
    return fuera


def _decodificar(ffmpeg: str, source: Path) -> list[str]:
    """Decodifica el fichero entero sin guardar nada, y devuelve las quejas."""
    orden = [ffmpeg, "-v", "error", "-hide_banner", "-i", str(source),
             "-f", "null", "-"]
    try:
        hecho = subprocess.run(orden, capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               timeout=TIMEOUT_S, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [f"    no se ha podido comprobar: {exc}"]

    quejas = [l for l in (hecho.stderr or "").strip().splitlines() if l.strip()]
    if hecho.returncode == 0 and not quejas:
        return ["    sin errores: el audio esta entero."]
    fuera = [f"    {len(quejas)} errores al decodificar "
             f"(ffmpeg termino con {hecho.returncode}):"]
    fuera += [f"      {linea}" for linea in quejas[:20]]
    if len(quejas) > 20:
        fuera.append(f"      ... y {len(quejas) - 20} mas")
    return fuera


def _numero(valor: Any) -> float:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return 0.0


def tamano_legible(size: float) -> str:
    """Bytes en algo que se lea de un vistazo."""
    for unidad in ("B", "KB", "MB"):
        if size < 1024 or unidad == "MB":
            return f"{size:.0f} B" if unidad == "B" else f"{size:.1f} {unidad}"
        size /= 1024.0
    return f"{size:.1f} MB"


def caratula_de(ffprobe: str, source: Path) -> str:
    """Con que formato viene la portada dentro del fichero ("" si no trae)."""
    orden = [ffprobe, "-v", "quiet", "-print_format", "json", "-show_streams",
             "-select_streams", "v:0", str(source)]
    try:
        salida = subprocess.run(orden, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=60,
                                creationflags=NO_WINDOW)
        streams = json.loads(salida.stdout or "{}").get("streams") or []
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return ""
    return str(streams[0].get("codec_name") or "") if streams else ""


def args_caratula(codec: str) -> list[str]:
    """Como meter la portada en un .m4a sin que quede fuera de norma.

    Un FLAC suele traerla en PNG, y en un MP4 la portada va en JPEG. Copiarla
    tal cual deja un fichero que iTunes se traga pero que otros programas no:
    rekordbox, sin ir mas lejos, se cierra al cargarlo. Si ya viene en JPEG se
    copia sin tocarla; si no, se recodifica, que en una caratula no se nota.
    """
    if not codec:
        return ["-vn"]
    salida = ["-c:v", "copy"] if codec in ART_JPEG else ["-c:v", "mjpeg", "-q:v", "2"]
    # Sin "-frames:v 1": marcarla como attached_pic ya dice que es una imagen
    # suelta, y ese limite de fotogramas se llevo por delante la pista de audio
    # de una cancion de verdad. Lo que no hace falta, no se pone.
    return ["-map", "0:v:0?"] + salida + ["-disposition:v", "attached_pic"]


def _leer_tags(ffprobe: str, source: Path) -> dict[str, str]:
    """Etiquetas que ya trae el fichero, en minusculas y sin vacias."""
    orden = [ffprobe, "-v", "quiet", "-print_format", "json", "-show_format",
             str(source)]
    try:
        salida = subprocess.run(orden, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=60,
                                creationflags=NO_WINDOW)
        datos = json.loads(salida.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {}
    etiquetas = (datos.get("format") or {}).get("tags") or {}
    fuera = {str(k).lower(): str(v).strip()
             for k, v in etiquetas.items() if str(v).strip()}
    # Las libres de un .m4a no siempre las cuenta ffprobe, y ahi es donde vive
    # el ISRC despues de convertir: se leen del fichero y se suman.
    for clave, valor in leer_libres(source).items():
        fuera.setdefault(clave, valor)
    return fuera


# "(feat. X)", "ft. Y", "con Z"... y lo que va detras, hasta cerrar el
# parentesis o acabar el titulo.
_INVITADOS = re.compile(
    r"[\(\[\-]?\s*(?:feat\.?|ft\.?|featuring|with|con)\s+([^)\]]+)[\)\]]?",
    re.IGNORECASE)
# Dentro de ese trozo puede haber varios: "X, Y & Z".
_ENTRE_INVITADOS = re.compile(r"\s*(?:,|&|;|\+|\by\b)\s*", re.IGNORECASE)
# Como se separan los interpretes al escribirlos: es lo que ya usan los FLAC
# de las tiendas y lo que iTunes entiende.
SEPARADOR_ARTISTAS = "; "


def artistas_del_titulo(titulo: str) -> list[str]:
    """Los interpretes que van escondidos en el titulo, no en el artista.

    "EL BACHATON (feat. Lucho RK)" trae a Lucho RK, pero la etiqueta de
    artista suele decir solo "Lola Indigo", y asi es como entra la cancion en
    iTunes: a nombre de uno solo.
    """
    encontrado = _INVITADOS.search(titulo or "")
    if not encontrado:
        return []
    fuera = []
    for trozo in _ENTRE_INVITADOS.split(encontrado.group(1)):
        nombre = trozo.strip(" -_()[]")
        if nombre and nombre.lower() not in ("los", "las", "el", "la"):
            fuera.append(nombre)
    return fuera


def sumar_artistas(artista: str, titulo: str) -> str:
    """El artista con los invitados del titulo anadidos, o "" si no cambia.

    Solo suma: nunca quita ni cambia lo que ya hubiera, y no repite a uno que
    ya estuviera escrito aunque sea de otra manera.
    """
    invitados = artistas_del_titulo(titulo)
    if not invitados:
        return ""
    hay = [a.strip() for a in re.split(r"\s*[;,]\s*", artista or "") if a.strip()]
    vistos = {_llano(a) for a in hay}
    nuevos = [a for a in invitados if _llano(a) and _llano(a) not in vistos]
    if not nuevos:
        return ""
    return SEPARADOR_ARTISTAS.join(hay + nuevos)


def _llano(texto: str) -> str:
    """Para comparar nombres sin que estorben mayusculas ni puntuacion."""
    return re.sub(r"[^a-z0-9]+", "", (texto or "").lower())


_SEPARADOR = re.compile(r"\s+-\s+")
_PISTA = re.compile(r"^\s*(\d{1,2})\s*(?:[-._)]\s*|\s+)")


def _partir_nombre(stem: str) -> tuple[str, str, str]:
    """De "01 - Xiyo - Do You Remember" saca artista, titulo y numero de pista."""
    texto, pista = stem.strip(), ""
    principio = _PISTA.match(texto)
    if principio:
        resto = texto[principio.end():]
        # El numero solo cuenta como pista si lleva separador ("01 - X") o si
        # lo que queda tiene forma de "Artista - Titulo". Sin eso podria ser
        # parte del nombre, como en "99 Luftballons".
        if principio.group(0).strip()[-1] in "-._)" or _SEPARADOR.search(resto):
            pista, texto = str(int(principio.group(1))), resto

    partes = _SEPARADOR.split(texto, maxsplit=1)
    if len(partes) == 2 and partes[0].strip() and partes[1].strip():
        return partes[0].strip(), partes[1].strip(), pista
    return "", texto.strip(), pista


def _size_change(source: Path, target: Path) -> str:
    """Cuanto ha bajado. iTunes puede llevarse el .m4a antes de que lo miremos."""
    try:
        antes, despues = source.stat().st_size, target.stat().st_size
    except OSError:
        return ""
    if not antes or not despues:
        return ""
    return f"  ({_mb(antes)} -> {_mb(despues)})"


def _mb(size: int) -> str:
    return f"{size / (1024 * 1024):.1f} MB"


def nombre_libre(destino: Path) -> Path:
    """Un nombre que no pise nada de lo que ya haya en esa carpeta."""
    candidato, numero = destino, 2
    while candidato.exists():
        candidato = destino.with_name(f"{destino.stem} ({numero}){destino.suffix}")
        numero += 1
    return candidato


def _free_name(folder: Path, stem: str, suffix: str = ".m4a") -> Path:
    """Un nombre libre en la carpeta: nunca se machaca lo que ya hay."""
    return nombre_libre(folder / f"{stem}{suffix}")
