#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Comprueba el resultado de una ejecución del programa sobre el disco de prueba.

Uso:  python tests/comprobar_resultado.py CARPETA_INFORME CARPETA_BRUTOS [--ffprobe RUTA]
Sale con código 1 si algo no cuadra, para que el flujo de GitHub falle.
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
    esperado = {"proyectos": 2, "bins": 4, "bins_ilegibles": 1, "offline": 1, "renders_en_bins": 1, "clips_compartidos": 1}
    for k, v in esperado.items():
        if r.get(k) != v:
            fallo("resumen.%s = %r, esperado %r" % (k, r.get(k), v))
    if not r.get("modo_coincidencia"):
        fallo("el cruce bin<->MXF no ha coincidido con nada")
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
    esperado_brutos = {"15 SEPT CENTROAMERICA": ("copia", 3, 7.0), "ABORTO": ("convertir", 2, 4.0), "SIN_PROYECTO": ("copia", 2, 3.0)}  # huérfano + fichero en cuarentena
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
    print("Brutos OK:", ", ".join("%s (%s, %.1fs)" % (p, por[p]["modo"], por[p]["segundos"]) for p in esperado_brutos))


if __name__ == "__main__":
    main()
