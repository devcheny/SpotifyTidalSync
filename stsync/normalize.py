"""Repaso de toda la biblioteca de iTunes: volumen parejo y calidad CD.

Es el trabajo que hacia NormalizeLibrary.ps1, pero sin duplicar la biblioteca
en otra carpeta y midiendo antes de tocar nada: cada cancion se analiza y solo
se reescribe si de verdad hace falta.

Se hace una vez y se olvida. Lo que entre despues ya sale normalizado del
conversor de FLAC.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import Config
from .convert import (CD_RATE, NO_WINDOW, OBJETIVOS_NOMBRE, POR_DEFECTO,
                      TIMEOUT_S, ConvertError, args_calidad, args_caratula,
                      caratula_de, completar_etiquetas, comprobar_salida,
                      filtro_audio, find_ffmpeg,
                      fotogramas_imposibles, leer_audio, medir_volumen,
                      nombre_libre, taller, volumen_actual, _buscar_ffprobe,
                      _rate_de, _traer)
from .itunes import ITunesError, recorrer_biblioteca
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
    objetivo = str(cfg.get("quality_target", POR_DEFECTO))
    con_perdida = bool(cfg.get("library_include_lossy", False))

    log("")
    log("== Repaso de la biblioteca ==")
    if cfg.dry_run:
        log("  (simulacion: solo se mide, no se reescribe nada)")
    log(f"  volumen objetivo entre {minimo} y {maximo} LUFS, y bajando lo que "
        f"pase de {OBJETIVOS_NOMBRE.get(objetivo, objetivo)}")
    if not con_perdida:
        log("  los MP3 y demas formatos con perdida se dejan como estan")

    # Lo ya repasado se apunta, porque medir es lo que cuesta: sin esto, una
    # segunda pasada vuelve a decodificar la biblioteca entera para nada.
    state = StateStore()
    huella = _huella(minimo, maximo, objetivo, con_perdida,
                     bool(cfg.get("library_to_alac", True)))
    saltar = bool(cfg.get("library_skip_done", True))
    hechas: dict[str, str] = {}
    if saltar and state.data.get("library_huella") == huella:
        hechas = dict(state.data.get("library_ok") or {})
        if hechas:
            log(f"  {len(hechas)} ya repasadas en su dia: no se vuelven a medir")
    elif state.data.get("library_huella"):
        log("  los ajustes han cambiado, asi que se repasa todo otra vez")

    def avisar(numero: int, total: int) -> None:
        log(f"    {numero}/{total}... ({stats.normalizadas} arregladas)")
        _guardar(state, hechas, huella)

    def una(track: Any) -> None:
        _revisar(track, ffmpeg, ffprobe, cfg, minimo, maximo, objetivo,
                 con_perdida, log, stats, hechas if saltar else None)

    try:
        recorrer_biblioteca(log, parar, _a_prueba_de_balas(una, log, stats),
                            paso=100, avisar=avisar)
    finally:
        # Tambien si se corta a medias: lo hecho hasta aqui no se repite.
        _guardar(state, hechas, huella)

    log(f"  {stats.summary()}")
    if stats.sin_refrescar:
        log("  Esas seguiran ensenando en iTunes los kbps de antes hasta que "
            "las selecciones y uses Archivo > Biblioteca > Obtener informacion.")
    return stats


def _a_prueba_de_balas(cada: Callable[[Any], None], log: Callable[[str], None],
                       stats: Any) -> Callable[[Any], None]:
    """Envuelve el trabajo de una cancion para que un fallo no pare el resto.

    Con una excepcion: quedarse sin disco no se arregla insistiendo, y seguir
    con las que faltan solo alarga la lista de fallos.
    """
    numero = [0]

    def protegida(track: Any) -> None:
        numero[0] += 1
        try:
            cada(track)
        except ConvertError:
            raise
        except Exception as exc:  # noqa: BLE001
            log(f"      se ha saltado por un error inesperado: {exc}")
            stats.fallidas.append((f"cancion {numero[0]}", str(exc)))

    return protegida


def _revisar(track: Any, ffmpeg: str, ffprobe: str, cfg: Config,
             minimo: float, maximo: float, objetivo: str, con_perdida: bool,
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
    # El destino sera .m4a si va a pasar a ALAC, y si no el mismo fichero.
    a_m4a = codec != "alac" and bool(cfg.get("library_to_alac", True))
    destino = fichero.with_suffix(".m4a") if a_m4a else fichero
    frecuencia, motivo_freq = args_calidad(int(audio.get("rate", 0)),
                                           int(audio.get("bits", 0)),
                                           objetivo, destino,
                                           "alac" if a_m4a else codec)
    # El motivo, no los argumentos: estos van siempre (ver args_calidad).
    bajar = bool(motivo_freq)
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
        motivos.append(f"{audio.get('rate')} Hz / {audio.get('bits')} bits, "
                       f"{motivo_freq}")
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
        error, final = _convertir_a_alac(ffmpeg, fichero, track, medida,
                                         frecuencia, log)
    else:
        error = _reescribir(ffmpeg, fichero, codec_args, medida, frecuencia,
                            log=lambda m: log(f"      {m}"))
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
class DownStats:
    revisadas: int = 0
    altas: int = 0              # por encima del techo de calidad
    con_saltos: int = 0         # con huecos en la tabla de tiempos
    bajadas: int = 0            # reescritas, por lo que fuera
    ahorrado: int = 0           # bytes que se dejan de ocupar
    sin_refrescar: int = 0
    # De las que no se miran, por que no se miran. Sin esto, un "37 mirados"
    # de 7047 canciones no se sabe si esta bien o si algo ha fallado en
    # silencio 7000 veces.
    sin_fichero: int = 0        # de la nube, o el fichero ya no esta
    con_perdida: int = 0        # MP3, AAC...: recomprimirlas las estropea
    ilegibles: int = 0          # ffprobe no ha sabido decir que son
    porques: list[str] = field(default_factory=list)   # un par de ejemplos
    fallidas: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        texto = (f"Ficheros: {self.revisadas} mirados | "
                 f"{self.altas} por encima del techo | "
                 f"{self.con_saltos} con saltos | {self.bajadas} arreglados")
        if self.ahorrado:
            texto += f" | {self.ahorrado / (1024 * 1024):.0f} MB liberados"
        if self.fallidas:
            texto += f" | {len(self.fallidas)} con error"
        if self.sin_refrescar:
            texto += f" | {self.sin_refrescar} sin releer en iTunes"
        return texto

    def descartadas(self) -> str:
        """Las que ni se han llegado a mirar, y por que. "" si no hay ninguna."""
        partes = []
        if self.con_perdida:
            partes.append(f"{self.con_perdida} con perdida (MP3, AAC...), que "
                          "no se tocan porque recomprimirlas las estropea")
        if self.sin_fichero:
            partes.append(f"{self.sin_fichero} sin fichero local (de la nube, "
                          "o la ruta ya no existe)")
        if self.ilegibles:
            partes.append(f"{self.ilegibles} que ffprobe no ha sabido leer")
        if not partes:
            return ""
        lineas = ["  no se han mirado: " + "; ".join(partes)]
        lineas += [f"      p.ej. {porque}" for porque in self.porques]
        return "\n".join(lineas)

    def apuntar(self, porque: str) -> None:
        """Guarda un ejemplo de por que se ha saltado una, sin repetirse."""
        if porque and len(self.porques) < 3 and porque not in self.porques:
            self.porques.append(porque)


def downsample_library(cfg: Config, log: Callable[[str], None],
                       should_stop: Callable[[], bool] | None = None
                       ) -> DownStats:
    """Reescribe los ficheros que tienen algo mal, por dos motivos:

    - **Estan por encima del techo de calidad.** Un ALAC de 24/192 ocupa cinco
      veces mas y ni siquiera puede declarar su frecuencia en la cabecera.
    - **Tienen saltos en la linea de tiempo**, de haberse normalizado sin
      reencuadrar. Ver convert.fotogramas_imposibles.

    Los dos se arreglan igual: volviendo a escribir el fichero con la cadena de
    filtros de ahora. Y se arreglan **sin perder nada**, porque estos formatos
    son sin perdida: descodificar y volver a codificar devuelve exactamente las
    mismas muestras, solo que bien encuadradas.

    Aqui no se mide el volumen, que es lo que tarda en el repaso completo: sale
    igual que estaba, para bien o para mal.
    """
    parar = should_stop or (lambda: False)
    stats = DownStats()

    ffmpeg = find_ffmpeg(str(cfg.get("ffmpeg_path", "")))
    if not ffmpeg:
        raise ConvertError(
            "No se encuentra ffmpeg. Instalalo con 'winget install Gyan.FFmpeg' "
            "o indica su ruta en la pestana Convertir a ALAC.")
    ffprobe = _buscar_ffprobe(ffmpeg)
    if not ffprobe:
        raise ConvertError("No se encuentra ffprobe, que viene con ffmpeg.")

    objetivo = str(cfg.get("quality_target", POR_DEFECTO))
    log("")
    log("== Revisar y arreglar los ficheros ==")
    if cfg.dry_run:
        log("  (simulacion: solo se dice cuales, no se toca nada)")
    log(f"  techo de calidad: {OBJETIVOS_NOMBRE.get(objetivo, objetivo)}")
    log("  y se arreglan los que tengan saltos en la linea de tiempo")
    log("  aqui no se mide el volumen: sale igual que estaba")

    def una(track: Any) -> None:
        _bajar_una(track, ffmpeg, ffprobe, cfg, objetivo, log, stats)

    recorrer_biblioteca(
        log, parar, _a_prueba_de_balas(una, log, stats),
        avisar=lambda n, t: log(f"    {n}/{t}... ({stats.bajadas} arreglados)"))

    log(f"  {stats.summary()}")
    descartadas = stats.descartadas()
    if descartadas:
        log(descartadas)
    return stats


def _bajar_una(track: Any, ffmpeg: str, ffprobe: str, cfg: Config,
               objetivo: str, log: Callable[[str], None],
               stats: DownStats) -> None:
    fichero, porque = _fichero_y_motivo(track)
    if fichero is None:
        stats.sin_fichero += 1
        stats.apuntar(porque)
        return
    audio = leer_audio(ffprobe, fichero)
    codec = str(audio.get("codec", ""))
    codec_args = SIN_PERDIDA.get(codec)
    if codec_args is None:
        # Con perdida: bajarlo no ahorra y si estropea. Pero una que ffprobe
        # no ha sabido leer no es lo mismo, y callarla seria taparla.
        if codec:
            stats.con_perdida += 1
        else:
            stats.ilegibles += 1
            stats.apuntar(f"ffprobe no ha sabido leer {fichero.name}")
        return
    stats.revisadas += 1
    frecuencia, motivo = args_calidad(int(audio.get("rate", 0)),
                                      int(audio.get("bits", 0)),
                                      objetivo, fichero,
                                      str(audio.get("codec", "")))
    motivos = []
    if motivo:
        stats.altas += 1
        motivos.append(motivo)
    saltos = fotogramas_imposibles(fichero)
    if saltos:
        stats.con_saltos += 1
        motivos.append(saltos)
    if not motivos:
        return

    antes = fichero.stat().st_size
    log(f"  ~ {fichero.name}  ({antes / (1024 * 1024):.0f} MB)")
    for razon in motivos:
        log(f"      {razon}")
    if cfg.dry_run:
        return

    error = _reescribir(ffmpeg, fichero, codec_args, None, frecuencia,
                        normalizar=False, log=lambda m: log(f"      {m}"))
    if error:
        log(f"      no se pudo: {error}")
        stats.fallidas.append((fichero.name, error))
        return
    stats.bajadas += 1
    stats.ahorrado += max(0, antes - fichero.stat().st_size)

    try:
        track.UpdateInfoFromFile()
    except Exception as exc:  # noqa: BLE001 - iTunes ocupado, fichero en uso...
        stats.sin_refrescar += 1
        log(f"      arreglada, pero iTunes no ha releido sus datos: {exc}")


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

    def una(track: Any) -> None:
        if _fichero_de(track) is None or not _parece_desfasada(track):
            return
        stats.miradas += 1
        if cfg.dry_run:
            return
        try:
            track.UpdateInfoFromFile()
            stats.refrescadas += 1
        except Exception as exc:  # noqa: BLE001 - iTunes ocupado, en uso...
            stats.fallidas.append((str(getattr(track, "Name", "?")), str(exc)))

    recorrer_biblioteca(log, parar, una, paso=500)

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


def _fichero_y_motivo(track: Any) -> tuple[Path | None, str]:
    """La ruta del fichero local, o **por que** no hay ninguna.

    El motivo importa: una pasada que dice "37 mirados" de 7047 canciones no
    se puede juzgar sin saber si las otras se han saltado porque son MP3, o
    porque iTunes apunta a un sitio donde ya no esta el fichero.
    """
    try:
        if int(track.Kind) != 1:      # 1 = fichero; lo demas es de la nube o CD
            return None, "no es un fichero local (de la nube, un CD, un stream)"
        ruta = str(track.Location or "")
    except Exception as exc:  # noqa: BLE001
        return None, f"iTunes no ha dado sus datos ({exc})"
    if not ruta:
        return None, "iTunes no le ve ninguna ruta"
    fichero = Path(ruta)
    if not fichero.is_file():
        return None, f"la ruta que apunta iTunes no existe: {ruta}"
    return fichero, ""


def _fichero_de(track: Any) -> Path | None:
    """La ruta del fichero, si la cancion es un fichero local que existe."""
    return _fichero_y_motivo(track)[0]


def _convertir_a_alac(ffmpeg: str, fichero: Path, track: Any,
                      medida: dict[str, str] | None, frecuencia: list[str],
                      log: Callable[[str], None]) -> tuple[str, Path]:
    """Pasa la cancion a ALAC y le dice a iTunes donde esta ahora.

    Aqui cambia la extension, asi que no basta con sustituir el fichero: si no
    se le reapunta, iTunes se queda buscando un .wav que ya no existe y la
    cancion aparece con la exclamacion. Por eso el original no se borra hasta
    que iTunes ha aceptado la ruta nueva.
    """
    nuevo = nombre_libre(fichero.with_suffix(".m4a"))
    with taller(nuevo.name) as temporal:
        error = _convertir(ffmpeg, fichero, temporal, ["-c:a", "alac"], medida,
                           frecuencia, log=lambda m: log(f"      {m}"))
        if error:
            return error, fichero
        fallo = _traer(temporal, nuevo)
        if fallo:
            return f"no se pudo dejar el .m4a en su sitio ({fallo})", fichero

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


def _huella(minimo: float, maximo: float, objetivo: str, con_perdida: bool,
            a_alac: bool) -> str:
    """Con que criterios se repaso. Si cambian, lo apuntado ya no vale."""
    return f"{minimo}|{maximo}|{objetivo}|{con_perdida}|{a_alac}"


def _guardar(state: StateStore, hechas: dict[str, str], huella: str) -> None:
    state.data["library_ok"] = hechas
    state.data["library_huella"] = huella
    try:
        state.save()
    except OSError:
        pass    # perder el apunte solo cuesta tiempo la proxima vez


def _reescribir(ffmpeg: str, fichero: Path, codec_args: list[str],
                medida: dict[str, str] | None, frecuencia: list[str],
                normalizar: bool = True,
                log: Callable[[str], None] | None = None) -> str:
    """Convierte a un temporal y solo entonces sustituye el original.

    Asi una cancion que falle a medias no se queda destrozada: el fichero de
    siempre no se toca hasta que hay uno nuevo entero.
    """
    # Fuera de la biblioteca: un fichero a medias ahi dentro lo abre iTunes en
    # cuanto aparece, y en el equipo de uso ademas lo sincroniza Qsync.
    with taller(fichero.name) as temporal:
        error = _convertir(ffmpeg, fichero, temporal, codec_args, medida,
                           frecuencia, normalizar, log)
        if error:
            return error

        fallo = _traer(temporal, fichero)
        if fallo:
            return f"no se pudo sustituir el fichero ({fallo})"
    return ""


def _convertir(ffmpeg: str, entrada: Path, salida: Path, codec_args: list[str],
               medida: dict[str, str] | None, frecuencia: list[str],
               normalizar: bool = True,
               log: Callable[[str], None] | None = None) -> str:
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
    apuntar = log or (lambda mensaje: None)

    detalle = "ffmpeg fallo"
    for con_caratula in (True, False):
        orden = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                 "-i", str(entrada), "-map", "0:a:0"]
        orden += args_caratula(arte) if con_caratula else ["-vn"]
        cadena = filtro_audio(medida, _rate_de(frecuencia), normalizar)
        if cadena:
            orden += ["-af", cadena]
        orden += codec_args + frecuencia
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
            # ffmpeg puede terminar tan contento y dejar un .m4a que iTunes
            # acepta pero otros programas no. Se comprueba antes de que llegue
            # a sustituir al que ya estaba bien.
            malo = comprobar_salida(salida, entrada)
            if not malo:
                # Y se le devuelven las etiquetas que ffmpeg tira por no ser
                # de su tabla -el ISRC, el sello, el codigo de barras-, que
                # "-map_metadata 0" no salva. Aqui, mientras el original sigue
                # entero: en cuanto se sustituya ya no habra de donde sacarlas.
                for aviso in completar_etiquetas(ffmpeg, entrada, salida):
                    apuntar(aviso)
                return ""
            _borrar(salida)
            return malo

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
