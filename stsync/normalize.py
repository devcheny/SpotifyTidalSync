"""Repaso de toda la biblioteca de iTunes: volumen parejo y calidad CD.

Es el trabajo que hacia NormalizeLibrary.ps1, pero sin duplicar la biblioteca
en otra carpeta y midiendo antes de tocar nada: cada cancion se analiza y solo
se reescribe si de verdad hace falta.

Se hace una vez y se olvida. Lo que entre despues ya sale normalizado del
conversor de FLAC.
"""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import Config
from .convert import (CD_FORMAT, CD_RATE, NO_WINDOW, TIMEOUT_S, ConvertError,
                      find_ffmpeg, leer_audio, loudnorm_con, medir_volumen,
                      supera_calidad_cd, volumen_actual, _buscar_ffprobe)
from .itunes import ITunesError, ITunesLibrary, _com_items

# Como volver a guardar cada formato. Los de la izquierda no pierden calidad al
# reescribirse; los de abajo si, porque hay que volver a comprimir.
SIN_PERDIDA = {
    "alac": ["-c:a", "alac"],
    "flac": ["-c:a", "flac"],
    "pcm_s16le": ["-c:a", "pcm_s16le"],
    "pcm_s24le": ["-c:a", "pcm_s24le"],
}
CON_PERDIDA = {
    "mp3": ["-c:a", "libmp3lame", "-q:a", "0"],
    "aac": ["-c:a", "aac", "-b:a", "256k"],
    "wmav2": ["-c:a", "wmav2", "-b:a", "192k"],
}

# En Windows, sustituir un fichero recien escrito falla de vez en cuando
# porque el antivirus o el indexador lo tienen abierto un momento.
INTENTOS_SUSTITUIR = 5
ESPERA_SUSTITUIR = 0.3


@dataclass
class LibraryStats:
    revisadas: int = 0
    normalizadas: int = 0
    bajadas: int = 0            # ademas, pasadas a calidad CD
    a_alac: int = 0             # ademas, convertidas de WAV/FLAC a ALAC
    ya_estaban: int = 0
    saltadas: int = 0           # sin fichero, o formato que no se toca
    sin_refrescar: int = 0      # cambiadas, pero iTunes no releyo sus datos
    fallidas: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        texto = (f"Biblioteca: {self.revisadas} revisadas | "
                 f"{self.normalizadas} normalizadas ({self.bajadas} ademas "
                 f"bajadas a calidad CD, {self.a_alac} pasadas a ALAC) | "
                 f"{self.ya_estaban} ya estaban bien | "
                 f"{self.saltadas} sin tocar")
        if self.fallidas:
            texto += f" | {len(self.fallidas)} con error"
        if self.sin_refrescar:
            texto += f" | {self.sin_refrescar} sin releer en iTunes"
        return texto


def normalize_library(cfg: Config, log: Callable[[str], None],
                      should_stop: Callable[[], bool] | None = None
                      ) -> LibraryStats:
    """Deja toda la biblioteca al mismo volumen, y en calidad CD si se pide."""
    parar = should_stop or (lambda: False)
    stats = LibraryStats()

    ffmpeg = find_ffmpeg(str(cfg.get("ffmpeg_path", "")))
    if not ffmpeg:
        raise ConvertError(
            "No se encuentra ffmpeg. Instalalo con 'winget install Gyan.FFmpeg' "
            "o indica su ruta en la pestana Convertir a ALAC.")
    ffprobe = _buscar_ffprobe(ffmpeg)
    if not ffprobe:
        raise ConvertError(
            "No se encuentra ffprobe, que viene con ffmpeg y hace falta para "
            "saber como esta grabada cada cancion.")

    minimo = float(cfg.get("library_min_lufs", -9.5))
    maximo = float(cfg.get("library_max_lufs", -8.5))
    a_cd = bool(cfg.get("flac_cd_quality", True))
    con_perdida = bool(cfg.get("library_include_lossy", False))

    log("")
    log("== Repaso de la biblioteca ==")
    if cfg.dry_run:
        log("  (simulacion: solo se mide, no se reescribe nada)")
    log(f"  volumen objetivo entre {minimo} y {maximo} LUFS"
        + (", y bajando lo que supere la calidad CD" if a_cd else ""))
    if not con_perdida:
        log("  los MP3 y demas formatos con perdida se dejan como estan")

    library = ITunesLibrary(log)
    library.connect()
    try:
        canciones = list(_com_items(library.app.LibraryPlaylist.Tracks))
        total = len(canciones)
        log(f"  {total} canciones en la biblioteca")
        for numero, track in enumerate(canciones, 1):
            if parar():
                log("  detenido por el usuario")
                break
            if numero % 100 == 0:
                log(f"    {numero}/{total}... ({stats.normalizadas} arregladas)")
            _revisar(track, ffmpeg, ffprobe, cfg, minimo, maximo, a_cd,
                     con_perdida, log, stats)
    finally:
        library.close()

    log(f"  {stats.summary()}")
    if stats.sin_refrescar:
        log("  Esas seguiran ensenando en iTunes los kbps de antes hasta que "
            "las selecciones y uses Archivo > Biblioteca > Obtener informacion.")
    return stats


