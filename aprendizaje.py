"""Aprender el origen del material de las decisiones de cada disco, sin internet.

Cada vez que se guarda la pestaña "Origen del material", cada procedencia decidida (carpeta de importación y bin) se
apunta con sus pistas: palabras de la carpeta y del bin, la forma de los nombres de fichero (IMG_####), la marca de
cámara y la extensión. Con eso se calcula, por frecuencias, qué suele significar cada pista (un clasificador bayesiano
ingenuo: cuenta, no adivina). La pestaña muestra la propuesta aprendida con su porcentaje y en cuántas decisiones se
basa, y un botón para aceptarla: solo propone, nunca decide (ante la duda, entra).

Lo aprendido va en config_aprendizaje_origen.json junto al programa (el actualizador conserva los config_*.json), o
en la ruta que diga config_brutos.json → aprendizaje_origen (p. ej. una carpeta compartida, para aprender en equipo).
Solo se guardan palabras y formas de nombre, nunca vídeo ni imágenes.
"""

import datetime as dt
import json
import math
import os
import re
import unicodedata
from collections import Counter
from pathlib import Path

CLASES = ("propio", "agencia", "importado")
# Palabras que no dicen nada del origen (partes fijas de las rutas de Windows y artículos)
VACIAS = {"users", "documents", "and", "settings", "mis", "documentos", "the", "del", "los", "las", "una", "por",
          "con", "para", "bin", "avb"}
MINIMO_DECISIONES = 3   # con menos, no se propone nada
MINIMO_PARECIDAS = 2    # decisiones que comparten alguna pista con la procedencia
MINIMA_CONFIANZA = 0.75


def _normal(texto):
    t = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode("ascii")
    return t.lower()


def _palabras(texto):
    return {p for p in re.split(r"[^a-z0-9]+", _normal(texto)) if len(p) >= 3 and p not in VACIAS and not p.isdigit()}


def forma_de_nombre(nombre):
    """La forma de un nombre de fichero: IMG_1234.MOV → IMG_####; los nombres con texto libre, "texto"."""
    base = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", re.split(r"[\\/]+", str(nombre or ""))[-1])
    if not base:
        return None
    forma = re.sub(r"\d", "#", _normal(base).upper())
    return "texto" if len(re.sub(r"[^A-Z]", "", forma)) > 8 or " " in forma else forma


def rasgos(grupo):
    """Las pistas de una procedencia (un grupo de exportar_brutos.grupos_de_origen) como conjunto de textos."""
    r = {"carpeta:" + p for p in _palabras(grupo.get("carpeta"))}
    r |= {"bin:" + p for p in _palabras(re.sub(r"\.avb$", "", grupo.get("bin") or "", flags=re.IGNORECASE))}
    for c in grupo.get("clips", []):
        fichero = c.get("fichero_origen")
        if fichero:
            forma = forma_de_nombre(fichero)
            if forma:
                r.add("nombre:" + forma)
            ext = Path(str(fichero).replace("\\", "/")).suffix.lower()
            if ext:
                r.add("ext:" + ext)
        if c.get("marca"):
            r.add("marca:" + _normal(c["marca"]).split()[0])
    if not grupo.get("carpeta"):
        r.add("sin_ruta")
    return r


def fuerte(rasgo):
    """Las pistas que distinguen de verdad una procedencia (la extensión o un nombre de texto libre las comparte casi todo)."""
    return not rasgo.startswith("ext:") and rasgo not in ("nombre:texto", "sin_ruta")


def clave(grupo):
    """Identifica una procedencia de un disco: la misma decisión guardada dos veces se sustituye, no se suma."""
    return "%s|%s|%s" % (grupo.get("carpeta") or "", grupo.get("bin") or "", ",".join(sorted(grupo.get("ejemplos") or [])))


