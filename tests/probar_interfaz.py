#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prueba de la ventana (interfaz.py) de principio a fin sobre el disco de prueba, como la usaría una persona:

  python tests/probar_interfaz.py DISCO CARPETA_INFORME CARPETA_BRUTOS CARPETA_CAPTURAS

Pestañas de las páginas arriba; en cada página se comprueba que todo cabe sin desplazar de lado (y Montaje y Exportar
tampoco de arriba abajo), con la pantalla que haya: en una pequeña la ventana va en modo compacto.
1 · Disco: al elegir el disco se leen solos sus proyectos ("8 proyectos · 2 carpetas sin Avid · …"), y "Proyectos…" reutiliza esa lectura;
   Montaje sin análisis avisa en vez de fallar; analizar; la tarjeta "Último análisis" se rellena.
2 · Montaje: "Fuera del bruto" con agencia, efecto e importado. Pestaña "Origen del material": el bin "compras" sale
   arriba como dudoso; se marca "Fuera: agencia", se guarda origen.json (y se aprende en el fichero de lo aprendido) y el
   montaje se rehace con ese clip fuera.
   Visor: "Todo" deja el bruto entero en la línea de tiempo y el zoom llega a PPS_MAX px por segundo; la flecha avanza un
   fotograma y el visor enseña el fotograma exacto; "Ver y oír" abre ffplay, el cursor va con lo que suena y se para.
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
import ventana_montaje as vm  # noqa: E402

os.environ.setdefault("SDL_AUDIODRIVER", "dummy")  # ffplay ("Ver y oír") sin tarjeta de sonido en el equipo de pruebas

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # la consola de Windows (cp1252) no tiene ☑
DISCO, DESTINO, BRUTOS, CAPTURAS = sys.argv[1], sys.argv[2], sys.argv[3], Path(sys.argv[4])
CAPTURAS.mkdir(parents=True, exist_ok=True)
r = {"errores": [], "dialogos_error": [], "fallos": []}
messagebox.askyesno = lambda *a, **k: True
messagebox.askyesnocancel = lambda *a, **k: r.setdefault("preguntas", []).append(a[0]) or True
messagebox.showinfo = lambda *a, **k: r.setdefault("info", []).append(a[0])
messagebox.showwarning = lambda *a, **k: r.setdefault("avisos", []).append(a[:2])
messagebox.showerror = lambda *a, **k: r["dialogos_error"].append(a[:2])
APRENDIDO = Path(DESTINO) / "aprendizaje_prueba.json"  # no se toca el config_aprendizaje_origen.json de junto al programa
APRENDIDO.unlink(missing_ok=True)
d.ruta_aprendizaje = lambda cfg=None: APRENDIDO
d.webbrowser.open = lambda *a, **k: None  # que no abra el informe en el navegador


def comprobar(condicion, texto):
    if not condicion:
        r["fallos"].append(texto)


def captura(nombre, ventana=None):
    try:
        from PIL import ImageGrab
        w = ventana or app.raiz
        w.update()
        x, y = w.winfo_rootx(), w.winfo_rooty()
        caja = (x, y, x + w.winfo_width(), y + w.winfo_height())
        img = ImageGrab.grab(bbox=caja, xdisplay=os.environ.get("DISPLAY")) if os.name != "nt" else ImageGrab.grab(bbox=caja, all_screens=True)
        img.save(CAPTURAS / nombre)
    except Exception as e:  # la captura es para mirarla; no hace fallar la prueba
        r["errores"].append("captura %s: %s" % (nombre, e))


def cabe(pagina, tambien_alto=False):
    """Todo a la vista sin desplazar de lado (y de arriba abajo, si se pide): en una pantalla pequeña la página se compacta."""
    d = app.contenedores[pagina]
    app.raiz.update()
    d.ajustar()
    comprobar(not d.barra_h.winfo_ismapped(), "%s no cabe de ancho: pide %d px y hay %d" % (pagina, d.interior.winfo_reqwidth(), d.lienzo.winfo_width()))
    if tambien_alto:
        comprobar(not d.barra_v.winfo_ismapped(), "%s no cabe de alto: pide %d px y hay %d" % (pagina, d.interior.winfo_reqheight(), d.lienzo.winfo_height()))


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
    esperar(lambda: app.vista_disco["lista"] is not None, disco_leido, limite=60)


