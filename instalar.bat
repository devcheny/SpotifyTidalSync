@echo off
setlocal
cd /d "%~dp0"
echo === Instalando Spotify ^<-^> TIDAL Sync ===
echo.

rem --- Un .venv copiado de otro equipo guarda rutas absolutas y no sirve aqui ---
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import sys" >nul 2>&1
    if errorlevel 1 (
        echo El entorno virtual existente apunta a otro equipo. Recreandolo...
        rmdir /s /q ".venv"
        if exist ".venv" (
            echo.
            echo ERROR: no se pudo borrar la carpeta .venv
            echo Cierra cualquier ventana de la aplicacion y vuelve a ejecutar este archivo.
            pause
            exit /b 1
        )
        echo.
    )
)

rem --- Localizar un Python utilizable ---
set "PYLAUNCH="
where py >nul 2>&1
if not errorlevel 1 set "PYLAUNCH=py -3"
if not defined PYLAUNCH (
    where python >nul 2>&1
    if not errorlevel 1 set "PYLAUNCH=python"
)
if not defined PYLAUNCH (
    echo ERROR: no se ha encontrado Python en este equipo.
    echo.
    echo Instala Python 3.10 o superior desde https://www.python.org/downloads/
    echo IMPORTANTE: marca la casilla "Add python.exe to PATH" al instalarlo.
    pause
    exit /b 1
)

%PYLAUNCH% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo ERROR: la version de Python encontrada es demasiado antigua.
    %PYLAUNCH% --version
    echo Hace falta Python 3.10 o superior: https://www.python.org/downloads/
    pause
    exit /b 1
)

%PYLAUNCH% -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo ERROR: este Python no incluye tkinter, necesario para la interfaz.
    echo Reinstala Python desde python.org marcando "tcl/tk and IDLE".
    echo ^(Las versiones de la Microsoft Store suelen venir sin tkinter.^)
    pause
    exit /b 1
)

rem --- Crear el entorno virtual ---
if not exist ".venv\Scripts\python.exe" (
    echo Creando entorno virtual...
    %PYLAUNCH% -m venv .venv
    if errorlevel 1 (
        echo.
        echo ERROR: no se pudo crear el entorno virtual.
        pause
        exit /b 1
    )
)

rem --- Dependencias ---
echo Instalando dependencias...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo.
    echo ERROR: fallo la instalacion de dependencias.
    echo Comprueba tu conexion a internet y vuelve a intentarlo.
    pause
    exit /b 1
)

rem --- Extra opcional: hablar con iTunes (pywin32) ---
rem Va aparte de requirements.txt: si falla, el resto de la app sigue sirviendo.
echo Instalando el soporte opcional para iTunes...
".venv\Scripts\python.exe" -m pip install "pywin32>=306" --quiet
if errorlevel 1 (
    echo   AVISO: no se ha podido instalar pywin32.
    echo   Todo lo demas funciona: solo quedara sin uso la pestana iTunes.
)

rem --- Comprobacion final ---
".venv\Scripts\python.exe" -c "import requests, mutagen, tkinter" >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: la instalacion termino pero faltan modulos.
    pause
    exit /b 1
)

echo.
".venv\Scripts\python.exe" --version
echo Instalacion correcta.
echo.
echo Siguiente paso: abre "abrir-interfaz.bat" para conectar tus cuentas.
pause
