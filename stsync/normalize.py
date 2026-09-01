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
                      args_caratula, caratula_de, find_ffmpeg, leer_audio,
                      loudnorm_con, medir_volumen, supera_calidad_cd,
                      volumen_actual, _buscar_ffprobe)
from .itunes import ITunesError, ITunesLibrary, _com_items
from .store import StateStore

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
    ya_hechas: int = 0          # repasadas en una pasada anterior
    saltadas: int = 0           # sin fichero, o formato que no se toca
    sin_refrescar: int = 0      # cambiadas, pero iTunes no releyo sus datos
    fallidas: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        texto = (f"Biblioteca: {self.revisadas} revisadas | "
                 f"{self.normalizadas} normalizadas ({self.bajadas} ademas "
                 f"bajadas a calidad CD, {self.a_alac} pasadas a ALAC) | "
                 f"{self.ya_estaban} ya estaban bien | "
                 f"{self.ya_hechas} de antes | "
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

    # Lo ya repasado se apunta, porque medir es lo que cuesta: sin esto, una
    # segunda pasada vuelve a decodificar la biblioteca entera para nada.
    state = StateStore()
    huella = _huella(minimo, maximo, a_cd, con_perdida,
                     bool(cfg.get("library_to_alac", True)))
    saltar = bool(cfg.get("library_skip_done", True))
    hechas: dict[str, str] = {}
    if saltar and state.data.get("library_huella") == huella:
        hechas = dict(state.data.get("library_ok") or {})
        if hechas:
            log(f"  {len(hechas)} ya repasadas en su dia: no se vuelven a medir")
    elif state.data.get("library_huella"):
        log("  los ajustes han cambiado, asi que se repasa todo otra vez")

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
                _guardar(state, hechas, huella)
            try:
                _revisar(track, ffmpeg, ffprobe, cfg, minimo, maximo, a_cd,
                         con_perdida, log, stats, hechas if saltar else None)
            except ConvertError:
                raise           # sin disco: parar de verdad
            except Exception as exc:  # noqa: BLE001
                # Una cancion rara no puede llevarse por delante las que faltan.
                log(f"      se ha saltado por un error inesperado: {exc}")
                stats.fallidas.append((f"cancion {numero}", str(exc)))
    finally:
        library.close()
        # Tambien si se corta a medias: lo hecho hasta aqui no se repite.
        _guardar(state, hechas, huella)

    log(f"  {stats.summary()}")
    if stats.sin_refrescar:
        log("  Esas seguiran ensenando en iTunes los kbps de antes hasta que "
            "las selecciones y uses Archivo > Biblioteca > Obtener informacion.")
    return stats


def _revisar(track: Any, ffmpeg: str, ffprobe: str, cfg: Config,
             minimo: float, maximo: float, a_cd: bool, con_perdida: bool,
             log: Callable[[str], None], stats: LibraryStats,
             hechas: dict[str, str] | None) -> None:
    fichero = _fichero_de(track)
    if fichero is None:
        stats.saltadas += 1
        return

    # Si ya se repaso y el fichero no ha cambiado desde entonces, no hay nada
    # que mirar: medirla nuevamente cuesta lo mismo que la primera vez.
    if hechas is not None and hechas.get(str(fichero)) == _marca(fichero):
        stats.ya_hechas += 1
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
        _apuntar(hechas, fichero)
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

    final = fichero
    if a_alac:
        error, final = _convertir_a_alac(ffmpeg, fichero, track, medida, bajar, log)
    else:
        error = _reescribir(ffmpeg, fichero, codec_args, medida, bajar)
    if error:
        log(f"      no se pudo: {error}")
        stats.fallidas.append((fichero.name, error))
        return

    stats.normalizadas += 1
    stats.bajadas += bool(bajar)
    stats.a_alac += bool(a_alac)
    _apuntar(hechas, final)

    # iTunes se queda con lo que anoto el dia que la importo: si no se le dice
    # que relea el fichero, sigue ensenando los kbps y el tamano de antes.
    try:
        track.UpdateInfoFromFile()
    except Exception as exc:  # noqa: BLE001 - iTunes ocupado, fichero en uso...
        stats.sin_refrescar += 1
        log(f"      cambiada, pero iTunes no ha releido sus datos: {exc}")


@dataclass
class RefreshStats:
    miradas: int = 0
    refrescadas: int = 0
    fallidas: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        texto = (f"Datos releidos: {self.refrescadas} de {self.miradas} "
                 f"candidatas")
        if self.fallidas:
            texto += f" | {len(self.fallidas)} que iTunes no ha querido releer"
        return texto


def refresh_info(cfg: Config, log: Callable[[str], None],
                 should_stop: Callable[[], bool] | None = None) -> RefreshStats:
    """Obliga a iTunes a releer de los ficheros los kbps y la frecuencia.

    iTunes se queda con lo que anoto el dia que importo la cancion. Si el
    fichero se ha bajado a calidad CD por fuera, la ventana sigue ensenando los
    9216 kbps de un 24/192 aunque en disco ya sea un 16/44,1 de 1411.

    Solo se tocan las que declaran mas de lo que cabe en un CD, que son las
    unicas que pueden estar desfasadas. Una que de verdad siga en alta
    resolucion volvera a decir lo mismo: releerla no le hace nada.
    """
    parar = should_stop or (lambda: False)
    stats = RefreshStats()

    log("")
    log("== Releer los datos en iTunes ==")
    if cfg.dry_run:
        log("  (simulacion: solo se dice cuales harian falta)")

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
            if numero % 500 == 0:
                log(f"    {numero}/{total}...")
            if _fichero_de(track) is None or not _parece_desfasada(track):
                continue

            stats.miradas += 1
            if cfg.dry_run:
                continue
            try:
                track.UpdateInfoFromFile()
                stats.refrescadas += 1
            except Exception as exc:  # noqa: BLE001 - iTunes ocupado, en uso...
                stats.fallidas.append((str(getattr(track, "Name", "?")), str(exc)))
    finally:
        library.close()

    log(f"  {stats.summary()}")
    return stats


def _parece_desfasada(track: Any) -> bool:
    """Declara mas calidad de la que cabe en un CD: puede ser un dato viejo."""
    try:
        if int(track.BitRate or 0) > 1411:
            return True
        return int(track.SampleRate or 0) > CD_RATE
    except Exception:  # noqa: BLE001 - la cancion no expone esos campos
        return False


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
                      log: Callable[[str], None]) -> tuple[str, Path]:
    """Pasa la cancion a ALAC y le dice a iTunes donde esta ahora.

    Aqui cambia la extension, asi que no basta con sustituir el fichero: si no
    se le reapunta, iTunes se queda buscando un .wav que ya no existe y la
    cancion aparece con la exclamacion. Por eso el original no se borra hasta
    que iTunes ha aceptado la ruta nueva.
    """
    nuevo = _nombre_libre(fichero.with_suffix(".m4a"))
    error = _convertir(ffmpeg, fichero, nuevo, ["-c:a", "alac"], medida, bajar)
    if error:
        return error, fichero

    try:
        track.Location = str(nuevo)
    except Exception as exc:  # noqa: BLE001 - iTunes puede no dejarse
        _borrar(nuevo)
        return f"iTunes no ha aceptado la ruta nueva ({exc})", fichero

    # Ya apunta al nuevo: el viejo sobra. Si no se puede borrar tampoco pasa
    # nada grave, solo ocupa; se avisa y se sigue.
    try:
        fichero.unlink()
    except OSError as exc:
        log(f"      convertida, pero el {fichero.suffix} viejo sigue ahi: {exc}")
    return "", nuevo


