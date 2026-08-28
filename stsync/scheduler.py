"""Alta/baja de las tareas programadas de Windows (cada 24 h).

Se registran en el contexto del usuario actual, asi que NO hace falta ser
administrador. Con StartWhenAvailable, si el equipo estaba apagado a la hora
prevista la tarea se ejecuta en cuanto arranca.

Hay dos: la sincronizacion entre Spotify y TIDAL, y el repaso de FLAC sin
convertir. Son independientes: puedes tener una, la otra o las dos.
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .paths import project_dir


@dataclass(frozen=True)
class Task:
    name: str           # como aparece en el Programador de tareas de Windows
    argument: str       # que se le pasa a main.py
    description: str


SYNC = Task(
    "SpotifyTidalSync", "--sync",
    "Sincroniza favoritos y playlists entre Spotify y TIDAL")
FLAC = Task(
    "SpotifyTidalSync - FLAC a ALAC", "--flac2alac",
    "Convierte a ALAC los FLAC que iTunes no ha podido anadir")

TASK_NAME = SYNC.name   # se mantiene por compatibilidad


def _pythonw() -> str:
    """pythonw.exe ejecuta sin abrir ventana de consola."""
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else exe)


def _entry_script() -> str:
    return str(project_dir() / "main.py")


def _run_ps(script: str) -> subprocess.CompletedProcess[str]:
    # PowerShell responde en la codificacion de la consola (cp850 en un Windows
    # en espanol), no en UTF-8: sin decirlo, un mensaje de error con acentos
    # revienta la lectura en cuanto Python trabaja en modo UTF-8.
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, encoding="oem", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _quote(value: str) -> str:
    """En PowerShell, dentro de comillas simples la comilla se duplica."""
    return value.replace("'", "''")


def task_exists(task: Task = SYNC) -> bool:
    result = _run_ps(
        f"if (Get-ScheduledTask -TaskName '{_quote(task.name)}' "
        f"-ErrorAction SilentlyContinue) {{ 'YES' }} else {{ 'NO' }}"
    )
    return "YES" in (result.stdout or "")


def task_info(task: Task = SYNC) -> str:
    result = _run_ps(
        f"$t = Get-ScheduledTask -TaskName '{_quote(task.name)}' "
        f"-ErrorAction SilentlyContinue; "
        f"if ($t) {{ $i = $t | Get-ScheduledTaskInfo; "
        f"'Ultima: ' + $i.LastRunTime + ' | Proxima: ' + $i.NextRunTime + "
        f"' | Resultado: ' + $i.LastTaskResult }}"
    )
    return (result.stdout or "").strip()


def create_task(at_time: str = "03:00", task: Task = SYNC) -> tuple[bool, str]:
    """Crea (o reemplaza) la tarea diaria. at_time en formato HH:MM."""
    python = _quote(_pythonw())
    script_path = _quote(_entry_script())
    workdir = _quote(str(project_dir()))

    script = f"""
$ErrorActionPreference = 'Stop'
$action = New-ScheduledTaskAction -Execute '{python}' `
    -Argument '"{script_path}" {task.argument}' -WorkingDirectory '{workdir}'
$trigger = New-ScheduledTaskTrigger -Daily -At '{at_time}'
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName '{_quote(task.name)}' -Action $action `
    -Trigger $trigger -Settings $settings `
    -Description '{_quote(task.description)}' -Force | Out-Null
'CREADA'
"""
    result = _run_ps(script)
    if "CREADA" in (result.stdout or ""):
        return True, f"Tarea diaria creada a las {at_time}."
    return False, (result.stderr or result.stdout or "Error desconocido").strip()


def delete_task(task: Task = SYNC) -> tuple[bool, str]:
    result = _run_ps(
        f"$ErrorActionPreference='Stop'; "
        f"Unregister-ScheduledTask -TaskName '{_quote(task.name)}' "
        f"-Confirm:$false; 'BORRADA'"
    )
    if "BORRADA" in (result.stdout or ""):
        return True, "Tarea programada eliminada."
    return False, (result.stderr or result.stdout or "Error desconocido").strip()


def run_task_now(task: Task = SYNC) -> tuple[bool, str]:
    result = _run_ps(
        f"$ErrorActionPreference='Stop'; "
        f"Start-ScheduledTask -TaskName '{_quote(task.name)}'; 'LANZADA'"
    )
    if "LANZADA" in (result.stdout or ""):
        return True, "Tarea lanzada."
    return False, (result.stderr or result.stdout or "Error desconocido").strip()
