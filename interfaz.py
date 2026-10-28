# -*- coding: utf-8 -*-
"""
VENTANA DEL PROGRAMA: TRES PÁGINAS AL ESTILO DE DAVINCI RESOLVE
===============================================================

Una sola ventana oscura con una barra de páginas abajo: 1 · Disco (carpetas, proyectos y
análisis), 2 · Montaje (ventana_montaje.PantallaMontaje) y 3 · Exportar (ajustes, cola de
brutos con su avance, espacio y tiempo). Diseño en docs/diseno_interfaz.md.

Todo lo que no es ventana (análisis, montaje, exportación) está en diagnostico_avid.py,
montaje.py y exportar_brutos.py. A lanzar() se le pasa el módulo diagnostico_avid ya
cargado (da) para no importarlo dos veces cuando se ejecuta como programa principal.

Hilos: el análisis y la exportación van en un hilo aparte; sus mensajes y su avance pasan
por una cola que lee la ventana cada 200 ms. Nada toca tkinter fuera del hilo principal.
"""

import json
import queue
import threading
import time
import traceback
import webbrowser
from pathlib import Path

import tema
import ventana_montaje as vm
import ventana_origen as vo

PAGINAS = [("disco", "1 · Disco", "disco"), ("montaje", "2 · Montaje", "montaje"), ("exportar", "3 · Exportar", "exportar")]
ESTADOS = {"empezando": "Empezando", "uniendo": "Uniendo los clips", "comprobando": "Comprobando (ficha y MD5)"}


