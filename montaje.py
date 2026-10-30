# -*- coding: utf-8 -*-
"""
MONTAJE DE LOS BRUTOS
=====================

Lógica (sin ventana) de la pantalla de montaje: qué clips van en cada bruto y en
qué orden, antes de exportar. Diseño en docs/diseno_montaje_exportacion.md.

- Se parte del reparto automático (exportar_brutos.trozos_proyecto: orden
  cronológico estricto, brutos de hasta horas_por_bruto).
- Sobre él se puede quitar clips (van a la papelera del proyecto), moverlos
  (dentro del bruto o al anterior/siguiente), cortar, renombrar y deshacer.
- Solo los proyectos tocados se guardan en montaje.json (carpeta de brutos); los
  demás siguen con el reparto automático. Lo que se ve es lo que se exporta.
- Si se repite el análisis, las claves que ya no existen se quitan y los clips
  nuevos esperan en "nuevos" (no se exportan) hasta que se colocan.

También saca los fotogramas del visor (1 por segundo, 480x270) a un .mjpeg por
clip en la carpeta del análisis, el fotograma exacto donde se para el cursor y la
orden para ver y oír un clip de verdad con ffplay.
"""

import copy
import datetime as dt
import hashlib
import json
import logging
import shutil
import subprocess
import uuid
from pathlib import Path

import exportar_brutos as eb

LOG = logging.getLogger("diagnostico")

FICHERO = "montaje.json"
MAX_DESHACER = 50


