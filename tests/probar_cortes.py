#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prueba del paso "Revisar cortes" sin ventana, sobre el disco de prueba ya analizado.

  python tests/probar_cortes.py preparar CARPETA_ANALISIS CARPETA_BRUTOS
      Escribe cortes.json como si en la pantalla de cortes se hubiera elegido que en 15 SEPT CENTROAMERICA
      el segundo bruto empiece en el segundo clip.
  python tests/probar_cortes.py comprobar CARPETA_BRUTOS
      Tras exportar con --usar-analisis: dos brutos de 15 SEPT (1 clip de 2 s y 2 clips de 5 s) y nada más
      de ese proyecto. Sale con 1 si no cuadra.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import exportar_brutos as eb  # noqa: E402

PROYECTO = "15 SEPT CENTROAMERICA"


def preparar(analisis, brutos):
    leer = lambda n: json.loads((Path(analisis) / n).read_text(encoding="utf-8"))
    clips, cruce, mxf = leer("clips.json"), leer("cruce.json"), leer("mxf.json")
    cfg = json.loads(json.dumps(eb.CONFIG_DEFECTO))
    cs = eb.listas_por_proyecto(clips, cruce, {m["ruta"]: m for m in mxf}, cfg)[PROYECTO]["material"]
    print("15 SEPT en orden:", [c["nombre"] for c in cs])
    eb.guardar_cortes(brutos, {PROYECTO: [cs[1]["clave"]]})


def comprobar(brutos):
    res = json.loads((Path(brutos) / "brutos_resultado.json").read_text(encoding="utf-8"))
    de15 = {b["proyecto"]: (b["incluidos"], round(b["segundos"], 1), b["estado"][:2]) for b in res if b.get("proyecto_avid") == PROYECTO
            and b.get("tipo") == "material"}
    esperado = {PROYECTO + "_2021-09_1": (1, 2.0, "OK"), PROYECTO + "_2021-09_2": (2, 5.0, "OK")}
    if de15 != esperado:
        print("FALLO: brutos de 15 SEPT %s, esperados %s" % (de15, esperado))
        sys.exit(1)
    print("Cortes OK:", de15)


if __name__ == "__main__":
    if sys.argv[1] == "preparar":
        preparar(sys.argv[2], sys.argv[3])
    else:
        comprobar(sys.argv[2])
