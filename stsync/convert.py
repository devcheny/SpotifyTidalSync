"""Conversion de FLAC a ALAC para la carpeta de auto-anadir de iTunes.

iTunes no sabe leer FLAC: lo que dejas en "Anadir automaticamente a iTunes"
acaba arrinconado en su subcarpeta "No anadido". Esto recorre esa carpeta
entera, convierte cada FLAC a ALAC con ffmpeg y deja el .m4a en la raiz, que
es donde iTunes si lo recoge solo.

Viene del flac2alac.bat de siempre y mantiene su normalizacion de volumen
(loudnorm I=-9). Cambia en tres cosas, todas para no perder nada: comprueba
como termino ffmpeg en vez de mirar si el fichero destino existe (iTunes se lo
lleva en cuanto aparece), no machaca un .m4a que ya estuviera ahi, y conserva
la caratula si el fichero la trae.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import Config

# Misma normalizacion que el .bat original: deja la musica bastante alta.
LOUDNORM = "loudnorm=I=-9:TP=-1.5:LRA=11"

# Carpeta donde van los originales cuando se pide no borrarlos.
DONE_DIR = "_convertidos"

# Una cancion larga con loudnorm puede tardar; aun asi no debe colgarse.
TIMEOUT_S = 1800

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ConvertError(RuntimeError):
    """No se puede convertir: falta ffmpeg o la carpeta no sirve."""


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
        return (f"FLAC a ALAC: {self.converted} convertidos | "
                f"{len(self.failed)} con error | "
                f"{self.cleaned_dirs} carpetas vacias eliminadas")


class FlacConverter:
    def __init__(self, cfg: Config, log: Callable[[str], None] | None = None,
                 should_stop: Callable[[], bool] | None = None) -> None:
        self.cfg = cfg
        self.log = log or (lambda msg: print(msg))
        self.should_stop = should_stop or (lambda: False)
        self.stats = ConvertStats()

    # ---------------------------------------------------------------- publico
    def run(self) -> ConvertStats:
        self.log("")
        self.log("== FLAC a ALAC ==")
        if self.cfg.dry_run:
            self.log("  (simulacion: no se convierte ni se borra nada)")

        ffmpeg = find_ffmpeg(str(self.cfg.get("ffmpeg_path", "")))
        if not ffmpeg:
            raise ConvertError(
                "No se encuentra ffmpeg. Instalalo con 'winget install Gyan.FFmpeg' "
                "o indica su ruta en la pestana FLAC a ALAC.")

        folder = Path(str(self.cfg.get("flac_folder", "")))
        if not folder.is_dir():
            raise ConvertError(f"La carpeta no existe: {folder}")

        sources = self._find_flacs(folder)
        if not sources:
            self.log(f"  no hay ningun FLAC en {folder}")
            return self.stats
        self.log(f"  {len(sources)} FLAC encontrados en {folder}")

        for i, source in enumerate(sources, 1):
            if self.should_stop():
                self.log("  detenido por el usuario")
                break
            self.log(f"  [{i}/{len(sources)}] {source.name}")
            self._convert_one(ffmpeg, source, folder)

        self._clean_empty_dirs(folder)
        return self.stats

    # ----------------------------------------------------------------- buscar
    def _find_flacs(self, folder: Path) -> list[Path]:
        """Todos los FLAC de la carpeta y sus subcarpetas, menos los ya hechos."""
        done = folder / DONE_DIR
        out = []
        for path in sorted(folder.rglob("*")):
            if path.suffix.lower() != ".flac" or not path.is_file():
                continue
            if done in path.parents:
                continue
            out.append(path)
        return out

    # -------------------------------------------------------------- convertir
    def _convert_one(self, ffmpeg: str, source: Path, folder: Path) -> None:
        target = _free_name(folder, source.stem)
        if self.cfg.dry_run:
            self.log(f"      [simulacion] -> {target.name}")
            self.stats.converted += 1
            return

        keep_art = bool(self.cfg.get("flac_keep_artwork", True))
        result = self._run_ffmpeg(ffmpeg, source, target, keep_art)
        if result.returncode != 0 and keep_art:
            # La causa mas comun de fallo es una caratula que no entra en el
            # .m4a, asi que antes de darlo por perdido se prueba sin ella.
            self.log("      fallo el primer intento, reintento sin la caratula")
            target.unlink(missing_ok=True)
            result = self._run_ffmpeg(ffmpeg, source, target, keep_art=False)

        if result.returncode != 0:
            detail = (result.stderr or "").strip().splitlines()
            self.stats.failed.append((source.name, detail[-1] if detail else "error"))
            self.log(f"      ERROR: {detail[-1] if detail else 'ffmpeg fallo'}")
            target.unlink(missing_ok=True)
            return

        self.stats.converted += 1
        self.log(f"      -> {target.name}")
        self._retire(source, folder)

    def _run_ffmpeg(self, ffmpeg: str, source: Path, target: Path,
                    keep_art: bool) -> subprocess.CompletedProcess[str]:
        command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                   "-i", str(source), "-map", "0:a:0"]
        if keep_art:
            command += ["-map", "0:v:0?", "-c:v", "copy",
                        "-disposition:v", "attached_pic"]
        else:
            command += ["-vn"]
        command += ["-c:a", "alac"]
        if self.cfg.get("flac_normalize", True):
            command += ["-af", LOUDNORM]
        command += ["-movflags", "+faststart", str(target)]

        try:
            return subprocess.run(command, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=TIMEOUT_S, creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(command, 1, "", "ffmpeg tardo demasiado")

    def _retire(self, source: Path, folder: Path) -> None:
        """El original ya sobra: si se queda, la proxima vez se duplicaria."""
        if self.cfg.get("flac_delete_source", True):
            try:
                source.unlink()
            except OSError as exc:
                self.log(f"      no se pudo borrar el FLAC: {exc}")
            return

        done = folder / DONE_DIR
        try:
            done.mkdir(exist_ok=True)
            source.replace(_free_name(done, source.stem, ".flac"))
        except OSError as exc:
            self.log(f"      no se pudo mover el FLAC a {DONE_DIR}: {exc}")

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


def _free_name(folder: Path, stem: str, suffix: str = ".m4a") -> Path:
    """Un nombre libre en la carpeta: nunca se machaca lo que ya hay."""
    candidate = folder / f"{stem}{suffix}"
    number = 2
    while candidate.exists():
        candidate = folder / f"{stem} ({number}){suffix}"
        number += 1
    return candidate
