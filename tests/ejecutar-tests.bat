@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal enabledelayedexpansion

set "PY=..\.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo El entorno no esta listo. Ejecuta primero "instalar.bat".
    echo.
    pause
    exit /b 1
)

rem test_tareas.py no entra aqui: da de alta tareas en el Programador de
rem Windows. Se lanza a mano cuando se toca stsync\scheduler.py.
set "PRUEBAS=test_emparejar test_tidal test_playlists test_convertir test_buscar test_borrar test_actualizar test_completar test_publicar test_normalizar"

set /a FALLOS=0
for %%T in (%PRUEBAS%) do (
    "%PY%" -X utf8 "%%T.py" >nul 2>&1
    if errorlevel 1 (
        echo   FALLA  %%T
        set /a FALLOS+=1
    ) else (
        echo   OK     %%T
    )
)

echo.
if !FALLOS!==0 (
    echo Todo correcto.
) else (
    echo !FALLOS! pruebas con fallos. Lanza la que falle a mano para ver el detalle:
    echo    ..\.venv\Scripts\python.exe -X utf8 test_emparejar.py
)
echo.
pause
exit /b !FALLOS!
