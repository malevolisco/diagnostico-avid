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
echo [2/5] Instalando herramientas (pyinstaller, pyavb, openpyxl)...
%PY% -m pip install --quiet --upgrade pip
%PY% -m pip install --quiet --only-binary :all: pyavb openpyxl pyinstaller
if not %errorlevel%==0 (
  echo   Fallo al instalar. Copia el texto de arriba y enviaselo a Adrian.
  pause & exit /b 1
)

echo.
echo [3/5] Descargando ffmpeg (unos 100 MB, puede tardar)...
if exist "bin\ffmpeg.exe" if exist "bin\ffprobe.exe" (
  echo   Ya hay ffmpeg en bin\, se reutiliza.
  goto :ffmpeg_listo
)
if not exist bin mkdir bin
curl -L --fail --progress-bar -o "%TEMP%\ffmpeg_win64.zip" "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
if not %errorlevel%==0 (
  echo   No se ha podido descargar ffmpeg. Descargalo a mano desde
  echo   https://github.com/BtbN/FFmpeg-Builds/releases  (ffmpeg-master-latest-win64-gpl.zip)
  echo   copia ffmpeg.exe y ffprobe.exe a la carpeta bin\ y vuelve a ejecutar este fichero.
  pause & exit /b 1
)
if exist "%TEMP%\ffmpeg_extraido" rmdir /s /q "%TEMP%\ffmpeg_extraido"
mkdir "%TEMP%\ffmpeg_extraido"
tar -xf "%TEMP%\ffmpeg_win64.zip" -C "%TEMP%\ffmpeg_extraido"
for /r "%TEMP%\ffmpeg_extraido" %%f in (ffmpeg.exe ffprobe.exe) do copy /y "%%f" bin\ >nul
del "%TEMP%\ffmpeg_win64.zip"
rmdir /s /q "%TEMP%\ffmpeg_extraido"
if not exist "bin\ffprobe.exe" (
  echo   ffmpeg descargado pero no se encontraron los .exe dentro. Avisa a Adrian.
  pause & exit /b 1
)
:ffmpeg_listo

echo.
echo [4/5] Construyendo el ejecutable (1-3 minutos)...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
%PY% -m PyInstaller --noconfirm --clean --noconsole --onedir --name DiagnosticoAvid --add-data "bin;bin" diagnostico_avid.py
if not %errorlevel%==0 (
  echo   PyInstaller ha fallado. Copia el texto de arriba y enviaselo a Adrian.
  pause & exit /b 1
)
copy /y INSTRUCCIONES.txt "dist\DiagnosticoAvid\" >nul

echo.
echo [5/5] Empaquetando DiagnosticoAvid_portable.zip ...
if exist DiagnosticoAvid_portable.zip del DiagnosticoAvid_portable.zip
powershell -NoProfile -Command "Compress-Archive -Path 'dist\DiagnosticoAvid' -DestinationPath 'DiagnosticoAvid_portable.zip' -CompressionLevel Optimal"
if exist DiagnosticoAvid_portable.zip (
  echo.
  echo   LISTO: DiagnosticoAvid_portable.zip
  echo   Pruebalo antes de enviarlo: entra en dist\DiagnosticoAvid y abre DiagnosticoAvid.exe
) else (
  echo   No se ha creado el zip. Comprime a mano la carpeta dist\DiagnosticoAvid.
)
echo.
pause
endlocal
