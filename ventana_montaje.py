# -*- coding: utf-8 -*-
"""
PÁGINA DE MONTAJE Y OPCIONES AVANZADAS DE EXPORTACIÓN
=====================================================

- PantallaMontaje: la página "2 · Montaje" de la ventana (interfaz.py). Visor de
  fotogramas (1 por segundo, 480x270; al parar el cursor, el fotograma exacto), "Ver y
  oír" (el clip de verdad, con sonido, en una ventana de ffplay), ficha del clip, línea
  de tiempo con zoom, regla y tira de fotogramas, lista de clips y acciones (quitar,
  mover, cortar, renombrar, deshacer), papelera, clips nuevos y "Fuera del bruto".
  Teclas: Espacio, flechas, Mayús+flechas, Inicio/Fin, J/K/L. La lógica está en
  montaje.py; aquí solo la vista.
- PanelOpciones / dialogo_opciones: "Opciones avanzadas…" de la página Exportar, con
  todas las opciones (enrutado, ganancias, prefijo, predeterminados).

Diseño en docs/diseno_interfaz.md y docs/diseno_montaje_exportacion.md. Necesita Pillow
para mostrar los JPEG del visor (sin Pillow la página funciona, pero sin imagen).
"""

import io
import queue
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import exportar_brutos as eb
import montaje as mj

try:
    from PIL import Image, ImageTk
    PIL_OK = True
except Exception:  # pragma: no cover
    PIL_OK = False

VISOR_W, VISOR_H = 480, 270
FPS_BRUTO = 25  # el bruto sale a 25 imágenes/s: un paso de fotograma es 1/25 s
PPS_MAX = 250  # zoom máximo de la línea de tiempo: píxeles por segundo (10 por fotograma)


