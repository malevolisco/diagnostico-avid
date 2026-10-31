# -*- coding: utf-8 -*-
"""
TEMA OSCURO DE LA VENTANA (estilo DaVinci Resolve)
==================================================

Colores, letras y estilos ttk de la interfaz, y unos pocos componentes que se repiten:
botón principal naranja, botones de elección (4 pistas | 8 pistas), tarjeta con título,
la barra de páginas (arriba, en la cabecera) y el marco que se desplaza cuando la página
no cabe en una pantalla pequeña. Diseño en docs/diseno_interfaz.md.

tkinter no hace esquinas redondeadas, sombras ni degradados: el tema se queda en
superficies planas, bordes finos y un único color de acento.
"""

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

FONDO = "#18191b"
PANEL = "#1e1f22"
TARJETA = "#232427"
CAMPO = "#141517"
BORDE = "#333539"
BORDE_CLARO = "#44474c"
BOTON = "#2d2f33"
BOTON_ENCIMA = "#383b40"
TEXTO = "#e6e7e9"
SUAVE = "#a3a7ad"
ACENTO = "#e8833a"
ACENTO_ENCIMA = "#f3a46b"
SOBRE_ACENTO = "#141517"
AVISO = "#ff8a80"
BIEN = "#6fdc9b"
CLIP = "#3d6fa8"
CABEZAL = "#ff5252"
HECHO = BIEN  # proyectos marcados como HECHOS: en verde
PRELIMINAR = "#4ec3c0"  # el informe preliminar: turquesa con borde, distinto del azul de Elegir proyectos
ONDA = "#8fd3b0"  # la forma de onda del sonido en la línea de tiempo
DETENER = "#9e3434"  # rojo apagado: se ve sin competir con el naranja de la acción principal

LETRA = "Segoe UI"
MONO = "Consolas"


def familia_disponible(raiz, preferidas):
    disponibles = set(tkfont.families(raiz))
    return next((f for f in preferidas if f in disponibles), preferidas[-1])


