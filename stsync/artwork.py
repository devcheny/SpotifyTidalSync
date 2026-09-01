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
from .convert import (ART_JPEG, NO_WINDOW, TIMEOUT_S, ConvertError,
                      args_caratula, caratula_de, find_ffmpeg, _buscar_ffprobe)
from .itunes import ITunesLibrary, _com_items
from .normalize import _borrar, _fichero_de, _sustituir

# Solo el contenedor MP4 tiene este problema. Un FLAC o un MP3 con la portada
# en PNG estan en su derecho.
CONTENEDORES = (".m4a", ".mp4", ".m4b")


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
            if numero % 250 == 0:
                log(f"    {numero}/{total}... ({stats.malas} fuera de norma)")
            _revisar(track, ffmpeg, ffprobe, cfg, quitar, log, stats)
    finally:
        library.close()

    log(f"  {stats.summary()}")
    if stats.formatos:
        detalle = ", ".join(f"{formato}: {cuantas}"
                            for formato, cuantas in sorted(stats.formatos.items()))
        log(f"  formatos de portada encontrados -> {detalle}")
    return stats


def _revisar(track: Any, ffmpeg: str, ffprobe: str, cfg: Config, quitar: bool,
             log: Callable[[str], None], stats: ArtStats) -> None:
    fichero = _fichero_de(track)
    if fichero is None or fichero.suffix.lower() not in CONTENEDORES:
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

    fallo = _sustituir(temporal, fichero)
    if fallo:
        _borrar(temporal)
        return f"no se pudo sustituir el fichero ({fallo})"
    return ""