def fmt_h(segundos):
    s = int(round(segundos or 0))
    return "%d h %02d min" % (s // 3600, s % 3600 // 60) if s >= 3600 else "%d min %02d s" % (s // 60, s % 60)


def fmt_tc(segundos, fps=25):
    f = int(round((segundos or 0) * fps))
    return "%02d:%02d:%02d:%02d" % (f // (3600 * fps), f // (60 * fps) % 60, f // fps % 60, f % fps)


def duracion_visible(c):
    """Lo que ocupa un clip en el bruto: su vídeo (un clip solo de audio no se exporta)."""
    return (c.get("duracion") or 0) if c.get("fichero_video") else 0


# ---------------------------------------------------------------------------
# Página de montaje
# ---------------------------------------------------------------------------

class PantallaMontaje:
    """Se construye dentro de contenedor (un marco de la ventana). al_cambiar() avisa de que cambió el estado de
    "cambios sin guardar" (para que la ventana pregunte antes de salir de la página)."""

    def __init__(self, contenedor, m, origen, carpeta_visor, carpeta_brutos, ffmpeg, hilos=2, proyectos=None, al_cambiar=None,
                 pestanas=None, compacto=False):
        """pestanas(cabecera): pone las pestañas de la página (Brutos | Origen del material) a la izquierda de la cabecera.
        compacto: pantalla pequeña (portátil): visor, listas y columnas más pequeños para que todo quepa."""
        import tkinter as tk
        from tkinter import ttk
        import tema
        self.tk, self.ttk, self.tema = tk, ttk, tema
        self.m, self.origen, self.carpeta_visor, self.carpeta_brutos, self.ffmpeg = m, Path(origen), Path(carpeta_visor), carpeta_brutos, ffmpeg
        self.proyectos = [p for p in m.proyectos() if proyectos is None or p in proyectos]
        self.al_cambiar = al_cambiar
        self.cambios = False
        self.bloqueada = False
        self.p, self.k, self.t = None, 0, 0.0  # proyecto, bruto, segundo del cabezal dentro del bruto
        self.zoom, self.reproduciendo, self.vivo = 1.0, False, True
        self.compacto = compacto
        self.vw, self.vh = (304, 171) if compacto else (VISOR_W, VISOR_H)  # el visor (los fotogramas se escalan)
        ancho_lista = 20 if compacto else 34
        accion = "Compacta.TButton" if compacto else "Accion.TButton"
        fantasma = "Fantasma.Compacta.TButton" if compacto else "Fantasma.TButton"
        self.listos, self.jpegs, self.fotos = {}, {}, {}  # listos: clave -> .mjpeg con un fotograma por segundo
        # Visor: primero el clip que se mira (mj.siguiente_preparacion). portadas: clave -> una imagen que sale al momento;
        # en_curso / fallidos: (tipo, clave); mirados: claves cuyo visor de otra vez ya se ha buscado en la carpeta
        self.portadas, self.en_curso, self.fallidos, self.mirados = {}, set(), set(), set()
        self.hilos = max(1, hilos)
        self.claves_tv = []
        self.cola = queue.Queue()
        self.pool = ThreadPoolExecutor(max(1, hilos)) if ffmpeg else None
        # Fotograma exacto donde se para el cursor (los de la tira son de 1 por segundo): de uno en uno, el último pedido
        self.pool_exacto = ThreadPoolExecutor(1) if ffmpeg else None
        self.exactos, self.pedido_exacto, self.espera_exacto = {}, None, None
        self.ffplay = mj.buscar_ffplay(ffmpeg)
        self.sonando = None  # {"procesos", "n" (clip), "desde" (s del bruto), "t0", "fin"}: "Ver y oír" en marcha
        self.miniaturas = {}  # (clave, segundo) -> imagen pequeña para la tira de la línea de tiempo
        self.ondas = {}  # clave -> picos del sonido (mj.sacar_onda), para la forma de onda de la línea de tiempo
        self.ondas_en_curso, self.ondas_fallidas = set(), set()
        self.rango_linea = None

        f = self.marco = ttk.Frame(contenedor, style="Fondo.TFrame")
        f.pack(fill="both", expand=True)

        cab = ttk.Frame(f, style="Cabecera.TFrame", padding=(10, 6) if compacto else (16, 8))
        cab.pack(fill="x")
        if pestanas:
            pestanas(cab)
        if not compacto:
            ttk.Label(cab, text="Proyecto", style="Tarjeta.Suave.TLabel").pack(side="left")
        self.ancho_cb_p = (22 if compacto else 26) if pestanas else 56
        self.cb_p = ttk.Combobox(cab, state="readonly", width=self.ancho_cb_p)
        self.cb_p.pack(side="left", padx=8)
        self.lbl_p = ttk.Label(cab, text="", style="Tarjeta.Suave.TLabel", wraplength=150 if compacto else 200)
        self.lbl_p.pack(side="left", padx=6)
        self.bt_guardar = ttk.Button(cab, text="Guardar", style="Elegido.TButton" if compacto else "Principal.TButton", command=self.guardar)
        self.bt_guardar.pack(side="right")
        self.lbl_guardado = ttk.Label(cab, text="", style="Tarjeta.Suave.TLabel", wraplength=220 if compacto else 0)
        self.lbl_guardado.pack(side="right", padx=10)

        cuerpo = ttk.Frame(f, style="Fondo.TFrame")
        cuerpo.pack(fill="both", expand=True)

        izq = ttk.Frame(cuerpo, style="Panel.TFrame", padding=(8, 6) if compacto else (12, 10))
        izq.pack(side="left", fill="y")
        ttk.Label(izq, text="BRUTOS", style="Panel.Seccion.TLabel").pack(anchor="w")
        self.lb_b = tema.lista(izq, width=ancho_lista, height=4 if compacto else 8)
        self.lb_b.pack(fill="both", expand=True, pady=(4, 4))
        fila = ttk.Frame(izq, style="Panel.TFrame")
        fila.pack(fill="x")
        ttk.Button(fila, text="Renombrar…", style=accion, command=self.renombrar).pack(side="left")
        ttk.Button(fila, text="Orden automático", style=fantasma, command=self.automatico).pack(side="right")
        self.lbl_q = ttk.Label(izq, text="PAPELERA", style="Panel.Seccion.TLabel")
        self.lbl_q.pack(anchor="w", pady=(12, 0))
        self.lb_q = tema.lista(izq, width=ancho_lista, height=2 if compacto else 3, selectmode="extended")
        self.lb_q.pack(fill="x", pady=(4, 4))
        ttk.Button(izq, text="Recuperar los marcados", style=accion, command=self.recuperar).pack(anchor="w")
        self.marco_n = ttk.Frame(izq, style="Panel.TFrame")
        ttk.Label(self.marco_n, text="CLIPS NUEVOS SIN COLOCAR", style="Panel.Aviso.TLabel").pack(anchor="w")
        self.lb_n = tema.lista(self.marco_n, width=ancho_lista, height=2 if compacto else 3, selectmode="extended")
        self.lb_n.pack(fill="x", pady=(4, 4))
        ttk.Button(self.marco_n, text="Colocar por fecha", style=accion, command=self.colocar).pack(anchor="w")
        self.lbl_f = ttk.Label(izq, text="FUERA DEL BRUTO", style="Panel.Seccion.TLabel")
        self.lbl_f.pack(anchor="w", pady=(12, 0))
        self.lb_f = tema.lista(izq, width=ancho_lista, height=3 if compacto else 4)
        self.lb_f.pack(fill="x", pady=(4, 0))
        ttk.Label(izq, text="Efectos de Avid, agencias e importados: no entran en ningún bruto.", style="Panel.Suave.TLabel",
                  wraplength=200 if compacto else 290).pack(anchor="w", pady=(4, 0))

        centro = ttk.Frame(cuerpo, style="Fondo.TFrame")
        centro.pack(side="left", fill="both", expand=True)
        # En una pantalla pequeña la lista de clips va a la derecha del visor y la ficha del clip no se pone (la lista ya
        # dice nombre, fecha, duración y origen): así el visor, la línea de tiempo y la lista caben sin desplazar.
        arriba = ttk.Frame(centro, style="Fondo.TFrame")
        arriba.pack(fill="both" if compacto else "x", expand=compacto)
        ficha = self.ficha_caja = ttk.Frame(arriba, style="Panel.TFrame", padding=(14, 12))
        if not compacto:
            ficha.pack(side="right", fill="y")
        self.ficha_vista = not compacto
        visor_caja = self.visor_caja = ttk.Frame(arriba, style="Fondo.TFrame", padding=(8, 6, 8, 4) if compacto else (16, 10, 16, 6))
        visor_caja.pack(side="left", fill="y" if compacto else "both", expand=not compacto)
        self.visor = tk.Canvas(visor_caja, width=self.vw, height=self.vh, bg="#0b0b0c", highlightthickness=1, highlightbackground=tema.BORDE)
        self.visor.pack()
        # Mandos y timecode: en una fila; en una ventana estrecha (o pantalla pequeña), el timecode debajo (adaptar)
        mandos = self.mandos = ttk.Frame(visor_caja, style="Fondo.TFrame")
        mandos.pack(pady=(4, 0) if compacto else (8, 0))
        ctrl = ttk.Frame(mandos, style="Fondo.TFrame")
        ctrl.grid(row=0, column=0)
        ttk.Button(ctrl, text="◀◀", width=4, command=lambda: self.mover_cursor(-10)).pack(side="left")
        self.bt_sonido = ttk.Button(ctrl, text="▶", width=4, style="Elegido.TButton", command=self.ver_y_oir)  # con sonido
        self.bt_sonido.pack(side="left", padx=(4, 0))
        if not self.ffplay:
            self.bt_sonido.state(["disabled"])
        self.bt_play = ttk.Button(ctrl, text="⏩ Avance", command=self.reproducir)
        self.bt_play.pack(side="left", padx=4)
        ttk.Button(ctrl, text="▶▶", width=4, command=lambda: self.mover_cursor(10)).pack(side="left")
        ctrl = self.ctrl_tc = ttk.Frame(mandos, style="Fondo.TFrame")
        self.tc_debajo = None
        self.poner_tc(debajo=compacto)  # en una fila no cabe con un visor pequeño
        self.lbl_tc = ttk.Label(ctrl, text="00:00:00:00", style="TC.TLabel")
        self.lbl_tc.pack(side="left", padx=(0, 8))
        self.lbl_total = ttk.Label(ctrl, text="", style="Suave.TLabel")
        self.lbl_total.pack(side="left")
        self.cb_vel = ttk.Combobox(ctrl, state="readonly", width=5, values=["x4", "x10", "x60"])
        self.cb_vel.current(1)
        self.cb_vel.pack(side="left", padx=(12, 0))
        if not compacto:
            self.lbl_teclas = ttk.Label(visor_caja, text="Espacio: reproducir · ←/→: fotograma · Mayús+←/→: 1 s · Inicio/Fin: clip · J/L: 5 s",
                                        style="Suave.TLabel", justify="center", wraplength=self.vw + 40)
            self.lbl_teclas.pack(pady=(4, 0))

        ttk.Label(ficha, text="CLIP", style="Panel.Seccion.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        self.lbl_nombre = ttk.Label(ficha, text="", style="Panel.Grande.TLabel", wraplength=200 if compacto else 270)
        self.lbl_nombre.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 8))
        self.ficha = {}
        for i, (clave, texto) in enumerate((("fecha", "Grabado"), ("tc", "TC original"), ("dur", "Duración"), ("formato", "Formato"),
                                            ("audio", "Audio"), ("origen", "Origen"), ("montaje", "Montaje")), 2):
            ttk.Label(ficha, text=texto, style="Panel.Suave.TLabel").grid(row=i, column=0, sticky="nw", pady=2)
            self.ficha[clave] = ttk.Label(ficha, text="", style="Panel.TLabel", wraplength=130 if compacto else 170)
            self.ficha[clave].grid(row=i, column=1, sticky="w", padx=(10, 0), pady=2)
        self.pref_bruto = "" if compacto else "Panel."  # el fondo de donde va el rótulo del bruto
        self.lbl_bruto = ttk.Label(visor_caja if compacto else ficha, text="", style=self.pref_bruto + "Suave.TLabel",
                                   wraplength=self.vw + 20 if compacto else 270)
        if compacto:
            self.lbl_bruto.pack(anchor="w", pady=(4, 0))
        else:
            self.lbl_bruto.grid(row=20, column=0, columnspan=2, sticky="w", pady=(12, 0))

        # Acciones y zoom: en una fila; en una ventana estrecha, el zoom debajo, a la derecha (adaptar)
        filas_acc = ttk.Frame(centro, style="Panel.TFrame", padding=(8, 4) if compacto else (12, 6))
        filas_acc.pack(fill="x")
        filas_acc.columnconfigure(1, weight=1)
        acc = ttk.Frame(filas_acc, style="Panel.TFrame")
        acc.grid(row=0, column=0, sticky="w")
        zoom = self.caja_zoom = ttk.Frame(filas_acc, style="Panel.TFrame")
        self.zoom_debajo = None
        self.poner_zoom(debajo=False)
        self.botones = []
        for texto, orden in (("Quitar", self.quitar), ("Subir", lambda: self.accion("subir")), ("Bajar", lambda: self.accion("bajar")),
                             ("← Bruto anterior" if not compacto else "← Bruto", lambda: self.accion("mover", -1)),
                             ("Bruto siguiente →" if not compacto else "Bruto →", lambda: self.accion("mover", 1)),
                             ("Cortar aquí", self.cortar), ("Deshacer", self.deshacer)):
            b = ttk.Button(acc, text=texto, style=accion, command=orden)
            b.pack(side="left", padx=(0, 4))
            self.botones.append(b)
        self.textos_bruto = {self.botones[3]: ("← Bruto anterior", "← Bruto"), self.botones[4]: ("Bruto siguiente →", "Bruto →")}
        ttk.Button(zoom, text="+", width=3, style=fantasma, command=lambda: self.ampliar(2)).pack(side="right")
        ttk.Button(zoom, text="−", width=3, style=fantasma, command=lambda: self.ampliar(0.5)).pack(side="right", padx=4)
        ttk.Button(zoom, text="Todo", width=5, style=fantasma, command=self.ajustar).pack(side="right")  # el bruto entero

        lt = ttk.Frame(centro, style="Fondo.TFrame", padding=(8, 4, 8, 0) if compacto else (12, 6, 12, 0))
        lt.pack(fill="x")
        self.alto_linea = 56 if compacto else 90
        # Cada clip mide al menos esto en la línea de tiempo: en un bruto de 2 h con cientos de clips, uno de 5 s medía
        # menos de un píxel y no se podía pinchar
        self.min_clip = 30 if compacto else 40
        self.linea = tk.Canvas(lt, height=self.alto_linea, bg=tema.CAMPO, highlightthickness=1, highlightbackground=tema.BORDE)
        self.linea.pack(fill="x")
        self.sb_linea = ttk.Scrollbar(lt, orient="horizontal", command=self.linea.xview)
        self.sb_linea.pack(fill="x")
        self.linea.configure(xscrollcommand=self.al_desplazar_linea)
        leyenda = ttk.Frame(lt, style="Fondo.TFrame")
        leyenda.pack(fill="x", pady=(4, 0))
        self.textos_leyenda = {}
        for color, largo, corto in ((tema.CLIP, "en orden cronológico", "cronológico"), (tema.ACENTO, "movido a mano", "movido")):
            tk.Frame(leyenda, bg=color, width=10, height=10).pack(side="left", padx=(0, 4))
            etiqueta = ttk.Label(leyenda, text=corto if compacto else largo, style="Suave.TLabel")
            etiqueta.pack(side="left", padx=(0, 14))
            self.textos_leyenda[etiqueta] = (largo, corto)
        # La ayuda, o el clip que hay bajo el ratón (en los cortos no cabe el nombre). Ancho fijo: si no, un nombre largo
        # ensancharía la página
        self.ayuda_linea = "Rueda: mover · Ctrl+rueda: zoom · Ctrl+clic: varios"
        if compacto:
            self.ayuda_linea = "Espacio: reproducir · ←/→: fotograma · Ctrl+rueda: zoom"
        self.lbl_encima = ttk.Label(leyenda, text=self.ayuda_linea, style="Suave.TLabel", width=38)
        self.lbl_encima.pack(side="left")
        self.lbl_foto = ttk.Label(leyenda, text="", style="Suave.TLabel")
        self.lbl_foto.pack(side="right")

        tabla = ttk.Frame(arriba if compacto else centro, style="Fondo.TFrame", padding=(4, 6, 8, 4) if compacto else (12, 4, 12, 8))
        tabla.pack(side="right" if compacto else "top", fill="both", expand=True)
        cols = ("n", "clip", "fecha", "dur", "origen", "montaje")
        self.tv = ttk.Treeview(tabla, columns=cols, show="headings", selectmode="extended", height=6 if compacto else 4)
        anchos = (28, 96, 126, 68, 80, 80) if compacto else (44, 240, 150, 90, 170, 120)
        for c, t, w in zip(cols, ("Nº", "CLIP", "FECHA Y HORA", "DURACIÓN", "ORIGEN", "MONTAJE"), anchos):
            self.tv.heading(c, text=t)
            self.tv.column(c, width=w, anchor="w", stretch=c == "clip")
        if compacto:  # "movido a mano" ya se ve en el color de la fila
            self.tv["displaycolumns"] = ("n", "clip", "fecha", "dur", "origen")
        self.tv.tag_configure("movido", foreground=tema.ACENTO)
        self.tv.tag_configure("audio", foreground=tema.SUAVE)
        self.tv.tag_configure("sinaudio", foreground=tema.ACENTO)  # clip con vídeo y sin ninguna pista de sonido
        sb = ttk.Scrollbar(tabla, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=sb.set)
        self.tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.cb_p.bind("<<ComboboxSelected>>", lambda e: self.elegir_proyecto(self.proyectos[self.cb_p.current()]))
        self.lb_b.bind("<<ListboxSelect>>", self.al_elegir_bruto)
        self.tv.bind("<<TreeviewSelect>>", self.al_elegir_clip)
        self.linea.bind("<Button-1>", self.al_pulsar_linea)
        self.linea.bind("<Control-Button-1>", lambda e: self.al_pulsar_linea(e, sumar=True))
        self.linea.bind("<B1-Motion>", self.al_arrastrar_linea)
        self.linea.bind("<Configure>", lambda e: self.pintar_linea())
        self.linea.bind("<Motion>", self.al_pasar_linea)
        self.linea.bind("<Leave>", lambda e: self.lbl_encima.configure(text=self.ayuda_linea))
        # rueda: la línea de lado; Ctrl + rueda: zoom (en Linux, botones 4 y 5)
        self.linea.bind("<MouseWheel>", lambda e: self.rueda_linea(-1 if e.delta > 0 else 1))
        self.linea.bind("<Control-MouseWheel>", lambda e: self.rueda_linea(-1 if e.delta > 0 else 1, zoom=True, x=e.x))
        self.linea.bind("<Button-4>", lambda e: self.rueda_linea(-1))
        self.linea.bind("<Button-5>", lambda e: self.rueda_linea(1))
        self.linea.bind("<Control-Button-4>", lambda e: self.rueda_linea(-1, zoom=True, x=e.x))
        self.linea.bind("<Control-Button-5>", lambda e: self.rueda_linea(1, zoom=True, x=e.x))
        top = f.winfo_toplevel()
        self.atajos = [("<Control-z>", lambda e: self.deshacer()), ("<Control-s>", lambda e: self.guardar() if self.activa() else None),
                       ("<Left>", self.tecla(lambda: self.paso_fotograma(-1))), ("<Right>", self.tecla(lambda: self.paso_fotograma(1))),
                       ("<Shift-Left>", self.tecla(lambda: self.mover_cursor(-1))), ("<Shift-Right>", self.tecla(lambda: self.mover_cursor(1))),
                       ("<Home>", self.tecla(lambda: self.ir_al_clip(0))), ("<End>", self.tecla(lambda: self.ir_al_clip(0, final=True))),
                       ("<Up>", self.tecla(lambda: self.ir_al_clip(-1), lista=False)),
                       ("<Down>", self.tecla(lambda: self.ir_al_clip(1), lista=False)),
                       ("<space>", self.tecla(self.ver_y_oir)), ("<Key-k>", self.tecla(self.parar_sonido)),
                       ("<Key-j>", self.tecla(lambda: self.mover_cursor(-5))), ("<Key-l>", self.tecla(lambda: self.mover_cursor(5)))]
        for tecla, accion in self.atajos:
            top.bind(tecla, accion, add="+")
        if not PIL_OK:
            self.lbl_foto.configure(text="Falta Pillow: el visor no puede mostrar imágenes.")
        elif not ffmpeg:
            self.lbl_foto.configure(text="Sin ffmpeg: no se pueden sacar los fotogramas del visor.")
        if m.avisos:
            self.lbl_guardado.configure(text="Avisos: " + " · ".join(m.avisos), style="Tarjeta.Aviso.TLabel")
        self.pintar_proyectos()
        if self.proyectos:
            self.cb_p.current(0)
            self.elegir_proyecto(self.proyectos[0])
        f.after(150, self.vaciar_cola)

    # --- datos -------------------------------------------------------------------------------------------------

    def bruto(self):
        bs = self.m.brutos(self.p) if self.p else []
        return bs[min(self.k, len(bs) - 1)] if bs else None

    def tramos(self):
        """[(clip, inicio, duración)] del bruto actual en segundos."""
        b, t, salida = self.bruto(), 0.0, []
        for c in (b["clips"] if b else []):
            d = duracion_visible(c)
            salida.append((c, t, d))
            t += d
        return salida

    def seleccion(self):
        """Claves de los clips marcados en la lista, en orden."""
        return [self.claves_tv[int(i)] for i in self.tv.selection() if int(i) < len(self.claves_tv)]

    def activa(self):
        """Si la página está a la vista y se puede tocar (para los atajos de teclado de la ventana)."""
        return self.vivo and not self.bloqueada and self.marco.winfo_ismapped()

    # --- pintar ------------------------------------------------------------------------------------------------

    def texto_proyecto(self, p):
        bs = self.m.brutos(p)
        largo = any(b["largo"] for b in bs)
        nuevos = len(self.m.nuevos(p))
        return "%s%s%s — %d brutos%s" % ("⚠ " if largo else "", "✎ " if self.m.tocado(p) else "", p, len(bs),
                                         " · %d nuevos sin colocar" % nuevos if nuevos else "")

    def pintar_proyectos(self):
        self.cb_p.configure(values=[self.texto_proyecto(p) for p in self.proyectos])
        if self.p in self.proyectos:
            self.cb_p.current(self.proyectos.index(self.p))

    def pintar_brutos(self):
        tema = self.tema
        self.lb_b.delete(0, "end")
        for b in self.m.brutos(self.p):
            self.lb_b.insert("end", "%s%s   %s · %d clips%s" % ("✎ " if b["manual"] else "", b["nombre"], fmt_h(b["segundos"]), len(b["clips"]),
                                                                "  ·  PASA DE 2 H" if b["largo"] else ""))
            if b["largo"]:
                self.lb_b.itemconfig("end", foreground=tema.AVISO)
        if self.lb_b.size():
            self.k = min(self.k, self.lb_b.size() - 1)
            self.lb_b.selection_clear(0, "end")
            self.lb_b.selection_set(self.k)
            self.lb_b.see(self.k)
        papelera = self.m.papelera(self.p)
        self.lbl_q.configure(text="PAPELERA · %d" % len(papelera))
        self.lb_q.delete(0, "end")
        for c in papelera:  # un trozo repetido dice de qué clip es parte
            self.lb_q.insert("end", c["nombre"] + ("  · " + eb.texto_duplicado(c) if c.get("contenido_en") else ""))
        nuevos = self.m.nuevos(self.p)
        self.lb_n.delete(0, "end")
        for c in nuevos:
            self.lb_n.insert("end", c["nombre"])
        if nuevos:
            self.marco_n.pack(fill="x", pady=(12, 0), before=self.lbl_f)
        else:
            self.marco_n.pack_forget()
        fuera = self.m.fuera_del_bruto(self.p)
        self.lbl_f.configure(text="FUERA DEL BRUTO · %d" % len(fuera))
        self.lb_f.delete(0, "end")
        for c, motivo in fuera:
            self.lb_f.insert("end", "%s — %s" % (c["nombre"], motivo))
        movidos = sum(1 for b in self.m.brutos(self.p) for c in b["clips"] if c.get("clave") in self.m.movidos(self.p))
        self.lbl_p.configure(text="%s%s" % ("Montaje a mano" if self.m.tocado(self.p) else "Reparto automático",
                                            " · %d clips movidos" % movidos if movidos else ""))

    def pintar_tabla(self, mantener=()):
        self.tv.delete(*self.tv.get_children())
        self.claves_tv = []
        movidos = self.m.movidos(self.p)
        for n, (c, _, d) in enumerate(self.tramos()):
            f = self.m.fecha(c)
            sin_audio = bool(c.get("fichero_video")) and not c.get("num_audio")
            tags = (("movido",) if c.get("clave") in movidos else ("audio",) if not c.get("fichero_video") else
                    ("sinaudio",) if sin_audio else ())
            self.tv.insert("", "end", iid=str(n), tags=tags, values=(
                n + 1, c["nombre"] + ("" if c.get("fichero_video") else "  (solo audio: no se exporta)") + ("  (SIN AUDIO)" if sin_audio else ""),
                f.strftime("%d/%m/%Y %H:%M:%S") if f else "sin fecha", fmt_h(d), c.get("origen_texto") or c.get("origen") or "",
                "movido a mano" if c.get("clave") in movidos else "automático"))
            self.claves_tv.append(c.get("clave"))
        sel = [str(i) for i, k in enumerate(self.claves_tv) if k in mantener]
        if sel:
            self.tv.selection_set(sel)
            self.tv.see(sel[0])

    def escala(self):
        """(píxeles por segundo, duración del bruto, ancho mínimo de un clip). Con zoom 1 el bruto entero cabe en la
        línea; cada clip mide al menos unos píxeles para verse, y más (hasta min_clip, para poder pincharlo) al ampliar."""
        tramos = self.tramos()
        total = sum(d for _, _, d in tramos) or 1
        ancho = max(200, self.linea.winfo_width() - 4)
        n = max(1, len(tramos))
        minimo = min(self.min_clip, max(3.0, self.zoom * ancho * 0.5 / n))
        pps = max((ancho * self.zoom - n * minimo) / total, ancho * self.zoom * 0.5 / total)
        return pps, total, minimo

    def zoom_maximo(self):
        """El zoom con el que un segundo mide PPS_MAX píxeles (o 1 si el bruto es tan corto que ya cabe así)."""
        total = sum(d for _, _, d in self.tramos()) or 1
        return max(1.0, PPS_MAX * total / max(200, self.linea.winfo_width() - 4))

    def geometria(self):
        """Dónde va cada clip en la línea de tiempo: ([(x0, x1, t0, dur)], ancho total). Proporcional al tiempo, pero cada
        clip mide al menos unos píxeles, para que se pueda pinchar aunque dure 2 s en un bruto de 2 h."""
        pps, _, minimo = self.escala()
        x, sitios = 2.0, []
        for _, t, d in self.tramos():
            w = max(minimo, d * pps)
            sitios.append((x, x + w, t, d))
            x += w
        return sitios, x

    def x_de_t(self, t, sitios=None):
        sitios = sitios if sitios is not None else self.geometria()[0]
        for x0, x1, t0, d in sitios:
            if t < t0 + d or (x0, x1, t0, d) == sitios[-1]:
                return x0 + ((min(max(t - t0, 0), d) / d) * (x1 - x0) if d else 0)
        return 2.0

    def clip_en_x(self, x, sitios=None):
        """(índice del clip en esa x, segundo del bruto) o (None, 0)."""
        sitios = sitios if sitios is not None else self.geometria()[0]
        if not sitios:
            return None, 0.0
        for i, (x0, x1, t0, d) in enumerate(sitios):
            if x < x1 or i == len(sitios) - 1:
                return i, t0 + (min(max(x - x0, 0), x1 - x0) / (x1 - x0)) * d if d else t0
        return None, 0.0

    def pintar_linea(self):
        """Regla con timecodes (más finos cuanto más zoom, hasta los fotogramas), los clips y, en los que caben, una tira
        de fotogramas. La regla y la tira, solo de la parte visible (se repinta al desplazar)."""
        tema, cv = self.tema, self.linea
        cv.delete("all")
        sitios, ancho = self.geometria()
        movidos = self.m.movidos(self.p) if self.p else set()
        sel = set(self.seleccion())
        vx0 = cv.canvasx(0)
        vx1 = vx0 + max(200, cv.winfo_width())
        self.rango_linea = (round(vx0), round(vx1), round(ancho))
        arriba, abajo = 22, self.alto_linea - 6
        alto_onda = 16 if not self.compacto else 10  # la forma de onda, abajo de cada clip
        alto_tira = abajo - arriba - 6 - alto_onda
        tw = int(alto_tira * 16 / 9)
        for (x0, x1, t0, d), (c, _, _) in zip(sitios, self.tramos()):
            if x1 < vx0 - 2 or x0 > vx1 + 2:
                continue
            color = tema.SUAVE if not c.get("fichero_video") else tema.ACENTO if c.get("clave") in movidos else tema.CLIP
            marcado = c.get("clave") in sel
            cv.create_rectangle(x0, arriba, x1, abajo, fill=color, outline="#ffffff" if marcado else tema.CAMPO, width=2 if marcado else 1)
            if PIL_OK and alto_tira >= 18 and x1 - x0 >= tw + 6 and c.get("clave") in self.listos:
                self.pintar_tira(c, x0, x1, d, arriba + 3, tw, alto_tira, vx0, vx1)
            if c.get("clave") in self.ondas:
                self.pintar_onda(self.ondas[c.get("clave")], x0, x1, d, abajo - 2 - alto_onda, abajo - 2, vx0, vx1)
            if x1 - x0 > 50:
                texto = cv.create_text(x0 + 5, arriba + 3, text=c["nombre"], anchor="nw", font=(tema.LETRA, 8), width=x1 - x0 - 8,
                                       fill=tema.SOBRE_ACENTO if color == tema.ACENTO else "#ffffff")
                caja = cv.bbox(texto)
                if caja and c.get("clave") in self.listos:  # sobre la tira, con fondo para que se lea
                    fondo = cv.create_rectangle(caja[0] - 2, caja[1] - 1, caja[2] + 2, caja[3] + 1, fill=color, outline="")
                    cv.tag_lower(fondo, texto)
        self.pintar_regla(sitios, vx0, vx1)
        x = self.x_de_t(self.t, sitios) if sitios else 2
        cv.create_line(x, 16, x, self.alto_linea, fill=tema.CABEZAL, width=2)
        cv.create_polygon(x - 6, 14, x + 6, 14, x, 22, fill=tema.CABEZAL, outline="")
        cv.configure(scrollregion=(0, 0, ancho + 4, self.alto_linea))

    def pintar_regla(self, sitios, vx0, vx1):
        """Timecodes del bruto a intervalos redondos (paso_regla) y marcas pequeñas entre ellos, en la parte visible."""
        tema, cv = self.tema, self.linea
        if not sitios:
            return
        pps, total, _ = self.escala()
        paso, menor = mj.paso_regla(pps)
        _, t_ini = self.clip_en_x(max(vx0, 2), sitios)
        _, t_fin = self.clip_en_x(vx1, sitios)
        for p, largo in ((menor, 4), (paso, 8)):
            if not p:
                continue
            k = int(t_ini // p)
            ultimo = -1000
            while k * p <= min(t_fin, total) + 1e-6:
                tt = k * p
                x = self.x_de_t(tt, sitios)
                cv.create_line(x, 0, x, largo, fill=tema.SUAVE)
                if p == paso and x - ultimo >= 80:
                    cv.create_text(x + 3, 1, text=fmt_tc(tt), anchor="nw", fill=tema.SUAVE, font=(tema.MONO, 8))
                    ultimo = x
                k += 1

    def pintar_tira(self, c, x0, x1, d, y, tw, th, vx0, vx1):
        """Fotogramas del clip (de su tira de 1 por segundo) uno detrás de otro, solo los que se ven."""
        jpgs = self.jpegs_de(c)
        if not jpgs:
            return
        cv = self.linea
        x = x0 + 3 + max(0, int((vx0 - x0) // tw)) * tw
        while x + tw <= x1 - 2 and x <= vx1:
            segundo = (x - x0) / (x1 - x0) * d if x1 > x0 else 0
            i = min(int(segundo), len(jpgs) - 1)
            foto = self.miniatura(c.get("clave"), i, jpgs, tw, th)
            if foto:
                cv.create_image(x, y, image=foto, anchor="nw")
            x += tw

    def pintar_onda(self, picos, x0, x1, d, y0, y1, vx0, vx1):
        """La forma de onda del clip en su franja de abajo: una raya cada 2 píxeles con el pico de ese tramo (solo lo
        visible). Se ve dónde hay voz y dónde silencio sin reproducir nada."""
        if not picos or x1 - x0 < 3 or not d:
            return
        cv, n, mitad, alto = self.linea, len(picos), (y0 + y1) / 2, (y1 - y0) / 2
        cv.create_rectangle(x0 + 1, y0, x1 - 1, y1, fill=self.tema.CAMPO, outline="")
        x = max(x0 + 1, vx0)
        fin = min(x1 - 1, vx1)
        por_px = n / (x1 - x0)
        while x < fin:
            a = int((x - x0) * por_px)
            pico = max(picos[a:max(a + 1, int((x + 2 - x0) * por_px))] or b"\0")
            h = max(0.5, pico / 255 * alto)
            cv.create_line(x, mitad - h, x, mitad + h, fill=self.tema.ONDA)
            x += 2

    def miniatura(self, clave, i, jpgs, tw, th):
        k = (clave, i, tw)
        foto = self.miniaturas.get(k)
        if foto is None:
            try:
                imagen = Image.open(io.BytesIO(jpgs[i]))
                imagen.draft("RGB", (tw, th))
                foto = self.miniaturas[k] = ImageTk.PhotoImage(imagen.resize((tw, th)), master=self.marco)
            except Exception:
                return None
            if len(self.miniaturas) > 600:
                for viejo in list(self.miniaturas)[:200]:
                    self.miniaturas.pop(viejo)
        return foto

    def jpegs_de(self, c):
        """Los JPEG de la tira de 1 por segundo del clip (leídos una vez; los de los últimos 30 clips, en memoria)."""
        k = c.get("clave")
        ruta = self.listos.get(k)
        if not ruta:
            return None
        jpgs = self.jpegs.get(k)
        if jpgs is None:
            jpgs = self.jpegs[k] = mj.fotogramas(ruta)
            if len(self.jpegs) > 30:
                self.jpegs.pop(next(iter(self.jpegs)))
        return jpgs

    def al_desplazar_linea(self, a, b):
        """La barra de la línea de tiempo; y, si se ve otra parte, se repinta (la regla y la tira son solo de lo visible)."""
        self.sb_linea.set(a, b)
        if not self.vivo or self.rango_linea is None:
            return
        vx0 = round(self.linea.canvasx(0))
        if abs(vx0 - self.rango_linea[0]) > 2:
            self.rango_linea = None  # una sola vez aunque lleguen varios
            self.linea.after_idle(self.pintar_linea)

    def ver_cursor(self):
        """Si el cabezal se ha salido de la parte visible de la línea de tiempo, la mueve para que se vea."""
        sitios, ancho = self.geometria()
        if not sitios:
            return
        x, visible = self.x_de_t(self.t, sitios), self.linea.winfo_width()
        izq = self.linea.canvasx(0)
        if x < izq + 10 or x > izq + visible - 10:
            self.linea.xview_moveto(max(0.0, (x - visible / 2) / (ancho + 4)))

    def al_pasar_linea(self, e):
        """Debajo, el nombre y la duración del clip que hay bajo el ratón (en los clips cortos no cabe el nombre)."""
        i, _ = self.clip_en_x(self.linea.canvasx(e.x))
        tramos = self.tramos()
        if i is None or i >= len(tramos):
            self.lbl_encima.configure(text=self.ayuda_linea)
            return
        c = tramos[i][0]
        self.lbl_encima.configure(text="%d. %s · %s" % (i + 1, c["nombre"], fmt_h(duracion_visible(c))))

    def rueda_linea(self, sentido, zoom=False, x=None):
        if zoom:
            self.ampliar(1 / 1.5 if sentido > 0 else 1.5, x)
        else:
            self.linea.xview_scroll(sentido * 3, "units")
        return "break"  # que no mueva también la página

    def donde_cursor(self):
        tramos = self.tramos()
        for n, (c, t, d) in enumerate(tramos):
            if t <= self.t < t + d or (n == len(tramos) - 1 and self.t >= t):
                return n, c, self.t - t
        return None, None, 0

    def pintar_visor(self):
        tema = self.tema
        self.visor.delete("all")
        n, c, dentro = self.donde_cursor()
        b = self.bruto()
        total = sum(d for _, _, d in self.tramos())
        self.lbl_tc.configure(text=fmt_tc(self.t))
        self.lbl_total.configure(text="de %s" % fmt_tc(total))
        if b:
            self.lbl_bruto.configure(text="Bruto %d: %s · %s%s" % (self.k + 1, b["nombre"], fmt_h(b["segundos"]), " · PASA DE 2 H" if b["largo"] else ""),
                                     style=self.pref_bruto + ("Aviso.TLabel" if b["largo"] else "Suave.TLabel"))
        else:
            self.lbl_bruto.configure(text="")
        if c is None:
            self.lbl_nombre.configure(text="")
            for w in self.ficha.values():
                w.configure(text="")
            self.visor.create_text(self.vw / 2, self.vh / 2, fill=tema.SUAVE, text="Sin clips en este bruto")
            return
        f = self.m.fecha(c)
        movido = c.get("clave") in self.m.movidos(self.p)
        valores = {"fecha": f.strftime("%d/%m/%Y %H:%M:%S") if f else "sin fecha", "tc": c.get("timecode") or "—",
                   "dur": fmt_h(duracion_visible(c)),
                   "formato": " · ".join(str(x) for x in (c.get("codec_video"), c.get("resolucion")) if x) or "—",
                   "audio": "%s pistas" % c["num_audio"] if c.get("num_audio") else "SIN AUDIO" if c.get("fichero_video") else "—", "origen": (c.get("origen_texto") or c.get("origen") or "—") + ("\n" + c["origen_motivo"] if c.get("origen_motivo") else ""),
                   "montaje": "movido a mano" if movido else "automático"}
        self.lbl_nombre.configure(text="%d. %s" % (n + 1, c["nombre"]))
        for k, w in self.ficha.items():
            w.configure(text=valores[k], style="Panel.Acento.TLabel" if (k == "montaje" and movido) or (k == "audio" and valores[k] == "SIN AUDIO")
                        else "Panel.TLabel")
        ruta = self.listos.get(c.get("clave"))
        self.pedir_fotogramas()  # si se ha cambiado de clip, lo suyo pasa delante
        exacto = self.exactos.get((c.get("clave"), int(round(dentro * FPS_BRUTO))))
        if exacto and PIL_OK:  # el cursor parado: el fotograma exacto
            self.dibujar(("exacto", c.get("clave"), int(round(dentro * FPS_BRUTO))), lambda: exacto)
            return
        if not self.sonando and not self.reproduciendo:
            self.pedir_exacto(c, dentro)
        if not ruta or not PIL_OK:
            portada = self.portadas.get(c.get("clave")) if PIL_OK else None
            if portada:  # mientras se preparan todos sus fotogramas, una imagen del clip
                self.dibujar(("portada", c.get("clave")), lambda: Path(portada).read_bytes())
                self.visor.create_text(self.vw / 2, self.vh - 12, fill=tema.TEXTO, text="Preparando el resto del clip…",
                                       font=(tema.LETRA, 8))
                return
            self.visor.create_text(self.vw / 2, self.vh / 2, fill=tema.SUAVE, text="Preparando fotogramas…" if self.ffmpeg and PIL_OK else "Sin imagen")
            return
        jpgs = self.jpegs_de(c)
        if not jpgs:
            self.visor.create_text(self.vw / 2, self.vh / 2, fill=tema.SUAVE, text="Sin fotogramas")
            return
        i = min(int(dentro), len(jpgs) - 1)
        self.dibujar((c.get("clave"), i), lambda: jpgs[i])

    def dibujar(self, clave, jpeg):
        """Pone en el visor la imagen clave (jpeg(): sus bytes), escalada al visor; las últimas 64 se guardan hechas."""
        foto = self.fotos.get(clave)
        if foto is None:
            imagen = Image.open(io.BytesIO(jpeg()))
            if imagen.size != (self.vw, self.vh):  # visor más pequeño (pantalla de portátil)
                imagen = imagen.resize((self.vw, self.vh))
            foto = self.fotos[clave] = ImageTk.PhotoImage(imagen, master=self.marco)
            if len(self.fotos) > 64:
                self.fotos.pop(next(iter(self.fotos)))
        self.visor.create_image(0, 0, image=foto, anchor="nw")

    def refrescar(self, mantener=()):
        self.pintar_proyectos()
        self.pintar_brutos()
        self.pintar_tabla(mantener)
        total = sum(d for _, _, d in self.tramos())
        self.t = min(self.t, total)
        self.pintar_linea()
        self.pintar_visor()

    # --- fotogramas ----------------------------------------------------------------------------------------------

    def pedir_fotogramas(self):
        """Lanza lo siguiente que hay que preparar para el visor, por orden (mj.siguiente_preparacion): primero el clip
        que se mira y los siguientes. Las portadas (una imagen, al momento) van por su cuenta, de una en una, para que
        un clip nuevo enseñe algo enseguida aunque los demás hilos estén con clips largos. Se llama al pintar el visor
        (si se cambia de clip, lo suyo pasa delante) y cada vez que acaba un trabajo."""
        if not self.pool or not self.vivo:
            return
        cs = [c for c, _, _ in self.tramos() if c.get("fichero_video")]
        for c in cs:  # lo hecho otra vez (el visor se guarda en la carpeta del análisis) no se repite
            k = c.get("clave")
            if k in self.mirados:
                continue
            self.mirados.add(k)
            for hechos, fichero in ((self.listos, mj.fichero_visor), (self.portadas, mj.fichero_portada)):
                ruta = fichero(self.carpeta_visor, k)
                if ruta.exists() and ruta.stat().st_size:
                    hechos[k] = ruta
        claves = [c.get("clave") for c in cs]
        por_clave = {c.get("clave"): c for c in cs}
        _, actual, _ = self.donde_cursor()
        i = claves.index(actual.get("clave")) if actual and actual.get("clave") in claves else 0
        for tipo, maximo in (("portada", 1), ("tira", self.hilos)):
            while sum(1 for t, _ in self.en_curso if t == tipo) < maximo:
                t = mj.siguiente_preparacion(claves, i, self.listos, self.portadas, self.en_curso, self.fallidos, tipos=(tipo,))
                if t is None:
                    break
                self.en_curso.add(t)
                funcion = mj.sacar_portada if tipo == "portada" else mj.sacar_fotogramas
                fut = self.pool.submit(funcion, self.ffmpeg, self.origen / por_clave[t[1]]["fichero_video"], self.carpeta_visor, t[1])
                fut.add_done_callback(lambda f, t=t: self.cola.put((t, f.result() if not f.cancelled() and not f.exception() else None)))
        self.pedir_onda(cs, i)
        self.estado_fotogramas()

    def pedir_onda(self, cs, i):
        """La forma de onda del sonido de los clips, de una en una, empezando por el que se mira (la de otra vez se lee de
        la carpeta del visor)."""
        if self.ondas_en_curso:
            return
        for c in cs[i:] + cs[:i][::-1]:
            k = c.get("clave")
            if k in self.ondas or k in self.ondas_fallidas:
                continue
            ruta = mj.fichero_onda(self.carpeta_visor, k)
            if ruta.exists():
                self.ondas[k] = ruta.read_bytes()
                continue
            canales = eb.canales_clip(c, getattr(self.m, "mxf_por_ruta", None) or {})
            if not canales:
                self.ondas_fallidas.add(k)
                continue
            self.ondas_en_curso.add(k)
            fut = self.pool.submit(mj.sacar_onda, self.ffmpeg, self.origen, c, canales, self.carpeta_visor, k)
            fut.add_done_callback(lambda f, k=k: self.cola.put((("onda", k), f.result() if not f.cancelled() and not f.exception() else None)))
            return

    def estado_fotogramas(self):
        cs = [c for c, _, _ in self.tramos() if c.get("fichero_video")]
        hechos = sum(1 for c in cs if c.get("clave") in self.listos)
        if PIL_OK and self.ffmpeg:
            corta = self.compacto or getattr(self, "muy_estrecha", False)
            self.lbl_foto.configure(text=("Fotogramas %d/%d" if corta else "Fotogramas: %d de %d clips") % (hechos, len(cs)))

    def vaciar_cola(self):
        if not self.vivo:
            return
        hubo = False
        try:
            while True:
                (tipo, k), ruta = self.cola.get_nowait()
                if tipo == "onda":
                    self.ondas_en_curso.discard(k)
                    if ruta:
                        self.ondas[k] = Path(ruta).read_bytes()
                    else:
                        self.ondas_fallidas.add(k)
                    hubo = True
                    continue
                if tipo == "exacto":
                    if ruta:
                        self.exactos[k] = ruta
                        if len(self.exactos) > 64:
                            self.exactos.pop(next(iter(self.exactos)))
                        n, c, dentro = self.donde_cursor()
                        if c is not None and k == (c.get("clave"), int(round(dentro * FPS_BRUTO))):
                            self.pintar_visor()
                    continue
                self.en_curso.discard((tipo, k))
                if ruta:
                    (self.listos if tipo == "tira" else self.portadas)[k] = ruta
                else:
                    self.fallidos.add((tipo, k))
                hubo = True
        except queue.Empty:
            pass
        if hubo:
            self.pintar_visor()  # y, con el hueco libre, lo siguiente (pedir_fotogramas)
            if self.marco.winfo_ismapped():
                self.pintar_linea()  # la tira de fotogramas de los clips que ya los tienen
        self.seguir_sonido()
        self.marco.after(200, self.vaciar_cola)

    # --- eventos -----------------------------------------------------------------------------------------------

    def elegir_proyecto(self, p):
        self.parar_sonido()
        self.p, self.k, self.t = p, 0, 0.0
        self.refrescar()

    def al_elegir_bruto(self, _=None):
        sel = self.lb_b.curselection()
        if sel and sel[0] != self.k:
            self.parar_sonido()
            self.k, self.t = sel[0], 0.0
            self.pintar_tabla()
            self.pintar_linea()
            self.pintar_visor()
            self.pedir_fotogramas()

    def al_elegir_clip(self, _=None):
        sel = self.tv.selection()
        if len(sel) == 1:
            self.t = self.tramos()[int(sel[0])][1]
            self.pintar_visor()
        self.pintar_linea()
        self.ver_cursor()

    def al_pulsar_linea(self, e, sumar=False):
        n, self.t = self.clip_en_x(self.linea.canvasx(e.x))
        if n is not None:
            if not sumar:
                self.tv.selection_set(str(n))
            elif str(n) in self.tv.selection():
                self.tv.selection_remove(str(n))
            else:
                self.tv.selection_add(str(n))
            self.tv.see(str(n))
        self.pintar_linea()
        self.pintar_visor()
        if self.sonando:  # sigue sonando desde donde se ha pinchado
            self.empezar_sonido()
        return "break"

    def al_arrastrar_linea(self, e):
        _, self.t = self.clip_en_x(self.linea.canvasx(e.x))
        self.pintar_linea()
        self.pintar_visor()

    def mover_cursor(self, segundos):
        if not self.vivo or not self.marco.winfo_ismapped():
            return
        total = sum(d for _, _, d in self.tramos())
        self.t = max(0.0, min(total, self.t + segundos))
        self.pintar_linea()
        self.ver_cursor()
        self.pintar_visor()
        if self.sonando:
            self.empezar_sonido()

    def paso_fotograma(self, n):
        """Uno o varios fotogramas del bruto adelante o atrás (para; el visor enseña el fotograma exacto)."""
        if self.sonando:
            self.parar_sonido()
        if self.reproduciendo:
            self.reproducir()
        total = sum(d for _, _, d in self.tramos())
        self.t = max(0.0, min(total, (round(self.t * FPS_BRUTO) + n) / FPS_BRUTO))
        self.pintar_linea()
        self.ver_cursor()
        self.pintar_visor()

    def ir_al_clip(self, salto, final=False):
        """Al principio (o al último fotograma, con final) del clip actual (salto 0), del anterior (-1) o del siguiente (1)."""
        tramos = self.tramos()
        n, _, _ = self.donde_cursor()
        if n is None:
            return
        n = max(0, min(len(tramos) - 1, n + salto))
        _, t0, d = tramos[n]
        self.t = max(t0, t0 + d - 1 / FPS_BRUTO) if final else t0
        if salto:
            self.tv.selection_set(str(n))
            self.tv.see(str(n))
        self.pintar_linea()
        self.ver_cursor()
        self.pintar_visor()
        if self.sonando:
            self.empezar_sonido()

    def tecla(self, accion, lista=True):
        """Atajo de teclado de la página: solo si se ve, y no mientras se escribe en un campo ni (con lista=False) si la
        lista de clips tiene el foco, porque sus flechas ya cambian de clip."""
        def atajo(_):
            if not self.activa():
                return None
            try:
                clase = self.marco.focus_get().winfo_class()
            except Exception:
                clase = ""
            if clase in ("TEntry", "Entry", "TSpinbox", "Spinbox", "TCombobox", "Text") or (not lista and clase == "Treeview"):
                return None
            accion()
            return "break"
        return atajo

    # --- ver y oír (ffplay) ------------------------------------------------------------------------------------

    def poner_tc(self, debajo):
        if debajo != self.tc_debajo:
            self.tc_debajo = debajo
            self.ctrl_tc.grid_forget()
            self.ctrl_tc.grid(row=1, column=0, pady=(2, 0)) if debajo else self.ctrl_tc.grid(row=0, column=1, padx=(16, 0))

    def poner_zoom(self, debajo):
        if debajo != self.zoom_debajo:
            self.zoom_debajo = debajo
            self.caja_zoom.grid_forget()
            self.caja_zoom.grid(row=1, column=0, columnspan=2, sticky="e", pady=(4, 0)) if debajo else \
                self.caja_zoom.grid(row=0, column=1, sticky="e")

    def adaptar(self, ancho, alto):
        """Se recoloca para la ventana de ancho x alto (al abrir y al cambiar su tamaño): el visor, del tamaño que quepa
        (tema.tamano_visor), y la ficha del clip, a la derecha si hay sitio (si no, lo mismo lo dice la lista de clips).
        En una pantalla pequeña (compacto) ya va todo apretado y no cambia."""
        if self.compacto or not self.vivo:
            return
        vw, vh = self.tema.tamano_visor(ancho, alto)
        if (vw, vh) != (self.vw, self.vh):
            self.vw, self.vh = vw, vh
            self.visor.configure(width=vw, height=vh)
            self.fotos.clear()
            self.exactos.clear()
            self.pedido_exacto = None
            self.pintar_visor()
        self.lbl_teclas.configure(wraplength=vw + 40)
        self.poner_tc(debajo=ancho < 1350)
        self.poner_zoom(debajo=ancho < 1350)
        muy = self.muy_estrecha = ancho < 1200  # muy estrecha: textos y columnas más cortos
        for etiqueta, (largo, corto) in self.textos_leyenda.items():
            etiqueta.configure(text=corto if muy else largo)
        self.lbl_encima.configure(width=28 if muy else 38)
        self.estado_fotogramas()
        for b, (largo, corto) in self.textos_bruto.items():
            b.configure(text=corto if muy else largo)
        self.cb_p.configure(width=18 if muy else self.ancho_cb_p)
        self.lbl_p.configure(wraplength=110 if muy else 200)
        for b in self.botones:
            b.configure(style="Compacta.TButton" if muy else "Accion.TButton")
        for lb in (self.lb_b, self.lb_q, self.lb_n, self.lb_f):
            lb.configure(width=24 if muy else 34)
        for c, w in zip(("n", "clip", "fecha", "dur", "origen", "montaje"),
                        (36, 150, 126, 76, 120, 120) if muy else (44, 240, 150, 90, 170, 120)):
            self.tv.column(c, width=w)
        self.tv["displaycolumns"] = ("n", "clip", "fecha", "dur", "origen") if muy else ("n", "clip", "fecha", "dur", "origen", "montaje")
        quiere = ancho >= 1350
        if quiere != self.ficha_vista:
            self.ficha_vista = quiere
            if quiere:
                self.ficha_caja.pack(side="right", fill="y", before=self.visor_caja)
            else:
                self.ficha_caja.pack_forget()

    def ver_y_oir(self):
        if self.sonando:
            self.parar_sonido()
        else:
            self.empezar_sonido()

    def empezar_sonido(self):
        """El clip del cursor, de verdad (imagen y todo su sonido), desde donde está, en una ventana de ffplay. Al acabar
        el clip sigue con el siguiente; el cursor de la línea de tiempo va con lo que suena."""
        self.parar_sonido()
        if not self.ffplay or not self.ffmpeg or not self.vivo:
            return
        if self.reproduciendo:
            self.reproducir()
        n, c, dentro = self.donde_cursor()
        if c is None or not c.get("fichero_video"):
            return
        d = duracion_visible(c)
        if dentro >= d - 0.2:
            dentro = 0.0
        canales = eb.canales_clip(c, getattr(self.m, "mxf_por_ruta", None) or {})[:16]
        titulo = "%d. %s  (Espacio en el programa: parar)" % (n + 1, c["nombre"])
        try:
            a = subprocess.Popen(mj.orden_sonido(self.ffmpeg, self.origen, c, dentro, canales), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **eb.flags_subproceso())
            b = subprocess.Popen(mj.orden_ffplay(self.ffplay, titulo), stdin=a.stdout, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, **eb.flags_subproceso())
            a.stdout.close()
        except OSError as e:
            self.lbl_foto.configure(text="No se puede reproducir: %s" % e)
            return
        self.sonando = {"procesos": (a, b), "n": n, "inicio": self.t - dentro, "dentro": dentro, "dur": d, "t0": time.monotonic()}
        self.bt_sonido.configure(text="■")

    def parar_sonido(self):
        s, self.sonando = self.sonando, None
        if s:
            for proc in reversed(s["procesos"]):
                if proc.poll() is None:
                    try:
                        proc.kill()
                    except OSError:
                        pass
            for proc in s["procesos"]:
                try:
                    proc.wait(timeout=3)
                except Exception:
                    pass
        if hasattr(self, "bt_sonido"):
            self.bt_sonido.configure(text="▶")

    def seguir_sonido(self):
        """Mientras suena, el cursor va con el clip; cuando ffplay acaba, sigue con el siguiente clip (si se ha cerrado su
        ventana antes de acabar, para)."""
        s = self.sonando
        if not s:
            return
        pasado = time.monotonic() - s["t0"]
        if s["procesos"][1].poll() is None:
            self.t = s["inicio"] + min(s["dentro"] + pasado, s["dur"])
            if self.marco.winfo_ismapped():
                self.pintar_linea()
                self.ver_cursor()
                self.pintar_visor()
            return
        acabado = s["dentro"] + pasado >= s["dur"] - 1.0
        fallo = s["procesos"][1].returncode not in (0, None) and pasado < 2
        self.parar_sonido()
        if fallo:
            self.lbl_foto.configure(text="ffplay no ha podido reproducir el clip")
            return
        tramos = self.tramos()
        if acabado and s["n"] + 1 < len(tramos) and self.vivo:
            self.t = tramos[s["n"] + 1][1]
            self.empezar_sonido()

    # --- fotograma exacto --------------------------------------------------------------------------------------

    def pedir_exacto(self, c, dentro):
        """Pide a ffmpeg el fotograma exacto donde está el cursor (la tira es de 1 por segundo). Espera un poco por si el
        cursor se sigue moviendo, y solo el último."""
        if not self.pool_exacto or not PIL_OK:
            return
        clave = (c.get("clave"), int(round(dentro * FPS_BRUTO)))
        if clave in self.exactos or clave == self.pedido_exacto:
            return
        if self.espera_exacto:
            self.marco.after_cancel(self.espera_exacto)

        def lanzar():
            self.espera_exacto = None
            if not self.vivo:
                return
            self.pedido_exacto = clave
            ruta = self.origen / c["fichero_video"]
            fut = self.pool_exacto.submit(mj.fotograma_exacto, self.ffmpeg, ruta, clave[1] / FPS_BRUTO, tamano=(self.vw, self.vh))
            fut.add_done_callback(lambda f: self.cola.put((("exacto", clave), f.result() if not f.cancelled() and not f.exception() else None)))
        self.espera_exacto = self.marco.after(150, lanzar)

    def ampliar(self, factor, x=None):
        """Zoom de la línea de tiempo, desde el bruto entero hasta PPS_MAX píxeles por segundo. Con la rueda (x: donde
        está el ratón) se queda quieto lo que hay bajo el ratón; con los botones, el cabezal, en el centro."""
        if x is not None:
            _, t_raton = self.clip_en_x(self.linea.canvasx(x))
        self.zoom = max(1.0, min(self.zoom_maximo(), self.zoom * factor))
        self.pintar_linea()
        sitios, ancho = self.geometria()
        if sitios:
            fijo, pantalla = (t_raton, x) if x is not None else (self.t, self.linea.winfo_width() / 2)
            self.linea.xview_moveto(max(0.0, (self.x_de_t(fijo, sitios) - pantalla) / (ancho + 4)))
        self.pintar_linea()

    def ajustar(self):
        """El bruto entero en la línea de tiempo."""
        self.zoom = 1.0
        self.linea.xview_moveto(0)
        self.pintar_linea()

    def reproducir(self):
        """El avance rápido por la tira de fotogramas (x4, x10, x60), sin sonido."""
        self.reproduciendo = not self.reproduciendo
        self.bt_play.configure(text="⏸  Parar" if self.reproduciendo else "⏩ Avance")
        if self.reproduciendo:
            self.parar_sonido()
            self.paso()

    def paso(self):
        if not self.reproduciendo or not self.vivo:
            return
        vel = (4, 10, 60)[max(0, self.cb_vel.current())]
        total = sum(d for _, _, d in self.tramos())
        self.t += vel * 0.2
        if self.t >= total:
            self.t = total
            self.reproducir()
        self.pintar_linea()
        self.ver_cursor()
        self.pintar_visor()
        if self.reproduciendo:
            self.marco.after(200, self.paso)

    # --- acciones ----------------------------------------------------------------------------------------------

    def bloquear(self, si):
        """Mientras se analiza o se exporta: se puede mirar, pero no cambiar el montaje."""
        self.bloqueada = si
        for b in self.botones + [self.bt_guardar]:
            b.state(["disabled"] if si else ["!disabled"])
        if si:
            self.lbl_guardado.configure(text="Trabajando…: el montaje no se puede cambiar ahora", style="Tarjeta.Acento.TLabel")
        else:
            self.lbl_guardado.configure(text="Cambios sin guardar" if self.cambios else "", style="Tarjeta.Acento.TLabel")

    def permitido(self):
        if self.bloqueada:
            from tkinter import messagebox
            messagebox.showinfo("Trabajando", "Espera a que termine el análisis o la exportación para cambiar el montaje.", parent=self.marco)
        return not self.bloqueada

    def hecho(self, mantener=()):
        self.cambios = True
        self.lbl_guardado.configure(text="Cambios sin guardar", style="Tarjeta.Acento.TLabel")
        if self.al_cambiar:
            self.al_cambiar()
        self.refrescar(mantener)

    def sin_seleccion(self):
        from tkinter import messagebox
        messagebox.showinfo("Elige clips", "Marca uno o varios clips en la lista o en la línea de tiempo (Ctrl para varios).", parent=self.marco)

    def quitar(self):
        if not self.permitido():
            return
        sel = self.seleccion()
        if not sel:
            return self.sin_seleccion()
        self.m.quitar(self.p, sel)
        self.hecho()

    def accion(self, que, delta=None):
        if not self.permitido():
            return
        sel = self.seleccion()
        if not sel:
            return self.sin_seleccion()
        try:
            if que == "mover":
                self.m.mover(self.p, sel, delta)
                nuevo = self.m.donde(self.p, sel[0])
                if nuevo is not None:  # se sigue a los clips movidos
                    self.k = nuevo
                    self.t = next((t for c, t, _ in self.tramos() if c.get("clave") == sel[0]), 0.0)
            else:
                getattr(self.m, que)(self.p, sel)
        except ValueError as e:
            from tkinter import messagebox
            return messagebox.showinfo("Mover", str(e), parent=self.marco)
        self.hecho(sel)

    def cortar(self):
        from tkinter import messagebox
        if not self.permitido():
            return
        sel = self.seleccion()
        if len(sel) != 1:
            return messagebox.showinfo("Cortar", "Marca un solo clip: el bruto acabará justo antes de él.", parent=self.marco)
        self.m.cortar(self.p, sel[0])
        self.hecho()

    def renombrar(self):
        from tkinter import messagebox, simpledialog
        b = self.bruto()
        if not b or not self.permitido():
            return
        nombre = simpledialog.askstring("Renombrar bruto", "Nombre del fichero (vacío = el automático):", initialvalue=b["nombre"], parent=self.marco)
        if nombre is None:
            return
        errores = eb.validar_nombres([nombre]) if nombre.strip() else []
        if errores:
            return messagebox.showwarning("Nombre no válido", "\n".join(errores), parent=self.marco)
        self.m.renombrar(self.p, self.k, nombre)
        self.hecho()

    def recuperar(self):
        papelera = self.m.papelera(self.p)
        sel = [papelera[i].get("clave") for i in self.lb_q.curselection()]
        if sel and self.permitido():
            self.m.recuperar(self.p, sel)
            self.hecho()

    def colocar(self):
        nuevos = self.m.nuevos(self.p)
        sel = [nuevos[i].get("clave") for i in self.lb_n.curselection()]
        if sel and self.permitido():
            self.m.colocar(self.p, sel)
            self.hecho()

    def deshacer(self):
        if self.activa() and self.m.deshacer():
            self.hecho()

    def automatico(self):
        from tkinter import messagebox
        if not self.p or not self.permitido():
            return
        if messagebox.askyesno("Orden automático", "¿Deshacer todos los cambios a mano de %s y volver al orden cronológico "
                                                   "y a los cortes automáticos?" % self.p, parent=self.marco):
            self.m.volver_automatico(self.p)
            self.k, self.t = 0, 0.0
            self.hecho()

    def guardar(self, _=None):
        if not self.vivo or self.bloqueada:
            return
        self.m.guardar(self.carpeta_brutos)
        self.cambios = False
        self.lbl_guardado.configure(text="Guardado en montaje.json", style="Tarjeta.Bien.TLabel")
        if self.al_cambiar:
            self.al_cambiar()

    def cerrar(self):
        """Deja de trabajar (fotogramas, avance) y quita la página."""
        self.vivo = False
        self.reproduciendo = False
        self.parar_sonido()
        for pool in (self.pool, self.pool_exacto):
            if pool:
                pool.shutdown(wait=False, cancel_futures=True)
        top = self.marco.winfo_toplevel()
        for tecla, _ in self.atajos:
            top.unbind(tecla)
        self.marco.destroy()


# ---------------------------------------------------------------------------
# Opciones avanzadas de exportación
# ---------------------------------------------------------------------------

FORMATOS = [("xdcam_hd50", "XDCAM HD422 50 Mb/s 1080i50 16:9 (recomendado: lo pide el Archivo)"),
            ("auto", "Formato mayoritario de cada bruto (avanzado)")]
PISTAS = [("max", "Las que más haya en el bruto, hasta 8 (recomendado)"), (4, "4"), (8, "8")]
FUENTES = ["Silencio"] + ["Pista %d del clip" % i for i in range(1, 9)]
NIVELES = [("sin_tocar", "Sin tocar (recomendado: el bruto es documento de archivo)"),
           ("ganancia", "Ganancia fija por pista"),
           ("r128", "Normalizar cada clip a -23 LUFS (EBU R128). ALTERA EL ORIGINAL; tope de ±12 dB")]


def espacio_necesario(segundos, pistas):
    """Bytes de un XDCAM HD50 de esa duración: 50 Mb/s de vídeo + 1,152 Mb/s por pista de audio + ~2 % de envoltura."""
    return segundos * (50e6 + pistas * 1.152e6) / 8 * 1.02


class PanelOpciones:
    def __init__(self, raiz, cfg, resumen, guardar_predeterminados=None):
        import tkinter as tk
        from tkinter import ttk
        import tema
        self.tk, self.ttk = tk, ttk
        self.cfg, self.resumen, self.guardar_predeterminados = dict(cfg), resumen, guardar_predeterminados
        self.resultado = None
        v = self.v = tk.Toplevel(raiz)
        v.title("Opciones avanzadas de exportación")
        v.geometry("1200x860")
        v.configure(bg=tema.FONDO)
        v.transient(raiz)
        marco = ttk.Frame(v, padding=12, style="Fondo.TFrame")
        marco.pack(fill="both", expand=True)
        izq, der = ttk.Frame(marco, style="Fondo.TFrame"), ttk.Frame(marco, style="Fondo.TFrame")
        izq.pack(side="left", fill="both", expand=True)
        der.pack(side="left", fill="both", expand=True, padx=(12, 0))

        f = tema.Tarjeta(izq, "Vídeo")
        f.pack(fill="x")
        self.formato = ttk.Combobox(f, state="readonly", width=64, values=[t for _, t in FORMATOS])
        self.formato.pack(anchor="w")

        f = tema.Tarjeta(izq, "Timecode")
        f.pack(fill="x", pady=(10, 0))
        fila = ttk.Frame(f, style="Tarjeta.TFrame")
        fila.pack(anchor="w")
        ttk.Label(fila, text="TC de inicio de cada bruto (hh:mm:ss:ff):", style="Tarjeta.TLabel").pack(side="left")
        self.tc = tk.StringVar()
        ttk.Entry(fila, textvariable=self.tc, width=14, font=(tema.MONO, 11)).pack(side="left", padx=6)
        ttk.Label(f, text="Recomendado 00:00:00:00. El TC interno del vídeo se ajusta al del fichero.", style="Tarjeta.Suave.TLabel").pack(anchor="w", pady=(4, 0))

        f = tema.Tarjeta(izq, "Audio: pistas y enrutado")
        f.pack(fill="x", pady=(10, 0))
        fila = ttk.Frame(f, style="Tarjeta.TFrame")
        fila.pack(anchor="w")
        ttk.Label(fila, text="Pistas del bruto:", style="Tarjeta.TLabel").pack(side="left")
        self.pistas = ttk.Combobox(fila, state="readonly", width=48, values=[t for _, t in PISTAS])
        self.pistas.pack(side="left", padx=6)
        ttk.Label(f, text="XDCAM HD422 solo admite 4 u 8 pistas: se redondea y las que falten van en silencio.", style="Tarjeta.Suave.TLabel").pack(anchor="w", pady=(4, 0))
        rejilla = ttk.Frame(f, style="Tarjeta.TFrame")
        rejilla.pack(anchor="w", pady=(6, 0))
        self.fuentes, self.ganancias = [], []
        for col, texto in enumerate(("SALIDA", "QUÉ LLEVA", "GANANCIA (dB)")):
            ttk.Label(rejilla, text=texto, style="Tarjeta.Seccion.TLabel").grid(row=0, column=col, sticky="w", padx=(0 if col < 2 else 8, 0))
        for k in range(eb.MAX_PISTAS):
            ttk.Label(rejilla, text="Pista %d" % (k + 1), style="Tarjeta.TLabel").grid(row=k + 1, column=0, sticky="w", padx=(0, 10))
            cb = ttk.Combobox(rejilla, state="readonly", width=18, values=FUENTES)
            cb.grid(row=k + 1, column=1, sticky="w", pady=1)
            sp = ttk.Spinbox(rejilla, from_=-20, to=20, increment=0.5, width=6)
            sp.grid(row=k + 1, column=2, sticky="w", padx=(8, 0))
            self.fuentes.append(cb)
            self.ganancias.append(sp)

        f = tema.Tarjeta(izq, "Audio: niveles")
        f.pack(fill="x", pady=(10, 0))
        self.niveles = tk.StringVar()
        for valor, texto in NIVELES:
            ttk.Radiobutton(f, text=texto, value=valor, variable=self.niveles, style="Tarjeta.TRadiobutton").pack(anchor="w")

        f = tema.Tarjeta(der, "Comprobaciones (recomendado: todas)")
        f.pack(fill="x")
        self.md5, self.ficha, self.medidor = tk.BooleanVar(), tk.BooleanVar(), tk.BooleanVar()
        for texto, var in (("MD5 de cada bruto (integridad al copiarlo)", self.md5), ("Ficha técnica: conforme o no con XDCAM HD422", self.ficha),
                           ("Medidor de audio: avisa de saturados y casi mudos (no los toca)", self.medidor)):
            ttk.Checkbutton(f, text=texto, variable=var, style="Tarjeta.TCheckbutton").pack(anchor="w")

        f = tema.Tarjeta(der, "Nombres de los ficheros")
        f.pack(fill="both", expand=True, pady=(10, 0))
        fila = ttk.Frame(f, style="Tarjeta.TFrame")
        fila.pack(anchor="w")
        ttk.Label(fila, text="Prefijo (opcional):", style="Tarjeta.TLabel").pack(side="left")
        self.prefijo = tk.StringVar()
        ttk.Entry(fila, textvariable=self.prefijo, width=20).pack(side="left", padx=6)
        ttk.Label(f, text="Cada bruto se puede renombrar en la página Montaje.", style="Tarjeta.Suave.TLabel").pack(anchor="w", pady=(4, 0))
        self.lb_nombres = tema.lista(f, height=10)
        self.lb_nombres.pack(fill="both", expand=True, pady=(6, 0))

        f = tema.Tarjeta(der, "Resumen")
        f.pack(fill="x", pady=(10, 0))
        self.lbl_resumen = ttk.Label(f, text="", justify="left", wraplength=520, style="Tarjeta.TLabel")
        self.lbl_resumen.pack(anchor="w")
        self.lbl_errores = ttk.Label(der, text="", justify="left", wraplength=520, style="Aviso.TLabel")
        self.lbl_errores.pack(anchor="w", pady=(8, 0))

        pie = ttk.Frame(v, padding=12, style="Fondo.TFrame")
        pie.pack(fill="x")
        ttk.Button(pie, text="Restablecer recomendados", command=lambda: self.poner(eb.CONFIG_DEFECTO)).pack(side="left")
        if guardar_predeterminados:
            ttk.Button(pie, text="Guardar como predeterminados", command=self.predeterminados).pack(side="left", padx=6)
        self.bt_exportar = ttk.Button(pie, text="Aceptar", style="Principal.TButton", command=self.exportar)
        self.bt_exportar.pack(side="right")
        ttk.Button(pie, text="Cancelar", style="Fantasma.TButton", command=v.destroy).pack(side="right", padx=6)

        self.poner(cfg)
        for var in (self.tc, self.niveles, self.md5, self.ficha, self.medidor, self.prefijo):
            var.trace_add("write", lambda *a: self.revisar())
        for cb in [self.formato, self.pistas] + self.fuentes:
            cb.bind("<<ComboboxSelected>>", lambda e: self.revisar())
        for sp in self.ganancias:
            sp.configure(command=self.revisar)
            sp.bind("<KeyRelease>", lambda e: self.revisar())
        self.revisar()

    def poner(self, cfg):
        self.formato.current(next((i for i, (k, _) in enumerate(FORMATOS) if k == cfg.get("formato_salida", "xdcam_hd50")), 0))
        self.tc.set(cfg.get("timecode_inicio", "00:00:00:00"))
        self.pistas.current(next((i for i, (k, _) in enumerate(PISTAS) if k == cfg.get("pistas_audio", "max")), 0))
        enr = list(cfg.get("enrutado_audio") or [])
        gan = list(cfg.get("ganancia_db") or [])
        for k in range(eb.MAX_PISTAS):
            r = enr[k] if k < len(enr) else k + 1
            self.fuentes[k].current(r if 0 <= r <= eb.MAX_PISTAS else 0)
            self.ganancias[k].delete(0, "end")
            self.ganancias[k].insert(0, str(gan[k] if k < len(gan) else 0))
        self.niveles.set(cfg.get("niveles_audio", "sin_tocar"))
        self.md5.set(bool(cfg.get("md5")))
        self.ficha.set(bool(cfg.get("ficha_tecnica")))
        self.medidor.set(bool(cfg.get("medidor_audio")))
        self.prefijo.set(cfg.get("prefijo_fichero") or "")
        if hasattr(self, "bt_exportar"):
            self.revisar()

    def leer(self):
        """Configuración que sale del panel (y errores de lectura de las ganancias)."""
        cfg = dict(self.cfg)
        cfg["formato_salida"] = FORMATOS[self.formato.current()][0]
        cfg["timecode_inicio"] = self.tc.get().strip()
        cfg["pistas_audio"] = PISTAS[self.pistas.current()][0]
        fuentes = [cb.current() for cb in self.fuentes]
        cfg["enrutado_audio"] = [] if fuentes == list(range(1, eb.MAX_PISTAS + 1)) else fuentes
        errores = []
        gan = []
        for k, sp in enumerate(self.ganancias):
            try:
                g = float(sp.get().replace(",", "."))
            except ValueError:
                errores.append("Ganancia de la pista %d: escribe un número" % (k + 1))
                g = 0.0
            gan.append(int(g) if g == int(g) else g)
        while gan and gan[-1] == 0:
            gan.pop()
        cfg["niveles_audio"] = self.niveles.get()
        cfg["ganancia_db"] = gan if cfg["niveles_audio"] == "ganancia" else []
        cfg["md5"], cfg["ficha_tecnica"], cfg["medidor_audio"] = self.md5.get(), self.ficha.get(), self.medidor.get()
        cfg["prefijo_fichero"] = self.prefijo.get()
        return cfg, errores

    def revisar(self):
        cfg, errores = self.leer()
        for sp in self.ganancias:
            sp.state(["!disabled"] if cfg["niveles_audio"] == "ganancia" else ["disabled"])
        nombres = [cfg["prefijo_fichero"] + n for n in self.resumen["brutos"]]
        errores += eb.validar_opciones(cfg) + eb.validar_nombres(nombres)
        self.lb_nombres.delete(0, "end")
        for n in nombres[:500]:
            self.lb_nombres.insert("end", n + ".mxf")
        if len(nombres) > 500:
            self.lb_nombres.insert("end", "... y %d más" % (len(nombres) - 500))
        pistas = eb.pistas_salida(self.resumen.get("pistas_max", 0), cfg)
        segundos = self.resumen.get("segundos", 0)
        necesario, libre = espacio_necesario(segundos, pistas), self.resumen.get("libre")
        texto = "%d brutos · %s de material · %d pistas de audio\nEspacio necesario: unos %.0f GB" % (
            len(nombres), fmt_h(segundos), pistas, necesario / 1e9)
        if libre is not None:
            texto += " · libres en el destino: %.0f GB" % (libre / 1e9)
        if cfg["niveles_audio"] == "r128":
            texto += "\nAtención: la normalización cambia el sonido original de cada clip."
        self.lbl_resumen.configure(text=texto)
        self.lbl_errores.configure(text="\n".join(errores))
        self.bt_exportar.state(["disabled"] if errores else ["!disabled"])
        self.errores = errores
        return cfg

    def predeterminados(self):
        from tkinter import messagebox
        cfg = self.revisar()
        if self.errores:
            return messagebox.showwarning("No se puede", "Corrige antes:\n" + "\n".join(self.errores), parent=self.v)
        self.guardar_predeterminados(cfg)
        messagebox.showinfo("Guardado", "Estas opciones son ahora las predeterminadas (config_brutos.json).", parent=self.v)

    def exportar(self):
        cfg = self.revisar()
        if not self.errores:
            self.resultado = cfg
            self.v.destroy()


def dialogo_opciones(raiz, cfg, resumen, guardar_predeterminados=None):
    """Opciones avanzadas. resumen: {"brutos": [nombres], "segundos": float, "pistas_max": int, "libre": bytes|None}.
    Devuelve la configuración elegida o None si se cancela."""
    panel = PanelOpciones(raiz, cfg, resumen, guardar_predeterminados)
    panel.v.grab_set()
    raiz.wait_window(panel.v)
    return panel.resultado


def espacio_libre(carpeta):
    """Bytes libres en la carpeta (o en la primera carpeta existente hacia arriba), o None."""
    p = Path(carpeta)
    while not p.exists() and p.parent != p:
        p = p.parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return None
