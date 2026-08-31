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

import json
import os
import re
import shutil
import subprocess
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
    def _convert_one(self, ffmpeg: str, source: Path, folder: Path) -> None:
        target = _free_name(folder, source.stem)
        if self.cfg.dry_run:
            self.log(f"      [simulacion] -> {target.name}")
            self.stats.converted += 1
            return

        keep_art = bool(self.cfg.get("flac_keep_artwork", True))
        medida = None
        if self.cfg.get("flac_normalize", True) and \
                self.cfg.get("flac_two_pass", True):
            medida = medir_volumen(ffmpeg, source, self.log)
        result = self._run_ffmpeg(ffmpeg, source, target, keep_art, medida)
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
        self.log(f"      -> {target.name}{_size_change(source, target)}")
        self._retire(source, folder)

    def _run_ffmpeg(self, ffmpeg: str, source: Path, target: Path,
                    keep_art: bool, medida: dict[str, str] | None = None
                    ) -> subprocess.CompletedProcess[str]:
        command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                   "-i", str(source), "-map", "0:a:0"]
        if keep_art:
            command += ["-map", "0:v:0?", "-c:v", "copy",
                        "-disposition:v", "attached_pic"]
        else:
            command += ["-vn"]
        for campo, valor in self._tags_que_faltan(ffmpeg, source).items():
            command += ["-metadata", f"{campo}={valor}"]
        command += ["-c:a", "alac"]
        if self.cfg.get("flac_cd_quality", True):
            # Un FLAC de 24 bits y 192 kHz sale a 9216 kbps y ocupa una
            # barbaridad; a 16/44,1 son los 1411 kbps de un CD.
            command += ["-ar", str(CD_RATE), "-sample_fmt", CD_FORMAT]
        if self.cfg.get("flac_normalize", True):
            command += ["-af", loudnorm_con(medida)]
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
        if faltan:
            self.log(f"      completando: {', '.join(sorted(faltan))}")
        return faltan

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
            # Con su extension de siempre: un WAV archivado como .flac no hay
            # programa que lo abra.
            source.replace(_free_name(done, source.stem, source.suffix))
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


def supera_calidad_cd(audio: dict[str, Any]) -> bool:
    """True si esta por encima de 16 bits / 44,1 kHz y se puede bajar."""
    return bool(audio) and (audio.get("rate", 0) > CD_RATE
                            or audio.get("bits", 0) > 16)


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
    return {str(k).lower(): str(v).strip()
            for k, v in etiquetas.items() if str(v).strip()}


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


def _free_name(folder: Path, stem: str, suffix: str = ".m4a") -> Path:
    """Un nombre libre en la carpeta: nunca se machaca lo que ya hay."""
    candidate = folder / f"{stem}{suffix}"
    number = 2
    while candidate.exists():
        candidate = folder / f"{stem} ({number}){suffix}"
        number += 1
    return candidate
