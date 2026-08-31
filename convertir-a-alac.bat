@echo off
cd /d "%~dp0"

".venv\Scripts\python.exe" -c "import requests" >nul 2>&1
if errorlevel 1 (
    echo El entorno no esta listo en este equipo.
    echo Ejecuta primero "instalar.bat".
    echo.
    pause
    exit /b 1
)

rem Convierte a ALAC lo que haya sin perdida (FLAC, WAV, AIFF...) en la
rem carpeta de auto-anadir. Se configura en la pestana "Convertir a ALAC".
".venv\Scripts\python.exe" "main.py" --flac2alac
echo.
pause
