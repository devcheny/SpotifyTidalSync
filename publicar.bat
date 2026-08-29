@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem Sube el numero de version y lo empuja: GitHub publica la release sola.
rem   publicar.bat            arreglos      1.1.0 -> 1.1.1
rem   publicar.bat menor      cosas nuevas  1.1.0 -> 1.2.0
rem   publicar.bat mayor      cambios gordos 1.1.0 -> 2.0.0
rem   publicar.bat 1.5.2      ese numero exacto

".venv\Scripts\python.exe" "publicar.py" %*
echo.
pause
