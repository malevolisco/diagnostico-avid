# -*- coding: utf-8 -*-
"""
TEMA OSCURO DE LA VENTANA (estilo DaVinci Resolve)
==================================================

Colores, letras y estilos ttk de la interfaz, y unos pocos componentes que se repiten:
botón principal naranja, botones de elección (4 pistas | 8 pistas), tarjeta con título y
la barra de páginas de abajo. Diseño en docs/diseno_interfaz.md.

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
    e.configure("Accion.TButton", padding=(9, 4))
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
    """Barra de páginas de abajo, como la de Resolve: icono y nombre; la activa con raya naranja arriba."""

    def __init__(self, padre, paginas, al_cambiar):
        super().__init__(padre, bg=TARJETA, highlightthickness=0)
        self.al_cambiar, self.items = al_cambiar, {}
        tk.Frame(self, bg=BORDE, height=1).pack(fill="x", side="top")
        fila = tk.Frame(self, bg=TARJETA)
        fila.pack(pady=4)
        for clave, nombre, icono in paginas:
            caja = tk.Frame(fila, bg=TARJETA, cursor="hand2")
            caja.pack(side="left", padx=2)
            raya = tk.Frame(caja, bg=TARJETA, height=3)
            raya.pack(fill="x")
            dibujo = tk.Canvas(caja, width=24, height=24, bg=TARJETA, highlightthickness=0)
            dibujo.pack(pady=(6, 2), padx=50)
            etiqueta = tk.Label(caja, text=nombre, bg=TARJETA, fg=SUAVE, font=(LETRA, 10))
            etiqueta.pack(pady=(0, 6))
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
