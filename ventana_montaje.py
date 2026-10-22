# -*- coding: utf-8 -*-
"""
VENTANAS DE MONTAJE Y DE OPCIONES DE EXPORTACIÓN
================================================

- dialogo_montaje: revisar y corregir cada bruto antes de exportar. Visor de fotogramas
  (1 por segundo, 480x270, sin sonido), línea de tiempo por la que se arrastra, lista de
  clips y acciones (quitar, mover, cortar, renombrar, deshacer). La lógica está en
  montaje.py; aquí solo la ventana.
- dialogo_opciones: panel de opciones de exportación con los valores recomendados, que no
  deja exportar con un valor no válido.

Diseño en docs/diseno_montaje_exportacion.md. Necesita Pillow para mostrar los JPEG del
visor (sin Pillow la ventana funciona, pero sin imagen).
"""

import io
import queue
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import exportar_brutos as eb
import montaje as mj

try:
    from PIL import Image, ImageTk
    PIL_OK = True
except Exception:  # pragma: no cover
    PIL_OK = False

AZUL, NARANJA, GRIS, ROJO = "#4a78b5", "#e08a1e", "#9aa3ad", "#c00000"
VISOR_W, VISOR_H = 480, 270


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
# Pantalla de montaje
# ---------------------------------------------------------------------------

