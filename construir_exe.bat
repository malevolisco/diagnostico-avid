@echo off
rem ============================================================
rem  CONSTRUIR EL EJECUTABLE PORTABLE  (se ejecuta UNA vez, en tu PC)
rem  Resultado: DiagnosticoAvid_portable.zip en esta misma carpeta.
rem  Quien lo reciba solo tiene que descomprimirlo y hacer doble clic
rem  en DiagnosticoAvid.exe. No necesita instalar nada.
rem ============================================================
setlocal
cd /d "%~dp0"
echo.
echo Carpeta de trabajo: %CD%
echo.
echo [1/5] Comprobando Python...
where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)
%PY% --version 2>nul
if not %errorlevel%==0 (
  echo   No se encuentra Python. Instalalo desde https://www.python.org/downloads/
  echo   marcando "Add python.exe to PATH" y vuelve a ejecutar este fichero.
  pause & exit /b 1
)

echo.
echo [2/5] Instalando herramientas (pyinstaller, pyavb, openpyxl, pillow)...
%PY% -m pip install --quiet --upgrade pip
%PY% -m pip install --quiet --only-binary :all: pyavb openpyxl pillow pyinstaller
if not %errorlevel%==0 (
  echo   Fallo al instalar. Copia el texto de arriba y enviaselo a Adrian.
  pause & exit /b 1
)

echo.
echo [3/5] ffmpeg...
if exist "bin\ffmpeg.exe" if exist "bin\ffprobe.exe" (
  echo   Ya existen bin\ffmpeg.exe y bin\ffprobe.exe, se reutilizan.
  goto :ffmpeg_listo
)
echo   Descargando ffmpeg (unos 100 MB) y copiando ffmpeg.exe y ffprobe.exe a:
echo   %CD%\bin
powershell -NoProfile -ExecutionPolicy Bypass -Command "& { $ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; $z=Join-Path $env:TEMP 'ffmpeg_win64.zip'; $x=Join-Path $env:TEMP 'ffmpeg_extraido'; Write-Host '  descargando...'; Invoke-WebRequest -Uri 'https://github.com/GyanD/codexffmpeg/releases/download/7.1.1/ffmpeg-7.1.1-essentials_build.zip' -OutFile $z; Write-Host ('  descargado: ' + [math]::Round((Get-Item $z).Length/1MB) + ' MB'); if (Test-Path $x) { Remove-Item $x -Recurse -Force }; Write-Host '  descomprimiendo...'; Expand-Archive -Path $z -DestinationPath $x -Force; New-Item -ItemType Directory -Force -Path 'bin' | Out-Null; $n=0; Get-ChildItem -Path $x -Recurse -Include ffmpeg.exe,ffprobe.exe | ForEach-Object { Copy-Item $_.FullName -Destination 'bin' -Force; Write-Host ('  copiado ' + $_.Name + ' -> bin\'); $n++ }; Remove-Item $z -Force; Remove-Item $x -Recurse -Force; if ($n -lt 2) { throw 'no se encontraron ffmpeg.exe y ffprobe.exe dentro del zip' } }"
if not %errorlevel%==0 (
  echo.
  echo   No se ha podido descargar o extraer ffmpeg. Hazlo a mano:
  echo   1. Descarga ffmpeg-7.1.1-essentials_build.zip de https://github.com/GyanD/codexffmpeg/releases/tag/7.1.1
  echo   2. Descomprimelo; dentro hay una carpeta bin con ffmpeg.exe y ffprobe.exe
  echo   3. Copia esos dos ficheros a:  %CD%\bin
  echo   4. Vuelve a ejecutar este fichero.
  pause & exit /b 1
)
:ffmpeg_listo
echo   Comprobando que ffprobe arranca...
"bin\ffprobe.exe" -version | findstr /i "ffprobe version"
if not %errorlevel%==0 (
  echo   bin\ffprobe.exe no arranca. Borra la carpeta bin y vuelve a ejecutar este fichero.
  pause & exit /b 1
)
echo   ffmpeg listo en: %CD%\bin

echo.
echo [4/5] Construyendo el ejecutable (1-3 minutos)...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
%PY% -m PyInstaller --noconfirm --clean --noconsole --onedir --name DiagnosticoAvid --add-data "bin;bin" --hidden-import interfaz --hidden-import tema --hidden-import ventana_montaje --hidden-import ventana_origen --hidden-import aprendizaje --hidden-import montaje --hidden-import mxf_util --hidden-import lecturas diagnostico_avid.py
if not %errorlevel%==0 (
  echo   PyInstaller ha fallado. Copia el texto de arriba y enviaselo a Adrian.
  pause & exit /b 1
)
copy /y INSTRUCCIONES.txt "dist\DiagnosticoAvid\" >nul
echo   Ejecutable en: %CD%\dist\DiagnosticoAvid\DiagnosticoAvid.exe
echo   (ffmpeg va dentro, en dist\DiagnosticoAvid\_internal\bin; no hace falta tocarlo)

echo.
echo [5/5] Empaquetando DiagnosticoAvid_portable.zip ...
if exist DiagnosticoAvid_portable.zip del DiagnosticoAvid_portable.zip
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path 'dist\DiagnosticoAvid' -DestinationPath 'DiagnosticoAvid_portable.zip' -CompressionLevel Optimal"
if exist DiagnosticoAvid_portable.zip (
  echo.
  echo   LISTO: %CD%\DiagnosticoAvid_portable.zip
  echo   Pruebalo antes de enviarlo: abre dist\DiagnosticoAvid\DiagnosticoAvid.exe
) else (
  echo   No se ha creado el zip. Comprime a mano la carpeta dist\DiagnosticoAvid.
)
echo.
pause
endlocal