def texto_estado(ev):
    """Lo que dice la columna Estado de la cola para un evento de avance de la exportación."""
    if ev["estado"] == "convirtiendo":
        total = ev.get("total") or 1
        return "Convirtiendo · clip %d de %d (%d %%)" % (ev.get("clip", 0), total, 100 * ev.get("clip", 0) // total)
    if ev["estado"] in ("terminado", "reanudado"):
        res = ev.get("resultado") or {}
        estado = str(res.get("estado", ""))
        if ev["estado"] == "reanudado":
            return "Ya exportado (se salta)"
        return "Terminado" if estado.startswith("OK") else estado[:60] or "Terminado"
    return ESTADOS.get(ev["estado"], ev["estado"])


def gb(n_bytes):
    return "%.1f GB" % (n_bytes / 1e9) if n_bytes < 10e9 else "%.0f GB" % (n_bytes / 1e9)


def texto_calidad(res):
    """Columna Comprobación: "Conforme · MD5", "NO CONFORME" o "—"."""
    if not res:
        return "—"
    if res.get("conforme") is False:
        return "NO CONFORME"
    partes = (["Conforme"] if res.get("conforme") else []) + (["MD5"] if res.get("md5") else [])
    return " · ".join(partes) or ("Hecho" if str(res.get("estado", "")).startswith("OK") else "—")


def resumen_disco(lista):
    """'7 proyectos · 11 bins · último cambio 15/09/2021' a partir de listar_proyectos."""
    if not lista:
        return "No hay proyectos de Avid en esta carpeta (ningún .avp ni .avb)."
    proyectos = [p for p in lista if p["nombre"] != "(bins sin proyecto)"]
    sueltos = sum(p["bins"] for p in lista if p["nombre"] == "(bins sin proyecto)")
    bins = sum(p["bins"] for p in proyectos)
    partes = ["%d proyecto%s" % (len(proyectos), "" if len(proyectos) == 1 else "s"), "%d bin%s" % (bins, "" if bins == 1 else "s")]
    if sueltos:
        partes.append("%d bin%s sin proyecto" % (sueltos, "" if sueltos == 1 else "s"))
    fechas = sorted(p["modificado"] for p in lista if p.get("modificado"))
    if fechas:
        partes.append("último cambio %s/%s/%s" % (fechas[-1][8:10], fechas[-1][5:7], fechas[-1][:4]))
    return " · ".join(partes)


class App:
    def __init__(self, da):
        import tkinter as tk
        from tkinter import ttk
        self.da, self.tk, self.ttk = da, tk, ttk
        raiz = self.raiz = tk.Tk()
        raiz.title("DiagnosticoAvid — v%s (%s)" % (da.VERSION, da.BUILD))
        # La ventana cabe en la pantalla: en un portátil con Windows ampliando al 125-150 % (≈1100x600 útiles) se abre
        # maximizada y en modo compacto; antes medía 1280x800 y las pestañas de abajo quedaban fuera (26/10/2026).
        self.compacto = tema.pantalla_pequena(raiz)
        an, al = raiz.winfo_screenwidth(), raiz.winfo_screenheight()
        raiz.geometry("%dx%d+0+0" % (min(1280, an - 16), min(800, al - 90)))
        raiz.minsize(min(900, an - 16), min(520, al - 90))
        if self.compacto:
            try:
                raiz.state("zoomed")  # Windows; en Linux no existe y se queda del tamaño de la pantalla
            except tk.TclError:
                pass
        self.letras = tema.aplicar(raiz)

        self.origen = tk.StringVar()
        self.destino = tk.StringVar(value=str(Path.home() / "Desktop"))
        self.brutos = tk.StringVar()
        self.minis = tk.BooleanVar(value=True)
        self.zip_minis = tk.BooleanVar(value=True)
        self.capturas = tk.BooleanVar(value=True)
        self.seguido = tk.BooleanVar(value=False)
        self.cola = queue.Queue()
        self.estado = {"ocupado": None, "cerrar": False, "proyectos": None, "media_extra": []}  # ocupado: None | "analisis" | "exportacion" | "actualizacion"
        self.pagina = None
        self.montaje = None  # PantallaMontaje
        self.pantalla_origen = None  # PantallaOrigen (pestaña "Origen del material" del montaje)
        self.pestana_montaje = "brutos"
        self.clave_montaje = None
        self.exp = None  # datos de la página Exportar: clave, resumen, cfg, marcados, estados, resultados
        # Proyectos del disco elegido, leídos solos al elegirlo (listar_proyectos): el botón "Proyectos…" los reutiliza
        self.vista_disco = {"origen": None, "lista": None, "turno": 0}
        self.espera_disco = None
        self.cronometro = None

        self.cabecera()
        self.cuerpo = ttk.Frame(raiz, style="Fondo.TFrame")
        self.cuerpo.pack(fill="both", expand=True)
        # Cada página va en un Desplazable: si no cabe en la pantalla, se desplaza en vez de quedar cortada
        self.contenedores = {c: tema.Desplazable(self.cuerpo) for c, _, _ in PAGINAS}
        self.paginas = {c: d.interior for c, d in self.contenedores.items()}
        tema.rueda_para_paginas(raiz, lambda: self.contenedores.get(self.pagina))
        self.pagina_disco()
        self.pagina_exportar()
        self.origen.trace_add("write", lambda *a: self.cambio_carpetas(proyectos=True))
        self.destino.trace_add("write", lambda *a: self.cambio_carpetas())
        self.brutos.trace_add("write", lambda *a: self.cambio_carpetas())
        raiz.protocol("WM_DELETE_WINDOW", self.al_cerrar)
        self.escribe("Programa de diagnóstico de discos Avid (solo lectura). pyavb: %s. ffmpeg: %s."
                     % ("OK" if da.PYAVB_OK else "NO INSTALADO", "OK" if da.localizar_ffmpeg()[0] else "no encontrado"))
        self.ir("disco")
        raiz.after(200, self.vaciar_cola)

    # --- estructura ------------------------------------------------------------------------------------------------

    def cabecera(self):
        ttk, da = self.ttk, self.da
        cab = ttk.Frame(self.raiz, style="Cabecera.TFrame", padding=(16, 0))
        cab.pack(side="top", fill="x")
        ttk.Label(cab, text="DiagnosticoAvid", style="Tarjeta.Grande.TLabel").pack(side="left", pady=8)
        # Las páginas, arriba y siempre a la vista (antes, abajo: en un portátil se salían de la pantalla)
        self.barra_paginas = tema.BarraPaginas(cab, PAGINAS, self.ir)
        self.barra_paginas.pack(side="left", padx=(18, 0), fill="y")
        if not self.compacto:
            ttk.Label(cab, text="%s · solo lectura del disco de origen" % da.BUILD, style="Tarjeta.Suave.TLabel").pack(side="left", padx=14)
        # Sin actualización automática al abrir (decisión de Adrián para la revisión de Ingeniería, oct. 2026): solo se
        # busca una versión nueva si alguien pulsa este botón (aparece solo en el .exe de Windows).
        self.bt_act = ttk.Button(cab, text="Buscar actualización", style="Fantasma.TButton", command=self.buscar_actualizacion)
        if da.puede_actualizarse():
            self.bt_act.pack(side="right")
        self.lbl_trabajo = ttk.Label(cab, text="", style="Tarjeta.Acento.TLabel")
        self.lbl_trabajo.pack(side="right", padx=14)
        self.tk.Frame(self.raiz, bg=tema.BORDE, height=1).pack(side="top", fill="x")

    def ir(self, clave):
        """Cambia de página. Si se sale del montaje con cambios sin guardar, pregunta."""
        from tkinter import messagebox
        if clave == self.pagina:
            return
        if self.pagina == "montaje" and self.pantalla_origen and self.pantalla_origen.cambios:
            r = messagebox.askyesnocancel("Origen sin guardar", "Hay cambios en el origen del material sin guardar. ¿Guardarlos?\n\n"
                                          "Sí: se guardan. No: se descartan. Cancelar: sigues en el montaje.", parent=self.raiz)
            if r is None:
                return
            if r:
                self.pantalla_origen.guardar()  # el montaje se rehace en cuanto pueda (origen_guardado)
            else:
                self.cerrar_montaje()
        if self.pagina == "montaje" and self.montaje and self.montaje.cambios:
            r = messagebox.askyesnocancel("Montaje sin guardar", "Hay cambios en el montaje sin guardar. ¿Guardarlos?\n\n"
                                          "Sí: se guardan. No: se descartan. Cancelar: sigues en el montaje.", parent=self.raiz)
            if r is None:
                return
            if r:
                self.montaje.guardar()
            else:
                self.cerrar_montaje()
        if self.pagina:
            self.contenedores[self.pagina].pack_forget()
        self.pagina = clave
        self.contenedores[clave].pack(fill="both", expand=True)
        self.barra_paginas.marcar(clave)
        if clave == "montaje":
            self.preparar_montaje()
        elif clave == "exportar":
            self.preparar_exportar()
        elif clave == "disco":
            self.pintar_ultimo()

    def ocupar(self, que):
        """que: None (libre), "analisis", "exportacion" o "actualizacion"."""
        self.estado["ocupado"] = que
        textos = {"analisis": "Trabajando… analizando el disco", "exportacion": "Trabajando… exportando brutos",
                  "actualizacion": "Buscando actualización…"}
        self.lbl_trabajo.configure(text=textos.get(que, ""))
        libre = ["!disabled"] if que is None else ["disabled"]
        for b in (self.bt_analizar, self.bt_proy, self.bt_act):
            b.state(libre)
        self.bt_parar.state(["!disabled"] if que == "analisis" else ["disabled"])
        self.bt_parar_exp.state(["!disabled"] if que == "exportacion" else ["disabled"])
        if que in ("analisis", "exportacion"):
            self.barra.start(12)
        else:
            self.barra.stop()
        if self.montaje:
            self.montaje.bloquear(que is not None)
        if self.pantalla_origen:
            self.pantalla_origen.bloquear(que is not None)
        self.revisar_exportar()

    def escribe(self, txt):
        self.registro.configure(state="normal")
        self.registro.insert("end", txt + "\n")
        self.registro.see("end")
        self.registro.configure(state="disabled")

    def elegir_carpeta(self, var, titulo):
        from tkinter import filedialog
        if self.estado["ocupado"]:
            return
        var.set(filedialog.askdirectory(title=titulo, parent=self.raiz) or var.get())

    def cambio_carpetas(self, proyectos=False):
        if proyectos:
            self.estado["proyectos"] = None
            self.pintar_proyectos()
            self.programar_vista_disco()
        if self.pagina == "disco":
            self.pintar_ultimo()

    # --- página 1 · Disco ------------------------------------------------------------------------------------------

    def pagina_disco(self):
        ttk = self.ttk
        p = self.paginas["disco"]
        der = ttk.Frame(p, style="Panel.TFrame", padding=(12, 12) if self.compacto else (18, 18))
        der.pack(side="right", fill="y")
        izq = ttk.Frame(p, style="Fondo.TFrame", padding=(14, 10) if self.compacto else (22, 18))
        izq.pack(side="left", fill="both", expand=True)

        t = tema.Tarjeta(izq)
        t.pack(fill="x")
        ttk.Label(t, text="CARPETAS", style="Tarjeta.Seccion.TLabel").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 6))
        for fila, (texto, var, titulo, ayuda) in enumerate((
                ("Disco de Avid", self.origen, "Disco de Avid", "solo se lee, no se modifica"),
                ("Informe", self.destino, "Carpeta de salida", "otro disco o el escritorio"),
                ("Carpeta de brutos", self.brutos, "Carpeta para los brutos", "un disco con espacio"))):
            ttk.Label(t, text=texto, style="Tarjeta.TLabel", width=16 if self.compacto else 18).grid(row=fila + 1, column=0, sticky="w", pady=4)
            ttk.Entry(t, textvariable=var, width=12 if self.compacto else 20).grid(row=fila + 1, column=1, sticky="ew", pady=4)
            ttk.Button(t, text="Elegir…", command=lambda v=var, tt=titulo: self.elegir_carpeta(v, tt)).grid(row=fila + 1, column=2, padx=(8, 0))
            if not self.compacto:  # en una pantalla pequeña no caben; lo mismo lo dice el título del diálogo
                ttk.Label(t, text=ayuda, style="Tarjeta.Suave.TLabel").grid(row=fila + 1, column=3, sticky="w", padx=(10, 0))
        fila_extra = len(("Disco de Avid", "Informe", "Carpeta de brutos")) + 1
        ttk.Label(t, text="Más media", style="Tarjeta.TLabel", width=18).grid(row=fila_extra, column=0, sticky="w", pady=4)
        self.lbl_extra = ttk.Label(t, text="", style="Tarjeta.Suave.TLabel")
        self.lbl_extra.grid(row=fila_extra, column=1, sticky="w", pady=4)
        caja = ttk.Frame(t, style="Cabecera.TFrame")  # mismo fondo que la tarjeta, sin borde
        caja.grid(row=fila_extra, column=2, columnspan=2, sticky="w", padx=(8, 0))
        ttk.Button(caja, text="Añadir…", command=self.anadir_media).pack(side="left")
        self.bt_quitar_extra = ttk.Button(caja, text="Quitar", style="Fantasma.TButton", command=self.quitar_media)
        self.bt_quitar_extra.pack(side="left", padx=(6, 0))
        self.pintar_media_extra()
        t.columnconfigure(1, weight=1)

        self.lbl_disco = ttk.Label(izq, text="", style="TLabel")
        self.lbl_disco.pack(anchor="w", pady=(12, 0))

        fila = ttk.Frame(izq, style="Fondo.TFrame")
        fila.pack(fill="x", pady=(10, 0))
        self.bt_proy = ttk.Button(fila, text="Proyectos: todos…", command=self.elegir_proyectos)
        self.bt_proy.pack(side="left")
        self.lbl_proy = ttk.Label(fila, text="", style="Suave.TLabel")
        self.lbl_proy.pack(side="left", padx=12)

        fila = ttk.Frame(izq, style="Fondo.TFrame")
        fila.pack(fill="x", pady=(16, 0))
        self.bt_analizar = ttk.Button(fila, text="Analizar disco", style="Principal.TButton", command=self.iniciar)
        self.bt_analizar.pack(side="left")
        self.bt_parar = ttk.Button(fila, text="Detener", style="Fantasma.TButton", command=self.detener)
        self.bt_parar.pack(side="left", padx=10)
        self.bt_avanzadas = ttk.Button(fila, text="Opciones avanzadas ▸", style="Fantasma.TButton", command=self.plegar)
        self.bt_avanzadas.pack(side="right")
        self.avanzadas = ttk.Frame(izq, style="Panel.TFrame", padding=(14, 10))
        for texto, var in (("Miniaturas de cada clip para el informe (más lento; el visor del montaje no las necesita)", self.minis),
                           ("Sin miniaturas, al menos una captura de cada clip de cámara para decidir qué proyectos restaurar", self.capturas),
                           ("Incluir las miniaturas en el zip para enviar (desmarcado: el zip solo lleva texto)", self.zip_minis),
                           ("Analizar y exportar seguido: exporta los brutos sin pasar por Montaje ni Exportar", self.seguido)):
            ttk.Checkbutton(self.avanzadas, text=texto, variable=var, style="Panel.TCheckbutton").pack(anchor="w", pady=1)

        self.barra = ttk.Progressbar(izq, mode="indeterminate")
        self.barra.pack(fill="x", pady=(16, 0))
        self.registro = tema.texto(izq, height=5 if self.compacto else 12, state="disabled")
        self.registro.pack(fill="both", expand=True, pady=(8, 0))

        ancho = 210 if self.compacto else 300  # columna de la derecha, más estrecha en una pantalla pequeña
        t = tema.Tarjeta(der, "Último análisis" if self.compacto else "Último análisis de este disco", width=ancho + 30)
        t.pack(fill="x")
        self.lbl_ultimo = ttk.Label(t, text="", style="Tarjeta.TLabel", wraplength=ancho, justify="left")
        self.lbl_ultimo.pack(anchor="w")
        self.bt_informe = ttk.Button(t, text="Abrir el informe", command=self.abrir_informe)
        self.bt_informe.pack(anchor="w", pady=(10, 0))
        self.bt_triaje = ttk.Button(t, text="Qué proyectos restaurar", command=self.abrir_triaje)
        self.bt_triaje.pack(anchor="w", pady=(6, 0))
        t = tema.Tarjeta(der, "Siguiente paso")
        t.pack(fill="x", pady=(14, 0))
        self.lbl_siguiente = ttk.Label(t, text="", style="Tarjeta.TLabel", wraplength=ancho, justify="left")
        self.lbl_siguiente.pack(anchor="w")
        self.bt_siguiente = ttk.Button(t, text="Ir a Montaje →", style="Elegido.TButton", command=lambda: self.ir("montaje"))
        self.bt_siguiente.pack(anchor="w", pady=(10, 0))
        self.pintar_proyectos()

    def pintar_media_extra(self):
        extra = self.estado["media_extra"]
        self.lbl_extra.configure(text="; ".join(extra) if extra else "opcional" if self.compacto else "opcional: media en otros discos")
        self.bt_quitar_extra.state(["!disabled"] if extra else ["disabled"])

    def anadir_media(self):
        from tkinter import filedialog
        if self.estado["ocupado"]:
            return
        carpeta = filedialog.askdirectory(title="Otra carpeta con media (solo se lee)", parent=self.raiz)
        if carpeta and carpeta not in self.estado["media_extra"]:
            self.estado["media_extra"].append(carpeta)
            self.pintar_media_extra()

    def quitar_media(self):
        if not self.estado["ocupado"] and self.estado["media_extra"]:
            self.estado["media_extra"].pop()
            self.pintar_media_extra()

    def plegar(self):
        if self.avanzadas.winfo_ismapped():
            self.avanzadas.pack_forget()
            self.bt_avanzadas.configure(text="Opciones avanzadas ▸")
        else:
            self.avanzadas.pack(fill="x", pady=(12, 0), before=self.barra)
            self.bt_avanzadas.configure(text="Opciones avanzadas ▾")

    def programar_vista_disco(self):
        """Medio segundo después de cambiar la carpeta del disco (para no leer a cada letra si se escribe a mano)."""
        if self.espera_disco:
            self.raiz.after_cancel(self.espera_disco)
        self.espera_disco = self.raiz.after(500, self.leer_vista_disco)

    def leer_vista_disco(self):
        """Lee los proyectos del disco por detrás (solo nombres de .avp y .avb: segundos) y los resume en la página Disco."""
        self.espera_disco = None
        origen = self.origen.get()
        self.vista_disco.update(origen=None, lista=None, turno=self.vista_disco["turno"] + 1)
        if not origen:
            self.lbl_disco.configure(text="", style="TLabel")
            return
        if not Path(origen).is_dir():
            self.lbl_disco.configure(text="Esa carpeta no existe o no se puede abrir.", style="Aviso.TLabel")
            return
        self.lbl_disco.configure(text="Leyendo los proyectos del disco…", style="Suave.TLabel")
        turno, da = self.vista_disco["turno"], self.da

        def leer():
            try:
                self.cola.put(("vista_disco", (turno, origen, da.listar_proyectos(origen), None)))
            except Exception as e:
                self.cola.put(("vista_disco", (turno, origen, None, str(e))))
        threading.Thread(target=leer, daemon=True).start()

    def al_leer_disco(self, turno, origen, lista, error):
        if turno != self.vista_disco["turno"]:  # se cambió de disco mientras se leía: vale la lectura nueva
            return
        if error is not None:
            self.lbl_disco.configure(text="No se han podido leer los proyectos: %s" % error, style="Aviso.TLabel")
            return
        self.vista_disco.update(origen=origen, lista=lista)
        self.lbl_disco.configure(text=resumen_disco(lista), style="TLabel" if lista else "Aviso.TLabel")

    def pintar_proyectos(self):
        p = self.estado["proyectos"]
        if p is None:
            self.bt_proy.configure(text="Proyectos: todos…")
            self.lbl_proy.configure(text="Se analizan todos los proyectos del disco.")
        else:
            self.bt_proy.configure(text="Proyectos: %d de %d…" % (len(p), self.estado.get("total_proyectos", len(p))))
            texto = "Solo " + ", ".join(p)
            self.lbl_proy.configure(text=texto[:90] + ("…" if len(texto) > 90 else ""))

    def analisis_actual(self):
        """Carpeta del último análisis del disco elegido en la carpeta del informe, o None."""
        o, d = self.origen.get(), self.destino.get()
        if not o or not d or not Path(d).exists():
            return None
        try:
            return self.da.buscar_analisis(d, o)
        except OSError:
            return None

    def pintar_ultimo(self):
        carpeta = self.analisis_actual()
        self.ultimo = carpeta
        if not carpeta:
            self.lbl_ultimo.configure(text="Todavía no hay análisis de este disco en la carpeta del informe."
                                      if self.origen.get() else "Elige el disco de Avid.")
            self.bt_informe.state(["disabled"])
            self.bt_triaje.state(["disabled"])
            self.lbl_siguiente.configure(text="Pulsa \"Analizar disco\". Tarda de minutos a horas según el disco; se puede detener.")
            self.bt_siguiente.state(["disabled"])
            return
        try:
            r = json.loads((carpeta / "resumen.json").read_text(encoding="utf-8"))
        except Exception:
            r = {}
        horas = vm.fmt_h(r.get("segundos_media") or 0)
        parcial = r.get("parcial")
        solo = ("Solo %d de %d proyectos: %s\n" % (len(parcial["proyectos"]), parcial["proyectos_disco"], ", ".join(parcial["proyectos"]))
                if parcial else "")
        tri = r.get("triaje")
        tri = ("\nPara restaurar %d · poco material %d · sin cámara %d" % (tri.get("restaurar", 0), tri.get("poco", 0), tri.get("sin", 0))
               if tri is not None else "")
        self.lbl_ultimo.configure(text="%s\n\n%s%s proyectos · %s clips · %s de material\n%s GB de media · %s bins (%s ilegibles)%s\nCarpeta: %s"
                                  % (r.get("fecha", "?"), solo[:200], r.get("proyectos", "?"), r.get("clips_fisicos", "?"), horas,
                                     r.get("gb_media", "?"), r.get("bins", "?"), r.get("bins_ilegibles", 0), tri, carpeta.name))
        self.bt_informe.state(["!disabled"])
        self.bt_triaje.state(["!disabled"] if (carpeta / "proyectos.html").exists() else ["disabled"])
        if self.brutos.get():
            self.lbl_siguiente.configure(text="Mira \"Qué proyectos restaurar\" y marca en Proyectos… los que sí. Luego, "
                                      "revisa los brutos en Montaje (opcional) y expórtalos en Exportar."
                                      if (carpeta / "proyectos.html").exists() else
                                      "Revisa los brutos en Montaje (opcional) y expórtalos en Exportar.")
            self.bt_siguiente.state(["!disabled"])
        else:
            self.lbl_siguiente.configure(text="Elige la carpeta de brutos para seguir con Montaje y Exportar.")
            self.bt_siguiente.state(["disabled"])

    def abrir_informe(self):
        if self.ultimo and (self.ultimo / "informe.html").exists():
            webbrowser.open((self.ultimo / "informe.html").resolve().as_uri())

    def abrir_triaje(self):
        if self.ultimo and (self.ultimo / "proyectos.html").exists():
            webbrowser.open((self.ultimo / "proyectos.html").resolve().as_uri())

    def triaje_actual(self):
        """{proyecto: fila de triaje.json} del último análisis de este disco (qué proyectos restaurar), o {}."""
        carpeta = self.analisis_actual()
        try:
            return {t["proyecto"]: t for t in json.loads((carpeta / "triaje.json").read_text(encoding="utf-8"))} if carpeta else {}
        except (OSError, ValueError, KeyError, TypeError):
            return {}

    def elegir_proyectos(self):
        from tkinter import messagebox
        da = self.da
        if self.estado["ocupado"]:
            return
        if not self.origen.get() or not Path(self.origen.get()).exists():
            return messagebox.showwarning("Falta el disco", "Elige primero el disco o la carpeta de Avid.", parent=self.raiz)
        lista = self.vista_disco["lista"] if self.vista_disco["origen"] == self.origen.get() else None
        if lista is None:  # aún no se ha leído (o se está leyendo): se lee ahora
            self.raiz.configure(cursor="watch")
            self.raiz.update()
            try:
                lista = da.listar_proyectos(self.origen.get())
            finally:
                self.raiz.configure(cursor="")
        hechos = da.brutos_hechos(self.brutos.get()) if self.brutos.get() else set()
        elegidos = da.dialogo_proyectos(self.raiz, lista, self.estado["proyectos"], hechos, self.triaje_actual())
        if elegidos is None:
            return
        todos = [p["nombre"] for p in lista] + ["SIN_PROYECTO"]
        self.estado["total_proyectos"] = len(todos)
        self.estado["proyectos"] = None if set(elegidos) >= set(todos) else elegidos
        self.pintar_proyectos()
        self.escribe("Proyectos para brutos: %s" % ("todos" if self.estado["proyectos"] is None else "; ".join(self.estado["proyectos"])))

    def iniciar(self):
        from tkinter import messagebox
        da = self.da
        if self.estado["ocupado"]:
            return
        if not self.origen.get() or not Path(self.origen.get()).exists():
            return messagebox.showwarning("Falta el disco", "Elige el disco o la carpeta de Avid que quieres analizar.", parent=self.raiz)
        if not self.destino.get():
            return messagebox.showwarning("Falta el informe", "Elige dónde guardar el informe.", parent=self.raiz)
        seguido = self.seguido.get()
        if self.estado["proyectos"] == []:
            return messagebox.showwarning("Ningún proyecto", "No has elegido ningún proyecto. Pulsa \"Proyectos…\" y marca al menos uno.",
                                          parent=self.raiz)
        if seguido and not self.brutos.get():
            return messagebox.showwarning("Falta la carpeta de brutos", "Has marcado \"Analizar y exportar seguido\": elige la carpeta de brutos.",
                                          parent=self.raiz)
        ff, _ = da.localizar_ffmpeg()
        if seguido and not ff:
            return messagebox.showerror("Sin ffmpeg", "Para exportar brutos hace falta ffmpeg (carpeta bin o PATH).", parent=self.raiz)
        if not ff and not messagebox.askyesno("Sin ffmpeg", "No se encuentra ffmpeg/ffprobe (carpeta bin o PATH).\nSe analizarán los bins pero "
                                              "no los ficheros de vídeo.\n¿Continuar igualmente?", parent=self.raiz):
            return
        da.PARAR.clear()
        self.ocupar("analisis")
        self.escribe("— %s —" % da.ahora_txt())
        # las variables de tkinter se leen aquí: el hilo no puede tocarlas
        datos = dict(origen=self.origen.get(), destino_base=self.destino.get(), hacer_miniaturas=self.minis.get(), capturas_camara=self.capturas.get(),
                     brutos=self.brutos.get() if seguido else None, zip_miniaturas=self.zip_minis.get(),
                     proyectos_brutos=self.estado["proyectos"] if seguido else None, carpetas_media=list(self.estado["media_extra"]),
                     solo_proyectos=self.estado["proyectos"])
        threading.Thread(target=self.hilo_analisis, args=(datos,), daemon=True).start()

    def hilo_analisis(self, datos):
        da = self.da
        try:
            d, z, resumen = da.ejecutar(progreso=lambda m: self.cola.put(("log", m)), **datos)
            self.cola.put(("detenido_brutos" if resumen.get("detenido") else "fin", (str(d / "informe.html"), str(z))))
        except da.OrigenPerdido as e:  # antes que Detenido, que es su clase madre
            self.cola.put(("error", str(e)))
        except da.Detenido:
            self.cola.put(("detenido", None))
        except Exception as e:
            da.LOG.error("Fallo: %s\n%s", e, traceback.format_exc())
            log = getattr(e, "log", None)
            self.cola.put(("error", "El análisis se ha parado en: %s.\n\nError: %s\n\nNo se ha tocado nada del disco. Para arreglarlo "
                                    "hace falta este fichero (solo texto, sin vídeo):\n%s"
                                    % (getattr(e, "fase", "el inicio"), e, log if log else "diagnostico.log, en la carpeta del informe")))

    def detener(self):
        from tkinter import messagebox
        if not self.estado["ocupado"] or self.da.PARAR.is_set():
            return
        if messagebox.askyesno("Detener", "¿Detener ahora?\n\n"
                               "· Si está exportando brutos: los terminados se conservan y el que está a medias se descarta. "
                               "Al volver a exportar con las mismas carpetas, sigue por donde se quedó.\n\n"
                               "· Si aún está analizando: no habrá informe y al relanzar empezará de cero.", parent=self.raiz):
            self.da.PARAR.set()
            self.bt_parar.state(["disabled"])
            self.bt_parar_exp.state(["disabled"])
            self.escribe("Deteniendo... (unos segundos)")
            self.lbl_trabajo.configure(text="Deteniendo…")

    # --- página 2 · Montaje ----------------------------------------------------------------------------------------

    def aviso_en(self, pagina, titulo, texto, boton=None):
        """Sustituye el contenido de una página por un aviso centrado (sin análisis, sin carpeta…)."""
        ttk = self.ttk
        caja = ttk.Frame(self.paginas[pagina], style="Fondo.TFrame")
        caja.place(relx=0.5, rely=0.4, anchor="center")
        ttk.Label(caja, text=titulo, style="Titulo.TLabel").pack()
        ttk.Label(caja, text=texto, style="Suave.TLabel", wraplength=560, justify="center").pack(pady=(10, 16))
        if boton:
            ttk.Button(caja, text=boton[0], style="Elegido.TButton", command=boton[1]).pack()
        return caja

    def cerrar_montaje(self):
        if self.montaje:
            self.montaje.cerrar()
        if self.pantalla_origen:
            self.pantalla_origen.cerrar()
        self.montaje, self.pantalla_origen, self.clave_montaje = None, None, None

    def faltan_en_analisis(self, carpeta):
        """Texto del aviso si el último análisis es solo de algunos proyectos y no tiene alguno de los elegidos, o None."""
        try:
            r = json.loads((Path(carpeta) / "resumen.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        faltan = self.da.fuera_del_analisis(r, self.estado["proyectos"])
        if not faltan:
            return None
        return ("El último análisis de este disco es solo de %s. Para %s, ve a 1 · Disco y pulsa \"Analizar disco\" "
                "(o elige en \"Proyectos…\" los que ya están analizados)." % (", ".join(r["parcial"]["proyectos"]), ", ".join(faltan)))

    def preparar_montaje(self):
        from tkinter import messagebox
        da = self.da
        p = self.paginas["montaje"]
        carpeta = self.analisis_actual()
        clave = (str(carpeta), self.brutos.get(), tuple(self.estado["proyectos"] or ()))
        if self.montaje and clave == self.clave_montaje:
            return
        self.cerrar_montaje()
        for w in p.winfo_children():
            w.destroy()
        if not carpeta:
            self.aviso_en("montaje", "Primero analiza el disco", "Para revisar los brutos hace falta el análisis del disco elegido. "
                          "Ve a 1 · Disco, elige las carpetas y pulsa \"Analizar disco\".", ("← Ir a 1 · Disco", lambda: self.ir("disco")))
            return
        if not self.brutos.get():
            self.aviso_en("montaje", "Falta la carpeta de brutos", "El montaje se guarda en la carpeta de brutos (montaje.json). "
                          "Elígela en 1 · Disco.", ("← Ir a 1 · Disco", lambda: self.ir("disco")))
            return
        if self.faltan_en_analisis(carpeta):
            self.aviso_en("montaje", "El análisis no tiene esos proyectos", self.faltan_en_analisis(carpeta),
                          ("← Ir a 1 · Disco", lambda: self.ir("disco")))
            return
        if da.exportar_brutos.cargar_config(da.carpeta_base() / "config_brutos.json").get("agrupar", "proyecto_mes") != "proyecto_mes":
            self.aviso_en("montaje", "Montaje no disponible", "El montaje solo se aplica con agrupar = proyecto_mes (config_brutos.json).")
            return
        self.raiz.configure(cursor="watch")
        self.raiz.update()
        try:
            m, clips, _, _, resumen, cfg = da.montaje_de(carpeta, self.brutos.get())
        except Exception as e:
            da.LOG.error("Montaje: %s\n%s", e, traceback.format_exc())
            self.raiz.configure(cursor="")
            messagebox.showerror("Montaje", "No se ha podido cargar el montaje: %s" % e, parent=self.raiz)
            return
        self.raiz.configure(cursor="")
        if not m.proyectos():
            self.aviso_en("montaje", "No hay brutos", "En este análisis no hay clips de cámara que exportar.")
            return
        # Dos pestañas, a la izquierda de la cabecera de cada pantalla (sin fila de más: la lista de clips no encoge):
        # los brutos (montaje) y el origen del material (qué entra y qué no, por procedencia)
        dudosos = sum(1 for c in clips if c.get("origen_clase") == "dudoso")
        self.var_pestana = self.tk.StringVar(value=self.pestana_montaje)
        self.sel_pestanas = []

        def pestanas(cabecera):
            sel = tema.Seleccion(cabecera, [("brutos", "Brutos"),
                                            ("origen", ("Origen" if self.compacto else "Origen del material")
                                             + (" · %d para revisar" % dudosos if dudosos else ""))],
                                 self.var_pestana, al_cambiar=self.ver_pestana_montaje, estilo="Cabecera")
            sel.pack(side="left", padx=(0, 16))
            self.sel_pestanas.append(sel)

        self.cajas_montaje = {k: self.ttk.Frame(p, style="Fondo.TFrame") for k in ("brutos", "origen")}
        self.montaje = vm.PantallaMontaje(self.cajas_montaje["brutos"], m, resumen["origen"], Path(carpeta) / "visor", self.brutos.get(),
                                          da.localizar_ffmpeg()[0], da.exportar_brutos.clips_a_la_vez(cfg), self.estado["proyectos"],
                                          al_cambiar=self.montaje_cambiado, pestanas=pestanas, compacto=self.compacto)
        self.pantalla_origen = vo.PantallaOrigen(self.cajas_montaje["origen"], clips, cfg, self.brutos.get(), al_guardar=self.origen_guardado,
                                                 recordar=da.recordar_reglas_origen, pestanas=pestanas, aprendizaje=da.ruta_aprendizaje(cfg),
                                                 compacto=self.compacto)
        self.ver_pestana_montaje()
        self.clave_montaje = clave
        self.montaje.bloquear(self.estado["ocupado"] is not None)
        self.pantalla_origen.bloquear(self.estado["ocupado"] is not None)
        self.escribe("Montaje con el análisis %s" % carpeta.name)

    def ver_pestana_montaje(self):
        self.pestana_montaje = self.var_pestana.get()
        for sel in self.sel_pestanas:
            sel.pintar()
        for k, caja in self.cajas_montaje.items():
            if k == self.pestana_montaje:
                caja.pack(fill="both", expand=True)
            else:
                caja.pack_forget()

    def origen_guardado(self):
        """Se guardó origen.json: los brutos cambian (entran o salen clips), así que se rehace el montaje."""
        from tkinter import messagebox
        if self.montaje and self.montaje.cambios and messagebox.askyesno(
                "Montaje sin guardar", "Hay cambios en los brutos sin guardar. ¿Guardarlos antes de rehacer los brutos con el origen "
                "elegido?", parent=self.raiz):
            self.montaje.guardar()
        self.pestana_montaje = "origen"
        self.clave_montaje = None
        if self.exp:
            self.exp["clave"] = None  # la cola de exportación se rehace
        self.escribe("Origen del material guardado (origen.json): se rehacen los brutos")
        self.raiz.after_idle(self.preparar_montaje)

    def montaje_cambiado(self):
        if self.montaje and not self.montaje.cambios and self.exp:  # recién guardado: la cola se rehace con el montaje nuevo
            self.exp["clave"] = None

    # --- página 3 · Exportar ---------------------------------------------------------------------------------------

    def pagina_exportar(self):
        tk, ttk = self.tk, self.ttk
        p = self.paginas["exportar"]
        izq = ttk.Frame(p, style="Panel.TFrame", padding=(12, 10) if self.compacto else (18, 16))
        izq.pack(side="left", fill="y")
        self.aj = {}
        g = 5 if self.compacto else 12  # separación entre bloques (menos en una pantalla pequeña)
        ttk.Label(izq, text="AJUSTES DE EXPORTACIÓN", style="Panel.Seccion.TLabel").pack(anchor="w", pady=(0, g))
        ttk.Label(izq, text="Formato", style="Panel.TLabel", font=self.letras["negrita"]).pack(anchor="w")
        self.aj["formato"] = ttk.Combobox(izq, state="readonly", width=32 if self.compacto else 40, values=["XDCAM HD422 50 Mb/s · 1080i50 · 16:9 (recomendado)",
                                                                                 "Formato mayoritario de cada bruto (avanzado)"])
        self.aj["formato"].pack(anchor="w", pady=(4, g))
        ttk.Label(izq, text="TC de inicio", style="Panel.TLabel", font=self.letras["negrita"]).pack(anchor="w")
        self.aj["tc"] = tk.StringVar()
        ttk.Entry(izq, textvariable=self.aj["tc"], width=14, font=(tema.MONO, 10 if self.compacto else 12)).pack(anchor="w", pady=(4, g))
        ttk.Label(izq, text="Audio", style="Panel.TLabel", font=self.letras["negrita"]).pack(anchor="w")
        self.aj["pistas"] = tk.IntVar(value=4)
        self.sel_pistas = tema.Seleccion(izq, [(4, "4 pistas"), (8, "8 pistas")], self.aj["pistas"], al_cambiar=self.pistas_elegidas)
        self.sel_pistas.pack(anchor="w", pady=(4, 2))
        self.lbl_pistas = ttk.Label(izq, text="", style="Panel.Suave.TLabel", wraplength=330)
        self.lbl_pistas.pack(anchor="w", pady=(0, 2 if self.compacto else 6))
        self.aj["niveles"] = tk.StringVar()
        for valor, texto in (("sin_tocar", "Sin tocar (recomendado)"),
                             ("ganancia", "Ganancia fija (Opciones avanzadas)" if self.compacto else "Ganancia fija por pista (en Opciones avanzadas)"),
                             ("r128", "Normalizar a −23 LUFS (altera el original)")):
            ttk.Radiobutton(izq, text=texto, value=valor, variable=self.aj["niveles"], style="Panel.TRadiobutton").pack(anchor="w")
        ttk.Label(izq, text="Comprobaciones", style="Panel.TLabel", font=self.letras["negrita"]).pack(anchor="w", pady=(g, 2))
        for clave, texto in (("ficha_tecnica", "Ficha técnica (conforme / no conforme)"), ("md5", "MD5 de cada bruto"),
                             ("medidor_audio", "Medidor de audio (avisa, no toca)")):
            self.aj[clave] = tk.BooleanVar()
            ttk.Checkbutton(izq, text=texto, variable=self.aj[clave], style="Panel.TCheckbutton").pack(anchor="w")
        self.bt_opciones = ttk.Button(izq, text="Opciones avanzadas…", style="Fantasma.TButton", command=self.opciones_avanzadas)
        self.bt_opciones.pack(anchor="w", pady=(8 if self.compacto else 16, 0))
        for clave in ("tc", "niveles", "ficha_tecnica", "md5", "medidor_audio"):
            self.aj[clave].trace_add("write", lambda *a: self.leer_ajustes())
        self.aj["formato"].bind("<<ComboboxSelected>>", lambda e: self.leer_ajustes())

        centro = ttk.Frame(p, style="Fondo.TFrame", padding=(12, 10) if self.compacto else (20, 16))
        centro.pack(side="left", fill="both", expand=True)
        fila = ttk.Frame(centro, style="Fondo.TFrame")
        fila.pack(fill="x")
        ttk.Label(fila, text="Cola de exportación", style="Grande.TLabel" if self.compacto else "Titulo.TLabel").pack(side="left")
        self.lbl_cola = ttk.Label(fila, text="", style="Suave.TLabel")
        self.lbl_cola.pack(side="left", padx=14)
        fantasma = "Fantasma.Compacta.TButton" if self.compacto else "Fantasma.TButton"
        ttk.Button(fila, text="Ninguno", style=fantasma, command=lambda: self.marcar_todos(False)).pack(side="right")
        ttk.Button(fila, text="Todos", style=fantasma, command=lambda: self.marcar_todos(True)).pack(side="right", padx=6)
        caja = ttk.Frame(centro, style="Fondo.TFrame")
        caja.pack(fill="both", expand=True, pady=(12, 0))
        cols = ("si", "bruto", "dur", "estado", "calidad")
        self.tv = ttk.Treeview(caja, columns=cols, show="headings", selectmode="browse", height=5 if self.compacto else 10)
        anchos = (34, 220, 80, 170, 110) if self.compacto else (40, 300, 90, 240, 130)
        for c, t, w, st in zip(cols, ("", "BRUTO", "DURACIÓN", "ESTADO", "COMPROBACIÓN"), anchos, (False, True, False, True, False)):
            self.tv.heading(c, text=t)
            self.tv.column(c, width=w, anchor="center" if c == "si" else "w", stretch=st)
        self.tv.tag_configure("fuera", foreground=tema.SUAVE)
        self.tv.tag_configure("mal", foreground=tema.AVISO)
        self.tv.tag_configure("bien", foreground=tema.BIEN)
        self.tv.tag_configure("activo", foreground=tema.ACENTO)
        sb = ttk.Scrollbar(caja, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=sb.set)
        self.tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tv.bind("<Button-1>", self.pulsar_cola)
        self.tv.bind("<space>", lambda e: self.cambiar_marca(self.tv.focus()))
        ttk.Label(centro, text="Clic en la casilla para incluir o quitar un bruto. Los de piezas vienen sin marcar.",
                  style="Suave.TLabel").pack(anchor="w", pady=(4, 0))

        tarjetas = ttk.Frame(centro, style="Fondo.TFrame")
        tarjetas.pack(fill="x", pady=(14, 0))
        t = tema.Tarjeta(tarjetas, "Espacio")
        t.pack(side="left", fill="both", expand=True)
        self.titulo_espacio = t.winfo_children()[0]
        self.barra_espacio = ttk.Progressbar(t, mode="determinate", maximum=100)
        self.barra_espacio.pack(fill="x")
        self.lbl_espacio = ttk.Label(t, text="", style="Tarjeta.TLabel")
        self.lbl_espacio.pack(anchor="w", pady=(6, 0))
        t = tema.Tarjeta(tarjetas, "Tiempo")
        t.pack(side="left", fill="both", expand=True, padx=(14, 0))
        self.lbl_tiempo = ttk.Label(t, text="", style="Tarjeta.TLabel", wraplength=260 if self.compacto else 380, justify="left")
        self.lbl_tiempo.pack(anchor="w")

        pie = ttk.Frame(centro, style="Fondo.TFrame")
        pie.pack(fill="x", pady=(14, 0))
        self.bt_exportar = ttk.Button(pie, text="Exportar", style="Principal.TButton", command=self.exportar)
        self.bt_exportar.pack(side="right")
        self.bt_parar_exp = ttk.Button(pie, text="Detener", style="Fantasma.TButton", command=self.detener)
        self.bt_parar_exp.pack(side="right", padx=10)
        self.lbl_motivo = ttk.Label(pie, text="", style="Aviso.TLabel", wraplength=280 if self.compacto else 560, justify="right")
        self.lbl_motivo.pack(side="right", padx=10)
        if not self.compacto:
            ttk.Label(pie, text="Se puede detener y seguir otro día.", style="Suave.TLabel").pack(side="left")
        self.contenido_exportar = [izq, centro]

    def preparar_exportar(self):
        """Carga la cola (brutos del análisis con el montaje guardado) al entrar en la página, si cambió algo."""
        from tkinter import messagebox
        da = self.da
        if getattr(self, "aviso_exportar", None):
            self.aviso_exportar.destroy()
            self.aviso_exportar = None
        carpeta = self.analisis_actual()
        falta = None
        if not carpeta:
            falta = ("Primero analiza el disco", "Para exportar hace falta el análisis del disco elegido. Ve a 1 · Disco y pulsa \"Analizar disco\".")
        elif not self.brutos.get():
            falta = ("Falta la carpeta de brutos", "Elige en 1 · Disco dónde guardar los brutos (un disco con espacio).")
        elif self.estado["proyectos"] == []:
            falta = ("Ningún proyecto elegido", "En 1 · Disco, pulsa \"Proyectos…\" y marca al menos uno.")
        elif self.faltan_en_analisis(carpeta):
            falta = ("El análisis no tiene esos proyectos", self.faltan_en_analisis(carpeta))
        if falta:
            for w in self.contenido_exportar:
                w.pack_forget()
            self.aviso_exportar = self.aviso_en("exportar", falta[0], falta[1], ("← Ir a 1 · Disco", lambda: self.ir("disco")))
            return
        if not self.contenido_exportar[0].winfo_manager():
            self.contenido_exportar[0].pack(side="left", fill="y")
            self.contenido_exportar[1].pack(side="left", fill="both", expand=True)
        clave = (str(carpeta), self.brutos.get(), tuple(self.estado["proyectos"] or ()))
        if self.exp and self.exp["clave"] == clave:
            self.pintar_cola()
            return
        if self.estado["ocupado"] == "exportacion":  # no se rehace la cola mientras exporta
            return
        self.raiz.configure(cursor="watch")
        self.raiz.update()
        try:
            resumen, cfg = da.resumen_exportacion(carpeta, self.brutos.get(), self.estado["proyectos"])
        except Exception as e:
            da.LOG.error("Cola de exportación: %s\n%s", e, traceback.format_exc())
            messagebox.showerror("Exportar", "No se ha podido preparar la cola: %s" % e, parent=self.raiz)
            return
        finally:
            self.raiz.configure(cursor="")
        anterior = self.exp or {}
        piezas = bool(cfg.get("exportar_piezas", False))
        marcados = {f["nombre"] for f in resumen["filas"] if piezas or f["tipo"] != "piezas"}
        if anterior.get("carpeta") == str(carpeta):  # mismo análisis: se respetan las casillas tocadas a mano
            marcados = {n for n in marcados if n not in anterior["desmarcados"]} | (anterior["marcados"] & {f["nombre"] for f in resumen["filas"]})
        self.exp = {"clave": clave, "carpeta": str(carpeta), "resumen": resumen, "cfg": anterior.get("cfg") or cfg, "marcados": marcados,
                    "desmarcados": anterior.get("desmarcados", set()) if anterior.get("carpeta") == str(carpeta) else set(),
                    "estados": {}, "resultados": self.resultados_previos(), "pistas_a_mano": anterior.get("pistas_a_mano", False)}
        self.poner_ajustes(self.exp["cfg"])
        self.pintar_cola()

    def resultados_previos(self):
        """Brutos ya exportados en esa carpeta (brutos_resultado.json): se ven como hechos en la cola."""
        try:
            res = json.loads((Path(self.brutos.get()) / "brutos_resultado.json").read_text(encoding="utf-8"))
            return {r["proyecto"]: r for r in res if str(r.get("estado", "")).startswith(("OK", "AVISO"))}
        except Exception:
            return {}

    def poner_ajustes(self, cfg):
        self.poniendo = True
        self.aj["formato"].current(0 if cfg.get("formato_salida", "xdcam_hd50") == "xdcam_hd50" else 1)
        self.aj["tc"].set(cfg.get("timecode_inicio", "00:00:00:00"))
        self.aj["niveles"].set(cfg.get("niveles_audio", "sin_tocar"))
        for clave in ("ficha_tecnica", "md5", "medidor_audio"):
            self.aj[clave].set(bool(cfg.get(clave, True)))
        self.poniendo = False
        self.pintar_pistas()

    def leer_ajustes(self):
        if getattr(self, "poniendo", False) or not self.exp:
            return
        cfg = self.exp["cfg"] = dict(self.exp["cfg"])
        cfg["formato_salida"] = "xdcam_hd50" if self.aj["formato"].current() == 0 else "auto"
        cfg["timecode_inicio"] = self.aj["tc"].get().strip()
        cfg["niveles_audio"] = self.aj["niveles"].get()
        for clave in ("ficha_tecnica", "md5", "medidor_audio"):
            cfg[clave] = self.aj[clave].get()
        self.pintar_pistas()
        self.revisar_exportar()

    def pistas_max(self):
        filas = [f for f in self.exp["resumen"]["filas"] if f["nombre"] in self.exp["marcados"]] or self.exp["resumen"]["filas"]
        return max((f["pistas"] for f in filas), default=0)

    def pintar_pistas(self):
        """4 | 8 pistas: marcado lo que sale del material, salvo que se haya elegido a mano."""
        cfg, maximo = self.exp["cfg"], self.pistas_max()
        auto = self.da.exportar_brutos.pistas_salida(maximo, dict(cfg, pistas_audio="max", formato_salida="xdcam_hd50"))
        if cfg.get("pistas_audio", "max") in (4, 8):
            self.aj["pistas"].set(cfg["pistas_audio"])
            nota = "Elegido a mano. El material trae como mucho %d pistas por clip." % maximo
        else:
            self.aj["pistas"].set(auto)
            nota = "Elegido según el material: como mucho %d pistas por clip." % maximo
        if cfg.get("formato_salida") == "auto":
            nota += " En formato mayoritario se exportan las del material."
        self.sel_pistas.pintar()
        self.lbl_pistas.configure(text=nota)

    def pistas_elegidas(self):
        if self.exp:
            self.exp["cfg"] = dict(self.exp["cfg"], pistas_audio=int(self.aj["pistas"].get()))
            self.pintar_pistas()
            self.revisar_exportar()

    def opciones_avanzadas(self):
        if not self.exp or self.estado["ocupado"]:
            return
        resumen = dict(self.exp["resumen"])
        filas = [f for f in resumen["filas"] if f["nombre"] in self.exp["marcados"]]
        resumen.update(brutos=[f["base"] for f in filas], segundos=sum(f["segundos"] for f in filas),
                       pistas_max=max((f["pistas"] for f in filas), default=0))
        cfg = vm.dialogo_opciones(self.raiz, self.exp["cfg"], resumen, self.da.guardar_config_brutos)
        if cfg is not None:
            self.exp["cfg"] = cfg
            self.poner_ajustes(cfg)
            self.pintar_cola()

    def pulsar_cola(self, e):
        fila = self.tv.identify_row(e.y)
        if fila and self.tv.identify_column(e.x) == "#1":
            self.cambiar_marca(fila)
            return "break"

    def cambiar_marca(self, nombre):
        if not nombre or not self.exp or self.estado["ocupado"] == "exportacion":
            return
        if nombre in self.exp["marcados"]:
            self.exp["marcados"].discard(nombre)
            self.exp["desmarcados"].add(nombre)
        else:
            self.exp["marcados"].add(nombre)
            self.exp["desmarcados"].discard(nombre)
        self.pintar_cola()

    def marcar_todos(self, si):
        if not self.exp or self.estado["ocupado"] == "exportacion":
            return
        nombres = {f["nombre"] for f in self.exp["resumen"]["filas"]}
        self.exp["marcados"] = set(nombres) if si else set()
        self.exp["desmarcados"] = set() if si else set(nombres)
        self.pintar_cola()

    def pintar_cola(self):
        e = self.exp
        if not e:
            return
        foco = self.tv.focus()
        self.tv.delete(*self.tv.get_children())
        for f in e["resumen"]["filas"]:
            n = f["nombre"]
            res = e["resultados"].get(n)
            estado = e["estados"].get(n) or ("Exportado antes" if res else "En espera" if n in e["marcados"] else "No se exporta")
            calidad = texto_calidad(res)
            tag = ("mal",) if calidad == "NO CONFORME" or estado.startswith(("ERROR", "SIN ESPACIO", "AVISO")) else \
                ("activo",) if n in e["estados"] and not res else ("bien",) if res else () if n in e["marcados"] else ("fuera",)
            nombre = n + ("   · piezas" if f["tipo"] == "piezas" else "")
            self.tv.insert("", "end", iid=n, tags=tag, values=("☑" if n in e["marcados"] else "☐", nombre, vm.fmt_h(f["segundos"]), estado, calidad))
        if foco and self.tv.exists(foco):
            self.tv.focus(foco)
        filas = [f for f in e["resumen"]["filas"] if f["nombre"] in e["marcados"]]
        self.lbl_cola.configure(text="%d de %d brutos · %s de material" % (len(filas), len(e["resumen"]["filas"]), vm.fmt_h(sum(f["segundos"] for f in filas))))
        self.pintar_pistas()
        self.revisar_exportar()

    def revisar_exportar(self):
        """Espacio, tiempo y si se puede exportar (si no, el motivo junto al botón)."""
        if not getattr(self, "bt_exportar", None):
            return
        e, da = self.exp, self.da
        if not e:
            self.bt_exportar.state(["disabled"])
            return
        cfg = e["cfg"]
        filas = [f for f in e["resumen"]["filas"] if f["nombre"] in e["marcados"]]
        pendientes = [f for f in filas if f["nombre"] not in e["resultados"]]
        pistas = int(self.aj["pistas"].get()) if cfg.get("formato_salida", "xdcam_hd50") == "xdcam_hd50" else self.pistas_max()
        necesario = sum(vm.espacio_necesario(f["segundos"], pistas) for f in pendientes)
        libre = vm.espacio_libre(self.brutos.get()) if self.brutos.get() else None
        unidad = Path(self.brutos.get()).anchor or self.brutos.get()
        self.titulo_espacio.configure(text=("Espacio en %s" % unidad).upper())
        if libre is None:
            self.barra_espacio.configure(value=0)
            self.lbl_espacio.configure(text="Hacen falta unos %s · no se puede leer el espacio libre" % gb(necesario), style="Tarjeta.TLabel")
        else:
            self.barra_espacio.configure(value=min(100, 100 * necesario / max(libre, 1)))
            self.lbl_espacio.configure(text="Hacen falta unos %s · libres %s%s" % (gb(necesario), gb(libre), "  ·  NO CABE" if necesario > libre else ""),
                                       style="Tarjeta.Aviso.TLabel" if necesario > libre else "Tarjeta.TLabel")
        self.pintar_tiempo(pendientes)
        nombres = [cfg.get("prefijo_fichero", "") + f["base"] for f in filas]
        errores = da.exportar_brutos.validar_opciones(cfg) + da.exportar_brutos.validar_nombres(nombres)
        if not filas:
            errores.append("Marca al menos un bruto en la cola.")
        if libre is not None and necesario > libre:
            errores.append("No cabe en la carpeta de brutos: libera espacio o elige otro disco.")
        if not da.localizar_ffmpeg()[0]:
            errores.append("Falta ffmpeg (carpeta bin o PATH).")
        if self.estado["ocupado"]:
            errores = ["Trabajando…"]
        self.bt_exportar.configure(text="Exportar %d bruto%s" % (len(filas), "" if len(filas) == 1 else "s"))
        self.bt_exportar.state(["disabled"] if errores else ["!disabled"])
        self.lbl_motivo.configure(text="\n".join(errores[:4]) if errores != ["Trabajando…"] else "")
        self.bt_opciones.state(["disabled"] if self.estado["ocupado"] else ["!disabled"])

    def pintar_tiempo(self, pendientes):
        c = self.cronometro
        if c and c.get("segundos_hechos"):
            ritmo = c["tardado"] / c["segundos_hechos"]
            queda = ritmo * max(0.0, c["segundos_total"] - c["segundos_hechos"])
            self.lbl_tiempo.configure(text="Queda aproximadamente %s (calculado con los brutos ya terminados)" % vm.fmt_h(queda))
        elif self.estado["ocupado"] == "exportacion":
            self.lbl_tiempo.configure(text="Se calcula al terminar el primer bruto.")
        elif not pendientes:
            self.lbl_tiempo.configure(text="Nada pendiente: los brutos marcados ya están exportados.")
        else:
            material = sum(f["segundos"] for f in pendientes)
            self.lbl_tiempo.configure(text="%s de material por exportar. El tiempo se calcula al terminar el primer bruto "
                                      "(depende del ordenador y del disco)." % vm.fmt_h(material))

    def exportar(self):
        from tkinter import messagebox
        da = self.da
        if self.estado["ocupado"] or not self.exp:
            return
        self.revisar_exportar()
        if "disabled" in self.bt_exportar.state():
            return
        e = self.exp
        filas = [f for f in e["resumen"]["filas"] if f["nombre"] in e["marcados"]]
        if not messagebox.askyesno("Exportar brutos", "Se exportan %d brutos con el análisis %s (no se repite) y el montaje guardado.\n\n"
                                   "Puede tardar muchas horas; se puede parar con Detener y seguir otro día.\n\n¿Empezar?"
                                   % (len(filas), Path(e["carpeta"]).name), parent=self.raiz):
            return
        da.PARAR.clear()
        e["estados"] = {}
        self.cronometro = {"inicio": time.time(), "segundos_total": sum(f["segundos"] for f in filas if f["nombre"] not in e["resultados"]),
                           "segundos_hechos": 0.0, "tardado": 0.0, "desde": time.time()}
        self.ocupar("exportacion")
        self.escribe("— %s — Exportación de brutos" % da.ahora_txt())
        threading.Thread(target=self.hilo_exportar, args=(e["carpeta"], self.brutos.get(), self.estado["proyectos"], dict(e["cfg"]),
                                                          [f["nombre"] for f in filas]), daemon=True).start()

    def hilo_exportar(self, carpeta, brutos, proyectos, cfg, solo):
        da = self.da
        try:
            res = da.exportar_desde_analisis(carpeta, brutos, progreso=lambda m: self.cola.put(("log", m)), proyectos_brutos=proyectos, cfg=cfg,
                                             solo_brutos=solo, evento=lambda ev: self.cola.put(("evento", ev)))
            ok = sum(1 for b in res if str(b["estado"]).startswith("OK"))
            self.cola.put(("exportado", (len(res), ok, da.PARAR.is_set(), brutos)))
        except da.OrigenPerdido as e:
            self.cola.put(("error", str(e)))
        except da.Detenido:
            self.cola.put(("exportado", (0, 0, True, brutos)))
        except Exception as e:
            da.LOG.error("Fallo: %s\n%s", e, traceback.format_exc())
            self.cola.put(("error", "%s\n\nDetalle en exportacion.log, en la carpeta de brutos." % e))

    def al_evento(self, ev):
        e = self.exp
        if not e:
            return
        n = ev.get("bruto")
        e["estados"][n] = texto_estado(ev)
        if ev["estado"] in ("terminado", "reanudado"):
            res = ev.get("resultado") or {}
            e["resultados"][n] = res
            e["estados"][n] = texto_estado(ev)
            c = self.cronometro
            if c is not None:
                seg = next((f["segundos"] for f in e["resumen"]["filas"] if f["nombre"] == n), 0)
                if ev["estado"] == "reanudado":  # ya estaba fuera de la cuenta (brutos_resultado.json)
                    pass
                elif str(res.get("estado", "")).startswith(("OK", "AVISO")):
                    c["segundos_hechos"] += seg
                    c["tardado"] += time.time() - c["desde"]
                else:  # fallido: no cuenta para el ritmo
                    c["segundos_total"] = max(0.0, c["segundos_total"] - seg)
                c["desde"] = time.time()
        if self.tv.exists(n):
            self.pintar_cola()
            self.tv.see(n)

    # --- cola de mensajes, cierre y actualización -------------------------------------------------------------------

    def vaciar_cola(self):
        from tkinter import messagebox
        try:
            while True:
                tipo, msg = self.cola.get_nowait()
                if tipo == "log":
                    self.escribe(msg)
                    continue
                if tipo == "evento":
                    self.al_evento(msg)
                    continue
                if tipo == "vista_disco":
                    self.al_leer_disco(*msg)
                    continue
                if tipo == "actualizacion":
                    if msg == "cambio_lanzado":  # el .bat espera a que se cierre para cambiar de versión y reabrir
                        self.raiz.destroy()
                        return
                    self.ocupar(None)
                    self.escribe("Versión al día (%s)." % self.da.BUILD if msg == "al_dia" else
                                 "No se ha podido buscar o instalar una versión nueva; se sigue con esta (%s)." % self.da.BUILD)
                    continue
                self.ocupar(None)
                self.cronometro = None
                if self.estado["cerrar"]:  # se pidió cerrar la ventana mientras trabajaba
                    self.raiz.destroy()
                    return
                if tipo in ("fin", "detenido_brutos"):
                    self.pintar_ultimo()
                    if self.exp:
                        self.exp["clave"] = None  # análisis nuevo: la cola se rehace al entrar (se conservan ajustes y casillas)
                if tipo == "fin":
                    messagebox.showinfo("Terminado", "Diagnóstico terminado.\n\nInforme: %s\n\nZIP para enviar: %s\n\n"
                                        "Siguiente paso: 2 · Montaje (opcional) y 3 · Exportar." % msg, parent=self.raiz)
                elif tipo == "detenido_brutos":
                    messagebox.showwarning("Detenido", "Exportación detenida. Los brutos terminados se conservan y el informe recoge lo hecho.\n\n"
                                           "Para seguir, exporta otra vez con las mismas carpetas: sigue por el bruto que faltaba.\n\n"
                                           "Informe: %s\n\nZIP para enviar: %s" % msg, parent=self.raiz)
                elif tipo == "detenido":
                    messagebox.showwarning("Detenido", "Análisis detenido antes de terminar: no se ha generado informe.\n\n"
                                           "Al volver a lanzarlo empezará desde el principio.", parent=self.raiz)
                elif tipo == "exportado":
                    total, ok, parado, carpeta_b = msg
                    self.pintar_cola()
                    (messagebox.showwarning if parado or ok < total else messagebox.showinfo)(
                        "Brutos", "%s\n\n%d brutos, %d OK.\n\nCarpeta: %s\n(detalle en brutos_resultado.json y exportacion.log)"
                        % ("Exportación detenida. Al volver a pulsar Exportar sigue por donde se quedó." if parado else "Exportación terminada.",
                           total, ok, carpeta_b), parent=self.raiz)
                elif tipo == "error":
                    messagebox.showerror("Error", msg, parent=self.raiz)
        except queue.Empty:
            pass
        self.raiz.after(200, self.vaciar_cola)

    def al_cerrar(self):
        from tkinter import messagebox
        if not self.estado["ocupado"]:
            if self.pantalla_origen and self.pantalla_origen.cambios:
                r = messagebox.askyesnocancel("Origen sin guardar", "Hay cambios en el origen del material sin guardar. ¿Guardarlos antes "
                                              "de cerrar?", parent=self.raiz)
                if r is None:
                    return
                if r:
                    self.pantalla_origen.al_guardar = None  # se cierra: no hace falta rehacer el montaje
                    self.pantalla_origen.guardar()
            if self.montaje and self.montaje.cambios:
                r = messagebox.askyesnocancel("Montaje sin guardar", "Hay cambios en el montaje sin guardar. ¿Guardarlos antes de cerrar?",
                                              parent=self.raiz)
                if r is None:
                    return
                if r:
                    self.montaje.guardar()
            self.cerrar_montaje()
            self.raiz.destroy()
            return
        if self.estado["ocupado"] == "actualizacion":
            return messagebox.showinfo("Actualizando", "Espera a que termine la actualización.", parent=self.raiz)
        if messagebox.askyesno("Está trabajando", "El programa está trabajando. ¿Detenerlo y cerrar la ventana?\n\n"
                               "Los brutos ya terminados se conservan.", parent=self.raiz):
            self.estado["cerrar"] = True
            self.da.PARAR.set()
            self.escribe("Deteniendo para cerrar... (unos segundos)")

    def buscar_actualizacion(self):
        from tkinter import messagebox
        da = self.da
        if self.estado["ocupado"]:
            return
        if not messagebox.askyesno("Buscar actualización", "Se consulta en GitHub si hay una versión más nueva que la %s y, si la hay, "
                                   "se descarga, se comprueba e instala, y el programa se vuelve a abrir.\n\n"
                                   "Se conserva la configuración (config_*.json). ¿Seguir?" % da.BUILD, parent=self.raiz):
            return
        self.ocupar("actualizacion")  # nunca se actualiza a mitad de un análisis o una exportación
        self.escribe("Buscando versión nueva en GitHub...")
        threading.Thread(target=lambda: self.cola.put(("actualizacion", da.actualizar.actualizar(
            da.BUILD, da.carpeta_base(), progreso=lambda m: self.cola.put(("log", m))))), daemon=True).start()


def lanzar(da):
    """Abre la ventana. da: el módulo diagnostico_avid."""
    app = App(da)
    app.raiz.mainloop()
    return app
