r"""Rutas de la aplicacion (todo vive en %APPDATA%\SpotifyTidalSync)."""
from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "SpotifyTidalSync"


def app_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_file() -> Path:
    return app_dir() / "config.json"


def tokens_file() -> Path:
    return app_dir() / "tokens.dat"


def state_file() -> Path:
    return app_dir() / "state.json"


def log_file() -> Path:
    return logs_dir() / "sync.log"


def logs_dir() -> Path:
    d = app_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def reports_dir() -> Path:
    d = app_dir() / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def project_dir() -> Path:
    """Carpeta donde vive el codigo (para la tarea programada)."""
    return Path(__file__).resolve().parent.parent
