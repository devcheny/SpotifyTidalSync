"""Actualizacion de la aplicacion desde las releases de GitHub.

Pensado para repartirla entre conocidos: cada uno instala Python una vez y la
app se encarga del resto. No hace falta git: se descarga el zip de la ultima
release publicada, se comprueba y se copia encima.

Lo que hay en %APPDATA% (cuentas, ajustes, informes, registros) no se toca
nunca, asi que actualizar no pierde ninguna configuracion.
"""
from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable, NamedTuple

import requests

from . import __version__
from .paths import project_dir

API = "https://api.github.com/repos"
TIMEOUT = 30

# Nunca se pisan al copiar: son del equipo, no del repositorio.
INTOCABLES = {".venv", ".git", "__pycache__"}


class UpdateError(RuntimeError):
    """No se ha podido mirar o aplicar la actualizacion."""


class Release(NamedTuple):
    version: str        # tal cual la etiqueta, por ejemplo "v1.1.0"
    zip_url: str
    notes: str

    @property
    def number(self) -> tuple[int, ...]:
        return _number(self.version)


def _number(version: str) -> tuple[int, ...]:
    """"v1.2.3" -> (1, 2, 3). Lo que no sea un numero se queda en 0."""
    limpio = version.strip().lstrip("vV").split("-")[0].split("+")[0]
    partes = []
    for trozo in limpio.split("."):
        digitos = "".join(c for c in trozo if c.isdigit())
        partes.append(int(digitos) if digitos else 0)
    return tuple(partes) or (0,)


def current_version() -> str:
    return __version__


def latest_release(repo: str) -> Release:
    """Ultima release publicada en GitHub. repo va como 'usuario/proyecto'."""
    if not repo or "/" not in repo:
        raise UpdateError("Falta el repositorio de GitHub, con la forma "
                          "usuario/proyecto.")
    try:
        resp = requests.get(f"{API}/{repo}/releases/latest",
                            headers={"Accept": "application/vnd.github+json"},
                            timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise UpdateError(f"No se ha podido consultar GitHub: {exc}") from exc

    if resp.status_code == 404:
        raise UpdateError(f"El repositorio '{repo}' no existe o todavia no "
                          "tiene ninguna release publicada.")
    if resp.status_code == 403:
        raise UpdateError("GitHub ha cortado por exceso de consultas. "
                          "Prueba dentro de un rato.")
    if resp.status_code != 200:
        raise UpdateError(f"GitHub ha respondido {resp.status_code}.")

    data: dict[str, Any] = resp.json()
    etiqueta = data.get("tag_name") or data.get("name") or ""
    zip_url = data.get("zipball_url") or ""
    if not etiqueta or not zip_url:
        raise UpdateError("La release de GitHub no trae etiqueta ni descarga.")
    return Release(str(etiqueta), str(zip_url), str(data.get("body") or ""))


def hay_novedad(release: Release, actual: str | None = None) -> bool:
    return release.number > _number(actual or current_version())


def check(repo: str) -> tuple[bool, Release]:
    """(si hay novedad, ultima release)."""
    release = latest_release(repo)
    return hay_novedad(release), release


# --------------------------------------------------------------------------
# Aplicar
# --------------------------------------------------------------------------
def apply_release(release: Release, log: Callable[[str], None],
                  destino: Path | None = None) -> None:
    """Descarga la release y la copia encima de la carpeta del programa.

    Se descarga y se comprueba entero antes de tocar nada: si la descarga se
    corta o el zip viene mal, la instalacion se queda como estaba.
    """
    carpeta = destino or project_dir()
    log(f"  descargando {release.version}...")
    try:
        resp = requests.get(release.zip_url, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise UpdateError(f"No se ha podido descargar: {exc}") from exc

    with tempfile.TemporaryDirectory() as tmp:
        temporal = Path(tmp)
        try:
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                zf.extractall(temporal)
        except zipfile.BadZipFile as exc:
            raise UpdateError(f"El fichero descargado no es un zip: {exc}") from exc

        raiz = _raiz_del_zip(temporal)
        _comprobar(raiz)
        copiados = _copiar(raiz, carpeta, log)
        log(f"  {copiados} ficheros actualizados en {carpeta}")

    _instalar_dependencias(carpeta, log)


def _raiz_del_zip(temporal: Path) -> Path:
    """GitHub mete todo dentro de una carpeta 'usuario-proyecto-sha'."""
    hijos = [p for p in temporal.iterdir() if p.is_dir()]
    if len(hijos) == 1 and not (temporal / "main.py").exists():
        return hijos[0]
    return temporal


def _comprobar(raiz: Path) -> None:
    """Que lo descargado tenga pinta de ser esta aplicacion y no otra cosa."""
    import ast
    principal = raiz / "main.py"
    if not principal.is_file() or not (raiz / "stsync").is_dir():
        raise UpdateError("Lo descargado no parece esta aplicacion: no trae "
                          "main.py y stsync.")
    try:
        ast.parse(principal.read_text(encoding="utf-8"))
    except (SyntaxError, OSError, UnicodeDecodeError) as exc:
        raise UpdateError(f"La version descargada esta danada: {exc}") from exc


def _copiar(origen: Path, destino: Path, log: Callable[[str], None]) -> int:
    copiados = 0
    for fichero in origen.rglob("*"):
        if not fichero.is_file():
            continue
        relativa = fichero.relative_to(origen)
        if INTOCABLES & set(relativa.parts):
            continue
        final = destino / relativa
        try:
            final.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(fichero, final)
            copiados += 1
        except OSError as exc:
            log(f"    no se pudo escribir {relativa}: {exc}")
    return copiados


def _instalar_dependencias(carpeta: Path, log: Callable[[str], None]) -> None:
    """La version nueva puede necesitar paquetes nuevos."""
    requisitos = carpeta / "requirements.txt"
    if not requisitos.is_file():
        return
    log("  revisando dependencias...")
    try:
        resultado = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(requisitos),
             "--quiet", "--disable-pip-version-check"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"    no se pudieron revisar las dependencias: {exc}")
        return
    if resultado.returncode != 0:
        log("    aviso: pip ha fallado; si algo no arranca, ejecuta instalar.bat")