class PantallaMontaje:
    def __init__(self, raiz, m, origen, carpeta_visor, ffmpeg, hilos=2, proyectos=None):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.m, self.origen, self.carpeta_visor, self.ffmpeg = m, Path(origen), Path(carpeta_visor), ffmpeg
        self.proyectos = [p for p in m.proyectos() if proyectos is None or p in proyectos]
        self.resultado = None
        self.cambios = False
        self.p, self.k, self.t = None, 0, 0.0  # proyecto, bruto, segundo del cursor dentro del bruto
        self.zoom, self.reproduciendo = 1.0, False
        self.listos, self.pedidos, self.jpegs, self.fotos = {}, set(), {}, {}
        self.cola = queue.Queue()
        self.pool = ThreadPoolExecutor(max(1, hilos)) if ffmpeg else None

        v = self.v = tk.Toplevel(raiz)
        v.title("Montaje de los brutos")
        v.geometry("1260x840")
        v.minsize(1000, 700)
        v.transient(raiz)
        arriba = ttk.Frame(v, padding=(8, 8, 8, 0))
        arriba.pack(fill="x")
        ttk.Label(arriba, text="Proyecto:").pack(side="left")
        self.cb_p = ttk.Combobox(arriba, state="readonly", width=60)
        self.cb_p.pack(side="left", padx=6)
        self.lbl_p = ttk.Label(arriba, text="")
        self.lbl_p.pack(side="left", padx=6)

        cuerpo = ttk.Frame(v, padding=8)
        cuerpo.pack(fill="both", expand=True)
        izq = ttk.Frame(cuerpo, width=340)
        izq.pack(side="left", fill="y")
        der = ttk.Frame(cuerpo)
        der.pack(side="left", fill="both", expand=True, padx=(8, 0))

        ttk.Label(izq, text="Brutos (en rojo, los que pasan del máximo; ✎ nombre a mano):").pack(anchor="w")
        self.lb_b = tk.Listbox(izq, width=42, height=16, exportselection=False, font=("Segoe UI", 10))
        self.lb_b.pack(fill="both", expand=True)
        ttk.Button(izq, text="Renombrar bruto...", command=self.renombrar).pack(fill="x", pady=(4, 0))
        ttk.Button(izq, text="Volver al orden automático (todo el proyecto)", command=self.automatico).pack(fill="x", pady=(4, 0))
        ttk.Label(izq, text="Papelera del proyecto (no se exportan):").pack(anchor="w", pady=(10, 0))
        self.lb_q = tk.Listbox(izq, width=42, height=6, selectmode="extended", exportselection=False)
        self.lb_q.pack(fill="x")
        ttk.Button(izq, text="Recuperar los marcados", command=self.recuperar).pack(fill="x", pady=(4, 0))
        self.lbl_n = ttk.Label(izq, text="", foreground=ROJO)
        self.lbl_n.pack(anchor="w", pady=(10, 0))
        self.lb_n = tk.Listbox(izq, width=42, height=4, selectmode="extended", exportselection=False)
        self.bt_n = ttk.Button(izq, text="Colocar por fecha los marcados", command=self.colocar)

        arriba_d = ttk.Frame(der)
        arriba_d.pack(fill="x")
        self.visor = tk.Canvas(arriba_d, width=VISOR_W, height=VISOR_H, bg="#111", highlightthickness=0)
        self.visor.pack(side="left")
        info = ttk.Frame(arriba_d, padding=(10, 0))
        info.pack(side="left", fill="both", expand=True)
        self.lbl_clip = ttk.Label(info, text="", font=("Segoe UI", 11, "bold"), wraplength=330, justify="left")
        self.lbl_clip.pack(anchor="w")
        self.lbl_tc = ttk.Label(info, text="", font=("Consolas", 12))
        self.lbl_tc.pack(anchor="w", pady=(6, 0))
        self.lbl_bruto = ttk.Label(info, text="", wraplength=330, justify="left")
        self.lbl_bruto.pack(anchor="w", pady=(6, 0))
        ctrl = ttk.Frame(info)
        ctrl.pack(anchor="w", pady=(12, 0))
        self.bt_play = ttk.Button(ctrl, text="▶ Avance", command=self.reproducir)
        self.bt_play.pack(side="left")
        self.cb_vel = ttk.Combobox(ctrl, state="readonly", width=12, values=["x1", "x10", "x60 (1 min/s)"])
        self.cb_vel.current(1)
        self.cb_vel.pack(side="left", padx=6)
        ctrl2 = ttk.Frame(info)
        ctrl2.pack(anchor="w", pady=(6, 0))
        ttk.Label(ctrl2, text="Línea de tiempo:").pack(side="left")
        ttk.Button(ctrl2, text="Zoom +", command=lambda: self.ampliar(2)).pack(side="left", padx=(6, 0))
        ttk.Button(ctrl2, text="Zoom −", command=lambda: self.ampliar(0.5)).pack(side="left", padx=4)
        self.lbl_foto = ttk.Label(info, text="", foreground="#555")
        self.lbl_foto.pack(anchor="w", pady=(10, 0))

        lt = ttk.Frame(der)
        lt.pack(fill="x", pady=(8, 0))
        self.linea = tk.Canvas(lt, height=74, bg="#f4f5f7", highlightthickness=1, highlightbackground="#ccc")
        self.linea.pack(fill="x")
        self.sb_linea = ttk.Scrollbar(lt, orient="horizontal", command=self.linea.xview)
        self.sb_linea.pack(fill="x")
        self.linea.configure(xscrollcommand=self.sb_linea.set)

        tabla = ttk.Frame(der)
        tabla.pack(fill="both", expand=True, pady=(6, 0))
        cols = ("n", "clip", "fecha", "dur", "origen", "montaje")
        self.tv = ttk.Treeview(tabla, columns=cols, show="headings", selectmode="extended", height=10)
        for c, t, w in zip(cols, ("Nº", "Clip", "Fecha y hora", "Duración", "Origen", "Montaje"), (40, 250, 150, 80, 170, 110)):
            self.tv.heading(c, text=t)
            self.tv.column(c, width=w, anchor="w", stretch=c == "clip")
        self.tv.tag_configure("movido", foreground=NARANJA)
        self.tv.tag_configure("audio", foreground=GRIS)
        sb = ttk.Scrollbar(tabla, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=sb.set)
        self.tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        acc = ttk.Frame(der)
        acc.pack(fill="x", pady=(6, 0))
        for texto, orden in (("Quitar", self.quitar), ("Subir", lambda: self.accion("subir")), ("Bajar", lambda: self.accion("bajar")),
                             ("← Al bruto anterior", lambda: self.accion("mover", -1)), ("Al bruto siguiente →", lambda: self.accion("mover", 1)),
                             ("Cortar aquí", self.cortar), ("Deshacer (Ctrl+Z)", self.deshacer)):
            ttk.Button(acc, text=texto, command=orden).pack(side="left", padx=(0, 4))

        pie = ttk.Frame(v, padding=8)
        pie.pack(fill="x")
        self.lbl_estado = ttk.Label(pie, text="")
        self.lbl_estado.pack(side="left")
        ttk.Button(pie, text="Guardar y exportar...", command=lambda: self.cerrar("exportar")).pack(side="right")
        ttk.Button(pie, text="Guardar", command=lambda: self.cerrar("guardado")).pack(side="right", padx=6)
        ttk.Button(pie, text="Cancelar", command=lambda: self.cerrar(None)).pack(side="right")

        self.cb_p.bind("<<ComboboxSelected>>", lambda e: self.elegir_proyecto(self.proyectos[self.cb_p.current()]))
        self.lb_b.bind("<<ListboxSelect>>", self.al_elegir_bruto)
        self.tv.bind("<<TreeviewSelect>>", self.al_elegir_clip)
        self.linea.bind("<Button-1>", self.al_pulsar_linea)
        self.linea.bind("<B1-Motion>", self.al_pulsar_linea)
        self.linea.bind("<Configure>", lambda e: self.pintar_linea())
        v.bind("<Control-z>", lambda e: self.deshacer())
        v.bind("<Left>", lambda e: self.mover_cursor(-1))
        v.bind("<Right>", lambda e: self.mover_cursor(1))
        v.protocol("WM_DELETE_WINDOW", lambda: self.cerrar(None))
        if not PIL_OK:
            self.lbl_foto.configure(text="Falta Pillow: el visor no puede mostrar imágenes.")
        if not ffmpeg:
            self.lbl_foto.configure(text="Sin ffmpeg: no se pueden sacar los fotogramas del visor.")
        if m.avisos:
            self.lbl_estado.configure(text="Avisos: " + " · ".join(m.avisos), foreground=ROJO)
        self.pintar_proyectos()
        if self.proyectos:
            self.cb_p.current(0)
            self.elegir_proyecto(self.proyectos[0])
        v.after(150, self.vaciar_cola)

    # --- datos -------------------------------------------------------------------------------------------------

    def bruto(self):
        bs = self.m.brutos(self.p)
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
        return [self.claves_tv[int(i)] for i in self.tv.selection()]

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
        self.lb_b.delete(0, "end")
        for n, b in enumerate(self.m.brutos(self.p), 1):
            self.lb_b.insert("end", "%d. %s%s · %s · %d clips" % (n, "✎ " if b["manual"] else "", b["nombre"], fmt_h(b["segundos"]), len(b["clips"])))
            if b["largo"]:
                self.lb_b.itemconfig("end", foreground=ROJO)
        if self.lb_b.size():
            self.k = min(self.k, self.lb_b.size() - 1)
            self.lb_b.selection_clear(0, "end")
            self.lb_b.selection_set(self.k)
            self.lb_b.see(self.k)
        self.lb_q.delete(0, "end")
        for c in self.m.papelera(self.p):
            self.lb_q.insert("end", c["nombre"])
        nuevos = self.m.nuevos(self.p)
        self.lb_n.delete(0, "end")
        for c in nuevos:
            self.lb_n.insert("end", c["nombre"])
        if nuevos:
            self.lbl_n.configure(text="Clips nuevos sin colocar (no se exportan):")
            self.lb_n.pack(fill="x")
            self.bt_n.pack(fill="x", pady=(4, 0))
        else:
            self.lbl_n.configure(text="")
            self.lb_n.pack_forget()
            self.bt_n.pack_forget()
        movidos = sum(1 for b in self.m.brutos(self.p) for c in b["clips"] if c.get("clave") in self.m.movidos(self.p))
        self.lbl_p.configure(text="%s%s" % ("Montaje a mano" if self.m.tocado(self.p) else "Reparto automático",
                                            " · %d clips movidos" % movidos if movidos else ""))

    def pintar_tabla(self, mantener=()):
        self.tv.delete(*self.tv.get_children())
        self.claves_tv = []
        movidos = self.m.movidos(self.p)
        for n, (c, _, d) in enumerate(self.tramos()):
            f = self.m.fecha(c)
            tags = ("movido",) if c.get("clave") in movidos else ("audio",) if not c.get("fichero_video") else ()
            self.tv.insert("", "end", iid=str(n), tags=tags, values=(
                n + 1, c["nombre"] + ("" if c.get("fichero_video") else "  (solo audio: no se exporta)"),
                f.strftime("%d/%m/%Y %H:%M:%S") if f else "sin fecha", fmt_h(d), c.get("origen") or "",
                "movido a mano" if c.get("clave") in movidos else "automático"))
            self.claves_tv.append(c.get("clave"))
        sel = [str(i) for i, k in enumerate(self.claves_tv) if k in mantener]
        if sel:
            self.tv.selection_set(sel)
            self.tv.see(sel[0])

    def escala(self):
        total = sum(d for _, _, d in self.tramos()) or 1
        ancho = max(200, self.linea.winfo_width() - 4)
        return ancho * self.zoom / total, total

    def pintar_linea(self):
        cv = self.linea
        cv.delete("all")
        pps, total = self.escala()
        movidos = self.m.movidos(self.p) if self.p else set()
        sel = set(self.seleccion()) if hasattr(self, "claves_tv") else set()
        for c, t, d in self.tramos():
            x0, x1 = 2 + t * pps, 2 + (t + d) * pps
            color = GRIS if not c.get("fichero_video") else NARANJA if c.get("clave") in movidos else AZUL
            cv.create_rectangle(x0, 10, max(x1, x0 + 2), 58, fill=color, outline="#000" if c.get("clave") in sel else "#fff",
                                width=2 if c.get("clave") in sel else 1)
            if x1 - x0 > 50:
                cv.create_text(x0 + 4, 34, text=c["nombre"], anchor="w", fill="#fff", font=("Segoe UI", 8), width=x1 - x0 - 6)
        x = 2 + self.t * pps
        cv.create_line(x, 2, x, 70, fill=ROJO, width=2)
        cv.configure(scrollregion=(0, 0, 4 + total * pps, 74))

    def donde_cursor(self):
        tramos = self.tramos()
        for n, (c, t, d) in enumerate(tramos):
            if t <= self.t < t + d or (n == len(tramos) - 1 and self.t >= t):
                return n, c, self.t - t
        return None, None, 0

    def pintar_visor(self):
        self.visor.delete("all")
        n, c, dentro = self.donde_cursor()
        b = self.bruto()
        total = sum(d for _, _, d in self.tramos())
        self.lbl_tc.configure(text="TC en el bruto %s\nde %s en total" % (fmt_tc(self.t), fmt_tc(total)))
        if b:
            self.lbl_bruto.configure(text="Bruto %d: %s · %s%s" % (self.k + 1, b["nombre"], fmt_h(b["segundos"]),
                                                                   " · PASA DEL MÁXIMO" if b["largo"] else ""),
                                     foreground=ROJO if b["largo"] else "#000")
        if c is None:
            self.lbl_clip.configure(text="")
            return
        f = self.m.fecha(c)
        self.lbl_clip.configure(text="%d. %s\n%s\nTC original %s" % (n + 1, c["nombre"], f.strftime("%d/%m/%Y %H:%M:%S") if f else "sin fecha",
                                                                      c.get("timecode") or "—"))
        ruta = self.listos.get(c.get("clave"))
        if not ruta or not PIL_OK:
            self.visor.create_text(VISOR_W / 2, VISOR_H / 2, fill="#bbb", text="Preparando fotogramas..." if self.ffmpeg and PIL_OK else "Sin imagen")
            return
        jpgs = self.jpegs.get(c.get("clave"))
        if jpgs is None:
            jpgs = self.jpegs[c.get("clave")] = mj.fotogramas(ruta)
            if len(self.jpegs) > 30:
                self.jpegs.pop(next(iter(self.jpegs)))
        if not jpgs:
            self.visor.create_text(VISOR_W / 2, VISOR_H / 2, fill="#bbb", text="Sin fotogramas")
            return
        i = min(int(dentro), len(jpgs) - 1)
        clave = (c.get("clave"), i)
        foto = self.fotos.get(clave)
        if foto is None:
            foto = self.fotos[clave] = ImageTk.PhotoImage(Image.open(io.BytesIO(jpgs[i])), master=self.v)
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
        self.pedir_fotogramas()

    # --- fotogramas ----------------------------------------------------------------------------------------------

    def pedir_fotogramas(self):
        if not self.pool:
            return
        for c, _, _ in self.tramos():
            k = c.get("clave")
            if not c.get("fichero_video") or k in self.pedidos:
                continue
            self.pedidos.add(k)
            ruta = mj.fichero_visor(self.carpeta_visor, k)
            if ruta.exists() and ruta.stat().st_size:
                self.listos[k] = ruta
                continue
            fut = self.pool.submit(mj.sacar_fotogramas, self.ffmpeg, self.origen / c["fichero_video"], self.carpeta_visor, k)
            fut.add_done_callback(lambda f, k=k: self.cola.put((k, f.result() if not f.cancelled() and not f.exception() else None)))
        self.estado_fotogramas()

    def estado_fotogramas(self):
        cs = [c for c, _, _ in self.tramos() if c.get("fichero_video")]
        hechos = sum(1 for c in cs if c.get("clave") in self.listos)
        if PIL_OK and self.ffmpeg:
            self.lbl_foto.configure(text="Fotogramas de este bruto: %d de %d clips%s" % (hechos, len(cs), "" if hechos == len(cs) else " (se puede ir mirando)"))

    def vaciar_cola(self):
        hubo = False
        try:
            while True:
                k, ruta = self.cola.get_nowait()
                if ruta:
                    self.listos[k] = ruta
                hubo = True
        except queue.Empty:
            pass
        if hubo:
            self.estado_fotogramas()
            self.pintar_visor()
        if self.v.winfo_exists():
            self.v.after(200, self.vaciar_cola)

    # --- eventos -----------------------------------------------------------------------------------------------

    def elegir_proyecto(self, p):
        self.p, self.k, self.t = p, 0, 0.0
        self.refrescar()

    def al_elegir_bruto(self, _=None):
        sel = self.lb_b.curselection()
        if sel and sel[0] != self.k:
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

    def al_pulsar_linea(self, e):
        pps, total = self.escala()
        self.t = max(0.0, min(total, (self.linea.canvasx(e.x) - 2) / pps))
        n, _, _ = self.donde_cursor()
        self.pintar_linea()
        self.pintar_visor()
        if n is not None:
            self.tv.see(str(n))

    def mover_cursor(self, segundos):
        total = sum(d for _, _, d in self.tramos())
        self.t = max(0.0, min(total, self.t + segundos))
        self.pintar_linea()
        self.pintar_visor()

    def ampliar(self, factor):
        self.zoom = max(1.0, min(256.0, self.zoom * factor))
        self.pintar_linea()
        pps, total = self.escala()
        if total:
            self.linea.xview_moveto(max(0.0, (self.t * pps - self.linea.winfo_width() / 2) / (total * pps + 4)))

    def reproducir(self):
        self.reproduciendo = not self.reproduciendo
        self.bt_play.configure(text="⏸ Parar" if self.reproduciendo else "▶ Avance")
        if self.reproduciendo:
            self.paso()

    def paso(self):
        if not self.reproduciendo or not self.v.winfo_exists():
            return
        vel = {0: 1, 1: 10, 2: 60}[self.cb_vel.current()]
        total = sum(d for _, _, d in self.tramos())
        self.t += vel * 0.2
        if self.t >= total:
            self.t = total
            self.reproducir()
        self.pintar_linea()
        self.pintar_visor()
        if self.reproduciendo:
            self.v.after(200, self.paso)

    # --- acciones ----------------------------------------------------------------------------------------------

    def hecho(self, mantener=()):
        self.cambios = True
        self.refrescar(mantener)

    def sin_seleccion(self):
        from tkinter import messagebox
        messagebox.showinfo("Elige clips", "Marca uno o varios clips en la lista (Ctrl o Mayús para varios).", parent=self.v)

    def quitar(self):
        sel = self.seleccion()
        if not sel:
            return self.sin_seleccion()
        self.m.quitar(self.p, sel)
        self.hecho()

    def accion(self, que, delta=None):
        sel = self.seleccion()
        if not sel:
            return self.sin_seleccion()
        if que == "mover":
            self.m.mover(self.p, sel, delta)
            nuevo = self.m.donde(self.p, sel[0])
            if nuevo is not None:  # se sigue a los clips movidos
                self.k = nuevo
                tramo = next(t for c, t, _ in self.tramos() if c.get("clave") == sel[0])
                self.t = tramo
        else:
            getattr(self.m, que)(self.p, sel)
        self.hecho(sel)

    def cortar(self):
        from tkinter import messagebox
        sel = self.seleccion()
        if len(sel) != 1:
            return messagebox.showinfo("Cortar", "Marca un solo clip: el bruto acabará justo antes de él.", parent=self.v)
        self.m.cortar(self.p, sel[0])
        self.hecho()

    def renombrar(self):
        from tkinter import messagebox, simpledialog
        b = self.bruto()
        if not b:
            return
        nombre = simpledialog.askstring("Renombrar bruto", "Nombre del fichero (vacío = el automático):", initialvalue=b["nombre"], parent=self.v)
        if nombre is None:
            return
        errores = eb.validar_nombres([nombre]) if nombre.strip() else []
        if errores:
            return messagebox.showwarning("Nombre no válido", "\n".join(errores), parent=self.v)
        self.m.renombrar(self.p, self.k, nombre)
        self.hecho()

    def recuperar(self):
        papelera = self.m.papelera(self.p)
        sel = [papelera[i].get("clave") for i in self.lb_q.curselection()]
        if sel:
            self.m.recuperar(self.p, sel)
            self.hecho()

    def colocar(self):
        nuevos = self.m.nuevos(self.p)
        sel = [nuevos[i].get("clave") for i in self.lb_n.curselection()]
        if sel:
            self.m.colocar(self.p, sel)
            self.hecho()

    def deshacer(self):
        if self.m.deshacer():
            self.hecho()

    def automatico(self):
        from tkinter import messagebox
        if messagebox.askyesno("Orden automático", "¿Deshacer todos los cambios a mano de %s y volver al orden cronológico "
                                                   "y a los cortes automáticos?" % self.p, parent=self.v):
            self.m.volver_automatico(self.p)
            self.k, self.t = 0, 0.0
            self.hecho()

    def cerrar(self, resultado):
        from tkinter import messagebox
        if resultado is None and self.cambios and not messagebox.askyesno("Salir sin guardar", "Hay cambios sin guardar. ¿Salir sin guardarlos?",
                                                                           parent=self.v):
            return
        self.reproduciendo = False
        self.resultado = resultado
        if self.pool:
            self.pool.shutdown(wait=False, cancel_futures=True)
        self.v.destroy()


