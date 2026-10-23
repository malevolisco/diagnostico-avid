#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prueba de la ventana (interfaz.py) de principio a fin sobre el disco de prueba, como la usaría una persona:

  python tests/probar_interfaz.py DISCO CARPETA_INFORME CARPETA_BRUTOS CARPETA_CAPTURAS

1 · Disco: Montaje sin análisis avisa en vez de fallar; analizar; la tarjeta "Último análisis" se rellena.
2 · Montaje: "Fuera del bruto" con agencia, efecto e importado. Pestaña "Origen del material": el bin "compras" sale
   arriba como dudoso; se marca "Fuera: agencia", se guarda origen.json y el montaje se rehace con ese clip fuera.
   Bajar un clip, deshacer, volver a bajar; al cambiar de página pregunta y guarda montaje.json.
3 · Exportar: piezas sin marcar; un TC no válido desactiva el botón y dice por qué; exportar; cada bruto acaba en
   "Terminado" y "Conforme · MD5".
Deja capturas de cada página en CARPETA_CAPTURAS. Los diálogos se contestan solos (sí). Sale con 1 si algo no cuadra.
En Linux necesita una pantalla (xvfb); en Windows, el escritorio normal.
"""

import json
import os
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tkinter import messagebox  # noqa: E402

import diagnostico_avid as d  # noqa: E402
import interfaz  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # la consola de Windows (cp1252) no tiene ☑
DISCO, DESTINO, BRUTOS, CAPTURAS = sys.argv[1], sys.argv[2], sys.argv[3], Path(sys.argv[4])
CAPTURAS.mkdir(parents=True, exist_ok=True)
r = {"errores": [], "dialogos_error": [], "fallos": []}
messagebox.askyesno = lambda *a, **k: True
messagebox.askyesnocancel = lambda *a, **k: r.setdefault("preguntas", []).append(a[0]) or True
messagebox.showinfo = lambda *a, **k: r.setdefault("info", []).append(a[0])
messagebox.showwarning = lambda *a, **k: r.setdefault("avisos", []).append(a[:2])
messagebox.showerror = lambda *a, **k: r["dialogos_error"].append(a[:2])
d.webbrowser.open = lambda *a, **k: None  # que no abra el informe en el navegador


def comprobar(condicion, texto):
    if not condicion:
        r["fallos"].append(texto)


def captura(nombre):
    try:
        from PIL import ImageGrab
        w = app.raiz
        w.update()
        x, y = w.winfo_rootx(), w.winfo_rooty()
        caja = (x, y, x + w.winfo_width(), y + w.winfo_height())
        img = ImageGrab.grab(bbox=caja, xdisplay=os.environ.get("DISPLAY")) if os.name != "nt" else ImageGrab.grab(bbox=caja, all_screens=True)
        img.save(CAPTURAS / nombre)
    except Exception as e:  # la captura es para mirarla; no hace fallar la prueba
        r["errores"].append("captura %s: %s" % (nombre, e))


def todos(w):
    yield w
    for h in w.winfo_children():
        yield from todos(h)


def seguro(f):
    def g(*a):
        try:
            return f(*a)
        except Exception:
            r["fallos"].append(traceback.format_exc())
            app.raiz.destroy()
    g.__name__ = f.__name__
    return g


def esperar(cond, luego, limite=900, t0=None):
    t0 = t0 or time.time()
    if cond():
        return luego()
    if time.time() - t0 > limite:
        r["fallos"].append("Tiempo agotado antes de: %s" % luego.__name__)
        return app.raiz.destroy()
    app.raiz.after(300, lambda: esperar(cond, luego, limite, t0))


@seguro
def disco():
    comprobar(not app.bt_act.winfo_manager(), "el botón de actualizar solo debe salir en el .exe de Windows")
    app.origen.set(DISCO)
    app.destino.set(DESTINO)
    app.brutos.set(BRUTOS)
    app.ir("montaje")
    app.raiz.update()
    textos = [w.cget("text") for w in todos(app.paginas["montaje"]) if w.winfo_class() == "TLabel"]
    comprobar("Primero analiza el disco" in textos, "Montaje sin análisis debe decir qué hacer: %s" % textos[:2])
    app.ir("disco")
    captura("1_disco.png")
    app.bt_analizar.invoke()
    comprobar(app.estado["ocupado"] == "analisis", "al pulsar Analizar debe quedar trabajando")
    esperar(lambda: app.estado["ocupado"] is None, montaje)


@seguro
def montaje():
    comprobar("proyectos" in app.lbl_ultimo.cget("text"), "la tarjeta Último análisis no se ha rellenado: %r" % app.lbl_ultimo.cget("text"))
    captura("2_disco_analizado.png")
    app.ir("montaje")
    pm = app.montaje
    comprobar(pm is not None, "no se ha abierto el montaje")
    p = "15 SEPT CENTROAMERICA"
    pm.cb_p.current(pm.proyectos.index(p))
    pm.elegir_proyecto(p)
    fuera = " | ".join(pm.lb_f.get(0, "end"))
    for motivo in ("agencia", "efecto de Avid", "importado"):
        comprobar(motivo in fuera, "Fuera del bruto sin '%s': %s" % (motivo, fuera))
    app.raiz.after(100, origen)


@seguro
def origen():
    app.sel_pestanas[0].elegir("origen")
    po = app.pantalla_origen
    comprobar(po is not None, "no hay pestaña Origen del material")
    filas = [po.tv.item(i, "values") for i in po.tv.get_children()]
    compras = next((n for n, v in enumerate(filas) if v[1] == "compras.avb"), None)
    comprobar(compras is not None and compras < 3 and "Dudoso" in filas[compras][5],
              "el bin compras debe salir arriba como dudoso: %s" % filas[:4])
    captura("2b_origen.png")
    po.tv.selection_set(str(compras))
    po.elegir("agencia")
    comprobar(po.cambios and filas[compras] != po.tv.item(str(compras), "values"), "al elegir debe quedar marcado y sin guardar")
    po.guardar()
    esperar(lambda: app.pantalla_origen is not None and app.pantalla_origen is not po, origen_aplicado, limite=60)


@seguro
def origen_aplicado():
    datos = json.loads((Path(BRUTOS) / "origen.json").read_text(encoding="utf-8"))
    comprobar([d["clase"] for d in datos["decisiones"] if d["bin"] == "compras.avb"] == ["agencia"], "origen.json: %s" % datos)
    comprobar(app.pestana_montaje == "origen", "tras guardar debe seguir en la pestaña de origen")
    pm = app.montaje
    pm.cb_p.current(pm.proyectos.index("ORIGEN"))
    pm.elegir_proyecto("ORIGEN")
    fuera = " | ".join(pm.lb_f.get(0, "end"))
    comprobar("VISTA AEREA" in fuera and "agencia" in fuera, "tras elegir agencia, VISTA AEREA debe salir del bruto: %s" % fuera)
    app.sel_pestanas[0].elegir("brutos")
    p = "15 SEPT CENTROAMERICA"
    pm.cb_p.current(pm.proyectos.index(p))
    pm.elegir_proyecto(p)
    esperar(lambda: all(c.get("clave") in pm.listos for c, _, _ in pm.tramos() if c.get("fichero_video")), montar, limite=180)


@seguro
def montar():
    pm = app.montaje
    pm.tv.selection_set("0")
    pm.al_elegir_clip()
    captura("3_montaje.png")
    antes = [c["nombre"] for c, _, _ in pm.tramos()]
    pm.tv.selection_set("0")
    pm.accion("bajar")
    comprobar([c["nombre"] for c, _, _ in pm.tramos()] == antes[1:2] + antes[:1] + antes[2:], "Bajar no ha movido el clip")
    pm.deshacer()
    comprobar([c["nombre"] for c, _, _ in pm.tramos()] == antes, "Deshacer no ha vuelto al orden de antes")
    pm.tv.selection_set("0")
    pm.accion("bajar")
    comprobar(pm.cambios, "tras bajar debe haber cambios sin guardar")
    captura("4_montaje_movido.png")
    app.ir("exportar")
    comprobar(r.get("preguntas") == ["Montaje sin guardar"], "al salir del montaje con cambios debe preguntar")
    comprobar((Path(BRUTOS) / "montaje.json").exists(), "no se ha guardado montaje.json")
    app.raiz.update()
    filas = {n: app.tv.item(n, "values") for n in app.tv.get_children()}
    piezas = [v for v in filas.values() if "piezas" in v[1]]
    comprobar(piezas and all(v[0] == "☐" for v in piezas), "los brutos de piezas deben venir sin marcar: %s" % piezas)
    app.aj["tc"].set("99:99")
    app.raiz.update()
    comprobar("disabled" in app.bt_exportar.state() and "TC" in app.lbl_motivo.cget("text"), "un TC no válido debe desactivar Exportar y decir por qué")
    app.aj["tc"].set("00:00:00:00")
    app.raiz.update()
    comprobar("disabled" not in app.bt_exportar.state(), "con todo bien, Exportar debe estar activo: %s" % app.lbl_motivo.cget("text"))
    captura("5_exportar.png")
    app.exportar()
    comprobar(app.montaje is not None and app.montaje.bloqueada, "mientras exporta, el montaje no se debe poder cambiar")
    app.raiz.after(3000, lambda: captura("6_exportando.png"))
    esperar(lambda: app.estado["ocupado"] is None, final)


@seguro
def final():
    app.raiz.update()
    filas = {n: app.tv.item(n, "values") for n in app.tv.get_children()}
    r["cola"] = filas
    marcadas = [v for v in filas.values() if v[0] == "☑"]
    comprobar(len(marcadas) >= 4, "deberían exportarse al menos 4 brutos: %s" % filas)
    for v in marcadas:
        comprobar(v[3] == "Terminado" and v[4] == "Conforme · MD5", "%s acaba en %s / %s" % (v[1], v[3], v[4]))
    captura("7_exportado.png")
    app.al_cerrar()


app = interfaz.App(d)
app.raiz.after(500, disco)
app.raiz.mainloop()
comprobar(not r["dialogos_error"], "ventanas de error: %s" % r["dialogos_error"])
print(json.dumps(r, ensure_ascii=False, indent=1, default=str))
if r["fallos"]:
    print("FALLO interfaz:\n  " + "\n  ".join(r["fallos"]))
    sys.exit(1)
print("Interfaz OK")