def aplicar(raiz):
    """Aplica el tema a la ventana y devuelve las letras {"normal", "negrita", "titulo", "seccion", "mono", "tc"}."""
    global LETRA, MONO
    LETRA = familia_disponible(raiz, ["Segoe UI", "DejaVu Sans", "Helvetica", "TkDefaultFont"])
    MONO = familia_disponible(raiz, ["Consolas", "Cascadia Mono", "DejaVu Sans Mono", "Courier"])
    letras = {"normal": (LETRA, 10), "negrita": (LETRA, 10, "bold"), "titulo": (LETRA, 16, "bold"), "grande": (LETRA, 12, "bold"),
              "seccion": (LETRA, 8, "bold"), "pequena": (LETRA, 9), "mono": (MONO, 9), "tc": (MONO, 16)}
    raiz.configure(bg=FONDO)
    raiz.option_add("*Font", letras["normal"])
    raiz.option_add("*TCombobox*Listbox.background", CAMPO)
    raiz.option_add("*TCombobox*Listbox.foreground", TEXTO)
    raiz.option_add("*TCombobox*Listbox.selectBackground", ACENTO)
    raiz.option_add("*TCombobox*Listbox.selectForeground", SOBRE_ACENTO)
    e = ttk.Style(raiz)
    e.theme_use("clam")
    e.configure(".", background=FONDO, foreground=TEXTO, fieldbackground=CAMPO, bordercolor=BORDE, lightcolor=BORDE, darkcolor=BORDE,
                troughcolor=CAMPO, focuscolor=ACENTO, selectbackground=ACENTO, selectforeground=SOBRE_ACENTO, font=letras["normal"],
                insertcolor=TEXTO)
    for nombre, fondo in (("TFrame", FONDO), ("Fondo.TFrame", FONDO), ("Panel.TFrame", PANEL), ("Tarjeta.TFrame", TARJETA),
                          ("Cabecera.TFrame", TARJETA)):
        e.configure(nombre, background=fondo)
    e.configure("Tarjeta.TFrame", bordercolor=BORDE, relief="solid", borderwidth=1)
    for fondo_nombre, fondo in (("", FONDO), ("Panel.", PANEL), ("Tarjeta.", TARJETA)):
        e.configure(fondo_nombre + "TLabel", background=fondo, foreground=TEXTO)
        e.configure(fondo_nombre + "Suave.TLabel", background=fondo, foreground=SUAVE, font=letras["pequena"])
        e.configure(fondo_nombre + "Seccion.TLabel", background=fondo, foreground=SUAVE, font=letras["seccion"])
        e.configure(fondo_nombre + "Titulo.TLabel", background=fondo, foreground=TEXTO, font=letras["titulo"])
        e.configure(fondo_nombre + "Grande.TLabel", background=fondo, foreground=TEXTO, font=letras["grande"])
        e.configure(fondo_nombre + "Aviso.TLabel", background=fondo, foreground=AVISO)
        e.configure(fondo_nombre + "Bien.TLabel", background=fondo, foreground=BIEN)
        e.configure(fondo_nombre + "Acento.TLabel", background=fondo, foreground=ACENTO)
        e.configure(fondo_nombre + "TC.TLabel", background=fondo, foreground=ACENTO, font=letras["tc"])
        e.configure(fondo_nombre + "TCheckbutton", background=fondo, foreground=TEXTO, indicatorbackground=CAMPO,
                    indicatorforeground=ACENTO)
        e.map(fondo_nombre + "TCheckbutton", background=[("active", fondo)], indicatorbackground=[("selected", CAMPO)])
        e.configure(fondo_nombre + "TRadiobutton", background=fondo, foreground=TEXTO, indicatorbackground=CAMPO,
                    indicatorforeground=ACENTO)
        e.map(fondo_nombre + "TRadiobutton", background=[("active", fondo)])
    e.configure("TButton", background=BOTON, foreground=TEXTO, bordercolor=BORDE_CLARO, lightcolor=BOTON, darkcolor=BOTON,
                padding=(12, 6), relief="flat", borderwidth=1)
    e.map("TButton", background=[("disabled", PANEL), ("pressed", BOTON_ENCIMA), ("active", BOTON_ENCIMA)],
          foreground=[("disabled", "#6b6f75")])
    e.configure("Principal.TButton", background=ACENTO, foreground=SOBRE_ACENTO, bordercolor=ACENTO, lightcolor=ACENTO, darkcolor=ACENTO,
                font=(LETRA, 12, "bold"), padding=(26, 10))
    e.map("Principal.TButton", background=[("disabled", "#5a4130"), ("pressed", ACENTO_ENCIMA), ("active", ACENTO_ENCIMA)],
          foreground=[("disabled", "#9a8a7e")])
    e.configure("Fantasma.TButton", background=FONDO, foreground=TEXTO, bordercolor=BORDE_CLARO)
    e.map("Fantasma.TButton", background=[("disabled", FONDO), ("active", BOTON)], foreground=[("disabled", "#6b6f75")])
    e.configure("Elegido.TButton", background=ACENTO, foreground=SOBRE_ACENTO, bordercolor=ACENTO, lightcolor=ACENTO, darkcolor=ACENTO,
                font=letras["negrita"], padding=(18, 6))
    e.map("Elegido.TButton", background=[("disabled", BOTON), ("active", ACENTO_ENCIMA)], foreground=[("disabled", "#6b6f75")],
          bordercolor=[("disabled", BORDE_CLARO)], lightcolor=[("disabled", BOTON)], darkcolor=[("disabled", BOTON)])
    # Botones que tienen que verse sin ser el principal (naranja): Elegir proyectos, en azul; Detener, en rojo
    e.configure("Proyectos.TButton", background=CLIP, foreground=TEXTO, bordercolor=CLIP, lightcolor=CLIP, darkcolor=CLIP,
                font=letras["negrita"], padding=(14, 6))
    e.map("Proyectos.TButton", background=[("disabled", PANEL), ("pressed", "#2f5a8c"), ("active", "#4a80bd")],
          foreground=[("disabled", "#6b6f75")])
    e.configure("Detener.TButton", background=DETENER, foreground=TEXTO, bordercolor=DETENER, lightcolor=DETENER, darkcolor=DETENER,
                font=letras["negrita"], padding=(14, 6))
    e.map("Detener.TButton", background=[("disabled", PANEL), ("pressed", "#7d2626"), ("active", "#b54545")],
          foreground=[("disabled", "#6b6f75")], bordercolor=[("disabled", BORDE)], lightcolor=[("disabled", PANEL)],
          darkcolor=[("disabled", PANEL)])
    # El informe preliminar: va antes de todo y no analiza nada; con borde turquesa y sin relleno, para no confundirlo
    e.configure("Preliminar.TButton", background=FONDO, foreground=PRELIMINAR, bordercolor=PRELIMINAR, lightcolor=FONDO,
                darkcolor=FONDO, font=letras["negrita"], padding=(12, 5))
    e.map("Preliminar.TButton", background=[("disabled", FONDO), ("pressed", "#1d3a3a"), ("active", "#16302f")],
          foreground=[("disabled", "#6b6f75")], bordercolor=[("disabled", BORDE)])
    # Marcar un proyecto como HECHO: verde, como "HECHO" en la lista
    e.configure("Hecho.TButton", background=HECHO, foreground=SOBRE_ACENTO, bordercolor=HECHO, lightcolor=HECHO, darkcolor=HECHO,
                font=letras["negrita"], padding=(14, 6))
    e.map("Hecho.TButton", background=[("disabled", PANEL), ("pressed", "#4fb87a"), ("active", "#8be6b0")],
          foreground=[("disabled", "#6b6f75")], bordercolor=[("disabled", BORDE)], lightcolor=[("disabled", PANEL)],
          darkcolor=[("disabled", PANEL)])
    e.configure("Accion.TButton", padding=(9, 4), width=-6)  # cada uno lo que mide su texto (el tema pide 11 letras)
    # botones de acción en una pantalla pequeña: sin el ancho mínimo del tema (11 letras), cada uno lo que mide su texto
    e.configure("Compacta.TButton", padding=(6, 3), font=letras["pequena"], width=-4)
    e.configure("Fantasma.Compacta.TButton", background=FONDO, foreground=TEXTO, bordercolor=BORDE_CLARO, padding=(6, 3),
                font=letras["pequena"], width=-4)
    e.map("Fantasma.Compacta.TButton", background=[("disabled", FONDO), ("active", BOTON)], foreground=[("disabled", "#6b6f75")])
    e.configure("Opcion.TButton", background=CAMPO, foreground=TEXTO, bordercolor=BORDE_CLARO, padding=(18, 6))
    e.map("Opcion.TButton", background=[("active", BOTON)])
    e.configure("Pagina.TButton", background=TARJETA, foreground=SUAVE, bordercolor=TARJETA, lightcolor=TARJETA, darkcolor=TARJETA,
                padding=(26, 8), font=letras["normal"])
    e.map("Pagina.TButton", background=[("active", BOTON)], foreground=[("active", TEXTO)])
    e.configure("PaginaActiva.TButton", background=BOTON, foreground=TEXTO, bordercolor=BOTON, lightcolor=BOTON, darkcolor=BOTON,
                padding=(26, 8), font=letras["negrita"])
    e.map("PaginaActiva.TButton", background=[("active", BOTON)])
    e.configure("TEntry", fieldbackground=CAMPO, foreground=TEXTO, bordercolor=BORDE_CLARO, padding=6)
    e.map("TEntry", bordercolor=[("focus", ACENTO)])
    e.configure("TCombobox", fieldbackground=CAMPO, background=BOTON, foreground=TEXTO, arrowcolor=TEXTO, bordercolor=BORDE_CLARO, padding=4)
    e.map("TCombobox", fieldbackground=[("readonly", CAMPO)], foreground=[("readonly", TEXTO)], bordercolor=[("focus", ACENTO)])
    e.configure("TSpinbox", fieldbackground=CAMPO, background=BOTON, foreground=TEXTO, arrowcolor=TEXTO, bordercolor=BORDE_CLARO)
    e.map("TSpinbox", fieldbackground=[("disabled", PANEL)], foreground=[("disabled", "#6b6f75")])
    e.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXTO, bordercolor=BORDE, rowheight=26)
    e.map("Treeview", background=[("selected", BOTON_ENCIMA)], foreground=[("selected", TEXTO)])
    e.configure("Treeview.Heading", background=TARJETA, foreground=SUAVE, bordercolor=BORDE, relief="flat", font=letras["seccion"])
    e.map("Treeview.Heading", background=[("active", BOTON)])
    e.configure("Horizontal.TProgressbar", background=ACENTO, troughcolor=BOTON, bordercolor=BOTON, lightcolor=ACENTO, darkcolor=ACENTO)
    for o in ("Vertical", "Horizontal"):
        e.configure(o + ".TScrollbar", background=BOTON, troughcolor=PANEL, bordercolor=PANEL, arrowcolor=SUAVE, lightcolor=BOTON,
                    darkcolor=BOTON)
    e.configure("TLabelframe", background=PANEL, bordercolor=BORDE)
    e.configure("TLabelframe.Label", background=PANEL, foreground=SUAVE, font=letras["seccion"])
    e.configure("TSeparator", background=BORDE)
    return letras


