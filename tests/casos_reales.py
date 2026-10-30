"""Casos reales como pruebas: las decisiones del programa sobre discos de verdad, repetidas sin el disco.

Cada caso es una carpeta tests/casos_reales/NOMBRE con:
  caso.json.gz   el análisis de un disco real, anonimizado con tests/anonimizar_caso.py (clips, cruce, mxf y lecturas.json:
                 solo texto; sin vídeo, sin miniaturas y sin nombres de proyectos, personas ni carpetas)
  esperado.json  lo que tiene que salir: qué clips entran en cada bruto, cuáles van a la papelera (trozos repetidos) y
                 cuáles se quedan fuera, y por qué. Revisado a mano la primera vez.

plan() repite lo que se decide a partir del análisis (fechas de grabación; trozos repetidos, con lo leído del disco que
guardó el análisis en lecturas.json; origen del material; reparto en brutos) con las opciones por defecto. test_casos_reales.py lo compara con
esperado.json en cada build: un arreglo para un disco no puede romper lo que ya funcionaba en otro.

  python tests/casos_reales.py                      plan de todos los casos, comparado con lo esperado
  python tests/casos_reales.py --ver NOMBRE         plan de un caso
  python tests/casos_reales.py --actualizar NOMBRE  guarda el plan de ahora como lo esperado (después de revisarlo)
"""

import argparse
import gzip
import json
import logging
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

CARPETA = Path(__file__).resolve().parent / "casos_reales"


def casos():
    return sorted(p.name for p in CARPETA.iterdir() if (p / "caso.json.gz").exists()) if CARPETA.exists() else []


def cargar(nombre):
    return json.loads(gzip.decompress((CARPETA / nombre / "caso.json.gz").read_bytes()).decode("utf-8"))


def esperado(nombre):
    ruta = CARPETA / nombre / "esperado.json"
    return json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else None


def decisiones(caso):
    """(montaje, clips, lecturas no grabadas) con las decisiones de ahora sobre el análisis del caso."""
    import copy
    import diagnostico_avid as da
    import exportar_brutos as eb
    import lecturas
    import montaje
    clips, cruce, mxf = copy.deepcopy(caso["clips"]), caso["cruce"], caso["mxf"]
    cfg = dict(json.loads(json.dumps(eb.CONFIG_DEFECTO)), **caso.get("config", {}))
    nivel = da.LOG.level
    da.LOG.setLevel(logging.ERROR)  # lo que no se pudo leer ya se cuenta en no_grabadas
    try:
        for c in clips:  # lo que se decide después de leer el disco, otra vez con el código de ahora
            for k in ("fecha_grabacion", "fecha_grabacion_fuente", "aviso_fecha"):
                c.pop(k, None)
        da.fijar_fechas_grabacion(clips, mxf)
        with lecturas.repetir(caso.get("lecturas")) as leidas:
            da.marcar_duplicados(clips, "(disco)", "ffmpeg", mxf)
        cfg["decisiones_origen"] = {}
        eb.anotar_origen(clips, cfg)
        with tempfile.TemporaryDirectory() as tmp:
            m = montaje.Montaje.desde_analisis(clips, cruce, mxf, cfg, tmp)
    finally:
        da.LOG.setLevel(nivel)
    return m, clips, leidas.no_grabadas


def todos(m):
    """Los proyectos con brutos y los que solo tienen material fuera de los brutos."""
    return sorted(set(m.proyectos()) | set(m.descartados))


def plan(caso):
    """Lo que sale de cada proyecto: {"proyectos": {proyecto: {"brutos": [{nombre, clips}], "papelera": [...],
    "fuera": ["clip: motivo"]}}, "lecturas_no_grabadas": n}."""
    import exportar_brutos as eb
    m, clips, no_grabadas = decisiones(caso)
    proyectos = {}
    for p in todos(m):
        hay = p in m.listas
        proyectos[p] = {"brutos": [{"nombre": b["nombre"], "clips": [c["nombre"] for c in b["clips"]]} for b in m.brutos(p)] if hay else [],
                        "papelera": sorted("%s: %s" % (c["nombre"], eb.texto_duplicado(c)) if c.get("contenido_en") else c["nombre"]
                                           for c in (m.papelera(p) if hay else [])),
                        "fuera": sorted("%s: %s" % (c["nombre"], motivo) for c, motivo in m.fuera_del_bruto(p))}
    return {"proyectos": proyectos, "lecturas_no_grabadas": no_grabadas}


