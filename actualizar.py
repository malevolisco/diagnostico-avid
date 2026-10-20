# -*- coding: utf-8 -*-
"""
AUTOACTUALIZACIÓN
=================

Al abrir la ventana del .exe se mira en GitHub cuál es la última build
publicada. Si es más nueva que la que está funcionando:

1. se descarga DiagnosticoAvid_portable.zip junto a la carpeta del programa,
2. se comprueba (zip íntegro, tamaño y que dentro está DiagnosticoAvid.exe),
3. se lanza un pequeño .bat que espera a que el programa se cierre, cambia la
   carpeta vieja por la nueva conservando config_brutos.json y
   config_origen.json, y vuelve a abrir el programa.

Sin internet, con GitHub bloqueado o ante cualquier fallo, el programa sigue
con la versión que tiene. Si un cambio de carpeta falla, esa build no se
vuelve a intentar (para no descargar 150 MB en cada arranque). Registro en
%LOCALAPPDATA%\\DiagnosticoAvid\\actualizacion.log.

Solo actúa en el .exe de Windows y nunca a mitad de un análisis (se comprueba
al abrir). Para pruebas: DIAGNOSTICO_URL_ACTUALIZACION apunta a otro JSON con
el formato de la API de GitHub (sirve file:///...).
"""

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path

URL_ULTIMA = "https://api.github.com/repos/malevolisco/diagnostico-avid/releases/latest"
NOMBRE_ZIP = "DiagnosticoAvid_portable.zip"
EXE_EN_ZIP = "DiagnosticoAvid/DiagnosticoAvid.exe"


def carpeta_registro():
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "DiagnosticoAvid"
    base.mkdir(parents=True, exist_ok=True)
    return base