def colores_tk(fondo=CAMPO):
    """Colores para los widgets clásicos de tk (Listbox, Text, Canvas), que ttk no estiliza."""
    return {"bg": fondo, "fg": TEXTO, "highlightthickness": 1, "highlightbackground": BORDE, "highlightcolor": ACENTO,
            "selectbackground": BOTON_ENCIMA, "selectforeground": TEXTO, "relief": "flat", "borderwidth": 0}


def lista(padre, **kw):
    """Listbox con el tema."""
    opciones = colores_tk(PANEL)
    opciones.update(activestyle="none", exportselection=False)
    opciones.update(kw)
    return tk.Listbox(padre, **opciones)


def texto(padre, **kw):
    """Text con el tema (registro)."""
    opciones = colores_tk(CAMPO)
    opciones.update(insertbackground=TEXTO, padx=10, pady=8, font=(MONO, 9), wrap="word")
    opciones.update(kw)
    opciones.pop("activestyle", None)
    return tk.Text(padre, **opciones)


class Tarjeta(ttk.Frame):
    """Marco con borde y un título pequeño en mayúsculas."""

    def __init__(self, padre, titulo=None, estilo="Tarjeta", **kw):
        super().__init__(padre, style=estilo + ".TFrame", padding=14, **kw)
        self.prefijo = estilo + "."
        if titulo:
            ttk.Label(self, text=titulo.upper(), style=self.prefijo + "Seccion.TLabel").pack(anchor="w", pady=(0, 8))


