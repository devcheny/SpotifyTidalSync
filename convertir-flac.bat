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

rem Convierte a ALAC los FLAC de la carpeta de auto-anadir de iTunes.
rem La carpeta y las opciones se configuran en la pestana "FLAC a ALAC".
".venv\Scripts\python.exe" "main.py" --flac2alac
echo.
pause
