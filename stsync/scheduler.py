"""Alta/baja de la tarea programada de Windows (sincronizacion cada 24 h).

Se registra en el contexto del usuario actual, asi que NO hace falta ser
administrador. Con StartWhenAvailable, si el equipo estaba apagado a la hora
prevista la tarea se ejecuta en cuanto arranca.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .paths import project_dir

TASK_NAME = "SpotifyTidalSync"


def _pythonw() -> str:
    """pythonw.exe ejecuta sin abrir ventana de consola."""
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else exe)


def _entry_script() -> str:
    return str(project_dir() / "main.py")


def _run_ps(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def task_exists() -> bool:
    result = _run_ps(
        f"if (Get-ScheduledTask -TaskName '{TASK_NAME}' "
        f"-ErrorAction SilentlyContinue) {{ 'YES' }} else {{ 'NO' }}"
    )
    return "YES" in (result.stdout or "")


def task_info() -> str:
    result = _run_ps(
        f"$t = Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue; "
        f"if ($t) {{ $i = $t | Get-ScheduledTaskInfo; "
        f"'Ultima: ' + $i.LastRunTime + ' | Proxima: ' + $i.NextRunTime + "
        f"' | Resultado: ' + $i.LastTaskResult }}"
    )
    return (result.stdout or "").strip()


def create_task(at_time: str = "03:00") -> tuple[bool, str]:
    """Crea (o reemplaza) la tarea diaria. at_time en formato HH:MM."""
    python = _pythonw().replace("'", "''")
    script_path = _entry_script().replace("'", "''")
    workdir = str(project_dir()).replace("'", "''")

    script = f"""
$ErrorActionPreference = 'Stop'
$action = New-ScheduledTaskAction -Execute '{python}' `
    -Argument '"{script_path}" --sync' -WorkingDirectory '{workdir}'
$trigger = New-ScheduledTaskTrigger -Daily -At '{at_time}'
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName '{TASK_NAME}' -Action $action -Trigger $trigger `
    -Settings $settings -Description 'Sincroniza favoritos y playlists entre Spotify y TIDAL' `
    -Force | Out-Null
'CREADA'
"""
    result = _run_ps(script)
    if "CREADA" in (result.stdout or ""):
        return True, f"Tarea diaria creada a las {at_time}."
    return False, (result.stderr or result.stdout or "Error desconocido").strip()


def delete_task() -> tuple[bool, str]:
    result = _run_ps(
        f"$ErrorActionPreference='Stop'; "
        f"Unregister-ScheduledTask -TaskName '{TASK_NAME}' -Confirm:$false; 'BORRADA'"
    )
    if "BORRADA" in (result.stdout or ""):
        return True, "Tarea programada eliminada."
    return False, (result.stderr or result.stdout or "Error desconocido").strip()


def run_task_now() -> tuple[bool, str]:
    result = _run_ps(
        f"$ErrorActionPreference='Stop'; "
        f"Start-ScheduledTask -TaskName '{TASK_NAME}'; 'LANZADA'"
    )
    if "LANZADA" in (result.stdout or ""):
        return True, "Tarea lanzada."
    return False, (result.stderr or result.stdout or "Error desconocido").strip()