class Seleccion(ttk.Frame):
    """Botones de elección pegados (4 pistas | 8 pistas): el elegido en naranja. variable: tk.Variable con el valor."""

    def __init__(self, padre, opciones, variable, al_cambiar=None, **kw):
        super().__init__(padre, style=kw.pop("estilo", "Panel") + ".TFrame", **kw)
        self.variable, self.al_cambiar, self.botones = variable, al_cambiar, {}
        for valor, etiqueta in opciones:
            b = ttk.Button(self, text=etiqueta, command=lambda v=valor: self.elegir(v))
            b.pack(side="left")
            self.botones[valor] = b
        self.pintar()

    def elegir(self, valor):
        self.variable.set(valor)
        self.pintar()
        if self.al_cambiar:
            self.al_cambiar()

    def pintar(self):
        actual = self.variable.get()
        for valor, b in self.botones.items():
            b.configure(style="Elegido.TButton" if str(valor) == str(actual) else "Opcion.TButton")


ICONOS = {  # trazos para la barra de páginas (líneas sobre un lienzo de 24x24)
    "disco": [("rect", 3, 6, 21, 18), ("oval", 16, 11, 18, 13)],
    "montaje": [("rect", 2, 8, 9, 16), ("rect", 10, 8, 15, 16), ("rect", 16, 8, 22, 16), ("line", 12, 4, 12, 20)],
    "exportar": [("line", 12, 3, 12, 15), ("line", 7, 10, 12, 15), ("line", 17, 10, 12, 15), ("line", 4, 19, 20, 19)],
}


class BarraPaginas(tk.Frame):
    """Pestañas de las páginas (1 · Disco, 2 · Montaje, 3 · Exportar) en la cabecera: icono y nombre en una fila, la
    activa con raya naranja debajo. Arriba siempre se ven: abajo se salían de una pantalla de portátil (26/10/2026)."""

    def __init__(self, padre, paginas, al_cambiar):
        super().__init__(padre, bg=TARJETA, highlightthickness=0)
        self.al_cambiar, self.items = al_cambiar, {}
        for clave, nombre, icono in paginas:
            caja = tk.Frame(self, bg=TARJETA, cursor="hand2")
            caja.pack(side="left", padx=1)
            raya = tk.Frame(caja, bg=TARJETA, height=3)
            raya.pack(fill="x", side="bottom")
            dibujo = tk.Canvas(caja, width=24, height=24, bg=TARJETA, highlightthickness=0)
            dibujo.pack(side="left", padx=(14, 6), pady=6)
            etiqueta = tk.Label(caja, text=nombre, bg=TARJETA, fg=SUAVE, font=(LETRA, 10))
            etiqueta.pack(side="left", padx=(0, 16), pady=6)
            for w in (caja, dibujo, etiqueta):
                w.bind("<Button-1>", lambda e, c=clave: self.al_cambiar(c))
            self.items[clave] = (caja, raya, dibujo, etiqueta, icono)
        self.activa = None

    def marcar(self, clave):
        self.activa = clave
        for c, (caja, raya, dibujo, etiqueta, icono) in self.items.items():
            on = c == clave
            fondo = BOTON if on else TARJETA
            color = TEXTO if on else SUAVE
            for w in (caja, dibujo, etiqueta):
                w.configure(bg=fondo)
            raya.configure(bg=ACENTO if on else TARJETA)
            etiqueta.configure(fg=color, font=(LETRA, 10, "bold" if on else "normal"))
            dibujo.delete("all")
            for forma, *xy in ICONOS[icono]:
                if forma == "rect":
                    dibujo.create_rectangle(*xy, outline=color, width=1.6)
                elif forma == "oval":
                    dibujo.create_oval(*xy, outline=color, width=1.6)
                else:
                    dibujo.create_line(*xy, fill=color, width=1.6)


