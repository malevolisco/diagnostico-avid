"""Pantalla "Origen del material" (pestaña de la página 2 · Montaje).

Los clips del disco agrupados por procedencia (la carpeta de la que se importaron y el bin), con lo que propone el
programa y por qué. Se elige para cada grupo Entra / Fuera: agencia / Fuera: importado, o se deja como propone el
programa. Al guardar se escribe origen.json en la carpeta de brutos y, con "Recordar para otros discos", una regla en
config_brutos.json → reglas_origen. Diseño en docs/diseno_cualquier_disco.md, apartado 4.
Además, cada decisión guardada se apunta para aprender (aprendizaje.py) y la columna "Aprendido" propone lo que se
decidió en procedencias parecidas de otros discos; solo se aplica si se pulsa "Aceptar lo aprendido".
"""

import aprendizaje as ap
import exportar_brutos as eb


def fmt_dur(segundos):
    s = int(round(segundos or 0))
    return "%d h %02d min" % (s // 3600, s % 3600 // 60) if s >= 3600 else "%d min %02d s" % (s // 60, s % 60)


def texto_eleccion(clase):
    return dict(eb.ELECCIONES_ORIGEN).get(clase, "—") if clase else "—"


class PantallaOrigen:
    def __init__(self, contenedor, clips, cfg, carpeta_brutos, al_guardar=None, recordar=None, pestanas=None, aprendizaje=None,
                 compacto=False):
        """al_guardar(): tras escribir origen.json (para rehacer el montaje). recordar(reglas): guarda las reglas para
        otros discos (diagnostico_avid.recordar_reglas_origen). pestanas(cabecera): las pestañas de la página.
        aprendizaje: ruta del fichero de lo aprendido (None = no se aprende ni se propone).
        compacto: pantalla pequeña (portátil): columnas más estrechas."""
        import tkinter as tk
        from tkinter import ttk
        import tema
        self.tk, self.ttk = tk, ttk
        self.clips, self.cfg, self.carpeta_brutos = clips, cfg, carpeta_brutos
        self.al_guardar, self.recordar_reglas = al_guardar, recordar
        self.grupos = eb.grupos_de_origen(clips, cfg)
        self.eleccion = {(g["carpeta"], g["bin"]): g["elegido"] for g in self.grupos}
        self.ruta_aprendizaje = aprendizaje
        aprendido = ap.cargar(aprendizaje) if aprendizaje else []
        self.sugerencias = [ap.sugerir(aprendido, g) for g in self.grupos]
        self.cambios, self.bloqueada = False, False

        f = self.marco = ttk.Frame(contenedor, style="Fondo.TFrame")
        f.pack(fill="both", expand=True)

        cab = ttk.Frame(f, style="Cabecera.TFrame", padding=(10, 6) if compacto else (16, 8))
        cab.pack(fill="x")
        if pestanas:
            pestanas(cab)
        dudosos = sum(g["dudosos"] for g in self.grupos)
        ttk.Label(cab, text="%d procedencias · %d clips dudosos" % (len(self.grupos), dudosos),
                  style="Tarjeta.Suave.TLabel").pack(side="left")
        self.bt_guardar = ttk.Button(cab, text="Guardar", style="Elegido.TButton" if compacto else "Principal.TButton", command=self.guardar)
        self.bt_guardar.pack(side="right")
        self.lbl_guardado = ttk.Label(cab, text="", style="Tarjeta.Suave.TLabel")
        self.lbl_guardado.pack(side="right", padx=10)
        self.recordar = tk.BooleanVar(value=False)
        ttk.Checkbutton(cab, text="Recordar para otros discos", variable=self.recordar,
                        style="Tarjeta.TCheckbutton").pack(side="right", padx=10)

        ayuda = ttk.Frame(f, style="Fondo.TFrame", padding=(16, 8, 16, 0))
        ayuda.pack(fill="x")
        ttk.Label(ayuda, wraplength=760 if compacto else 1100, style="Suave.TLabel", text=(
            "De dónde viene cada clip, agrupado por la carpeta de la que se importó y el bin. Arriba, lo dudoso: entra en el "
            "bruto marcado \"para revisar\" hasta que se decida. Marca una o varias filas (Ctrl para varias) y elige abajo. "
            "Los efectos de Avid no salen aquí: nunca entran.")).pack(anchor="w")

        tabla = ttk.Frame(f, style="Fondo.TFrame", padding=(12, 8, 12, 4))
        tabla.pack(fill="both", expand=True)
        cols = ("carpeta", "bin", "clips", "dur", "ejemplos", "propuesta", "porque", "aprendido", "eleccion")
        titulos = ("IMPORTADO DE", "BIN", "CLIPS", "DURACIÓN", "EJEMPLOS", "PROPUESTA", "POR QUÉ", "APRENDIDO", "ELEGIDO")
        anchos = (130, 90, 40, 70, 100, 100, 120, 120, 80) if compacto else (210, 130, 50, 90, 150, 140, 200, 160, 100)
        self.tv = ttk.Treeview(tabla, columns=cols, show="headings", selectmode="extended", height=6 if compacto else 10)
        for c, t, w in zip(cols, titulos, anchos):
            self.tv.heading(c, text=t)
            self.tv.column(c, width=w, anchor="w", stretch=c in ("carpeta", "ejemplos", "porque"))
        self.tv.tag_configure("dudoso", foreground=tema.ACENTO)
        self.tv.tag_configure("elegido", foreground=tema.BIEN)
        sb = ttk.Scrollbar(tabla, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=sb.set)
        self.tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        acc = ttk.Frame(f, style="Panel.TFrame", padding=(12, 8))
        acc.pack(fill="x")
        ttk.Label(acc, text="Para las filas marcadas:", style="Panel.TLabel").pack(side="left", padx=(0, 10))
        self.botones = []
        for clase, texto in eb.ELECCIONES_ORIGEN:
            b = ttk.Button(acc, text=texto, style="Compacta.TButton" if compacto else "Accion.TButton", command=lambda k=clase: self.elegir(k))
            b.pack(side="left", padx=(0, 6))
            self.botones.append(b)
        b = ttk.Button(acc, text="Aceptar lo aprendido", style="Compacta.TButton" if compacto else "Accion.TButton", command=self.aceptar_aprendido)
        b.pack(side="left", padx=(12, 0))
        self.botones.append(b)
        self.pintar()

    def clave(self, iid):
        g = self.grupos[int(iid)]
        return g["carpeta"], g["bin"]

    def pintar(self):
        sel = self.tv.selection()
        self.tv.delete(*self.tv.get_children())
        for i, g in enumerate(self.grupos):
            elegido = self.eleccion.get((g["carpeta"], g["bin"]))
            tags = ("elegido",) if elegido else ("dudoso",) if g["dudosos"] else ()
            self.tv.insert("", "end", iid=str(i), tags=tags, values=(
                g["carpeta"] or "(capturado o sin ruta)", g["bin"] or "—", len(g["clips"]), fmt_dur(g["segundos"]),
                ", ".join(g["ejemplos"]) + ("…" if len(g["clips"]) > 3 else ""), g["propuesta"], g["motivo"],
                ap.texto_sugerencia(self.sugerencias[i], eb.CLASES_ORIGEN), texto_eleccion(elegido)))
        self.tv.selection_set([i for i in sel if self.tv.exists(i)])

    def elegir(self, clase):
        from tkinter import messagebox
        if self.bloqueada:
            messagebox.showinfo("Trabajando", "Espera a que termine el análisis o la exportación.", parent=self.marco)
            return
        sel = self.tv.selection()
        if not sel:
            messagebox.showinfo("Elige filas", "Marca una o varias filas de la tabla (Ctrl para varias).", parent=self.marco)
            return
        for iid in sel:
            self.eleccion[self.clave(iid)] = clase
        self.cambios = True
        self.lbl_guardado.configure(text="Cambios sin guardar", style="Tarjeta.Acento.TLabel")
        self.pintar()

    def aceptar_aprendido(self):
        """Las filas marcadas toman lo que propone lo aprendido (las que no tienen propuesta se quedan como estaban)."""
        from tkinter import messagebox
        if self.bloqueada:
            return
        sel = [iid for iid in self.tv.selection() if self.sugerencias[int(iid)]]
        if not sel:
            messagebox.showinfo("Aceptar lo aprendido", "Marca filas que tengan algo en la columna \"Aprendido\" (Ctrl para varias).",
                                parent=self.marco)
            return
        for iid in sel:
            self.eleccion[self.clave(iid)] = self.sugerencias[int(iid)]["clase"]
        self.cambios = True
        self.lbl_guardado.configure(text="Cambios sin guardar", style="Tarjeta.Acento.TLabel")
        self.pintar()

    def decisiones(self):
        return [{"carpeta": c, "bin": b, "clase": k} for (c, b), k in sorted(self.eleccion.items()) if k]

    def guardar(self):
        if self.bloqueada:
            return
        eb.guardar_decisiones_origen(self.carpeta_brutos, self.decisiones())
        if self.ruta_aprendizaje:  # lo decidido se aprende (solo palabras y formas de nombre; nunca vídeo)
            cambios = [(g, self.eleccion.get((g["carpeta"], g["bin"]))) for g in self.grupos
                       if self.eleccion.get((g["carpeta"], g["bin"])) != g["elegido"]]
            if cambios:
                try:
                    ap.registrar(self.ruta_aprendizaje, cambios)
                except OSError as e:  # p. ej. la carpeta compartida no está: se guarda igual el origen de este disco
                    eb.LOG.warning("No se ha podido guardar lo aprendido en %s: %s", self.ruta_aprendizaje, e)
        if self.recordar.get() and self.recordar_reglas:
            cambiadas = [(g["carpeta"], g["bin"]) for g in self.grupos if self.eleccion.get((g["carpeta"], g["bin"])) != g["elegido"]]
            reglas = [eb.regla_de_origen(c, b, self.eleccion.get((c, b))) for c, b in cambiadas if c or b]
            if reglas:
                self.recordar_reglas(reglas)
        self.cambios = False
        self.lbl_guardado.configure(text="Guardado en origen.json", style="Tarjeta.Bien.TLabel")
        if self.al_guardar:
            self.al_guardar()

    def bloquear(self, si):
        self.bloqueada = si
        for b in self.botones + [self.bt_guardar]:
            b.state(["disabled"] if si else ["!disabled"])

    def cerrar(self):
        self.marco.destroy()
