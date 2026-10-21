#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Comprueba el resultado de una ejecución del programa sobre el disco de prueba.

Uso:  python tests/comprobar_resultado.py CARPETA_INFORME CARPETA_BRUTOS [--ffprobe RUTA] [--reanudado] [--agrupar proyecto_mes|proyecto]
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
    ap.add_argument("--agrupar", choices=["proyecto_mes", "proyecto"], default="proyecto_mes",
                    help="proyecto_mes = config por defecto (XDCAM HD 50, sin importados) | proyecto = agrupar proyecto, formato auto, con importados")
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
    # offline: CLIP OFFLINE + los 2 "APROX B" ambiguos. clips_fisicos: 9 + 2 NTSC + 3 APROX + agencia + pieza
    # + importado + render sin bin. huerfanos: HUERFANO, SOLOAUDIO, CUARENTENA y los 2 "APROX B" (el render no cuenta).
    esperado = {"proyectos": 4, "bins": 6, "bins_ilegibles": 1, "offline": 3, "renders_en_bins": 1, "clips_compartidos": 1, "clips_fisicos": 18,
                "huerfanos": 5}
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
    if a.agrupar == "proyecto_mes":
        # Lo que pidió el Archivo (oct. 2026): por proyecto, PROYECTO_AAAA-MM, todo convertido a XDCAM HD 50 1080i25 16:9.
        # Fuera la agencia (SISMO, por la ruta del bin) y el vídeo de móvil importado. La pieza, aparte. NTSC entra:
        # sus MXF se importaron de una tarjeta (CONTENTS) aunque el origen diga "Fichero importado".
        # SIN_PROYECTO: huérfano 2 s + cuarentena 1 s + los 2 "APROX B" ambiguos de 2 s. Renders de efectos, fuera.
        esperado_brutos = {"15 SEPT CENTROAMERICA_2021-09": ("convertir", 3, 7.0), "15 SEPT CENTROAMERICA_PIEZAS_2021-09": ("convertir", 1, 2.0),
                           "ABORTO_2021-09": ("convertir", 2, 4.0), "APROX_2021-09": ("convertir", 1, 2.0),
                           "NTSC_2021-09": ("convertir", 2, 4.0), "SIN_PROYECTO_2021-09": ("convertir", 4, 7.0)}
    else:
        # Un bruto por tarjeta (_Tnn_fecha), y aparte agencias, importados y piezas. 15 SEPT: el clip "2017 07 13" es del día 10
        # (otra tarjeta); el de Reuters lo dice la ruta del bin; la pieza se llama como la secuencia. El vídeo de móvil
        # importado va a _IMPORTADOS. Los renders de efectos no entran. NTSC: mismo día pero dos carpetas de tarjeta.
        # SIN_PROYECTO: huérfano 2 s + cuarentena 1 s + los 2 "APROX B" ambiguos de 2 s.
        esperado_brutos = {"15 SEPT CENTROAMERICA_T01_2021-09-10": ("copia", 1, 2.0), "15 SEPT CENTROAMERICA_T02_2021-09-15": ("copia", 2, 5.0),
                           "15 SEPT CENTROAMERICA_AGENCIAS": ("copia", 1, 2.0), "15 SEPT CENTROAMERICA_PIEZAS": ("copia", 1, 2.0),
                           "15 SEPT CENTROAMERICA_IMPORTADOS": ("copia", 1, 2.0),
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
            sep = ";" if proy.startswith("NTSC") and a.agrupar == "proyecto" else ":"  # drop-frame solo si sale a 29,97
            if len(tc) != 11 or tc[8] != sep:
                fallo("bruto %s: timecode %r, esperado con '%s' antes de los frames" % (proy, tc, sep))
            if a.agrupar == "proyecto_mes":
                v = subprocess.run([a.ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                                    "stream=codec_name,profile,width,height,pix_fmt,field_order,r_frame_rate,display_aspect_ratio",
                                    "-of", "default=nw=1", str(mxf)], capture_output=True, text=True).stdout.split()
                v = dict(x.split("=", 1) for x in v if "=" in x)
                xdcam = {"codec_name": "mpeg2video", "profile": "4:2:2", "width": "1920", "height": "1080", "pix_fmt": "yuv422p",
                         "field_order": "tt", "r_frame_rate": "25/1", "display_aspect_ratio": "16:9"}
                if any(v.get(k) != x for k, x in xdcam.items()):
                    fallo("bruto %s: vídeo %s, esperado XDCAM HD 50 1080i25 16:9 %s" % (proy, v, xdcam))
        tcs, _, clips, fechas = tc_shotlist(Path(a.brutos) / b["shotlist"])
        if a.agrupar == "proyecto_mes":
            if fechas != sorted(fechas):
                fallo("shotlist %s: no está en orden cronológico: %s" % (proy, fechas))
            if {"SISMO 15 SEPT", "MOVIL 15 SEPT"} & set(clips):
                fallo("shotlist %s: lleva importados (%s)" % (proy, clips))
        sep = ";" if proy.startswith("NTSC") and a.agrupar == "proyecto" else ":"
        if not tcs or any(len(t) != 11 or t[8] != sep for t in tcs):
            fallo("shotlist %s: timecodes %r, esperado '%s' antes de los frames" % (proy, tcs, sep))
    if a.reanudado:
        no = [b["proyecto"] for b in res if not b.get("reanudado")]
        if no:
            fallo("segunda ejecución: se han vuelto a exportar %s en vez de reanudar" % no)
    print("Brutos OK%s:" % (" (reanudados)" if a.reanudado else ""), ", ".join("%s (%s, %.1fs)" % (p, por[p]["modo"], por[p]["segundos"]) for p in esperado_brutos))


def tc_shotlist(ruta):
    """(TC de inicio de los clips incluidos, proyecto principal, clip y fecha de cada fila en orden), del xlsx o, si
    no había openpyxl, del csv de respaldo. "15 SEPT (también en HONDURAS)" cuenta como 15 SEPT."""
    principal = lambda t: str(t or "").split(" (también en ")[0].strip()
    if ruta.suffix == ".xlsx":
        from openpyxl import load_workbook
        filas = list(load_workbook(ruta, read_only=True).active.iter_rows(values_only=True))
        col, colp, colc, colf = (filas[0].index(x) for x in ("TC inicio (bruto)", "Proyecto", "Clip", "Fecha fichero"))
        return ([f[col] for f in filas[1:] if f[col]], [principal(f[colp]) for f in filas[1:]], [f[colc] for f in filas[1:]],
                [str(f[colf] or "") for f in filas[1:]])
    import csv
    with open(ruta, encoding="utf-8-sig", newline="") as f:
        filas = list(csv.DictReader(f, delimiter=";"))
    return ([x["tc_in"] for x in filas if x.get("tc_in")], [principal(x.get("proyecto")) for x in filas], [x.get("clip") for x in filas],
            [x.get("fecha") or "" for x in filas])


if __name__ == "__main__":
    main()
