"""Portadas que dejan un .m4a fuera de norma.

Un FLAC suele traer la caratula en PNG. Al pasarlo a ALAC copiandola tal cual,
esa PNG acaba dentro del .m4a, y ahi no pinta nada: un MP4 espera la portada en
JPEG. iTunes se lo traga y hasta la ensena, pero otros programas no perdonan:
rekordbox, por ejemplo, se cierra sin decir nada al cargar la cancion.

Arreglarlo **no toca el audio**: se copia bit a bit y solo se vuelve a
codificar la imagen. Son segundos por cancion y no se pierde nada de sonido,
asi que esto no tiene nada que ver con el repaso de la biblioteca, que si mide
y recodifica.

Lo normal es lanzarlo primero en simulacion: dice cuantas hay y con que formato
viene la portada de cada una, sin tocar un solo fichero.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import Config
from .convert import (ART_JPEG, CONTENEDOR_MP4, CONVERTIBLES, NO_WINDOW,
                      OBJETIVOS_NOMBRE, POR_DEFECTO, TIMEOUT_S, ConvertError,
                      FlacConverter, args_calidad, args_caratula, caratula_de,
                      avisar_etiquetas, comprobar_salida, find_ffmpeg,
                      informe_fichero,
                      leer_audio, _buscar_ffprobe)
from .itunes import recorrer_biblioteca
from .normalize import (SIN_PERDIDA, _borrar, _fichero_de, _reescribir,
                        _sustituir)

@dataclass
class ArtStats:
    revisadas: int = 0
    sin_caratula: int = 0
    correctas: int = 0
    malas: int = 0
    arregladas: int = 0
    sin_refrescar: int = 0
    formatos: dict[str, int] = field(default_factory=dict)
    fallidas: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        texto = (f"Caratulas: {self.revisadas} ficheros mirados | "
                 f"{self.correctas} ya estaban bien | "
                 f"{self.sin_caratula} sin portada | "
                 f"{self.malas} fuera de norma ({self.arregladas} arregladas)")
        if self.fallidas:
            texto += f" | {len(self.fallidas)} con error"
        if self.sin_refrescar:
            texto += f" | {self.sin_refrescar} sin releer en iTunes"
        return texto


def check_artwork(cfg: Config, log: Callable[[str], None],
                  should_stop: Callable[[], bool] | None = None) -> ArtStats:
    """Busca .m4a con la portada en un formato que el contenedor no admite."""
    parar = should_stop or (lambda: False)
    stats = ArtStats()

    ffmpeg = find_ffmpeg(str(cfg.get("ffmpeg_path", "")))
    if not ffmpeg:
        raise ConvertError(
            "No se encuentra ffmpeg. Instalalo con 'winget install Gyan.FFmpeg' "
            "o indica su ruta en la pestana Convertir a ALAC.")
    ffprobe = _buscar_ffprobe(ffmpeg)
    if not ffprobe:
        raise ConvertError(
            "No se encuentra ffprobe, que viene con ffmpeg y hace falta para "
            "ver con que formato viene cada portada.")

    quitar = bool(cfg.get("artwork_remove", False))
    log("")
    log("== Repaso de caratulas ==")
    if cfg.dry_run:
        log("  (simulacion: solo se mira, no se reescribe nada)")
    elif quitar:
        log("  las portadas que no valgan se quitaran, no se convertiran")

    recorrer_biblioteca(
        log, parar,
        lambda track: _revisar(track, ffmpeg, ffprobe, cfg, quitar, log, stats),
        avisar=lambda n, t: log(f"    {n}/{t}... ({stats.malas} fuera de norma)"))

    log(f"  {stats.summary()}")
    if stats.formatos:
        detalle = ", ".join(f"{formato}: {cuantas}"
                            for formato, cuantas in sorted(stats.formatos.items()))
        log(f"  formatos de portada encontrados -> {detalle}")
    return stats


def _revisar(track: Any, ffmpeg: str, ffprobe: str, cfg: Config, quitar: bool,
             log: Callable[[str], None], stats: ArtStats) -> None:
    fichero = _fichero_de(track)
    if fichero is None or fichero.suffix.lower() not in CONTENEDOR_MP4:
        return

    stats.revisadas += 1
    arte = caratula_de(ffprobe, fichero)
    if not arte:
        stats.sin_caratula += 1
        return
    stats.formatos[arte] = stats.formatos.get(arte, 0) + 1
    if arte in ART_JPEG:
        stats.correctas += 1
        return

    stats.malas += 1
    log(f"  ~ {fichero.name}  (portada en {arte})")
    if cfg.dry_run:
        return

    error = _reparar(ffmpeg, fichero, arte, quitar)
    if error:
        log(f"      no se pudo: {error}")
        stats.fallidas.append((fichero.name, error))
        return
    stats.arregladas += 1

    # El fichero es otro, aunque suene igual: si iTunes no lo relee se queda
    # con el tamano de antes.
    try:
        track.UpdateInfoFromFile()
    except Exception as exc:  # noqa: BLE001 - iTunes ocupado, fichero en uso...
        stats.sin_refrescar += 1
        log(f"      arreglada, pero iTunes no ha releido sus datos: {exc}")


def fix_one_file(cfg: Config, fichero: Path,
                 log: Callable[[str], None]) -> str:
    """Arregla una sola cancion y devuelve el antes y el despues.

    Sirve para probar sobre un fichero antes de soltar nada contra las 7000, y
    hace lo que le tocaria a esa cancion segun lo que sea:

    - un FLAC, WAV, APE... se **convierte** a ALAC, como si estuviera en la
      carpeta de auto-anadir, pero sin llevarse el original por delante;
    - un .m4a se **arregla**: se le baja la calidad si pasa del techo y se le
      pasa la portada a JPEG si hace falta.

    En los dos casos devuelve el informe de como estaba y de como ha quedado.
    """
    ffmpeg = find_ffmpeg(str(cfg.get("ffmpeg_path", "")))
    if not ffmpeg:
        raise ConvertError("No se encuentra ffmpeg.")
    ffprobe = _buscar_ffprobe(ffmpeg)
    if not ffprobe:
        raise ConvertError("No se encuentra ffprobe, que viene con ffmpeg.")
    if not fichero.is_file():
        raise ConvertError(f"No existe el fichero: {fichero}")

    partes = ["ANTES", "=" * 60, informe_fichero(cfg, fichero), "",
              "=" * 60, "QUE SE LE HACE", "=" * 60]

    # Un FLAC (o un WAV, o un APE) lo que necesita es convertirse, no que le
    # toquen nada: se le hace lo mismo que si estuviera en la carpeta de
    # auto-anadir, pero solo a el y sin llevarse el original por delante.
    if fichero.suffix.lower() in CONVERTIBLES:
        return _convertir_suelto(cfg, ffmpeg, fichero, partes, log)

    audio = leer_audio(ffprobe, fichero)
    codec_args = SIN_PERDIDA.get(str(audio.get("codec", "")))
    frecuencia, motivo = args_calidad(int(audio.get("rate", 0)),
                                      int(audio.get("bits", 0)),
                                      str(cfg.get("quality_target",
                                                  POR_DEFECTO)), fichero,
                                      str(audio.get("codec", "")))
    arte = caratula_de(ffprobe, fichero)

    hecho = False
    if motivo and codec_args:
        partes.append(f"  - bajar la calidad: {motivo}")
        if not cfg.dry_run:
            error = _reescribir(ffmpeg, fichero, codec_args, None, frecuencia,
                                normalizar=False)
            if error:
                partes.append(f"    NO SE PUDO: {error}")
            else:
                hecho = True
    elif motivo:
        partes.append("  - habria que bajar la frecuencia, pero su formato no "
                      "se toca desde aqui")

    if arte and arte not in ART_JPEG:
        partes.append(f"  - pasar la portada de {arte} a JPEG")
        if not cfg.dry_run:
            error = _reparar(ffmpeg, fichero, arte,
                             bool(cfg.get("artwork_remove", False)))
            if error:
                partes.append(f"    NO SE PUDO: {error}")
            else:
                hecho = True

    if not motivo and (not arte or arte in ART_JPEG):
        partes.append("  - nada: esta cancion ya esta bien")
    elif cfg.dry_run:
        partes.append("  (simulacion: no se ha tocado nada)")

    if hecho:
        partes += ["", "=" * 60, "DESPUES", "=" * 60,
                   informe_fichero(cfg, fichero)]
    texto = "\n".join(partes)
    log(texto)
    return texto


def _convertir_suelto(cfg: Config, ffmpeg: str, fichero: Path,
                      partes: list[str], log: Callable[[str], None]) -> str:
    """Pasa a ALAC una sola cancion, sin tocar el original.

    Es el conversor de siempre con un unico fichero de entrada, para poder ver
    en que queda antes de soltarlo contra una carpeta llena.
    """
    partes.append(f"  - convertir {fichero.suffix} a ALAC, con el techo en "
                  f"{OBJETIVOS_NOMBRE.get(str(cfg.get('quality_target', POR_DEFECTO)), '?')}")
    if cfg.get("flac_normalize", True):
        partes.append("  - y normalizar el volumen por el camino")
    partes.append("  - el original se queda donde esta (aqui no se borra nada)")

    if cfg.dry_run:
        partes.append("  (simulacion: no se ha tocado nada)")
        texto = "\n".join(partes)
        log(texto)
        return texto

    conversor = FlacConverter(cfg, lambda _m: None)
    nuevo = conversor._convert_one(ffmpeg, fichero, fichero.parent,
                                   retirar=False)
    if nuevo is None:
        motivo = conversor.stats.failed[0][1] if conversor.stats.failed \
            else "no se pudo convertir"
        partes.append(f"    NO SE PUDO: {motivo}")
    else:
        partes += avisar_etiquetas(ffmpeg, fichero, nuevo)
        partes += ["", "=" * 60, f"DESPUES  ->  {nuevo.name}", "=" * 60,
                   informe_fichero(cfg, nuevo)]
    texto = "\n".join(partes)
    log(texto)
    return texto


def _reparar(ffmpeg: str, fichero: Path, arte: str, quitar: bool) -> str:
    """Reescribe el fichero copiando el audio tal cual. Devuelve "" si va bien.

    El audio va con "-c:a copy", asi que sale identico al que habia: aqui no se
    normaliza, no se recodifica y no se pierde ni un bit.
    """
    temporal = fichero.with_name(f".{fichero.stem}.caratula{fichero.suffix}")
    orden = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
             "-i", str(fichero), "-map", "0:a:0", "-c:a", "copy"]
    orden += ["-vn"] if quitar else args_caratula(arte)
    orden += ["-movflags", "+faststart", "-map_metadata", "0", str(temporal)]

    try:
        hecho = subprocess.run(orden, capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               timeout=TIMEOUT_S, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        _borrar(temporal)
        return str(exc)

    if hecho.returncode != 0 or not temporal.is_file() or not temporal.stat().st_size:
        lineas = (hecho.stderr or "").strip().splitlines()
        _borrar(temporal)
        return lineas[-1] if lineas else "ffmpeg fallo"

    # Lo mismo que en el repaso: no se machaca una cancion buena por un
    # fichero que ha salido sin audio, aunque ffmpeg diga que todo fue bien.
    malo = comprobar_salida(temporal, fichero)
    if malo:
        _borrar(temporal)
        return malo

    fallo = _sustituir(temporal, fichero)
    if fallo:
        _borrar(temporal)
        return f"no se pudo sustituir el fichero ({fallo})"
    return ""