def dialogo_montaje(raiz, m, origen, carpeta_visor, carpeta_brutos, ffmpeg, hilos=2, proyectos=None):
    """Abre la pantalla de montaje. Devuelve "guardado", "exportar" (guardado y pide exportar) o None (cancelado).
    Al guardar, escribe montaje.json en la carpeta de brutos."""
    pantalla = PantallaMontaje(raiz, m, origen, carpeta_visor, ffmpeg, hilos, proyectos)
    pantalla.v.grab_set()
    raiz.wait_window(pantalla.v)
    if pantalla.resultado:
        m.guardar(carpeta_brutos)
    return pantalla.resultado


# ---------------------------------------------------------------------------
# Panel de opciones de exportación
# ---------------------------------------------------------------------------

FORMATOS = [("xdcam_hd50", "XDCAM HD422 50 Mb/s 1080i25 16:9 (recomendado: lo pide el Archivo)"),
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
        self.tk, self.ttk = tk, ttk
        self.cfg, self.resumen, self.guardar_predeterminados = dict(cfg), resumen, guardar_predeterminados
        self.resultado = None
        v = self.v = tk.Toplevel(raiz)
        v.title("Opciones de exportación")
        v.geometry("1200x860")
        v.transient(raiz)
        marco = ttk.Frame(v, padding=10)
        marco.pack(fill="both", expand=True)
        izq, der = ttk.Frame(marco), ttk.Frame(marco)
        izq.pack(side="left", fill="both", expand=True)
        der.pack(side="left", fill="both", expand=True, padx=(10, 0))

        f = ttk.LabelFrame(izq, text="Vídeo", padding=8)
        f.pack(fill="x")
        self.formato = ttk.Combobox(f, state="readonly", width=64, values=[t for _, t in FORMATOS])
        self.formato.pack(anchor="w")

        f = ttk.LabelFrame(izq, text="Timecode", padding=8)
        f.pack(fill="x", pady=(8, 0))
        fila = ttk.Frame(f)
        fila.pack(anchor="w")
        ttk.Label(fila, text="TC de inicio de cada bruto (hh:mm:ss:ff):").pack(side="left")
        self.tc = tk.StringVar()
        ttk.Entry(fila, textvariable=self.tc, width=14, font=("Consolas", 11)).pack(side="left", padx=6)
        ttk.Label(f, text="Recomendado 00:00:00:00. El TC interno del vídeo se ajusta al del fichero.", foreground="#555").pack(anchor="w")

        f = ttk.LabelFrame(izq, text="Audio: pistas y enrutado", padding=8)
        f.pack(fill="x", pady=(8, 0))
        fila = ttk.Frame(f)
        fila.pack(anchor="w")
        ttk.Label(fila, text="Pistas del bruto:").pack(side="left")
        self.pistas = ttk.Combobox(fila, state="readonly", width=48, values=[t for _, t in PISTAS])
        self.pistas.pack(side="left", padx=6)
        ttk.Label(f, text="XDCAM HD422 solo admite 4 u 8 pistas: se redondea y las que falten van en silencio.", foreground="#555").pack(anchor="w")
        rejilla = ttk.Frame(f)
        rejilla.pack(anchor="w", pady=(6, 0))
        self.fuentes, self.ganancias = [], []
        ttk.Label(rejilla, text="Salida").grid(row=0, column=0, sticky="w")
        ttk.Label(rejilla, text="Qué lleva").grid(row=0, column=1, sticky="w")
        ttk.Label(rejilla, text="Ganancia (dB)").grid(row=0, column=2, sticky="w", padx=(8, 0))
        for k in range(eb.MAX_PISTAS):
            ttk.Label(rejilla, text="Pista %d" % (k + 1)).grid(row=k + 1, column=0, sticky="w")
            cb = ttk.Combobox(rejilla, state="readonly", width=18, values=FUENTES)
            cb.grid(row=k + 1, column=1, sticky="w", pady=1)
            sp = tk.Spinbox(rejilla, from_=-20, to=20, increment=0.5, width=6)
            sp.grid(row=k + 1, column=2, sticky="w", padx=(8, 0))
            self.fuentes.append(cb)
            self.ganancias.append(sp)

        f = ttk.LabelFrame(izq, text="Audio: niveles", padding=8)
        f.pack(fill="x", pady=(8, 0))
        self.niveles = tk.StringVar()
        for valor, texto in NIVELES:
            ttk.Radiobutton(f, text=texto, value=valor, variable=self.niveles).pack(anchor="w")

        f = ttk.LabelFrame(der, text="Comprobaciones (recomendado: todas)", padding=8)
        f.pack(fill="x")
        self.md5, self.ficha, self.medidor = tk.BooleanVar(), tk.BooleanVar(), tk.BooleanVar()
        ttk.Checkbutton(f, text="MD5 de cada bruto (integridad al copiarlo)", variable=self.md5).pack(anchor="w")
        ttk.Checkbutton(f, text="Ficha técnica: conforme o no con XDCAM HD422", variable=self.ficha).pack(anchor="w")
        ttk.Checkbutton(f, text="Medidor de audio: avisa de saturados y casi mudos (no los toca)", variable=self.medidor).pack(anchor="w")

        f = ttk.LabelFrame(der, text="Nombres de los ficheros", padding=8)
        f.pack(fill="both", expand=True, pady=(8, 0))
        fila = ttk.Frame(f)
        fila.pack(anchor="w")
        ttk.Label(fila, text="Prefijo (opcional):").pack(side="left")
        self.prefijo = tk.StringVar()
        ttk.Entry(fila, textvariable=self.prefijo, width=20).pack(side="left", padx=6)
        ttk.Label(f, text="Cada bruto se puede renombrar en la pantalla de montaje.", foreground="#555").pack(anchor="w")
        self.lb_nombres = tk.Listbox(f, height=10)
        self.lb_nombres.pack(fill="both", expand=True, pady=(4, 0))

        f = ttk.LabelFrame(der, text="Resumen", padding=8)
        f.pack(fill="x", pady=(8, 0))
        self.lbl_resumen = ttk.Label(f, text="", justify="left", wraplength=520)
        self.lbl_resumen.pack(anchor="w")
        self.lbl_errores = ttk.Label(der, text="", foreground=ROJO, justify="left", wraplength=520)
        self.lbl_errores.pack(anchor="w", pady=(8, 0))

        pie = ttk.Frame(v, padding=10)
        pie.pack(fill="x")
        ttk.Button(pie, text="Restablecer recomendados", command=lambda: self.poner(eb.CONFIG_DEFECTO)).pack(side="left")
        if guardar_predeterminados:
            ttk.Button(pie, text="Guardar como predeterminados", command=self.predeterminados).pack(side="left", padx=6)
        self.bt_exportar = ttk.Button(pie, text="Exportar", command=self.exportar)
        self.bt_exportar.pack(side="right")
        ttk.Button(pie, text="Cancelar", command=v.destroy).pack(side="right", padx=6)

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
        self.md5.set(bool(cfg.get("md5", True)))
        self.ficha.set(bool(cfg.get("ficha_tecnica", True)))
        self.medidor.set(bool(cfg.get("medidor_audio", True)))
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
            sp.configure(state="normal" if cfg["niveles_audio"] == "ganancia" else "disabled")
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
        if cfg["formato_salida"] != "xdcam_hd50":
            texto += " (estimado como XDCAM; en otro formato varía)"
        if libre is not None:
            texto += " · libres en el destino: %.0f GB" % (libre / 1e9)
            if necesario > libre:
                errores.append("No cabe: hacen falta unos %.0f GB y hay %.0f GB libres en %s" % (necesario / 1e9, libre / 1e9, self.resumen.get("destino", "el destino")))
        texto += "\nEl tiempo estimado sale en el registro tras el primer bruto (depende del PC)."
        if cfg["niveles_audio"] == "r128":
            texto += "\nAtención: la normalización cambia el sonido original de cada clip."
        self.lbl_resumen.configure(text=texto)
        self.lbl_errores.configure(text="\n".join(errores))
        self.bt_exportar.configure(state="disabled" if errores else "normal")
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
    """Panel de opciones. resumen: {"brutos": [nombres], "segundos": float, "pistas_max": int, "libre": bytes|None,
    "destino": str}. Devuelve la configuración elegida o None si se cancela."""
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
