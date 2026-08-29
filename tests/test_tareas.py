"""Comprueba que las dos tareas programadas se registran con lo que toca."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stsync import scheduler as s


def accion(task: s.Task) -> str:
    script = (
        "$t = Get-ScheduledTask -TaskName '%s'; "
        "$t.Actions | ForEach-Object { $_.Execute + ' ~ ' + $_.Arguments }"
        % task.name.replace("'", "''")
    )
    result = s._run_ps(script)
    return (result.stdout or result.stderr or "").strip()


ya_estaba = {t.name: s.task_exists(t) for t in (s.SYNC, s.FLAC)}
print("tareas que ya existian:", ya_estaba)
assert not any(ya_estaba.values()), "hay tareas de verdad: no toco nada"

for task, hora, esperado in ((s.FLAC, "04:00", "--flac2alac"),
                             (s.SYNC, "03:00", "--sync")):
    ok, mensaje = s.create_task(hora, task)
    print(f"\n=== {task.name} ===")
    print("  crear    :", ok, mensaje)
    print("  existe   :", s.task_exists(task))
    print("  info     :", s.task_info(task))
    linea = accion(task)
    print("  ejecuta  :", linea)
    assert esperado in linea, f"falta {esperado} en la accion"
    assert "main.py" in linea, linea
    assert "pythonw.exe" in linea.lower(), "deberia usar pythonw para no abrir consola"

print("\nlas dos conviven:", s.task_exists(s.SYNC), s.task_exists(s.FLAC))

for task in (s.FLAC, s.SYNC):
    print("borrar", task.name, "->", s.delete_task(task))
print("queda algo:", s.task_exists(s.SYNC), s.task_exists(s.FLAC))
assert not s.task_exists(s.SYNC) and not s.task_exists(s.FLAC)
print("\nTAREAS OK")