@seguro
def disco_leido():
    texto = app.lbl_disco.cget("text")
    comprobar(texto.startswith("8 proyectos · 2 carpetas sin Avid · 12 bins"), "al elegir el disco deben salir solos sus proyectos: %r" % texto)
    llamadas = []
    listar, dialogo = d.listar_proyectos, d.dialogo_proyectos

    def selector(raiz, lista, *a, **k):  # en vez del diálogo: apunta lo que se le ofrece y cancela
        r["selector"] = [p["nombre"] for p in lista]
        return None

    d.listar_proyectos = lambda o: llamadas.append(o) or listar(o)
    d.dialogo_proyectos = selector
    app.elegir_proyectos()
    d.listar_proyectos, d.dialogo_proyectos = listar, dialogo
    # 8 proyectos de Avid y 2 carpetas sin proyecto (RODAJE y material: antes de analizar no se sabe que son media enlazada)
    comprobar(not llamadas and len(r.get("selector") or []) == 10, "Proyectos… debe reutilizar la lectura: %s, %s" % (llamadas, r.get("selector")))
    captura("1_disco.png")
    # Paso 0: qué hay en el disco
    app.bt_que_hay.invoke()
    esperar(lambda: app.v_que_hay is not None and app.v_que_hay.filas, que_hay, limite=60)


@seguro
def que_hay():
    v = app.v_que_hay
    app.raiz.update()
    filas = {f["carpeta"]: f for f in v.filas}
    comprobar(filas.get("RODAJE", {}).get("tipo") == "mezclado" and filas.get("material", {}).get("tipo") == "MOV"
              and filas.get("ABORTO", {}).get("tipo") == "Avid", "Paso 0: %s" % {k: f["tipo"] for k, f in filas.items()})
    comprobar(len(v.tv.get_children()) == len(v.filas) and v.tv.item(v.tv.get_children()[0], "values"), "Paso 0: la tabla no se ha rellenado")
    comprobar("carpetas:" in v.lbl.cget("text"), "Paso 0: falta el resumen: %r" % v.lbl.cget("text"))
    captura("0_que_hay.png", v)
    v.destroy()
    # HECHO: en el selector de verdad se marca ABORTO como hecho (en verde y sin marcar) y se quita; luego se cancela
    app.raiz.after(800, hecho_en_selector)
    app.elegir_proyectos()


@seguro
def hecho_en_selector():
    v = next(w for w in app.raiz.winfo_children() if w.winfo_class() == "Toplevel" and w.title() == "Elegir proyectos")
    i = next(n for n, (nombre, _) in enumerate(v.filas) if nombre == "ABORTO")
    v.lb.selection_clear(0, "end")
    v.lb.selection_set(i)
    v.marcar(True)
    comprobar(v.lb.get(i).startswith("✔ HECHO") and i not in v.lb.curselection(), "ABORTO marcado HECHO: %r %s" % (v.lb.get(i), v.lb.curselection()))
    comprobar("ABORTO" in d.leer_hechos(BRUTOS), "HECHO no guardado en la carpeta de brutos: %s" % d.leer_hechos(BRUTOS))
    captura("1b_hecho.png", v)
    v.lb.selection_set(i)
    v.marcar(False)
    comprobar(not v.lb.get(i).startswith("✔") and "ABORTO" not in d.leer_hechos(BRUTOS), "Quitar HECHO: %r" % v.lb.get(i))
    v.destroy()
    app.raiz.after(100, analizar)


@seguro
def analizar():
    app.bt_analizar.invoke()
    comprobar(app.estado["ocupado"] == "analisis", "al pulsar Analizar debe quedar trabajando")
    esperar(lambda: app.estado["ocupado"] is None, montaje)


