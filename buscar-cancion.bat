@echo off
chcp 65001 >nul
cd /d "%~dp0"

".venv\Scripts\python.exe" -c "import requests" >nul 2>&1
if errorlevel 1 (
    echo El entorno no esta listo en este equipo.
    echo Ejecuta primero "instalar.bat".
    echo.
    pause
    exit /b 1
)

rem Ensena como ve una cancion en iTunes y en TIDAL, para saber por que no casa.
if "%~1"=="" (
    set /p "TEXTO=Trozo del titulo a buscar: "
) else (
    set "TEXTO=%~1"
)

".venv\Scripts\python.exe" "main.py" --buscar "%TEXTO%"
echo.
pause
