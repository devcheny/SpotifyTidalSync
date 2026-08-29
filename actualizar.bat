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

rem Se baja la ultima version publicada en GitHub y la instala encima.
rem Tus cuentas y ajustes no se tocan: viven fuera de esta carpeta.
".venv\Scripts\python.exe" "main.py" --actualizar
echo.
pause