@seguro
def montaje():
    comprobar("proyectos" in app.lbl_ultimo.cget("text"), "la tarjeta Último análisis no se ha rellenado: %r" % app.lbl_ultimo.cget("text"))
    # Qué proyectos restaurar: botón activo, resumen en la tarjeta y la propuesta de cada proyecto en el selector
    comprobar(not app.bt_triaje.instate(["disabled"]) and (app.ultimo / "proyectos.html").exists(), "falta el listado de qué restaurar")
    comprobar("Para restaurar" in app.lbl_ultimo.cget("text"), "la tarjeta no dice qué restaurar: %r" % app.lbl_ultimo.cget("text"))
    tri = app.triaje_actual()
    comprobar(tri.get("NTSC", {}).get("clips") == 2 and tri["NTSC"].get("veredicto") in ("poco", "restaurar"), "triaje de NTSC: %s" % tri.get("NTSC"))
    captura("2_disco_analizado.png")
    cabe("disco")
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
    aprendido = json.loads(APRENDIDO.read_text(encoding="utf-8"))["decisiones"]
    comprobar([(a["clase"], "bin:compras" in a["rasgos"]) for a in aprendido] == [("agencia", True)],
              "lo decidido debe apuntarse para aprender: %s" % aprendido)
    comprobar(len(app.pantalla_origen.tv["columns"]) == 9, "falta la columna Aprendido")
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
    cabe("montaje", tambien_alto=True)
    # Línea de tiempo: cada clip mide al menos min_clip px (en un bruto de 2 h con cientos de clips, uno de pocos
    # segundos medía menos de un píxel) y al pinchar en el centro de cada uno se elige ese
    pm = app.montaje
    sitios, _ = pm.geometria()
    comprobar(sitios and min(x1 - x0 for x0, x1, _, _ in sitios) >= pm.min_clip, "clips más estrechos que %d px: %s" % (pm.min_clip, sitios))
    comprobar([pm.clip_en_x((x0 + x1) / 2, sitios)[0] for x0, x1, _, _ in sitios] == list(range(len(sitios))),
              "al pinchar en un clip de la línea de tiempo se elige otro")
    # Zoom: "Todo" deja el bruto entero a la vista; ampliando se llega a PPS_MAX píxeles por segundo
    pm.ajustar()
    app.raiz.update()
    _, ancho = pm.geometria()
    comprobar(ancho <= pm.linea.winfo_width() + 4, "con Todo el bruto debe caber entero: %.0f > %d" % (ancho, pm.linea.winfo_width()))
    for _ in range(40):
        pm.ampliar(2)
    app.raiz.update()
    comprobar(pm.escala()[0] >= vm.PPS_MAX * 0.75, "con el zoom máximo se debe llegar a los fotogramas: %.0f px/s" % pm.escala()[0])
    captura("3b_linea_zoom.png")
    pm.ajustar()
    # Fotograma a fotograma: la flecha avanza 1/25 s y el visor pide el fotograma exacto
    pm.t = 1.0
    pm.paso_fotograma(1)
    comprobar(abs(pm.t - 1.04) < 1e-6, "un paso de fotograma debe ser 1/25 s: %s" % pm.t)
    esperar(lambda: pm.exactos or not vm.PIL_OK, ver_y_oir, limite=30)  # sin Pillow, el visor no enseña imágenes


@seguro
def ver_y_oir():
    pm = app.montaje
    comprobar(pm.ffplay, "no se encuentra ffplay junto a ffmpeg")
    pm.t = 0.0
    pm.ver_y_oir()
    comprobar(pm.sonando is not None and pm.bt_sonido.cget("text").startswith("■"), "Ver y oír no ha arrancado")
    # a mitad del clip (los del disco de prueba duran 2 s): más tarde ffplay ya puede haber acabado, y con razón
    app.raiz.after(1000, sonando)


@seguro
def sonando():
    pm = app.montaje
    s = pm.sonando
    comprobar(s is not None and s["procesos"][1].poll() is None, "ffplay se ha cerrado antes de tiempo")
    comprobar(pm.t > 0.5, "el cursor no va con lo que suena: %.2f s" % pm.t)
    procesos = s["procesos"] if s else ()
    pm.ver_y_oir()
    comprobar(pm.sonando is None and all(x.poll() is not None for x in procesos), "Parar no ha cerrado ffplay")
    app.raiz.after(100, montar_cambios)


@seguro
def montar_cambios():
    pm = app.montaje
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
    cabe("exportar", tambien_alto=True)
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