def cargar(ruta):
    """Las decisiones aprendidas ([] si no hay fichero o no se puede leer)."""
    try:
        datos = json.loads(Path(ruta).read_text(encoding="utf-8"))
        return [d for d in datos.get("decisiones", []) if d.get("clase") in CLASES and d.get("rasgos")]
    except (OSError, ValueError, AttributeError):
        return []


def registrar(ruta, grupos_y_clases):
    """Apunta (o quita, con clase None) la decisión de cada procedencia: [(grupo, clase)]. Devuelve cuántas hay."""
    ruta = Path(ruta)
    decisiones = {d.get("clave"): d for d in cargar(ruta)}
    hoy = dt.date.today().isoformat()
    for grupo, clase in grupos_y_clases:
        k = clave(grupo)
        if clase in CLASES:
            decisiones[k] = {"clave": k, "clase": clase, "rasgos": sorted(rasgos(grupo)), "clips": len(grupo.get("clips", [])),
                             "fecha": hoy}
        else:
            decisiones.pop(k, None)
    datos = {"version": 1,
             "_comentario": "Lo aprendido de las decisiones de la pestaña Origen del material (solo palabras y formas de "
                            "nombre, nunca vídeo). Se puede borrar para empezar de cero.",
             "decisiones": sorted(decisiones.values(), key=lambda d: d["clave"])}
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_name(ruta.name + ".tmp")
    tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        os.replace(tmp, ruta)
    except OSError:  # Windows o una carpeta compartida que no deja sustituir: se escribe directamente
        ruta.write_text(tmp.read_text(encoding="utf-8"), encoding="utf-8")
        tmp.unlink(missing_ok=True)
    return len(decisiones)


def sugerir(decisiones, grupo):
    """Lo que sugiere lo aprendido para una procedencia: {"clase", "confianza", "parecidas", "pistas"} o None si no hay
    bastantes decisiones parecidas o ninguna clase destaca."""
    if len(decisiones) < MINIMO_DECISIONES:
        return None
    mios = rasgos(grupo)
    parecidas = [d for d in decisiones if any(fuerte(r) for r in mios & set(d["rasgos"]))]
    if len(parecidas) < MINIMO_PARECIDAS:
        return None
    por_clase = Counter(d["clase"] for d in decisiones)
    cuenta = {c: Counter(r for d in decisiones if d["clase"] == c for r in d["rasgos"]) for c in CLASES}
    conocidos = [r for r in mios if any(cuenta[c][r] for c in CLASES)]
    puntos = {}
    for c in CLASES:
        n = por_clase.get(c, 0)
        # Bayes ingenuo con suavizado de Laplace: P(clase) · Π P(pista | clase), solo con las pistas ya vistas
        puntos[c] = math.log((n + 1) / (len(decisiones) + len(CLASES))) + sum(
            math.log((cuenta[c][r] + 1) / (n + 2)) for r in conocidos)
    mayor = max(puntos.values())
    total = sum(math.exp(p - mayor) for p in puntos.values())
    mejor = max(CLASES, key=lambda c: puntos[c])
    confianza = math.exp(puntos[mejor] - mayor) / total
    if confianza < MINIMA_CONFIANZA:
        return None
    n = por_clase.get(mejor, 0)
    pistas = sorted((r for r in conocidos if fuerte(r) and cuenta[mejor][r]), key=lambda r: -(cuenta[mejor][r] / max(n, 1)))[:3]
    apoyo = [d for d in parecidas if d["clase"] == mejor]
    if not apoyo:  # la clase gana solo por ser la más frecuente, sin ninguna decisión parecida que la respalde
        return None
    return {"clase": mejor, "confianza": confianza, "parecidas": len(apoyo), "pistas": [r.split(":", 1)[1] for r in pistas]}


def texto_sugerencia(s, nombres):
    """'Agencia 92 % · 5 decisiones' (nombres: {clase: texto}), o '—'."""
    if not s:
        return "—"
    return "%s %d %% · %d decisi%s" % (nombres[s["clase"]], round(s["confianza"] * 100), s["parecidas"],
                                        "ón" if s["parecidas"] == 1 else "ones")