def pantalla_pequena(raiz):
    """¿Pantalla de portátil? Con Windows ampliando al 125-150 %, un 1366x768 o un 1920x1080 se quedan en ~1100x600 o
    1280x720 útiles: la ventana va en modo compacto (visor y listas más pequeños, columnas más estrechas)."""
    return raiz.winfo_screenwidth() < 1300 or raiz.winfo_screenheight() < 800


class Desplazable(ttk.Frame):
    """Marco para una página entera: si su contenido cabe, lo ocupa todo como siempre (lo que se estira, se estira); si
    no cabe (pantalla pequeña), salen barras de desplazamiento y la rueda del ratón lo mueve. Así nunca queda nada
    fuera de la vista. El contenido va en .interior."""

    def __init__(self, padre, estilo="Fondo.TFrame"):
        super().__init__(padre, style=estilo)
        self.lienzo = tk.Canvas(self, bg=FONDO, highlightthickness=0, borderwidth=0)
        self.barra_v = ttk.Scrollbar(self, orient="vertical", command=self.lienzo.yview)
        self.barra_h = ttk.Scrollbar(self, orient="horizontal", command=self.lienzo.xview)
        self.lienzo.configure(yscrollcommand=self.barra_v.set, xscrollcommand=self.barra_h.set)
        self.lienzo.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.interior = ttk.Frame(self.lienzo, style=estilo)
        self.ventana = self.lienzo.create_window(0, 0, window=self.interior, anchor="nw")
        self.medidas = None
        self.lienzo.bind("<Configure>", lambda e: self.ajustar())
        self.vigilar()

    def ajustar(self):
        an, al = self.lienzo.winfo_width(), self.lienzo.winfo_height()
        pa, pal = self.interior.winfo_reqwidth(), self.interior.winfo_reqheight()
        medidas = (an, al, pa, pal)
        if medidas == self.medidas or an <= 1:
            return
        self.medidas = medidas
        w, h = max(an, pa), max(al, pal)
        self.lienzo.itemconfigure(self.ventana, width=w, height=h)
        self.lienzo.configure(scrollregion=(0, 0, w, h))
        for barra, sobra, sitio in ((self.barra_h, pa > an, dict(row=1, column=0, sticky="ew")),
                                    (self.barra_v, pal > al, dict(row=0, column=1, sticky="ns"))):
            if sobra and not barra.winfo_ismapped():
                barra.grid(**sitio)
            elif not sobra and barra.winfo_ismapped():
                barra.grid_remove()
        if pal <= al:
            self.lienzo.yview_moveto(0)
        if pa <= an:
            self.lienzo.xview_moveto(0)

    def vigilar(self):
        """El tamaño que pide el contenido cambia sin avisar (se abre "Opciones avanzadas", llega el montaje): se mira
        cada poco y se ajusta."""
        try:
            self.ajustar()
            self.after(300, self.vigilar)
        except tk.TclError:  # la ventana se ha cerrado
            pass

    def desplazable(self):
        return self.barra_v.winfo_ismapped()


def rueda_para_paginas(raiz, pagina_actual):
    """La rueda del ratón mueve la página (Desplazable) que se ve, salvo encima de algo que ya se desplaza solo (listas,
    tablas, el registro). pagina_actual(): el Desplazable de la página que se ve, o None."""
    propios = ("Listbox", "Treeview", "Text", "TCombobox", "Scrollbar", "TScrollbar")

    def mover(evento, pasos):
        d = pagina_actual()
        if d is None or not d.desplazable():
            return
        w = evento.widget
        try:
            while w is not None:
                if w.winfo_class() in propios:
                    return
                if w is d:
                    break
                w = w.master
        except (AttributeError, tk.TclError):
            return
        if w is d:
            d.lienzo.yview_scroll(pasos, "units")

    raiz.bind_all("<MouseWheel>", lambda e: mover(e, -1 if e.delta > 0 else 1), add="+")
    raiz.bind_all("<Button-4>", lambda e: mover(e, -1), add="+")  # Linux
    raiz.bind_all("<Button-5>", lambda e: mover(e, 1), add="+")