def _revisar(track: Any, ffmpeg: str, ffprobe: str, cfg: Config,
             minimo: float, maximo: float, a_cd: bool, con_perdida: bool,
             log: Callable[[str], None], stats: LibraryStats) -> None:
    fichero = _fichero_de(track)
    if fichero is None:
        stats.saltadas += 1
        return

    audio = leer_audio(ffprobe, fichero)
    codec = str(audio.get("codec", ""))
    codec_args = SIN_PERDIDA.get(codec)
    if codec_args is None:
        codec_args = CON_PERDIDA.get(codec) if con_perdida else None
    if codec_args is None:
        stats.saltadas += 1
        return

    stats.revisadas += 1
    medida = medir_volumen(ffmpeg, fichero)
    ahora = volumen_actual(medida)
    fuera = ahora is None or not (minimo <= ahora <= maximo)
    bajar = a_cd and supera_calidad_cd(audio)
    # Un WAV o un FLAC guardan lo mismo que un ALAC ocupando bastante mas, y
    # ademas iTunes no lee el FLAC: pasarlos merece la pena aunque suenen bien.
    a_alac = bool(cfg.get("library_to_alac", True)) and codec in SIN_PERDIDA \
        and codec != "alac"
    if not fuera and not bajar and not a_alac:
        stats.ya_estaban += 1
        return

    motivos = []
    if fuera:
        motivos.append(f"{ahora if ahora is not None else '?'} LUFS")
    if bajar:
        motivos.append(f"{audio.get('rate')} Hz / {audio.get('bits')} bits")
    if a_alac:
        motivos.append(f"{codec} a alac")
    log(f"  ~ {fichero.name}  ({', '.join(motivos)})")

    if cfg.dry_run:
        stats.normalizadas += 1
        stats.bajadas += bool(bajar)
        stats.a_alac += bool(a_alac)
        return

    if a_alac:
        error = _convertir_a_alac(ffmpeg, fichero, track, medida, bajar, log)
    else:
        error = _reescribir(ffmpeg, fichero, codec_args, medida, bajar)
    if error:
        log(f"      no se pudo: {error}")
        stats.fallidas.append((fichero.name, error))
        return

    stats.normalizadas += 1
    stats.bajadas += bool(bajar)
    stats.a_alac += bool(a_alac)

    # iTunes se queda con lo que anoto el dia que la importo: si no se le dice
    # que relea el fichero, sigue ensenando los kbps y el tamano de antes.
    try:
        track.UpdateInfoFromFile()
    except Exception as exc:  # noqa: BLE001 - iTunes ocupado, fichero en uso...
        stats.sin_refrescar += 1
        log(f"      cambiada, pero iTunes no ha releido sus datos: {exc}")


def _fichero_de(track: Any) -> Path | None:
    """La ruta del fichero, si la cancion es un fichero local que existe."""
    try:
        if int(track.Kind) != 1:      # 1 = fichero; lo demas es de la nube o CD
            return None
        ruta = str(track.Location or "")
    except Exception:  # noqa: BLE001
        return None
    if not ruta:
        return None
    fichero = Path(ruta)
    return fichero if fichero.is_file() else None


