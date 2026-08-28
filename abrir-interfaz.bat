@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" goto sinvenv
".venv\Scripts\python.exe" -c "import requests" >nul 2>&1
if errorlevel 1 goto sinvenv

start "" ".venv\Scripts\pythonw.exe" "main.py"
exit /b 0

:sinvenv
echo El entorno no esta listo en este equipo.
echo Ejecuta primero "instalar.bat".
echo.
pause
exit /b 1