class Montaje:
    """listas: {proyecto: {"material": [clips], "piezas": [clips]}} de exportar_brutos.listas_por_proyecto (en orden
    cronológico); fecha: clip -> datetime | None; tope: segundos por bruto; guardado: "proyectos" de montaje.json."""

    def __init__(self, listas, fecha, tope, guardado=None, descartados=None, salto=eb.SALTO_DIAS):
        self.listas, self.fecha, self.tope, self.salto = listas, fecha, tope, salto
        self.descartados = descartados or {}  # {proyecto: [(clip, motivo)]}: lo que no entra en ningún bruto
        self.mxf_por_ruta = {}  # lo que dice el análisis de cada fichero (para oír el clip: sus canales de sonido)
        self.por_clave = {c.get("clave"): c for l in listas.values() for cs in l.values() for c in cs}
        self.orden = {p: {c.get("clave"): (f, i) for f in ("material", "piezas") for i, c in enumerate(l.get(f) or [])}
                      for p, l in listas.items()}
        self.estados = {}  # solo los proyectos tocados
        self.sin_colocar = {}
        self.historia = []
        self.avisos = []
        for p, e in sorted((guardado or {}).items()):
            self._restaurar(p, e)

    # --- consulta -------------------------------------------------------------------------------------------------

    def proyectos(self):
        return sorted(self.listas)

    def tocado(self, p):
        return p in self.estados

    def _estado(self, p):
        return self.estados.get(p) or self._automatico(p)

    def _automatico(self, p):
        trozos = eb.trozos_proyecto(self.listas[p], self.fecha, self.tope, salto=self.salto)
        # los trozos repetidos de otro clip (exportar_brutos.es_duplicado) empiezan en la papelera: se pueden recuperar
        return {"brutos": [{"nombre": None, "tipo": f, "clips": [c.get("clave") for c in t]} for f, t in trozos],
                "papelera": [c.get("clave") for c in self.listas[p].get("duplicados") or []], "movidos": []}

    def _segundos(self, claves):
        return sum((self.por_clave[k].get("duracion") or 0) for k in claves if self.por_clave[k].get("fichero_video"))

    def brutos(self, p):
        """[{nombre, manual, tipo, clips, segundos, largo}] del proyecto, en orden."""
        e = self._estado(p)
        auto = iter(eb.numerar([eb.nombre_bruto(p, [self.fecha(self.por_clave[k]) for k in b["clips"]], b["tipo"] == "piezas")
                                for b in e["brutos"] if not b["nombre"]]))
        salida = []
        for b in e["brutos"]:
            nombre = b["nombre"] or next(auto)
            s = self._segundos(b["clips"])
            salida.append({"nombre": b["nombre"] or nombre, "manual": bool(b["nombre"]), "tipo": b["tipo"],
                           "clips": [self.por_clave[k] for k in b["clips"]], "segundos": s, "largo": s > self.tope})
        return salida

    def papelera(self, p):
        return [self.por_clave[k] for k in self._estado(p)["papelera"]]

    def movidos(self, p):
        return set(self._estado(p)["movidos"])

    def fuera_del_bruto(self, p):
        """[(clip, motivo)] de lo que se queda fuera de los brutos del proyecto (efectos de Avid, agencias, importados)."""
        return list(self.descartados.get(p, []))

    def nuevos(self, p):
        return [self.por_clave[k] for k in self.sin_colocar.get(p, [])]

    def donde(self, p, clave):
        """Índice del bruto que contiene la clave, o None."""
        return next((i for i, b in enumerate(self._estado(p)["brutos"]) if clave in b["clips"]), None)

    # --- cambios --------------------------------------------------------------------------------------------------

    def _cambiar(self, p):
        """Guarda el estado anterior para deshacer y devuelve el estado editable del proyecto."""
        self.historia.append((p, copy.deepcopy(self.estados.get(p)), list(self.sin_colocar.get(p, []))))
        del self.historia[:-MAX_DESHACER]
        if p not in self.estados:
            self.estados[p] = self._automatico(p)
        return self.estados[p]

    @staticmethod
    def _limpiar(e):
        e["brutos"] = [b for b in e["brutos"] if b["clips"]]

    def deshacer(self):
        if not self.historia:
            return False
        p, e, nuevos = self.historia.pop()
        if e is None:
            self.estados.pop(p, None)
        else:
            self.estados[p] = e
        self.sin_colocar[p] = nuevos
        return True

    def quitar(self, p, claves):
        e = self._cambiar(p)
        for b in e["brutos"]:
            b["clips"] = [k for k in b["clips"] if k not in claves]
        e["papelera"] += [k for k in claves if k not in e["papelera"]]
        self._limpiar(e)

    def _insertar_por_orden(self, e, p, clave):
        """Coloca la clave junto a su vecino más cercano en el orden automático (mismo tipo): tras el anterior que esté
        colocado o, si no hay, delante del siguiente. Si el tipo no tiene ningún clip colocado, en un bruto nuevo."""
        tipo, i = self.orden[p][clave]
        colocados = [(self.orden[p][k][1], n, j) for n, b in enumerate(e["brutos"]) if b["tipo"] == tipo
                     for j, k in enumerate(b["clips"]) if k in self.orden[p]]
        antes = [x for x in colocados if x[0] < i]
        despues = [x for x in colocados if x[0] > i]
        if antes:
            _, n, j = max(antes)
            e["brutos"][n]["clips"].insert(j + 1, clave)
        elif despues:
            _, n, j = min(despues)
            e["brutos"][n]["clips"].insert(j, clave)
        else:
            nuevo = {"nombre": None, "tipo": tipo, "clips": [clave]}
            if tipo == "material":
                e["brutos"].insert(0, nuevo)
            else:
                e["brutos"].append(nuevo)

    def recuperar(self, p, claves):
        e = self._cambiar(p)
        for k in claves:
            if k in e["papelera"]:
                e["papelera"].remove(k)
                self._insertar_por_orden(e, p, k)
                e.setdefault("recuperados", []).append(k)  # un trozo repetido recuperado a mano no vuelve a la papelera

    def colocar(self, p, claves):
        """Coloca clips nuevos (de un análisis posterior) en su sitio por fecha."""
        e = self._cambiar(p)
        for k in claves:
            if k in self.sin_colocar.get(p, []):
                self.sin_colocar[p].remove(k)
                self._insertar_por_orden(e, p, k)

    def _un_bruto(self, e, claves):
        sitios = {n for n, b in enumerate(e["brutos"]) for k in b["clips"] if k in claves}
        if len(sitios) != 1:
            raise ValueError("Elige clips de un solo bruto")
        return sitios.pop()

    def mover(self, p, claves, delta):
        """Pasa los clips al bruto anterior (delta -1, al final) o al siguiente (+1, al principio). Si no hay, se crea
        uno nuevo. Puede pasar de horas_por_bruto: se marca en rojo, no se impide."""
        e = self._cambiar(p)
        n = self._un_bruto(e, claves)
        b = e["brutos"][n]
        sel = [k for k in b["clips"] if k in claves]
        b["clips"] = [k for k in b["clips"] if k not in claves]
        destino = n + delta
        if destino < 0 or destino >= len(e["brutos"]):
            e["brutos"].insert(max(destino, 0) if delta < 0 else len(e["brutos"]), {"nombre": None, "tipo": b["tipo"], "clips": sel})
        elif delta < 0:
            e["brutos"][destino]["clips"] += sel
        else:
            e["brutos"][destino]["clips"][:0] = sel
        e["movidos"] += [k for k in sel if k not in e["movidos"]]
        self._limpiar(e)

    def _desplazar(self, p, claves, paso):
        e = self._cambiar(p)
        b = e["brutos"][self._un_bruto(e, claves)]["clips"]
        orden = range(len(b)) if paso < 0 else range(len(b) - 1, -1, -1)
        for i in orden:
            j = i + paso
            if b[i] in claves and 0 <= j < len(b) and b[j] not in claves:
                b[i], b[j] = b[j], b[i]
                if b[j] not in e["movidos"]:
                    e["movidos"].append(b[j])

    def subir(self, p, claves):
        self._desplazar(p, claves, -1)

    def bajar(self, p, claves):
        self._desplazar(p, claves, +1)

    def cortar(self, p, clave):
        """El bruto acaba antes de este clip. Este clip y los de los brutos siguientes se reparten de nuevo hasta
        horas_por_bruto, en el orden en que están; se para en el primer bruto sin fecha, de otro tipo o con nombre
        puesto a mano (ese queda como está)."""
        e = self._cambiar(p)
        n = self._un_bruto(e, [clave])
        b = e["brutos"][n]
        i = b["clips"].index(clave)
        if i == 0:
            return
        fin = n + 1
        while fin < len(e["brutos"]) and e["brutos"][fin]["tipo"] == b["tipo"] and not e["brutos"][fin]["nombre"] and \
                any(self.fecha(self.por_clave[k]) for k in e["brutos"][fin]["clips"]):
            fin += 1
        resto = b["clips"][i:] + [k for x in e["brutos"][n + 1:fin] for k in x["clips"]]
        b["clips"] = b["clips"][:i]
        cs = [self.por_clave[k] for k in resto]
        nuevos = [{"nombre": None, "tipo": b["tipo"], "clips": [c.get("clave") for c in t]} for t in eb.trozos_hasta(cs, self.tope)]
        e["brutos"][n + 1:fin] = nuevos

    def renombrar(self, p, n, nombre):
        e = self._cambiar(p)
        e["brutos"][n]["nombre"] = (nombre or "").strip() or None

    def volver_automatico(self, p):
        self._cambiar(p)
        del self.estados[p]

    # --- guardar y cargar -----------------------------------------------------------------------------------------

    def a_json(self):
        ahora = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return {"_comentario": "Montaje de los brutos (pantalla de montaje). Solo los proyectos tocados; los demás van con el "
                               "reparto automático. Borra un proyecto (o el fichero) para volver al automático.",
                "proyectos": {p: dict(e, modificado=e.get("modificado") or ahora) for p, e in sorted(self.estados.items())}}

    def guardar(self, carpeta_brutos):
        Path(carpeta_brutos).mkdir(parents=True, exist_ok=True)
        for p in self.estados:
            self.estados[p]["modificado"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        (Path(carpeta_brutos) / FICHERO).write_text(json.dumps(self.a_json(), ensure_ascii=False, indent=1), encoding="utf-8")

    def _restaurar(self, p, e):
        if p not in self.listas:
            self.avisos.append("%s: el proyecto ya no está en el análisis; se ignora su montaje" % p)
            return
        validas = set(self.orden[p])
        brutos = [{"nombre": b.get("nombre") or None, "tipo": b.get("tipo", "material"),
                   "clips": [k for k in b.get("clips", []) if k in validas]} for b in e.get("brutos", [])]
        papelera = [k for k in e.get("papelera", []) if k in validas]
        guardadas = [k for b in e.get("brutos", []) for k in b.get("clips", [])] + list(e.get("papelera", []))
        perdidas = [k for k in guardadas if k not in validas]
        if perdidas:
            self.avisos.append("%s: %d clips del montaje ya no están en el análisis; se quitan" % (p, len(perdidas)))
        recuperados = [k for k in e.get("recuperados", []) if k in validas]
        # un montaje de antes de detectarlos: los trozos repetidos van a la papelera (también los que estaban en un bruto),
        # salvo los que se recuperaron a mano
        duplicados = [c.get("clave") for c in self.listas[p].get("duplicados") or []
                      if c.get("clave") not in papelera and c.get("clave") not in recuperados]
        if duplicados:
            for b in brutos:
                b["clips"] = [k for k in b["clips"] if k not in duplicados]
            papelera += duplicados
        colocadas = {k for b in brutos for k in b["clips"]} | set(papelera)
        nuevas = [k for f in ("material", "piezas") for k in (c.get("clave") for c in self.listas[p].get(f) or []) if k not in colocadas]
        if nuevas:
            self.avisos.append("%s: %d clips nuevos sin colocar; no se exportan hasta colocarlos" % (p, len(nuevas)))
            self.sin_colocar[p] = nuevas
        estado = {"brutos": brutos, "papelera": papelera, "movidos": [k for k in e.get("movidos", []) if k in validas],
                  "recuperados": recuperados, "modificado": e.get("modificado")}
        self._limpiar(estado)
        self.estados[p] = estado

    @classmethod
    def cargar(cls, carpeta_brutos, listas, fecha, tope, descartados=None, salto=eb.SALTO_DIAS):
        """Lee montaje.json de la carpeta de brutos; si no hay pero hay un cortes.json antiguo, lo convierte."""
        ruta = Path(carpeta_brutos) / FICHERO
        if ruta.exists():
            try:
                guardado = json.loads(ruta.read_text(encoding="utf-8")).get("proyectos") or {}
            except Exception as e:
                m = cls(listas, fecha, tope, descartados=descartados, salto=salto)
                m.avisos.append("%s ilegible (%s): se usa el reparto automático" % (FICHERO, e))
                return m
            return cls(listas, fecha, tope, guardado, descartados, salto)
        m = cls(listas, fecha, tope, descartados=descartados, salto=salto)
        cortes = eb.cargar_cortes(carpeta_brutos)
        for p, claves in sorted(cortes.items()):
            if p in listas:
                trozos = eb.trozos_proyecto(listas[p], fecha, tope, claves, salto)
                m.estados[p] = {"brutos": [{"nombre": None, "tipo": f, "clips": [c.get("clave") for c in t]} for f, t in trozos],
                                "papelera": [], "movidos": []}
        if m.estados:
            m.avisos.append("Cortes de %s convertidos al montaje (%d proyectos)" % (eb.FICHERO_CORTES, len(m.estados)))
        return m

    @classmethod
    def desde_analisis(cls, clips, cruce, mxf, cfg, carpeta_brutos):
        mxf_por_ruta = {m["ruta"]: m for m in mxf}
        listas = eb.listas_por_proyecto(clips, cruce, mxf_por_ruta, cfg)
        m = cls.cargar(carpeta_brutos, listas, lambda c: eb.fecha_clip(c, mxf_por_ruta), 3600 * float(cfg.get("horas_por_bruto", 2)),
                       eb.descartados_por_proyecto(clips, cruce, mxf_por_ruta, cfg), eb.salto_dias(cfg))
        m.mxf_por_ruta = mxf_por_ruta
        return m

    # --- exportar -------------------------------------------------------------------------------------------------

    def grupos(self, proyectos=None):
        """Brutos de los proyectos tocados (o de los pedidos), en el formato de exportar_brutos.grupos_por_proyecto_mes,
        con además movidos (claves movidas a mano), quitados (clips de la papelera del proyecto) y manual."""
        grupos = []
        for p in (proyectos if proyectos is not None else sorted(self.estados)):
            quitados = self.papelera(p)
            movidos = self.movidos(p)
            for b in self.brutos(p):
                g = eb.grupo_bruto(p, b["tipo"], b["nombre"], b["clips"], self.fecha, " · montaje a mano" if self.tocado(p) else "")
                g.update(movidos={c.get("clave") for c in b["clips"]} & movidos, quitados=quitados, manual=b["manual"])
                grupos.append(g)
        return grupos


# ---------------------------------------------------------------------------
# Fotogramas del visor
# ---------------------------------------------------------------------------

def fichero_visor(carpeta_visor, clave):
    return Path(carpeta_visor) / (hashlib.sha256(str(clave).encode("utf-8")).hexdigest()[:16] + ".mjpeg")


VF_VISOR = "scale=480:270:force_original_aspect_ratio=decrease,pad=480:270:(ow-iw)/2:(oh-ih)/2:black,setsar=1"


def fichero_portada(carpeta_visor, clave):
    return fichero_visor(carpeta_visor, clave).with_suffix(".jpg")


def _ffmpeg_a_fichero(cmd, salida, ruta_video, timeout):
    """Ejecuta ffmpeg (cmd sin el fichero de salida) escribiendo en un temporal propio y lo deja en salida. Si ya está
    hecho, no se repite. Devuelve la ruta o None si ffmpeg falla."""
    if salida.exists() and salida.stat().st_size > 0:
        return salida
    salida.parent.mkdir(parents=True, exist_ok=True)
    # Temporal propio: al rehacer el montaje (p. ej. tras elegir el origen) el de antes puede seguir sacando los mismos
    # fotogramas; con un temporal compartido, el que acaba primero se lo llevaba y el otro daba el clip por fallido.
    tmp = salida.with_name("%s.%s.tmp" % (salida.stem, uuid.uuid4().hex[:8]))
    try:
        r = subprocess.run(cmd + [str(tmp)], capture_output=True, timeout=timeout, **eb.flags_subproceso())
    except Exception as e:
        LOG.warning("Visor: no se pudieron sacar los fotogramas de %s (%s)", ruta_video, e)
        tmp.unlink(missing_ok=True)
        return None
    if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        if salida.exists() and salida.stat().st_size > 0:  # otro lo terminó mientras tanto
            return salida
        LOG.warning("Visor: ffmpeg falló con %s: %s", ruta_video, r.stderr.decode("utf-8", "replace")[-300:])
        return None
    if salida.exists() and salida.stat().st_size > 0:  # otro lo terminó antes: vale el suyo
        tmp.unlink(missing_ok=True)
        return salida
    try:
        tmp.replace(salida)
    except OSError as e:
        # Windows no deja sustituir un fichero que otro acaba de escribir o está leyendo ("Access is denied")
        tmp.unlink(missing_ok=True)
        if salida.exists() and salida.stat().st_size > 0:
            return salida
        LOG.warning("Visor: no se pudo guardar %s (%s)", salida, e)
        return None
    return salida


def sacar_fotogramas(ffmpeg, ruta_video, carpeta_visor, clave, timeout=1800):
    """Un fotograma por segundo a 480x270 (con bandas si el aspecto no es 16:9), todos seguidos en un .mjpeg por clip.
    Si ya está hecho, no se repite. Devuelve la ruta o None si ffmpeg falla."""
    # -skip_frame nokey: en MPEG-2/H.264 solo se decodifican las imágenes clave (3-4 veces más rápido; para 1 por
    # segundo sobra). En DV, DNxHD o ProRes todas lo son. Medido: ~1 s por minuto de DV, ~10 KB por fotograma.
    cmd = [ffmpeg, "-v", "error", "-y", "-skip_frame", "nokey", "-i", eb.ruta_larga(ruta_video), "-map", "0:v:0", "-an",
           "-vf", "fps=1," + VF_VISOR, "-q:v", "10", "-f", "mjpeg"]
    ruta = _ffmpeg_a_fichero(cmd, fichero_visor(carpeta_visor, clave), ruta_video, timeout)
    if ruta is None and Path(ruta_video).exists():
        # Un clip corto con una sola imagen clave (H.264 de un móvil o una descarga): con "nokey" el filtro de 1 por segundo
        # no saca ninguna. Se repite decodificándolo entero (más lento, pero solo en estos).
        ruta = _ffmpeg_a_fichero([x for x in cmd if x not in ("-skip_frame", "nokey")], fichero_visor(carpeta_visor, clave),
                                 ruta_video, timeout)
    return ruta


def sacar_portada(ffmpeg, ruta_video, carpeta_visor, clave, timeout=120):
    """Una sola imagen del clip (la primera imagen clave), a 480x270: sale en un momento aunque el clip dure una hora, y
    el visor la enseña mientras se preparan los demás fotogramas. Devuelve la ruta o None."""
    cmd = [ffmpeg, "-v", "error", "-y", "-skip_frame", "nokey", "-i", eb.ruta_larga(ruta_video), "-map", "0:v:0", "-an",
           "-frames:v", "1", "-vf", VF_VISOR, "-q:v", "6", "-f", "image2", "-c:v", "mjpeg"]
    return _ffmpeg_a_fichero(cmd, fichero_portada(carpeta_visor, clave), ruta_video, timeout)


def fotograma_exacto(ffmpeg, ruta_video, segundo, timeout=30):
    """El fotograma del segundo pedido del clip (con decimales: el fotograma exacto, no el de la tira de 1 por segundo),
    a 480x270, en JPEG (bytes), o None. Para el visor cuando se avanza fotograma a fotograma o se para el cursor."""
    cmd = [ffmpeg, "-v", "error", "-nostdin", "-ss", "%.3f" % max(0.0, segundo), "-i", eb.ruta_larga(ruta_video), "-map", "0:v:0",
           "-an", "-frames:v", "1", "-vf", VF_VISOR, "-q:v", "4", "-f", "image2pipe", "-c:v", "mjpeg", "pipe:1"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, **eb.flags_subproceso())
    except Exception as e:
        LOG.warning("Visor: no se pudo sacar el fotograma %.2f s de %s (%s)", segundo, ruta_video, e)
        return None
    return r.stdout if r.returncode == 0 and r.stdout[:3] == b"\xff\xd8\xff" else None


# ---------------------------------------------------------------------------
# Ver y oír un clip (ffplay)
# ---------------------------------------------------------------------------

PLAY_W, PLAY_H = 960, 540  # la ventana de ffplay


def buscar_ffplay(ffmpeg):
    """ffplay junto a ffmpeg (bin/ del programa) o en el PATH; None si no está."""
    if ffmpeg:
        junto = Path(ffmpeg).with_name("ffplay" + Path(ffmpeg).suffix)
        if junto.exists():
            return str(junto)
    return shutil.which("ffplay")


def orden_sonido(ffmpeg, origen, clip, desde, canales):
    """Orden de ffmpeg que saca el clip desde el segundo desde, con su vídeo a 960x540 y todo su sonido junto en
    estéreo, en NUT por la salida estándar, para que ffplay lo reproduzca (ffplay solo abre un fichero, y en la media de
    Avid el vídeo y cada pista de sonido van en ficheros distintos). canales: exportar_brutos.canales_clip(clip, ...).
    El vídeo va sin comprimir (decodificar lo hace ffmpeg; ffplay solo lo enseña) y el sonido, mezclado con un limitador
    para que no sature al sumar pistas."""
    rutas = [clip["fichero_video"]] + [r for r in dict.fromkeys(c["ruta"] for c in canales) if r != clip["fichero_video"]]
    entrada = {r: i for i, r in enumerate(rutas)}
    cmd = [ffmpeg, "-v", "error", "-nostdin"]
    for r in rutas:
        cmd += ["-ss", "%.3f" % max(0.0, desde), "-i", eb.ruta_larga(Path(origen) / r)]
    filtros = ["[0:v:0]yadif=0:-1:1,scale=%d:%d:force_original_aspect_ratio=decrease,pad=%d:%d:(ow-iw)/2:(oh-ih)/2:black,"
               "setsar=1,format=yuv420p[v]" % (PLAY_W, PLAY_H, PLAY_W, PLAY_H)]
    for k, c in enumerate(canales):
        filtros.append("[%d:a:%d]pan=mono|c0=c%d[c%d]" % (entrada[c["ruta"]], c["flujo"], c["canal"], k))
    if canales:
        filtros.append("%samix=inputs=%d:normalize=0:dropout_transition=0,alimiter,aformat=channel_layouts=stereo[a]"
                       % ("".join("[c%d]" % k for k in range(len(canales))), len(canales)))
    cmd += ["-filter_complex", ";".join(filtros), "-map", "[v]", "-c:v", "rawvideo"]
    if canales:
        cmd += ["-map", "[a]", "-c:a", "pcm_s16le", "-ar", "48000"]
    return cmd + ["-f", "nut", "pipe:1"]


def orden_ffplay(ffplay, titulo):
    """ffplay leyendo lo que saca orden_sonido por la entrada estándar; se cierra solo al acabar el clip."""
    return [ffplay, "-hide_banner", "-loglevel", "error", "-autoexit", "-alwaysontop", "-window_title", titulo,
            "-x", str(PLAY_W), "-y", str(PLAY_H), "-f", "nut", "-"]


# ---------------------------------------------------------------------------
# Línea de tiempo
# ---------------------------------------------------------------------------

PASOS_REGLA = (0.04, 0.2, 0.4, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200)


def paso_regla(pps, separacion=90):
    """Cada cuánto (s) va un timecode en la regla de la línea de tiempo con pps píxeles por segundo, para que los textos
    no se pisen (al menos separacion píxeles), y cada cuánto una marca pequeña (o None si quedarían muy juntas)."""
    paso = next((p for p in PASOS_REGLA if p * pps >= separacion), PASOS_REGLA[-1])
    menor = next((p for p in PASOS_REGLA if p < paso and abs(paso / p - round(paso / p)) < 1e-6 and p * pps >= 8), None)
    return paso, menor


def siguiente_preparacion(claves, actual, hechos, portadas, en_curso, fallidos, tipos=("portada", "tira")):
    """Qué preparar ahora para el visor. Primero el clip que se mira: su portada (una imagen, al momento) y luego todos
    sus fotogramas; después los clips siguientes y por último los de antes. Antes se preparaban todos en orden desde el
    primero: si se miraba el clip 20 había que esperar a los 19 anteriores.
    claves: las del bruto en orden; actual: índice del clip que se mira; tipos: qué se busca. Devuelve ("portada" | "tira",
    clave) o None."""
    if not claves:
        return None
    actual = min(max(actual or 0, 0), len(claves) - 1)
    for k in claves[actual:] + claves[:actual][::-1]:
        if k in hechos:
            continue
        for tipo in tipos:
            if tipo == "portada" and k in portadas:
                continue
            if (tipo, k) not in en_curso and (tipo, k) not in fallidos:
                return tipo, k
    return None


def indice_jpeg(datos):
    """[(inicio, fin)] de cada JPEG dentro de un .mjpeg (empiezan por FF D8 FF; en los datos comprimidos un FF va
    siempre seguido de 00, así que esa secuencia solo aparece al principio de cada imagen)."""
    inicios = []
    i = datos.find(b"\xff\xd8\xff")
    while i >= 0:
        inicios.append(i)
        i = datos.find(b"\xff\xd8\xff", i + 3)
    return list(zip(inicios, inicios[1:] + [len(datos)]))


def fotogramas(ruta):
    """Lista de JPEG (bytes) de un .mjpeg del visor."""
    datos = Path(ruta).read_bytes()
    return [datos[a:b] for a, b in indice_jpeg(datos)]
