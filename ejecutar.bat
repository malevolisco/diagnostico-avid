@echo off
rem Lanzador del diagnostico de disco Avid (Windows).
rem Comprueba Python, instala pyavb si falta y abre la ventana del programa.
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)

%PY% --version >nul 2>nul
if not %errorlevel%==0 (
  echo No se encuentra Python. Instalalo desde https://www.python.org/downloads/
  echo marcando la casilla "Add python.exe to PATH" y vuelve a ejecutar este fichero.
  pause
  exit /b 1
)

%PY% -c "import avb, openpyxl" >nul 2>nul
if not %errorlevel%==0 (
  echo Instalando componentes (pyavb, openpyxl)...
  %PY% -m pip install --quiet --only-binary :all: pyavb openpyxl pillow
)

if not exist "bin\ffprobe.exe" (
  where ffprobe >nul 2>nul
  if not %errorlevel%==0 (
    echo AVISO: no se encuentra ffmpeg. Copia ffmpeg.exe y ffprobe.exe en la carpeta "bin".
    echo El programa funcionara, pero sin analizar los ficheros de video ni generar miniaturas.
    echo.
  )
)

%PY% diagnostico_avid.py
if not %errorlevel%==0 pause
endlocal