def por_clave(caso):
    """Decisión de cada clip por su clave ("bruto N" del proyecto, "papelera", "fuera"), para comparar un caso con su
    versión anonimizada sin depender de los nombres."""
    m, _, _ = decisiones(caso)
    salida = {}
    for p in todos(m):
        for i, b in enumerate(m.brutos(p) if p in m.listas else []):
            for c in b["clips"]:
                salida.setdefault(c.get("clave"), []).append("bruto %d (%s)" % (i + 1, b["tipo"]))
        for c in m.papelera(p) if p in m.listas else []:
            salida.setdefault(c.get("clave"), []).append("papelera")
        for c, _ in m.fuera_del_bruto(p):
            salida.setdefault(c.get("clave"), []).append("fuera")
    return {k: sorted(v) for k, v in salida.items()}


def diferencias(antes, ahora):
    """Líneas legibles con lo que ha cambiado entre dos planes."""
    lineas = []
    if antes.get("lecturas_no_grabadas") != ahora.get("lecturas_no_grabadas"):
        lineas.append("lecturas no grabadas: %s -> %s" % (antes.get("lecturas_no_grabadas"), ahora.get("lecturas_no_grabadas")))
    pa, pb = antes["proyectos"], ahora["proyectos"]
    for p in sorted(set(pa) | set(pb)):
        a, b = pa.get(p), pb.get(p)
        if a is None or b is None:
            lineas.append("%s: %s" % (p, "proyecto nuevo" if a is None else "ya no sale"))
            continue
        for campo in ("papelera", "fuera"):
            for x in sorted(set(a[campo]) - set(b[campo])):
                lineas.append("%s: ya no está en %s: %s" % (p, campo, x))
            for x in sorted(set(b[campo]) - set(a[campo])):
                lineas.append("%s: ahora está en %s: %s" % (p, campo, x))
        if a["brutos"] != b["brutos"]:
            lineas.append("%s: brutos antes %s" % (p, [(x["nombre"], len(x["clips"])) for x in a["brutos"]]))
            lineas.append("%s: brutos ahora %s" % (p, [(x["nombre"], len(x["clips"])) for x in b["brutos"]]))
            ca = {c for x in a["brutos"] for c in x["clips"]}
            cb = {c for x in b["brutos"] for c in x["clips"]}
            if ca - cb:
                lineas.append("%s: ya no entran en ningún bruto: %s" % (p, sorted(ca - cb)))
            if cb - ca:
                lineas.append("%s: ahora entran: %s" % (p, sorted(cb - ca)))
    return lineas


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ver", metavar="NOMBRE")
    ap.add_argument("--actualizar", metavar="NOMBRE")
    a = ap.parse_args()
    if a.ver or a.actualizar:
        nombre = a.ver or a.actualizar
        resultado = plan(cargar(nombre))
        texto = json.dumps(resultado, ensure_ascii=False, indent=1)
        if a.actualizar:
            (CARPETA / nombre / "esperado.json").write_text(texto + "\n", encoding="utf-8")
            print("Guardado %s/esperado.json" % nombre)
        else:
            print(texto)
        return 0
    fallos = 0
    for nombre in casos():
        e = esperado(nombre)
        lineas = diferencias(e, plan(cargar(nombre))) if e else ["sin esperado.json"]
        print("%s: %s" % (nombre, "OK" if not lineas else "CAMBIA"))
        for l in lineas:
            print("   " + l)
        fallos += bool(lineas)
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