def _convertir_a_alac(ffmpeg: str, fichero: Path, track: Any,
                      medida: dict[str, str] | None, bajar: bool,
                      log: Callable[[str], None]) -> str:
    """Pasa la cancion a ALAC y le dice a iTunes donde esta ahora.

    Aqui cambia la extension, asi que no basta con sustituir el fichero: si no
    se le reapunta, iTunes se queda buscando un .wav que ya no existe y la
    cancion aparece con la exclamacion. Por eso el original no se borra hasta
    que iTunes ha aceptado la ruta nueva.
    """
    nuevo = _nombre_libre(fichero.with_suffix(".m4a"))
    orden = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
             "-i", str(fichero), "-map", "0", "-c:v", "copy",
             "-af", loudnorm_con(medida), "-c:a", "alac"]
    if bajar:
        orden += ["-ar", str(CD_RATE), "-sample_fmt", CD_FORMAT]
    orden += ["-map_metadata", "0", "-movflags", "+faststart", str(nuevo)]

    try:
        salida = subprocess.run(orden, capture_output=True, text=True,
                                encoding="utf-8", errors="replace",
                                timeout=TIMEOUT_S, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        nuevo.unlink(missing_ok=True)
        return str(exc)

    if salida.returncode != 0 or not nuevo.is_file() or nuevo.stat().st_size == 0:
        detalle = (salida.stderr or "").strip().splitlines()
        nuevo.unlink(missing_ok=True)
        return detalle[-1] if detalle else "ffmpeg fallo"

    try:
        track.Location = str(nuevo)
    except Exception as exc:  # noqa: BLE001 - iTunes puede no dejarse
        nuevo.unlink(missing_ok=True)
        return f"iTunes no ha aceptado la ruta nueva ({exc})"

    # Ya apunta al nuevo: el viejo sobra. Si no se puede borrar tampoco pasa
    # nada grave, solo ocupa; se avisa y se sigue.
    try:
        fichero.unlink()
    except OSError as exc:
        log(f"      convertida, pero el {fichero.suffix} viejo sigue ahi: {exc}")
    return ""


def _nombre_libre(destino: Path) -> Path:
    """Un nombre que no pise nada de lo que ya haya en esa carpeta."""
    candidato, numero = destino, 2
    while candidato.exists():
        candidato = destino.with_name(f"{destino.stem} ({numero}){destino.suffix}")
        numero += 1
    return candidato


def _reescribir(ffmpeg: str, fichero: Path, codec_args: list[str],
                medida: dict[str, str] | None, bajar: bool) -> str:
    """Convierte a un temporal y solo entonces sustituye el original.

    Asi una cancion que falle a medias no se queda destrozada: el fichero de
    siempre no se toca hasta que hay uno nuevo entero.
    """
    temporal = fichero.with_name(f".{fichero.stem}.normalizando{fichero.suffix}")
    orden = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
             "-i", str(fichero), "-map", "0", "-c:v", "copy",
             "-af", loudnorm_con(medida)]
    if bajar:
        orden += ["-ar", str(CD_RATE), "-sample_fmt", CD_FORMAT]
    orden += codec_args + ["-map_metadata", "0", str(temporal)]

    try:
        salida = subprocess.run(orden, capture_output=True, text=True,
                                encoding="utf-8", errors="replace",
                                timeout=TIMEOUT_S, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        temporal.unlink(missing_ok=True)
        return str(exc)

    if salida.returncode != 0 or not temporal.is_file() \
            or temporal.stat().st_size == 0:
        detalle = (salida.stderr or "").strip().splitlines()
        temporal.unlink(missing_ok=True)
        return detalle[-1] if detalle else "ffmpeg fallo"

    fallo = _sustituir(temporal, fichero)
    if fallo:
        temporal.unlink(missing_ok=True)
        return f"no se pudo sustituir el fichero ({fallo})"
    return ""


def _sustituir(temporal: Path, fichero: Path) -> str:
    """Cambia el fichero por el nuevo, con paciencia.

    En Windows esto falla de vez en cuando aunque nadie lo este usando: el
    antivirus o el indexador abren el fichero recien escrito un instante. Con
    una biblioteca entera pasa a menudo, asi que se reintenta antes de darlo
    por perdido. Si iTunes lo esta reproduciendo, no habra manera y se dira.
    """
    ultimo = ""
    for intento in range(INTENTOS_SUSTITUIR):
        try:
            os.replace(temporal, fichero)
            return ""
        except OSError as exc:
            ultimo = str(exc)
            time.sleep(ESPERA_SUSTITUIR * (intento + 1))
    return ultimo
