#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Comprueba el resultado de una ejecución del programa sobre el disco de prueba.

Uso:  python tests/comprobar_resultado.py CARPETA_INFORME CARPETA_BRUTOS [--ffprobe RUTA] [--reanudado]
Sale con código 1 si algo no cuadra, para que el flujo de GitHub falle.
--reanudado: tras una segunda ejecución sobre la misma carpeta de brutos, todos
los proyectos deben venir marcados como reanudados (no se han vuelto a exportar).
"""

import argparse
import glob
import json
import shutil
import subprocess
import sys
from pathlib import Path


def fallo(msg):
    print("FALLO:", msg)
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("informe")
    ap.add_argument("brutos")
    ap.add_argument("--ffprobe", default=shutil.which("ffprobe"))
    ap.add_argument("--reanudado", action="store_true")
    a = ap.parse_args()

    carpetas = glob.glob(str(Path(a.informe) / "Diagnostico_*"))
    carpetas = [c for c in carpetas if Path(c).is_dir()]
    if not carpetas:
        fallo("no hay carpeta Diagnostico_* en %s" % a.informe)
    carpeta = Path(sorted(carpetas)[-1])
    for f in ["informe.html", "resumen.json", "bins.json", "mxf.json", "clips.json", "cruce.json", "inventario.csv", "diagnostico.log"]:
        if not (carpeta / f).exists():
            fallo("falta %s" % f)
    r = json.loads((carpeta / "resumen.json").read_text(encoding="utf-8"))
    # offline: CLIP OFFLINE + los 2 "APROX B" ambiguos. clips_fisicos: 9 + 2 NTSC + 3 APROX + agencia + pieza.
    esperado = {"proyectos": 4, "bins": 6, "bins_ilegibles": 1, "offline": 3, "renders_en_bins": 1, "clips_compartidos": 1, "clips_fisicos": 16}
    for k, v in esperado.items():
        if r.get(k) != v:
            fallo("resumen.%s = %r, esperado %r" % (k, r.get(k), v))
    modos = r.get("modo_coincidencia") or {}
    if not modos.get("le"):
        fallo("el cruce bin<->MXF por identificador no ha coincidido con nada: %r" % modos)
    if modos.get("nombre_duracion") != 1:
        fallo("cruce aproximado: nombre_duracion = %r, esperado 1" % modos.get("nombre_duracion"))
    cruce = json.loads((carpeta / "cruce.json").read_text(encoding="utf-8"))
    aprox = next((p for p in cruce["por_proyecto"] if p["proyecto"] == "APROX"), {})
    if (aprox.get("clips_aproximados"), aprox.get("clips_ambiguos")) != (1, 2):
        fallo("APROX: aproximados %r, ambiguos %r; esperado 1 y 2" % (aprox.get("clips_aproximados"), aprox.get("clips_ambiguos")))
    if r.get("mxf_error"):
        fallo("%d MXF con error de lectura" % r["mxf_error"])
    if not glob.glob(str(carpeta / "miniaturas" / "*.jpg")):
        fallo("no hay miniaturas")
    print("Análisis OK: %d proyectos, %d clips físicos, cruce %s" % (r["proyectos"], r["clips_fisicos"], r["modo_coincidencia"]))

    res_path = Path(a.brutos) / "brutos_resultado.json"
    if not res_path.exists():
        fallo("no hay brutos_resultado.json")
    res = json.loads(res_path.read_text(encoding="utf-8"))
    por = {b["proyecto"]: b for b in res}
    # Un bruto por tarjeta (_Tnn_fecha), y aparte agencias y piezas. 15 SEPT: el clip "2017 07 13" es del día 14
    # (otra tarjeta); el de Reuters lo dice la ruta del bin; la pieza se llama como la secuencia y el render
    # del título también va a _PIEZAS. NTSC: mismo día pero dos carpetas de tarjeta (CARD01, CARD02).
    # SIN_PROYECTO: huérfano 2 s + cuarentena 1 s + los 2 "APROX B" ambiguos de 2 s.
    esperado_brutos = {"15 SEPT CENTROAMERICA_T01_2021-09-14": ("copia", 1, 2.0), "15 SEPT CENTROAMERICA_T02_2021-09-15": ("copia", 2, 5.0),
                       "15 SEPT CENTROAMERICA_AGENCIAS": ("copia", 1, 2.0), "15 SEPT CENTROAMERICA_PIEZAS": ("copia", 2, 3.0),
                       "ABORTO_T01_2021-09-15": ("convertir", 2, 4.0), "APROX_T01_2021-09-15": ("copia", 1, 2.0),
                       "NTSC_T01_2021-09-15": ("copia", 1, 2.0), "NTSC_T02_2021-09-15": ("copia", 1, 2.0),
                       "SIN_PROYECTO_T01_2021-09-15": ("copia", 4, 7.0)}
    if sorted(por) != sorted(esperado_brutos):
        fallo("brutos %s, esperados %s" % (sorted(por), sorted(esperado_brutos)))
    for proy, (modo, incl, seg) in esperado_brutos.items():
        b = por.get(proy)
        if not b:
            fallo("falta el bruto de %s" % proy)
        if b["modo"] != modo or b["incluidos"] != incl or abs(b["segundos"] - seg) > 0.2 or not b["estado"].startswith("OK"):
            fallo("bruto %s: %s" % (proy, {k: b[k] for k in ["modo", "incluidos", "segundos", "estado"]}))
        mxf = Path(a.brutos) / b["fichero"]
        if not mxf.exists() or not (Path(a.brutos) / b["shotlist"]).exists() or not (Path(a.brutos) / b["contactos"]).exists():
            fallo("faltan ficheros del bruto %s" % proy)
        if a.ffprobe:
            out = subprocess.run([a.ffprobe, "-v", "error", "-show_entries", "stream=codec_type", "-of", "default=nw=1:nk=1", str(mxf)],
                                 capture_output=True, text=True).stdout.split()
            if out.count("video") != 1 or out.count("audio") != b["pistas_audio"]:
                fallo("bruto %s: pistas %s, esperadas 1 vídeo + %d audio" % (proy, out, b["pistas_audio"]))
            tc = subprocess.run([a.ffprobe, "-v", "error", "-show_entries", "format_tags=timecode", "-of", "default=nw=1:nk=1", str(mxf)],
                                capture_output=True, text=True).stdout.strip()
            sep = ";" if proy.startswith("NTSC") else ":"  # drop-frame solo a 29,97
            if len(tc) != 11 or tc[8] != sep:
                fallo("bruto %s: timecode %r, esperado con '%s' antes de los frames" % (proy, tc, sep))
        tcs = tc_shotlist(Path(a.brutos) / b["shotlist"])
        sep = ";" if proy.startswith("NTSC") else ":"
        if not tcs or any(len(t) != 11 or t[8] != sep for t in tcs):
            fallo("shotlist %s: timecodes %r, esperado '%s' antes de los frames" % (proy, tcs, sep))
    if a.reanudado:
        no = [b["proyecto"] for b in res if not b.get("reanudado")]
        if no:
            fallo("segunda ejecución: se han vuelto a exportar %s en vez de reanudar" % no)
    print("Brutos OK%s:" % (" (reanudados)" if a.reanudado else ""), ", ".join("%s (%s, %.1fs)" % (p, por[p]["modo"], por[p]["segundos"]) for p in esperado_brutos))


def tc_shotlist(ruta):
    """TC de inicio de los clips incluidos, del xlsx o, si no había openpyxl, del csv de respaldo."""
    if ruta.suffix == ".xlsx":
        from openpyxl import load_workbook
        filas = list(load_workbook(ruta, read_only=True).active.iter_rows(values_only=True))
        col = filas[0].index("TC inicio (bruto)")
        return [f[col] for f in filas[1:] if f[col]]
    import csv
    with open(ruta, encoding="utf-8-sig", newline="") as f:
        return [fila["tc_in"] for fila in csv.DictReader(f, delimiter=";") if fila.get("tc_in")]


if __name__ == "__main__":
    main()