def registrar(texto):
    try:
        with open(carpeta_registro() / "actualizacion.log", "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), texto))
    except OSError:
        pass


def numero_build(texto):
    """'build-12 abc1234 2026-10-20' -> 12; 'local' -> None."""
    m = re.match(r"\s*build-(\d+)", str(texto or ""))
    return int(m.group(1)) if m else None


def comprobar_url(url):
    """Solo https (GitHub). file:// únicamente en pruebas, con DIAGNOSTICO_URL_ACTUALIZACION puesta."""
    if str(url).startswith("https://") or (os.environ.get("DIAGNOSTICO_URL_ACTUALIZACION") and str(url).startswith("file:")):
        return url
    raise RuntimeError("dirección no permitida para actualizar: %s" % url)


def marca_fallo(tag):
    return carpeta_registro() / ("fallo_%s.txt" % tag)


def buscar_version(build_actual, url=None, timeout=8):
    """Devuelve {tag, url, bytes} si en GitHub hay una build más nueva que build_actual; si no, None."""
    actual = numero_build(build_actual)
    if actual is None:
        return None
    url = url or os.environ.get("DIAGNOSTICO_URL_ACTUALIZACION") or URL_ULTIMA
    peticion = urllib.request.Request(comprobar_url(url), headers={"Accept": "application/vnd.github+json", "User-Agent": "DiagnosticoAvid"})
    with urllib.request.urlopen(peticion, timeout=timeout) as r:
        datos = json.loads(r.read().decode("utf-8"))
    tag = datos.get("tag_name")
    nueva = numero_build(tag)
    if nueva is None or nueva <= actual:
        return None
    asset = next((a for a in datos.get("assets", []) if a.get("name") == NOMBRE_ZIP), None)
    if not asset:
        raise RuntimeError("la release %s no tiene %s" % (tag, NOMBRE_ZIP))
    return {"tag": tag, "url": asset["browser_download_url"], "bytes": asset.get("size")}


def descargar(url, destino, esperado=None, progreso=None, timeout=60):
    peticion = urllib.request.Request(comprobar_url(url), headers={"User-Agent": "DiagnosticoAvid"})
    leidos, ultimo = 0, -1
    with urllib.request.urlopen(peticion, timeout=timeout) as r, open(destino, "wb") as f:
        total = esperado or int(r.headers.get("Content-Length") or 0)
        while True:
            bloque = r.read(1 << 20)
            if not bloque:
                break
            f.write(bloque)
            leidos += len(bloque)
            if progreso and total:
                pct = int(100 * leidos / total) // 10 * 10
                if pct != ultimo:
                    ultimo = pct
                    progreso(pct)
    if esperado and leidos != esperado:
        raise RuntimeError("descarga incompleta: %d de %d bytes" % (leidos, esperado))
    return destino


def preparar(zip_path, trabajo):
    """Comprueba el zip y lo extrae en trabajo/nuevo. Devuelve la carpeta DiagnosticoAvid extraída."""
    with zipfile.ZipFile(zip_path) as z:
        nombres = {n.replace("\\", "/") for n in z.namelist()}
        if EXE_EN_ZIP not in nombres:
            raise RuntimeError("el zip no contiene %s" % EXE_EN_ZIP)
        malo = z.testzip()
        if malo:
            raise RuntimeError("fichero dañado dentro del zip: %s" % malo)
        z.extractall(trabajo / "nuevo")
    return trabajo / "nuevo" / "DiagnosticoAvid"


# Espera a que el programa se cierre, cambia la carpeta (renombrando; si Windows no deja, copiando
# encima), recupera la configuración y vuelve a abrir. Rutas por variables de entorno para que los
# acentos de las carpetas no dependan de la página de códigos del .bat. Sin bloques ( ... ): una
# carpeta "DiagnosticoAvid (2)" cerraría el paréntesis antes de tiempo.
BAT = r"""@echo off
setlocal
set N=0
:esperar
tasklist /FI "PID eq %DA_PID%" /NH 2>nul | find " %DA_PID% " >nul
if errorlevel 1 goto libre
set /a N+=1
if %N% GEQ 120 goto fallo
ping -n 2 127.0.0.1 >nul
goto esperar
:libre
echo %date% %time% programa cerrado, cambiando de version >> "%DA_LOG%"
if exist "%DA_APP%_anterior" rmdir /s /q "%DA_APP%_anterior"
set N=0
:renombrar
move "%DA_APP%" "%DA_APP%_anterior" >nul 2>&1
if not errorlevel 1 goto mover
set /a N+=1
if %N% GEQ 5 goto copiar
ping -n 3 127.0.0.1 >nul
goto renombrar
:mover
move "%DA_NUEVA%" "%DA_APP%" >nul 2>&1
if not errorlevel 1 goto configuracion
move "%DA_APP%_anterior" "%DA_APP%" >nul 2>&1
goto fallo
:configuracion
copy /y "%DA_APP%_anterior\config_*.json" "%DA_APP%\" >nul 2>&1
rmdir /s /q "%DA_APP%_anterior"
goto hecho
:copiar
echo %date% %time% no se puede renombrar la carpeta; se copia encima >> "%DA_LOG%"
robocopy "%DA_NUEVA%" "%DA_APP%" /E /IS /IT /R:3 /W:2 /XF config_*.json /NFL /NDL /NJH /NJS >nul
if errorlevel 8 goto fallo
:hecho
echo %date% %time% actualizado a %DA_TAG% >> "%DA_LOG%"
rmdir /s /q "%DA_TRABAJO%"
if "%DA_RELANZAR%"=="1" start "" "%DA_APP%\DiagnosticoAvid.exe"
exit /b 0
:fallo
echo %date% %time% FALLO al cambiar a %DA_TAG%; se sigue con la version anterior >> "%DA_LOG%"
echo %DA_TAG% > "%DA_FALLO%"
rmdir /s /q "%DA_TRABAJO%"
if "%DA_RELANZAR%"=="1" start "" "%DA_APP%\DiagnosticoAvid.exe"
exit /b 1
"""


def lanzar_cambio(carpeta_app, carpeta_nueva, trabajo, tag, relanzar=True):
    # El .bat y su carpeta de trabajo van fuera de "trabajo", que el propio .bat borra al final
    # (si se borrase el .bat mientras corre, cmd dejaría de leerlo y no se reabriría el programa).
    bat = carpeta_registro() / "cambiar_version.bat"
    bat.write_text(BAT.replace("\n", "\r\n"), encoding="ascii")
    entorno = dict(os.environ, DA_PID=str(os.getpid()), DA_APP=str(carpeta_app), DA_NUEVA=str(carpeta_nueva),
                   DA_TRABAJO=str(trabajo), DA_TAG=tag, DA_RELANZAR="1" if relanzar else "0",
                   DA_LOG=str(carpeta_registro() / "actualizacion.log"), DA_FALLO=str(marca_fallo(tag)))
    if relanzar:  # el programa reabierto mira GitHub de verdad
        entorno.pop("DIAGNOSTICO_URL_ACTUALIZACION", None)
    subprocess.Popen(["cmd", "/c", str(bat)], env=entorno, cwd=str(carpeta_registro()), close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=0x08000000 | 0x00000200)  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP


def actualizar(build_actual, carpeta_app, progreso=lambda m: None, relanzar=True, url=None):
    """Hace todo el proceso. Devuelve 'al_dia', 'cambio_lanzado' (el programa debe cerrarse ya) o 'error: ...'."""
    carpeta_app = Path(carpeta_app)
    trabajo = carpeta_app.parent / (carpeta_app.name + "_actualizacion")
    try:
        info = buscar_version(build_actual, url)
        if not info:
            return "al_dia"
        if marca_fallo(info["tag"]).exists():
            registrar("%s ya falló antes al instalarse; no se reintenta (borrar %s para reintentar)" % (info["tag"], marca_fallo(info["tag"])))
            return "al_dia"
        registrar("hay versión nueva %s (actual: %s); descargando %s" % (info["tag"], build_actual, info["url"]))
        progreso("Hay una versión nueva (%s). Descargando..." % info["tag"])
        shutil.rmtree(trabajo, ignore_errors=True)
        trabajo.mkdir()
        zip_path = descargar(info["url"], trabajo / NOMBRE_ZIP, info.get("bytes"),
                             lambda pct: progreso("Descargando %s: %d %%" % (info["tag"], pct)))
        nueva = preparar(zip_path, trabajo)
        zip_path.unlink()
        lanzar_cambio(carpeta_app, nueva, trabajo, info["tag"], relanzar)
        registrar("descargada y comprobada %s; el programa se cierra para cambiar de versión" % info["tag"])
        progreso("Instalando %s: el programa se cerrará y se volverá a abrir solo." % info["tag"])
        return "cambio_lanzado"
    except Exception as e:
        registrar("no se ha podido actualizar: %s" % e)
        shutil.rmtree(trabajo, ignore_errors=True)
        return "error: %s" % e