def _marca(fichero: Path) -> str:
    """Como esta el fichero ahora: si cambia, hay que volver a mirarlo."""
    try:
        info = fichero.stat()
        return f"{int(info.st_mtime)}:{info.st_size}"
    except OSError:
        return ""


def _apuntar(hechas: dict[str, str] | None, fichero: Path) -> None:
    if hechas is not None:
        hechas[str(fichero)] = _marca(fichero)


def _huella(minimo: float, maximo: float, a_cd: bool, con_perdida: bool,
            a_alac: bool) -> str:
    """Con que criterios se repaso. Si cambian, lo apuntado ya no vale."""
    return f"{minimo}|{maximo}|{a_cd}|{con_perdida}|{a_alac}"


def _guardar(state: StateStore, hechas: dict[str, str], huella: str) -> None:
    state.data["library_ok"] = hechas
    state.data["library_huella"] = huella
    try:
        state.save()
    except OSError:
        pass    # perder el apunte solo cuesta tiempo la proxima vez


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
    error = _convertir(ffmpeg, fichero, temporal, codec_args, medida, bajar)
    if error:
        return error

    fallo = _sustituir(temporal, fichero)
    if fallo:
        _borrar(temporal)
        return f"no se pudo sustituir el fichero ({fallo})"
    return ""


def _convertir(ffmpeg: str, entrada: Path, salida: Path, codec_args: list[str],
               medida: dict[str, str] | None, bajar: bool) -> str:
    """Reescribe la cancion con el volumen arreglado. Devuelve "" si va bien.

    Se mapea el audio y, aparte, la caratula marcada como tal: con un simple
    "-map 0 -c:v copy" el contenedor de los .m4a se niega a cerrar el fichero
    ("Error closing file: Invalid argument") y no se salva ni una. Si aun asi
    la rechaza, se repite sin ella antes de darla por perdida.

    La caratula tampoco se copia a ciegas: un PNG dentro de un .m4a esta
    fuera de norma y hay programas que se cierran al abrirlo. Ver
    convert.args_caratula.
    """
    ffprobe = _buscar_ffprobe(ffmpeg)
    arte = caratula_de(ffprobe, entrada) if ffprobe else "desconocida"

    detalle = "ffmpeg fallo"
    for con_caratula in (True, False):
        orden = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                 "-i", str(entrada), "-map", "0:a:0"]
        orden += args_caratula(arte) if con_caratula else ["-vn"]
        orden += ["-af", loudnorm_con(medida)] + codec_args
        if bajar:
            orden += ["-ar", str(CD_RATE), "-sample_fmt", CD_FORMAT]
        if salida.suffix.lower() in (".m4a", ".mp4"):
            orden += ["-movflags", "+faststart"]
        orden += ["-map_metadata", "0", str(salida)]

        try:
            hecho = subprocess.run(orden, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace",
                                   timeout=TIMEOUT_S, creationflags=NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired) as exc:
            _borrar(salida)
            return str(exc)

        if hecho.returncode == 0 and salida.is_file() and salida.stat().st_size:
            return ""

        lineas = (hecho.stderr or "").strip().splitlines()
        detalle = lineas[-1] if lineas else "ffmpeg fallo"
        _borrar(salida)
        # Quedarse sin disco no se arregla repitiendo, y seguir con las 7000
        # que faltan solo alarga la lista de fallos.
        if "no space left" in detalle.lower():
            raise ConvertError(
                "El disco se ha quedado sin espacio. Haz sitio y vuelve a "
                "lanzarlo: lo que ya estaba arreglado no se repite.")
    return detalle


def _borrar(fichero: Path) -> None:
    """Quita un fichero a medias sin montar un drama si esta bloqueado."""
    try:
        fichero.unlink(missing_ok=True)
    except OSError:
        pass    # ya lo tiene otro; peor seria abortar el repaso entero


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
