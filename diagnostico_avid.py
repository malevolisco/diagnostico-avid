#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DIAGNÓSTICO DE DISCO AVID — Fase 1 (análisis) + Fase 2 (brutos)
=================================================================

Analiza un disco de edición Avid Media Composer completo y genera un informe
que explica qué contiene: proyectos, bins, clips, secuencias, ficheros MXF,
códecs, duraciones, huérfanos y avisos. Opcionalmente exporta un MXF por
proyecto con todos sus clips encadenados (ver exportar_brutos.py).
No escribe nada en el disco de origen.

Salida: una carpeta  Diagnostico_<disco>_<fecha>/  en el destino elegido, con
    informe.html        informe visual (se abre solo al terminar)
    resumen.json        totales y avisos
    proyectos.json      proyectos y sus bins
    bins.json           contenido de cada bin (clips y secuencias)
    mxf.json            metadatos de cada fichero MXF (ffprobe)
    clips.json          clips reconstruidos (MXF agrupados por identificador)
    cruce.json          relación proyecto <-> clips <-> MXF, huérfanos, offline
    inventario.csv      todos los ficheros del disco (ruta, tamaño, fecha)
    miniaturas/         un JPG por clip de vídeo
    diagnostico.log     registro detallado
y un ZIP con todo lo anterior, listo para enviar.

Uso con ventana:      python diagnostico_avid.py
Uso sin ventana:      python diagnostico_avid.py --origen F:\\ --destino D:\\salida --sin-gui
Con brutos (fase 2):  añade  --brutos E:\\BRUTOS   (un MXF por proyecto, ver exportar_brutos.py)

Requisitos: Python 3.9+, pyavb y openpyxl (pip install pyavb openpyxl), ffmpeg y ffprobe
(en la carpeta bin/ junto a este fichero, o en el PATH del sistema).
"""

import argparse
import csv
import datetime as dt
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import traceback
import uuid
import webbrowser
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # para encontrar exportar_brutos.py junto al programa
try:
    import exportar_brutos
    EXPORT_OK = True
except Exception:  # pragma: no cover
    exportar_brutos = None
    EXPORT_OK = False

try:
    import avb  # pyavb
    PYAVB_OK = True
except Exception:  # pragma: no cover
    avb = None
    PYAVB_OK = False

VERSION = "1.0"
LOG = logging.getLogger("diagnostico")

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

EXT_MEDIA = {".mxf", ".omf"}
CARPETAS_MEDIA = {"avid mediafiles", "omfi mediafiles"}
CARPETAS_CACHE = {"searchdata", "statistics", "waveformcache", "creating"}
CARPETAS_SISTEMA = {"$recycle.bin", "system volume information", ".spotlight-v100",
                    ".fseventsd", ".trashes", "found.000"}
NUM_HILOS = 4
MINIATURA_ANCHO = 320


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def ahora_txt():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def gb(n_bytes):
    return round(n_bytes / (1024 ** 3), 2)


def fmt_duracion(segundos):
    if segundos is None:
        return "—"
    segundos = int(round(segundos))
    h, r = divmod(segundos, 3600)
    m, s = divmod(r, 60)
    return "%d:%02d:%02d" % (h, m, s)


def nombre_seguro(texto, maximo=80):
    texto = re.sub(r'[\\/:*?"<>|]+', "_", str(texto)).strip()
    return (texto or "sin_nombre")[:maximo]


def flags_subproceso():
    if os.name == "nt":
        return {"creationflags": 0x08000000}  # CREATE_NO_WINDOW
    return {}


def carpeta_base():
    """Carpeta donde vive el programa (o el ejecutable congelado)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


def localizar_ffmpeg():
    """Devuelve (ffmpeg, ffprobe) buscando primero en bin/ y luego en el PATH."""
    exe = ".exe" if os.name == "nt" else ""
    base = carpeta_base()
    candidatos = [base / "bin", base]
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidatos.insert(0, Path(sys._MEIPASS) / "bin")
    for c in candidatos:
        ff, fp = c / ("ffmpeg" + exe), c / ("ffprobe" + exe)
        if ff.exists() and fp.exists():
            return str(ff), str(fp)
    ff, fp = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if ff and fp:
        return ff, fp
    return None, None


def leer_build():
    """Identificador de la build (build.txt lo escribe GitHub Actions); "local" si no existe."""
    candidatos = [carpeta_base() / "build.txt"]
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidatos.insert(0, Path(sys._MEIPASS) / "build.txt")
    for c in candidatos:
        try:
            texto = c.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if texto:
            return texto
    return "local"


BUILD = leer_build()


# ---------------------------------------------------------------------------
# Origen del material (según el fichero importado que recuerda el bin)
# ---------------------------------------------------------------------------

PATRONES_ORIGEN_DEFECTO = {
    "_comentario": "Reglas para clasificar el origen de un clip a partir del nombre del fichero importado (según el bin) o del nombre del clip. "
                   "Se aplican en orden; la primera que coincide gana. 'campo': fichero_origen | nombre. 'grupos' nombra los grupos capturados de la expresión regular.",
    "reglas": [
        {"origen": "Agencia Reuters", "campo": "fichero_origen",
         "regex": r"(?P<fecha>\d{4}-\d{2}-\d{2})T(?P<hora>\d{6})Z_\d+_(?P<id>[A-Z0-9]+)_RTR[A-Z0-9]*_[A-Z]_(?P<num>\d+)-(?P<slug>[^/\\]+?)\.(?:MP4|MXF|MOV|M4V)$",
         "grupos": ["id", "slug", "fecha"]},
        {"origen": "Cámara (tarjeta P2)", "campo": "nombre", "regex": r"^\d{4}[A-Z0-9]{2}(\.new)?(\.\d+)?$", "grupos": []},
        {"origen": "Fichero importado", "campo": "fichero_origen", "regex": r"\.(?:MP4|MOV|MXF|M4V|AVI|MTS|M2T)$", "grupos": []}
    ]
}


def cargar_patrones_origen():
    ruta = carpeta_base() / "config_origen.json"
    try:
        if not ruta.exists():
            with open(ruta, "w", encoding="utf-8") as f:
                json.dump(PATRONES_ORIGEN_DEFECTO, f, ensure_ascii=False, indent=2)
            datos = PATRONES_ORIGEN_DEFECTO
        else:
            with open(ruta, encoding="utf-8") as f:
                datos = json.load(f)
    except Exception as e:
        LOG.error("config_origen.json ilegible (%s); se usan las reglas por defecto", e)
        datos = PATRONES_ORIGEN_DEFECTO
    reglas = []
    for r in datos.get("reglas", []):
        try:
            reglas.append((r["origen"], r.get("campo", "fichero_origen"), re.compile(r["regex"], re.IGNORECASE), r.get("grupos", [])))
        except Exception as e:
            LOG.error("Regla de origen inválida %s: %s", r, e)
    return reglas


def clasificar_origen(reglas, nombre, fichero_origen):
    """Devuelve (origen, detalle). detalle = grupos capturados (id, slug...)."""
    base = (fichero_origen or "").replace("\\", "/").rsplit("/", 1)[-1]
    for origen, campo, rx, grupos in reglas:
        texto = base if campo == "fichero_origen" else (nombre or "")
        if not texto:
            continue
        m = rx.search(texto)
        if m:
            detalle = {g: m.group(g) for g in grupos if g in m.groupdict() and m.group(g)}
            return origen, detalle
    return "Sin determinar", {}


# ---------------------------------------------------------------------------
# Paso 1: inventario del disco
# ---------------------------------------------------------------------------

def inventariar(origen, progreso):
    """Recorre todo el disco. Devuelve lista de dicts con cada fichero."""
    origen = Path(origen)
    ficheros = []
    n = 0
    for raiz, dirs, nombres in os.walk(origen, onerror=lambda e: LOG.warning("No se puede leer: %s", e)):
        dirs[:] = [d for d in dirs if d.lower() not in CARPETAS_SISTEMA]
        for nombre in nombres:
            ruta = Path(raiz) / nombre
            try:
                st = ruta.stat()
            except OSError as e:
                LOG.warning("No se puede leer %s: %s", ruta, e)
                continue
            rel = ruta.relative_to(origen)
            ficheros.append({
                "ruta": str(rel),
                "carpeta": str(rel.parent),
                "nombre": nombre,
                "ext": ruta.suffix.lower(),
                "bytes": st.st_size,
                "modificado": dt.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            })
            n += 1
            if n % 500 == 0:
                progreso("Inventario: %d ficheros..." % n)
    LOG.info("Inventario: %d ficheros, %.2f GB", len(ficheros), gb(sum(f["bytes"] for f in ficheros)))
    return ficheros


def escribir_inventario_csv(ficheros, destino):
    with open(destino / "inventario.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["ruta", "carpeta", "nombre", "ext", "bytes", "modificado"], delimiter=";")
        w.writeheader()
        w.writerows(ficheros)


# ---------------------------------------------------------------------------
# Paso 2: proyectos y bins
# ---------------------------------------------------------------------------

def es_ruta_media(partes):
    return any(p.lower() in CARPETAS_MEDIA for p in partes)


def detectar_proyectos(ficheros):
    """Un proyecto es una carpeta que contiene un .avp. Sus bins son los .avb
    de esa carpeta y subcarpetas (los de Trash se marcan como papelera)."""
    carpetas_avp = {}
    for f in ficheros:
        if f["ext"] == ".avp":
            carpetas_avp.setdefault(f["carpeta"], []).append(f["nombre"])

    proyectos = []
    bins_asignados = set()
    for carpeta in sorted(carpetas_avp):
        partes = Path(carpeta).parts
        nombre = partes[-1] if partes and carpeta != "." else "(raíz del disco)"
        bins = []
        for f in ficheros:
            if f["ext"] != ".avb":
                continue
            fc = f["carpeta"]
            dentro = fc == carpeta or (carpeta != "." and fc.startswith(carpeta + os.sep))
            if carpeta == ".":
                dentro = fc == "."
            if not dentro:
                continue
            en_papelera = any(p.lower() == "trash" for p in Path(fc).parts)
            bins.append({"ruta": f["ruta"], "nombre": f["nombre"], "bytes": f["bytes"],
                         "modificado": f["modificado"], "papelera": en_papelera})
            bins_asignados.add(f["ruta"])
        proyectos.append({
            "nombre": nombre,
            "carpeta": carpeta,
            "ficheros_avp": carpetas_avp[carpeta],
            "bins": bins,
            "num_bins": len(bins),
        })

    sueltos = [{"ruta": f["ruta"], "nombre": f["nombre"], "bytes": f["bytes"], "modificado": f["modificado"], "papelera": False}
               for f in ficheros if f["ext"] == ".avb" and f["ruta"] not in bins_asignados]
    if sueltos:
        proyectos.append({"nombre": "(bins sin proyecto)", "carpeta": "", "ficheros_avp": [],
                          "bins": sueltos, "num_bins": len(sueltos)})
    LOG.info("Proyectos detectados: %d", len(proyectos))
    return proyectos


def mobid_claves(mob_id):
    """Devuelve las representaciones posibles de un MobID de pyavb como UMID
    hexadecimal (32 bytes), para cruzar con ffprobe. Se prueban dos
    codificaciones del UUID porque el orden de bytes puede variar."""
    try:
        raw = bytes(mob_id.bytes_le)
        a = raw.hex().upper()
        u = uuid.UUID(bytes_le=raw[16:])
        b = (raw[:16] + u.bytes).hex().upper()
        return {"le": a, "be": b}
    except Exception:
        return {}


def material(umid_hex):
    """Número de material de un UMID (los 16 últimos bytes). Avid puede
    reescribir la cabecera del UMID al guardar el MXF; el material no cambia."""
    return (umid_hex or "")[-32:]


def fuentes_de(mob, f, profundidad=4):
    """Sigue las pistas de un master clip hasta sus source mobs (ficheros de
    media, y de ahí al fichero importado o la cinta de origen) y devuelve sus
    identificadores, el volumen donde Avid vio la media por última vez y la
    ruta del fichero original importado, si la hay. Sigue la cadena de
    referencias hasta 'profundidad' niveles."""
    out, vistos = [], set()

    def clips_de(comp):
        """SourceClips contenidos en un componente (directo o dentro de una secuencia)."""
        if comp is None:
            return
        sid = getattr(comp, "mob_id", None)
        if sid is not None:
            yield comp
        for sub in getattr(comp, "components", None) or []:
            yield from clips_de(sub)

    def visitar(m, nivel):
        if m is None or nivel > profundidad:
            return
        try:
            pistas = list(m.tracks)
        except Exception:
            return
        for t in pistas:
            for sc in clips_de(getattr(t, "component", None)):
                sid = sc.mob_id
                try:
                    if int(sid) == 0 or sid in vistos:
                        continue
                except Exception:
                    continue
                vistos.add(sid)
                src = f.content.find_by_mob_id(sid)
                info = {"mob_id": mobid_claves(sid), "pista": str(getattr(sc, "media_kind", "") or ""), "nivel": nivel,
                        "volumen": None, "fichero_origen": None, "descriptor": None}
                desc = getattr(src, "descriptor", None) if src is not None else None
                if desc is not None:
                    info["descriptor"] = type(desc).__name__
                    loc = getattr(desc, "locator", None)
                    for l in (loc if isinstance(loc, list) else [loc]) if loc else []:
                        vol = getattr(l, "last_known_volume_utf8", None) or getattr(l, "last_known_volume", None)
                        ruta = getattr(l, "path_utf8", None) or getattr(l, "path", None)
                        if vol:
                            info["volumen"] = str(vol)
                        if ruta:
                            info["fichero_origen"] = str(ruta)
                out.append(info)
                if src is not None:
                    visitar(src, nivel + 1)

    try:
        visitar(mob, 1)
    except Exception as e:
        LOG.debug("fuentes_de %s: %s", getattr(mob, "name", "?"), e)
    return out


def edit_rate_de(mob):
    try:
        for t in mob.tracks:
            comp = getattr(t, "component", None)
            er = getattr(comp, "edit_rate", None)
            if er:
                return float(er)
    except Exception:
        pass
    return None


def tipos_pista(mob):
    tipos = Counter()
    try:
        for t in mob.tracks:
            mk = getattr(t, "media_kind", None)
            tipos[str(mk) if mk else "?"] += 1
    except Exception:
        pass
    return dict(tipos)


def leer_bin(ruta_abs, reglas_origen=()):
    """Lee un bin con pyavb. Devuelve dict con clips y secuencias o error."""
    res = {"ok": False, "error": None, "clips": [], "secuencias": [], "otros": 0}
    if not PYAVB_OK:
        res["error"] = "pyavb no está instalado"
        return res
    try:
        with avb.open(str(ruta_abs)) as f:
            for mob in f.content.mobs:
                try:
                    tipo = mob.mob_type
                    er = edit_rate_de(mob) or 25.0
                    length = int(getattr(mob, "length", 0) or 0)
                    item = {
                        "nombre": mob.name or "",
                        "tipo": tipo,
                        "uso": getattr(mob, "usage", None),
                        "uso_codigo": getattr(mob, "usage_code", None),
                        "frames": length,
                        "edit_rate": er,
                        "segundos": round(length / er, 2) if er else None,
                        "creado": str(getattr(mob, "creation_time", "") or ""),
                        "modificado": str(getattr(mob, "last_modified", "") or ""),
                        "mob_id": mobid_claves(mob.mob_id),
                        "pistas": tipos_pista(mob),
                    }
                    if tipo == "MasterMob":
                        item["render"] = getattr(mob, "usage", None) == "precompute"  # efectos renderizados, no material
                        item["fuentes"] = fuentes_de(mob, f)
                        item["volumenes"] = sorted({x["volumen"] for x in item["fuentes"] if x["volumen"]})
                        item["fichero_origen"] = next((x["fichero_origen"] for x in item["fuentes"] if x["fichero_origen"]), None)
                        item["origen"], item["origen_detalle"] = clasificar_origen(reglas_origen, item["nombre"], item["fichero_origen"])
                        res["clips"].append(item)
                    elif tipo == "CompositionMob":
                        res["secuencias"].append(item)
                    else:
                        res["otros"] += 1
                except Exception as e:  # un mob corrupto no debe tumbar el bin
                    LOG.warning("Mob ilegible en %s: %s", ruta_abs, e)
        res["ok"] = True
    except Exception as e:
        res["error"] = "%s: %s" % (type(e).__name__, e)
        LOG.error("Bin ilegible %s: %s", ruta_abs, res["error"])
    return res


def leer_bins(proyectos, origen, progreso):
    bins_out = []
    reglas = cargar_patrones_origen()
    total = sum(p["num_bins"] for p in proyectos)
    i = 0
    for p in proyectos:
        for b in p["bins"]:
            i += 1
            progreso("Bins: %d/%d  %s" % (i, total, b["nombre"]))
            r = leer_bin(Path(origen) / b["ruta"], reglas)
            r.update({"proyecto": p["nombre"], "ruta": b["ruta"], "nombre": b["nombre"], "papelera": b["papelera"]})
            bins_out.append(r)
    LOG.info("Bins leídos: %d (%d con error)", len(bins_out), sum(1 for b in bins_out if not b["ok"]))
    return bins_out


# ---------------------------------------------------------------------------
# Paso 3: ffprobe de cada MXF
# ---------------------------------------------------------------------------

def ffprobe_json(ffprobe, ruta):
    cmd = [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(ruta)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120, encoding="utf-8",
                             errors="replace", **flags_subproceso())
        if out.returncode != 0:
            return None, (out.stderr or "ffprobe devolvió error").strip()[:300]
        return json.loads(out.stdout), None
    except subprocess.TimeoutExpired:
        return None, "ffprobe: tiempo agotado"
    except Exception as e:
        return None, "ffprobe: %s" % e


def resumir_mxf(datos):
    fmt = datos.get("format", {})
    tags = fmt.get("tags", {}) or {}
    pistas = []
    for s in datos.get("streams", []):
        st = s.get("tags", {}) or {}
        pistas.append({
            "tipo": s.get("codec_type"),
            "codec": s.get("codec_name"),
            "perfil": s.get("profile"),
            "ancho": s.get("width"),
            "alto": s.get("height"),
            "fps": s.get("r_frame_rate"),
            "pix_fmt": s.get("pix_fmt"),
            "field_order": s.get("field_order"),
            "muestreo": s.get("sample_rate"),
            "canales": s.get("channels"),
            "bits": s.get("bits_per_raw_sample"),
            "duracion": float(s["duration"]) if s.get("duration") else None,
            "file_package_umid": (st.get("file_package_umid") or "").upper().replace("0X", ""),
            "file_package_name": st.get("file_package_name"),
        })
    umid = (tags.get("material_package_umid") or "").upper().replace("0X", "")
    comentarios = {k[len("comment_"):]: v for k, v in tags.items() if k.startswith("comment_")}
    return {
        "duracion": float(fmt["duration"]) if fmt.get("duration") else None,
        "formato": fmt.get("format_long_name"),
        "umid": umid,
        "nombre_clip": tags.get("material_package_name") or "",
        "timecode": tags.get("timecode"),
        "op": tags.get("operational_pattern_ul"),
        "producto": " ".join(x for x in [tags.get("company_name"), tags.get("product_name"), tags.get("product_version")] if x),
        "comentarios": comentarios,
        "pistas": pistas,
    }


def analizar_mxf(ficheros, origen, ffprobe, progreso):
    objetivo = [f for f in ficheros if f["ext"] == ".mxf"]
    resultados = []
    total = len(objetivo)
    hechos = 0

    def trabajo(f):
        datos, err = ffprobe_json(ffprobe, Path(origen) / f["ruta"])
        partes = [p.lower() for p in Path(f["ruta"]).parts]
        base = {"ruta": f["ruta"], "nombre": f["nombre"], "bytes": f["bytes"], "modificado": f["modificado"],
                "en_cuarentena": "quarantined files" in partes, "en_creating": "creating" in partes,
                "en_media": es_ruta_media(partes), "error": err}
        if datos:
            base.update(resumir_mxf(datos))
        return base

    with ThreadPoolExecutor(max_workers=NUM_HILOS) as ex:
        futuros = [ex.submit(trabajo, f) for f in objetivo]
        for fut in as_completed(futuros):
            resultados.append(fut.result())
            hechos += 1
            if hechos % 25 == 0 or hechos == total:
                progreso("MXF: %d/%d analizados" % (hechos, total))
    resultados.sort(key=lambda r: r["ruta"])
    LOG.info("MXF analizados: %d (%d con error)", len(resultados), sum(1 for r in resultados if r["error"]))
    return resultados


# ---------------------------------------------------------------------------
# Paso 4: agrupar MXF por clip y cruzar con bins
# ---------------------------------------------------------------------------

def agrupar_clips(mxf):
    """Los MXF OP-Atom llevan una pista por fichero; los que comparten UMID
    son el mismo clip. Los que no tienen UMID se agrupan por nombre."""
    grupos = defaultdict(list)
    for m in mxf:
        if m.get("error"):
            continue
        clave = m.get("umid") or ("NOMBRE:" + (m.get("nombre_clip") or m["nombre"]))
        grupos[clave].append(m)

    clips = []
    for clave, fichs in grupos.items():
        video = [f for f in fichs if any(p["tipo"] == "video" for p in f["pistas"])]
        audio = [f for f in fichs if any(p["tipo"] == "audio" for p in f["pistas"])]
        pv = next((p for f in video for p in f["pistas"] if p["tipo"] == "video"), None)
        dur = max((f["duracion"] or 0) for f in fichs) if fichs else None
        clips.append({
            "clave": clave,
            "nombre": next((f["nombre_clip"] for f in fichs if f.get("nombre_clip")), fichs[0]["nombre"]),
            "ficheros": [f["ruta"] for f in fichs],
            "fichero_video": video[0]["ruta"] if video else None,
            "num_video": len(video),
            "num_audio": len(audio),
            "duracion": dur,
            "bytes": sum(f["bytes"] for f in fichs),
            "codec_video": pv["codec"] if pv else None,
            "resolucion": ("%sx%s" % (pv["ancho"], pv["alto"])) if pv else None,
            "fps": pv["fps"] if pv else None,
            "timecode": next((f.get("timecode") for f in fichs if f.get("timecode")), None),
            "comentarios": next((f["comentarios"] for f in fichs if f.get("comentarios")), {}),
            "en_cuarentena": any(f["en_cuarentena"] for f in fichs),
            "proyectos": [],
            "render": False,
            "origen": "Sin determinar",
            "origen_detalle": {},
        })
    clips.sort(key=lambda c: c["nombre"])
    LOG.info("Clips reconstruidos: %d", len(clips))
    return clips


def cruzar(proyectos, bins, clips, mxf=()):
    """Relaciona clips de bin (MobID) con clips físicos (UMID de los MXF).

    Vía principal: número de material del master clip == material del
    material_package_umid del MXF (se prueban dos codificaciones del UUID).
    Vía secundaria: los source mobs del clip == file_package_umid de los MXF.
    Los clips 'precompute' (renders de efectos) se marcan y no cuentan como
    material. Un clip físico puede pertenecer a varios proyectos.

    Cruce aproximado: solo en un proyecto donde el exacto no ha dado ninguna coincidencia, por nombre
    y duración, y solo si el emparejamiento es único en los dos sentidos (lo demás queda como ambiguo)."""
    por_clave = {c["clave"]: c for c in clips}
    por_material = {material(c["clave"]): c for c in clips if c["clave"] and not c["clave"].startswith("NOMBRE:")}
    por_fichero_pkg = {}
    for m in mxf:
        for p in m.get("pistas", []):
            if p.get("file_package_umid"):
                por_fichero_pkg[material(p["file_package_umid"])] = m["ruta"]
    ruta_a_clip = {r: c for c in clips for r in c["ficheros"]}

    coincidencias = Counter()
    nuevo_pr = lambda: {"clips_bin": 0, "online": 0, "offline": 0, "renders": 0, "claves": set(), "exactos": 0, "aproximados": 0,
                        "ambiguos": 0, "offline_items": [], "secuencias": 0, "volumenes": Counter(), "origenes": Counter()}
    por_proyecto = defaultdict(nuevo_pr)

    def asignar(fisico, clip, proyecto, pr):
        pr["online"] += 1
        pr["claves"].add(fisico["clave"])
        if clip.get("fichero_origen") and not fisico.get("fichero_origen"):
            fisico["fichero_origen"] = clip["fichero_origen"]
        if clip.get("origen") and (not fisico.get("origen") or fisico["origen"] == "Sin determinar"):
            fisico["origen"], fisico["origen_detalle"] = clip["origen"], clip.get("origen_detalle", {})
        if clip.get("volumenes") and not fisico.get("volumenes"):
            fisico["volumenes"] = clip["volumenes"]
        if proyecto not in fisico["proyectos"]:
            fisico["proyectos"].append(proyecto)
    for b in bins:
        if not b["ok"]:
            continue
        pr = por_proyecto[b["proyecto"]]
        pr["secuencias"] += sum(1 for s in b["secuencias"] if not s.get("uso"))
        for clip in b["clips"]:
            ids = clip.get("mob_id") or {}
            fisico, modo = None, None
            for k in ("le", "be"):
                if material(ids.get(k)) in por_material:
                    fisico, modo = por_material[material(ids[k])], k
                    break
            if not fisico:
                for fu in clip.get("fuentes", []):
                    for k in ("le", "be"):
                        ruta = por_fichero_pkg.get(material((fu.get("mob_id") or {}).get(k)))
                        if ruta and ruta in ruta_a_clip:
                            fisico, modo = ruta_a_clip[ruta], "fuente_" + k
                            break
                    if fisico:
                        break
            for v in clip.get("volumenes", []):
                pr["volumenes"][v] += 1
            if fisico:
                pr["exactos"] += 1
            if clip.get("render"):
                pr["renders"] += 1
                if fisico:
                    fisico["render"] = True
                    coincidencias[modo] += 1
                continue
            pr["clips_bin"] += 1
            if fisico:
                coincidencias[modo] += 1
                asignar(fisico, clip, b["proyecto"], pr)
            else:
                pr["offline"] += 1
                pr["offline_items"].append((clip, b["nombre"]))
            pr["origenes"][clip.get("origen") or "Sin determinar"] += 1

    # Cruce aproximado por nombre y duración (ver docstring). Solo con clips físicos que no estén ya en ningún proyecto.
    def normal(t):
        return " ".join(str(t or "").lower().split())
    for proyecto, pr in sorted(por_proyecto.items()):
        if pr["exactos"] or not pr["offline_items"] or not clips:
            continue
        libres = [c for c in clips if not c["proyectos"] and not c.get("render") and c.get("duracion") is not None]
        candidatos = {}
        for i, (clip, _) in enumerate(pr["offline_items"]):
            if clip.get("segundos") is None:
                continue
            cs = [c for c in libres if normal(c["nombre"]) == normal(clip["nombre"]) and abs(clip["segundos"] - c["duracion"]) <= 0.5]
            if cs:
                candidatos[i] = cs
        veces = Counter(c["clave"] for cs in candidatos.values() for c in cs)
        emparejados = set()
        for i, cs in candidatos.items():
            if len(cs) == 1 and veces[cs[0]["clave"]] == 1:
                cs[0]["coincidencia"] = "aproximada"
                asignar(cs[0], pr["offline_items"][i][0], proyecto, pr)
                coincidencias["nombre_duracion"] += 1
                pr["aproximados"] += 1
                emparejados.add(i)
            else:
                pr["ambiguos"] += 1
        pr["offline"] -= len(emparejados)
        pr["offline_items"] = [x for i, x in enumerate(pr["offline_items"]) if i not in emparejados]
        if pr["aproximados"] or pr["ambiguos"]:
            LOG.info("Cruce aproximado en %s: %d emparejados por nombre y duración, %d ambiguos", proyecto, pr["aproximados"], pr["ambiguos"])

    huerfanos = [c for c in clips if not c["proyectos"] and not c.get("render")]
    renders = [c for c in clips if c.get("render")]
    compartidos = [c for c in clips if len(c["proyectos"]) > 1]
    resumen_proy = []
    for p in proyectos:
        pr = por_proyecto.get(p["nombre"]) or nuevo_pr()
        cs = [por_clave[k] for k in pr["claves"]]
        codecs = Counter((c["codec_video"], c["resolucion"], c["fps"]) for c in cs if c["codec_video"])
        resumen_proy.append({
            "proyecto": p["nombre"],
            "bins": p["num_bins"],
            "bins_ilegibles": sum(1 for b in bins if b["proyecto"] == p["nombre"] and not b["ok"]),
            "clips_en_bin": pr["clips_bin"],
            "renders": pr["renders"],
            "secuencias": pr["secuencias"],
            "clips_online": pr["online"],
            "clips_offline": pr["offline"],
            "clips_aproximados": pr["aproximados"],
            "clips_ambiguos": pr["ambiguos"],
            "clips_fisicos": len(cs),
            "clips_compartidos": sum(1 for c in cs if len(c["proyectos"]) > 1),
            "segundos": round(sum(c["duracion"] or 0 for c in cs), 1),
            "bytes": sum(c["bytes"] for c in cs),
            "formatos": [{"codec": k[0], "resolucion": k[1], "fps": k[2], "clips": v} for k, v in codecs.most_common()],
            "mezcla_formatos": len(codecs) > 1,
            "volumenes": dict(pr["volumenes"].most_common()),
            "origenes": dict(pr["origenes"].most_common()),
            "offline_detalle": [{"nombre": clip["nombre"], "volumenes": clip.get("volumenes", []), "segundos": clip.get("segundos"), "bin": nbin,
                                 "fichero_origen": clip.get("fichero_origen"), "origen": clip.get("origen")}
                                for clip, nbin in pr["offline_items"][:200]],
        })
    LOG.info("Cruce: coincidencias %s; huérfanos %d; renders %d; compartidos %d", dict(coincidencias), len(huerfanos), len(renders), len(compartidos))
    return {
        "modo_coincidencia": dict(coincidencias),
        "por_proyecto": resumen_proy,
        "huerfanos": [{"nombre": c["nombre"], "clave": c["clave"], "ficheros": c["ficheros"], "duracion": c["duracion"], "bytes": c["bytes"]}
                      for c in huerfanos],
        "renders": len(renders),
        "compartidos": [{"nombre": c["nombre"], "proyectos": c["proyectos"], "duracion": c["duracion"]} for c in compartidos],
    }


# ---------------------------------------------------------------------------
# Paso 5: miniaturas
# ---------------------------------------------------------------------------

def miniaturas(clips, origen, ffmpeg, destino, progreso, maximo=0):
    carpeta = destino / "miniaturas"
    carpeta.mkdir(exist_ok=True)
    con_video = [c for c in clips if c["fichero_video"]]
    if maximo:
        con_video = con_video[:maximo]
    total, hechos, fallos = len(con_video), 0, 0

    def trabajo(idx, c):
        salida = carpeta / ("%05d_%s.jpg" % (idx, nombre_seguro(c["nombre"], 40)))
        pos = max(0.0, (c["duracion"] or 0) * 0.10)
        cmd = [ffmpeg, "-v", "error", "-y", "-ss", "%.2f" % pos, "-i", str(Path(origen) / c["fichero_video"]),
               "-frames:v", "1", "-vf", "scale=%d:-2" % MINIATURA_ANCHO, "-q:v", "4", str(salida)]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, **flags_subproceso())
            if r.returncode == 0 and salida.exists():
                return c, "miniaturas/" + salida.name
            # segundo intento desde el principio (clips muy cortos)
            cmd[4] = "0"
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, **flags_subproceso())
            if r.returncode == 0 and salida.exists():
                return c, "miniaturas/" + salida.name
            LOG.warning("Sin miniatura %s: %s", c["nombre"], (r.stderr or "").strip()[:200])
        except Exception as e:
            LOG.warning("Sin miniatura %s: %s", c["nombre"], e)
        return c, None

    with ThreadPoolExecutor(max_workers=NUM_HILOS) as ex:
        futuros = [ex.submit(trabajo, i, c) for i, c in enumerate(con_video)]
        for fut in as_completed(futuros):
            c, ruta = fut.result()
            c["miniatura"] = ruta
            hechos += 1
            fallos += 0 if ruta else 1
            if hechos % 25 == 0 or hechos == total:
                progreso("Miniaturas: %d/%d" % (hechos, total))
    LOG.info("Miniaturas: %d generadas, %d fallidas", total - fallos, fallos)


# ---------------------------------------------------------------------------
# Paso 6: resumen y avisos
# ---------------------------------------------------------------------------

def construir_resumen(origen, ficheros, proyectos, bins, mxf, clips, cruce, ffmpeg_ok):
    bytes_total = sum(f["bytes"] for f in ficheros)
    bytes_media = sum(f["bytes"] for f in ficheros if f["ext"] in EXT_MEDIA)
    exts = Counter(f["ext"] or "(sin ext)" for f in ficheros)
    codecs = Counter((c["codec_video"], c["resolucion"], c["fps"]) for c in clips if c["codec_video"])
    tags_comentario = Counter(k for c in clips for k in c["comentarios"])
    avisos = []

    if not PYAVB_OK:
        avisos.append(("error", "pyavb no está instalado: no se han podido leer los bins."))
    if not ffmpeg_ok:
        avisos.append(("error", "ffmpeg/ffprobe no encontrados: no se han analizado los MXF ni generado miniaturas."))
    ilegibles = [b for b in bins if not b["ok"]]
    if ilegibles:
        avisos.append(("aviso", "%d bins no se han podido leer (ver bins.json). Candidatos a abrirlos en Avid." % len(ilegibles)))
    errores_mxf = [m for m in mxf if m["error"]]
    if errores_mxf:
        avisos.append(("aviso", "%d ficheros MXF dan error al leerse: posible corrupción o copia incompleta." % len(errores_mxf)))
    cuar = [m for m in mxf if m["en_cuarentena"]]
    if cuar:
        avisos.append(("aviso", "%d MXF en carpetas 'Quarantined Files' (%.2f GB): Avid los apartó al indexar; revisar antes de descartar."
                       % (len(cuar), gb(sum(m["bytes"] for m in cuar)))))
    creating = [m for m in mxf if m["en_creating"]]
    if creating:
        avisos.append(("aviso", "%d MXF en carpetas 'Creating': capturas o importaciones interrumpidas." % len(creating)))
    omf = sum(1 for f in ficheros if f["ext"] == ".omf")
    if omf:
        avisos.append(("info", "%d ficheros OMF (media de Avid antigua). No se analizan en esta fase." % omf))
    if cruce["huerfanos"]:
        avisos.append(("aviso", "%d clips físicos no aparecen en ningún bin (huérfanos, %.2f GB). Pueden ser de bins borrados o de otro disco."
                       % (len(cruce["huerfanos"]), gb(sum(h["bytes"] for h in cruce["huerfanos"])))))
    offline = sum(p["clips_offline"] for p in cruce["por_proyecto"])
    if offline:
        vols = Counter()
        for p in cruce["por_proyecto"]:
            for o in p["offline_detalle"]:
                for v in o["volumenes"]:
                    vols[v] += 1
        donde = ("; según los bins estaban en: " + ", ".join("%s (%d)" % kv for kv in vols.most_common(6))) if vols else ""
        avisos.append(("aviso", "%d clips de bin no tienen media en este disco (offline)%s." % (offline, donde)))
    if cruce.get("renders"):
        avisos.append(("info", "%d clips físicos son renders de efectos (títulos, fundidos). Se excluyen de los brutos." % cruce["renders"]))
    if cruce.get("compartidos"):
        avisos.append(("info", "%d clips aparecen en los bins de más de un proyecto. En los brutos cada uno va solo al primero (configurable)." % len(cruce["compartidos"])))
    mezcla = [p["proyecto"] for p in cruce["por_proyecto"] if p["mezcla_formatos"]]
    if mezcla:
        avisos.append(("info", "Proyectos con mezcla de formatos (habrá que convertir al unir el bruto): " + ", ".join(mezcla)))
    sin_audio = sum(1 for c in clips if c["num_video"] and not c["num_audio"])
    if sin_audio:
        avisos.append(("info", "%d clips de vídeo sin pista de audio." % sin_audio))
    solo_audio = sum(1 for c in clips if not c["num_video"])
    if solo_audio:
        avisos.append(("info", "%d grupos de ficheros solo con audio (sin vídeo con el mismo identificador)." % solo_audio))
    aprox = [p for p in cruce["por_proyecto"] if p.get("clips_aproximados") or p.get("clips_ambiguos")]
    if aprox:
        avisos.append(("aviso", "Cruce por nombre y duración en %d proyectos (%s): %d clips emparejados así y %d ambiguos que quedan sin emparejar. "
                       "El identificador no ha coincidido en esos proyectos; revisar con Adrián."
                       % (len(aprox), ", ".join(p["proyecto"] for p in aprox), sum(p["clips_aproximados"] for p in aprox), sum(p["clips_ambiguos"] for p in aprox))))
    exactos = {k: v for k, v in cruce["modo_coincidencia"].items() if k in ("le", "be") or k.startswith("fuente_")}
    if clips and not exactos and bins and any(b["ok"] and b["clips"] for b in bins):
        avisos.append(("error", "Ningún clip de bin ha coincidido con ningún MXF. El cruce por identificador no funciona con estos ficheros; revisar con Adrián."))
    if tags_comentario:
        avisos.append(("info", "Metadatos de usuario encontrados dentro de los MXF: " + ", ".join(sorted(tags_comentario))))
    origenes_tot = sum((Counter(p.get("origenes", {})) for p in cruce["por_proyecto"]), Counter())
    if origenes_tot:
        avisos.append(("info", "Origen del material según los bins: " + ", ".join("%s %d" % (o, n) for o, n in origenes_tot.most_common())
                       + ". Reglas en config_origen.json."))

    return {
        "version": VERSION,
        "build": BUILD,
        "fecha": ahora_txt(),
        "origen": str(origen),
        "ficheros": len(ficheros),
        "bytes_total": bytes_total,
        "gb_total": gb(bytes_total),
        "gb_media": gb(bytes_media),
        "proyectos": len([p for p in proyectos if p["ficheros_avp"]]),
        "bins": len(bins),
        "bins_ilegibles": len(ilegibles),
        "clips_en_bins": sum(1 for b in bins if b["ok"] for c in b["clips"] if not c.get("render")),
        "renders_en_bins": sum(1 for b in bins if b["ok"] for c in b["clips"] if c.get("render")),
        "clips_compartidos": len(cruce.get("compartidos", [])),
        "origenes": dict(sum((Counter(p.get("origenes", {})) for p in cruce["por_proyecto"]), Counter()).most_common()),
        "secuencias_en_bins": sum(1 for b in bins if b["ok"] for s in b["secuencias"] if not s.get("uso")),
        "mxf": len(mxf),
        "mxf_error": len(errores_mxf),
        "clips_fisicos": len(clips),
        "segundos_media": round(sum(c["duracion"] or 0 for c in clips), 1),
        "huerfanos": len(cruce["huerfanos"]),
        "offline": offline,
        "modo_coincidencia": cruce["modo_coincidencia"],
        "extensiones": dict(exts.most_common(30)),
        "formatos": [{"codec": k[0], "resolucion": k[1], "fps": k[2], "clips": v} for k, v in codecs.most_common()],
        "productos": dict(Counter(m.get("producto") for m in mxf if m.get("producto")).most_common(10)),
        "comentarios_mxf": dict(tags_comentario),
        "avisos": [{"nivel": n, "texto": t} for n, t in avisos],
    }


# ---------------------------------------------------------------------------
# Paso 7: informe HTML
# ---------------------------------------------------------------------------

def esc(t):
    return (str(t) if t is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


CSS = """
:root{--bg:#f5f6f8;--card:#fff;--ink:#1d2330;--muted:#6b7280;--line:#e3e6eb;--ok:#1a7f4b;--warn:#b7791f;--err:#b42318;--acc:#1f4e9c}
*{box-sizing:border-box}body{margin:0;font:15px/1.45 system-ui,Segoe UI,Roboto,sans-serif;color:var(--ink);background:var(--bg)}
header{background:#101828;color:#fff;padding:28px 36px}header h1{margin:0 0 4px;font-size:24px;font-weight:600}header p{margin:0;color:#aab2c0}
main{max-width:1280px;margin:0 auto;padding:24px 36px 60px}h2{font-size:18px;margin:34px 0 12px;border-bottom:1px solid var(--line);padding-bottom:6px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}.kpi{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px 16px}
.kpi b{display:block;font-size:26px;font-weight:600}.kpi span{color:var(--muted);font-size:13px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden;font-size:14px}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}th{background:#eef1f5;font-weight:600}td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.aviso{padding:10px 14px;border-radius:6px;margin:6px 0;border-left:4px solid}.error{background:#fef3f2;border-color:var(--err)}.aviso.aviso{background:#fffaeb;border-color:var(--warn)}.info{background:#eff6ff;border-color:var(--acc)}
.tag{display:inline-block;background:#eef1f5;border-radius:4px;padding:1px 7px;font-size:12px;margin:1px 2px 1px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px}.clip{background:var(--card);border:1px solid var(--line);border-radius:6px;overflow:hidden}
.clip img{width:100%;aspect-ratio:16/9;object-fit:cover;display:block;background:#222}.clip .sin{aspect-ratio:16/9;background:#2a2f3a;color:#9aa3b2;display:flex;align-items:center;justify-content:center;font-size:12px}
.clip div.t{padding:6px 8px;font-size:12px}.clip b{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.clip span{color:var(--muted)}
details{margin:8px 0}summary{cursor:pointer;font-weight:600;padding:8px 0}.muted{color:var(--muted)}footer{color:var(--muted);font-size:12px;margin-top:40px}
"""


def tarjeta_clip(c):
    if c.get("miniatura"):
        img = "<img loading='lazy' src='%s' alt=''>" % esc(c["miniatura"])
    elif not c["num_video"]:
        img = "<div class='sin'>solo audio</div>"
    else:
        img = "<div class='sin'>sin miniatura</div>"
    extra = " &middot; <span title='%s'>en %d proyectos</span>" % (esc(", ".join(c["proyectos"])), len(c["proyectos"])) if len(c.get("proyectos", [])) > 1 else ""
    if c.get("coincidencia") == "aproximada":
        extra += " &middot; <span class='tag' style='background:#fffaeb' title='Emparejado con el bin por nombre y duración, no por identificador'>cruce aproximado</span>"
    return ("<div class='clip'>%s<div class='t'><b title='%s'>%s</b><span>%s &middot; %s &middot; %dV/%dA%s</span></div></div>"
            % (img, esc(c["nombre"]), esc(c["nombre"]), fmt_duracion(c["duracion"]), esc(c["timecode"] or "sin TC"), c["num_video"], c["num_audio"], extra))


def informe_html(resumen, proyectos, bins, clips, cruce, destino):
    r = resumen
    h = []
    h.append("<!doctype html><html lang='es'><head><meta charset='utf-8'><title>Diagnóstico disco Avid</title><style>%s</style></head><body>" % CSS)
    h.append("<header><h1>Diagnóstico del disco de edición Avid</h1><p>%s &middot; analizado el %s &middot; versión %s (%s)</p></header><main>"
             % (esc(r["origen"]), esc(r["fecha"]), VERSION, esc(BUILD)))

    h.append("<div class='kpis'>")
    for v, k in [(r["proyectos"], "proyectos"), (r["clips_fisicos"], "clips con media"), (fmt_duracion(r["segundos_media"]), "duración total"),
                 ("%.1f GB" % r["gb_media"], "de media"), (r["secuencias_en_bins"], "secuencias (piezas)"), (r["mxf"], "ficheros MXF"),
                 (r["huerfanos"], "clips huérfanos"), (r["offline"], "clips offline")]:
        h.append("<div class='kpi'><b>%s</b><span>%s</span></div>" % (esc(v), esc(k)))
    h.append("</div>")

    h.append("<h2>Avisos</h2>")
    if not r["avisos"]:
        h.append("<div class='aviso info'>Sin incidencias destacables.</div>")
    for a in r["avisos"]:
        h.append("<div class='aviso %s'>%s</div>" % (a["nivel"], esc(a["texto"])))

    h.append("<h2>Proyectos</h2><table><tr><th>Proyecto</th><th class='n'>Bins</th><th class='n'>Clips en bin</th><th class='n'>Con media</th><th class='n'>Offline</th><th class='n'>Compartidos</th><th class='n'>Secuencias</th><th class='n'>Duración</th><th class='n'>GB</th><th>Formatos</th></tr>")
    for p in cruce["por_proyecto"]:
        fm = " ".join("<span class='tag'>%s %s %s (%d)</span>" % (esc(f["codec"]), esc(f["resolucion"]), esc(f["fps"]), f["clips"]) for f in p["formatos"])
        extra = " <span class='tag' style='background:#fef3f2'>%d bins ilegibles</span>" % p["bins_ilegibles"] if p["bins_ilegibles"] else ""
        if p.get("clips_aproximados") or p.get("clips_ambiguos"):
            extra += " <span class='tag' style='background:#fffaeb'>%d por nombre y duración, %d ambiguos</span>" % (p["clips_aproximados"], p["clips_ambiguos"])
        h.append("<tr><td>%s%s</td><td class='n'>%d</td><td class='n'>%d</td><td class='n'>%d</td><td class='n'>%d</td><td class='n'>%d</td><td class='n'>%d</td><td class='n'>%s</td><td class='n'>%.2f</td><td>%s</td></tr>"
                 % (esc(p["proyecto"]), extra, p["bins"], p["clips_en_bin"], p["clips_fisicos"], p["clips_offline"], p["clips_compartidos"], p["secuencias"], fmt_duracion(p["segundos"]), gb(p["bytes"]), fm))
    h.append("</table>")

    h.append("<h2>Formatos de vídeo en el disco</h2><table><tr><th>Códec</th><th>Resolución</th><th>fps</th><th class='n'>Clips</th></tr>")
    for f in r["formatos"]:
        h.append("<tr><td>%s</td><td>%s</td><td>%s</td><td class='n'>%d</td></tr>" % (esc(f["codec"]), esc(f["resolucion"]), esc(f["fps"]), f["clips"]))
    h.append("</table>")
    if r["productos"]:
        h.append("<p class='muted'>Escrito por: %s</p>" % esc("; ".join("%s (%d)" % kv for kv in r["productos"].items())))

    h.append("<h2>Contenido por proyecto</h2>")
    por_proy = defaultdict(list)
    for c in clips:
        for p in c["proyectos"]:
            por_proy[p].append(c)
    for p in cruce["por_proyecto"]:
        cs = sorted((c for c in por_proy.get(p["proyecto"], []) if not c.get("render")), key=lambda c: c["nombre"])
        h.append("<details><summary>%s &middot; %d clips &middot; %s</summary>" % (esc(p["proyecto"]), len(cs), fmt_duracion(p["segundos"])))
        bs = [b for b in bins if b["proyecto"] == p["proyecto"]]
        h.append("<table><tr><th>Bin</th><th class='n'>Clips</th><th class='n'>Renders</th><th class='n'>Secuencias</th><th>Estado</th></tr>")
        for b in bs:
            estado = "OK" if b["ok"] else "ERROR: " + esc(b["error"])
            if b["papelera"]:
                estado += " (papelera)"
            nr = sum(1 for c in b["clips"] if c.get("render"))
            h.append("<tr><td>%s</td><td class='n'>%d</td><td class='n'>%d</td><td class='n'>%d</td><td>%s</td></tr>" % (esc(b["nombre"]), len(b["clips"]) - nr, nr, len(b["secuencias"]), estado))
        h.append("</table>")
        secs = [s for b in bs if b["ok"] for s in b["secuencias"] if not s.get("uso")]  # sin los contenedores de efectos
        if secs:
            h.append("<p><b>Secuencias:</b> " + " ".join("<span class='tag'>%s (%s)</span>" % (esc(s["nombre"]), fmt_duracion(s["segundos"])) for s in secs[:60]) + "</p>")
        if p.get("origenes"):
            h.append("<p class='muted'><b>Origen del material (según los bins):</b> %s</p>"
                     % " ".join("<span class='tag'>%s (%d)</span>" % (esc(o), n) for o, n in p["origenes"].items()))
        if p.get("volumenes"):
            h.append("<p class='muted'><b>Discos donde los bins vieron la media:</b> %s</p>"
                     % " ".join("<span class='tag'>%s (%d clips)</span>" % (esc(v), n) for v, n in p["volumenes"].items()))
        if p["offline_detalle"]:
            od = p["offline_detalle"]
            h.append("<details><summary>Offline: %d clips sin media en este disco</summary><table><tr><th>Clip</th><th>Bin</th><th class='n'>Duración</th><th>Origen</th><th>Último disco conocido</th><th>Fichero importado</th></tr>" % p["clips_offline"])
            for o in od[:200]:
                fo = o.get("fichero_origen") or ""
                h.append("<tr><td>%s</td><td>%s</td><td class='n'>%s</td><td>%s</td><td>%s</td><td title='%s'>%s</td></tr>"
                         % (esc(o["nombre"]), esc(o["bin"]), fmt_duracion(o["segundos"]), esc(o.get("origen") or ""), esc(", ".join(o["volumenes"]) or "—"), esc(fo), esc(fo.replace("\\", "/").rsplit("/", 1)[-1])))
            if p["clips_offline"] > len(od):
                h.append("<tr><td colspan='6' class='muted'>… y %d más (ver cruce.json)</td></tr>" % (p["clips_offline"] - len(od)))
            h.append("</table></details>")
        h.append("<div class='grid'>")
        for c in cs:
            h.append(tarjeta_clip(c))
        h.append("</div></details>")

    if cruce["huerfanos"]:
        hs = [c for c in clips if not c["proyectos"]]
        h.append("<details><summary>Clips huérfanos (sin bin) &middot; %d</summary><div class='grid'>" % len(hs))
        for c in sorted(hs, key=lambda c: c["nombre"]):
            h.append(tarjeta_clip(c))
        h.append("</div></details>")

    if resumen.get("brutos") is not None:
        h.append("<h2>Brutos exportados</h2><p class='muted'>Carpeta: %s</p>" % esc(resumen.get("brutos_carpeta")))
        h.append("<table><tr><th>Proyecto</th><th>Fichero</th><th class='n'>Clips</th><th class='n'>Incluidos</th><th class='n'>Fallidos</th><th>Modo</th><th class='n'>Audio</th><th class='n'>Duración</th><th class='n'>GB</th><th>Estado</th></tr>")
        for b in resumen["brutos"]:
            color = "" if b["estado"].startswith("OK") else " style='background:#fef3f2'"
            h.append("<tr%s><td>%s</td><td>%s</td><td class='n'>%d</td><td class='n'>%d</td><td class='n'>%d</td><td>%s</td><td class='n'>%d</td><td class='n'>%s</td><td class='n'>%.2f</td><td>%s</td></tr>"
                     % (color, esc(b["proyecto"]), esc(b["fichero"] or "—"), b["clips"], b["incluidos"], b["fallidos"],
                        esc(b["modo"] + (" (no se pudo copiar)" if b.get("reintento") else "")), b["pistas_audio"],
                        fmt_duracion(b["segundos"]), gb(b["bytes"]), esc(b["estado"] + (" · de una ejecución anterior" if b.get("reanudado") else ""))))
        h.append("</table><p class='muted'>Junto a cada bruto hay un shotlist Excel con el timecode de cada clip y una hoja de contactos HTML.</p>")

    h.append("<footer>Generado por diagnostico_avid.py v%s, %s. No se ha modificado nada en el disco de origen.</footer></main></body></html>" % (VERSION, esc(BUILD)))
    (destino / "informe.html").write_text("\n".join(h), encoding="utf-8")


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------

def evitar_suspension(activar):
    """En Windows, impide que el PC se suspenda mientras dura el análisis o la exportación."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if activar else 0))
    except Exception as e:
        LOG.warning("No se ha podido %s la suspensión del PC: %s", "impedir" if activar else "restaurar", e)


def guardar_json(destino, nombre, datos):
    with open(destino / nombre, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=1, default=str)


def ejecutar(origen, destino_base, progreso=print, hacer_miniaturas=True, max_miniaturas=0, abrir=True, brutos=None,
             zip_miniaturas=True):
    origen = Path(origen)
    if not origen.exists():
        raise FileNotFoundError("No existe la carpeta de origen: %s" % origen)
    etiqueta = nombre_seguro(origen.name or origen.drive.rstrip(":\\/") or "disco", 30)
    destino = Path(destino_base) / ("Diagnostico_%s_%s" % (etiqueta, dt.datetime.now().strftime("%Y%m%d_%H%M")))
    destino.mkdir(parents=True, exist_ok=True)
    if destino.resolve().is_relative_to(origen.resolve()):
        raise ValueError("El destino no puede estar dentro del disco de origen (este programa no escribe en él).")
    if brutos:
        brutos = Path(brutos)
        if brutos.resolve().is_relative_to(origen.resolve()):
            raise ValueError("La carpeta de brutos no puede estar dentro del disco de origen.")
        if not EXPORT_OK:
            raise RuntimeError("Falta el módulo exportar_brutos.py junto al programa.")

    fh = logging.FileHandler(destino / "diagnostico.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOG.addHandler(fh)
    LOG.setLevel(logging.INFO)
    evitar_suspension(True)
    try:
        LOG.info("Diagnóstico v%s (%s). Origen=%s Destino=%s Python=%s pyavb=%s", VERSION, BUILD, origen, destino, sys.version.split()[0], PYAVB_OK)

        ffmpeg, ffprobe = localizar_ffmpeg()
        ffmpeg_ok = bool(ffmpeg)
        LOG.info("ffmpeg=%s ffprobe=%s", ffmpeg, ffprobe)
        progreso("Inicio. Destino: %s" % destino)

        progreso("1/6 Inventariando el disco...")
        ficheros = inventariar(origen, progreso)
        escribir_inventario_csv(ficheros, destino)

        progreso("2/6 Detectando proyectos y leyendo bins...")
        proyectos = detectar_proyectos(ficheros)
        bins = leer_bins(proyectos, origen, progreso)
        guardar_json(destino, "proyectos.json", proyectos)
        guardar_json(destino, "bins.json", bins)

        progreso("3/6 Analizando ficheros MXF...")
        mxf = analizar_mxf(ficheros, origen, ffprobe, progreso) if ffmpeg_ok else []
        guardar_json(destino, "mxf.json", mxf)

        progreso("4/6 Reconstruyendo clips y cruzando con los bins...")
        clips = agrupar_clips(mxf)
        cruce = cruzar(proyectos, bins, clips, mxf)

        if hacer_miniaturas and ffmpeg_ok:
            progreso("5/6 Generando miniaturas...")
            miniaturas(clips, origen, ffmpeg, destino, progreso, max_miniaturas)
        else:
            progreso("5/6 Miniaturas omitidas.")
        guardar_json(destino, "clips.json", clips)
        guardar_json(destino, "cruce.json", cruce)

        resultados_brutos = None
        if brutos:
            if not ffmpeg_ok:
                progreso("Brutos: omitidos, no hay ffmpeg.")
            else:
                progreso("Brutos: exportando un MXF por proyecto en %s" % brutos)
                cfg = exportar_brutos.cargar_config(carpeta_base() / "config_brutos.json")
                resultados_brutos = exportar_brutos.exportar_todo(clips, cruce, mxf, origen, brutos, ffmpeg, ffprobe, progreso, cfg,
                                                                  miniaturas_dir=destino / "miniaturas" if hacer_miniaturas else None)

        progreso("6/6 Escribiendo resumen e informe...")
        resumen = construir_resumen(origen, ficheros, proyectos, bins, mxf, clips, cruce, ffmpeg_ok)
        if resultados_brutos is not None:
            resumen["brutos"] = resultados_brutos
            resumen["brutos_carpeta"] = str(brutos)
            fallos = [b for b in resultados_brutos if not b["estado"].startswith("OK")]
            if fallos:
                resumen["avisos"].append({"nivel": "error", "texto": "%d brutos con problemas: %s" % (len(fallos), ", ".join(b["proyecto"] for b in fallos))})
        guardar_json(destino, "resumen.json", resumen)
        informe_html(resumen, proyectos, bins, clips, cruce, destino)

        zip_path = destino.with_suffix(".zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for f in destino.rglob("*"):
                if f.is_file() and (zip_miniaturas or f.parent.name != "miniaturas"):
                    z.write(f, f.relative_to(destino.parent))
        LOG.info("ZIP: %s (%.1f MB)", zip_path, zip_path.stat().st_size / 1e6)

        progreso("Terminado. Informe: %s" % (destino / "informe.html"))
        progreso("ZIP para enviar: %s" % zip_path)
        if abrir:
            try:
                webbrowser.open((destino / "informe.html").as_uri())
            except Exception:
                pass
        return destino, zip_path, resumen
    except Exception:
        LOG.exception("Fallo durante la ejecución")
        raise
    finally:  # aunque falle: sin esto el log se duplicaría en la siguiente ejecución desde la ventana
        evitar_suspension(False)
        LOG.removeHandler(fh)
        fh.close()


# ---------------------------------------------------------------------------
# Ventana (tkinter)
# ---------------------------------------------------------------------------

def lanzar_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    raiz = tk.Tk()
    raiz.title("Diagnóstico de disco Avid — v%s (%s)" % (VERSION, BUILD))
    raiz.geometry("780x600")
    raiz.minsize(640, 420)

    origen = tk.StringVar()
    destino = tk.StringVar(value=str(Path.home() / "Desktop"))
    minis = tk.BooleanVar(value=True)
    zip_minis = tk.BooleanVar(value=True)
    hacer_brutos = tk.BooleanVar(value=False)
    brutos = tk.StringVar()
    cola = queue.Queue()
    estado = {"ocupado": False}

    marco = ttk.Frame(raiz, padding=14)
    marco.pack(fill="both", expand=True)
    ttk.Label(marco, text="1. Disco o carpeta de Avid que quieres analizar (solo se lee, no se modifica):").grid(row=0, column=0, columnspan=2, sticky="w")
    ttk.Entry(marco, textvariable=origen).grid(row=1, column=0, sticky="ew", pady=(2, 10))
    ttk.Button(marco, text="Elegir...", command=lambda: origen.set(filedialog.askdirectory(title="Disco de Avid") or origen.get())).grid(row=1, column=1, padx=(8, 0))
    ttk.Label(marco, text="2. Dónde guardar el informe (otro disco o el escritorio):").grid(row=2, column=0, columnspan=2, sticky="w")
    ttk.Entry(marco, textvariable=destino).grid(row=3, column=0, sticky="ew", pady=(2, 10))
    ttk.Button(marco, text="Elegir...", command=lambda: destino.set(filedialog.askdirectory(title="Carpeta de salida") or destino.get())).grid(row=3, column=1, padx=(8, 0))
    ttk.Checkbutton(marco, text="Generar miniaturas de cada clip (más lento, pero el informe queda completo)", variable=minis).grid(row=4, column=0, columnspan=2, sticky="w")
    ttk.Checkbutton(marco, text="Incluir las miniaturas en el zip para enviar (desmarcado: el zip solo lleva texto)", variable=zip_minis).grid(row=5, column=0, columnspan=2, sticky="w")
    ttk.Checkbutton(marco, text="3. Exportar además un MXF por proyecto (brutos). Tarda mucho y necesita un disco con espacio:",
                    variable=hacer_brutos).grid(row=6, column=0, columnspan=2, sticky="w", pady=(10, 0))
    ttk.Entry(marco, textvariable=brutos).grid(row=7, column=0, sticky="ew", pady=(2, 10))
    ttk.Button(marco, text="Elegir...", command=lambda: brutos.set(filedialog.askdirectory(title="Carpeta para los brutos") or brutos.get())).grid(row=7, column=1, padx=(8, 0))

    boton = ttk.Button(marco, text="Analizar disco")
    boton.grid(row=8, column=0, columnspan=2, pady=12)
    barra = ttk.Progressbar(marco, mode="indeterminate")
    barra.grid(row=9, column=0, columnspan=2, sticky="ew")
    registro = tk.Text(marco, height=14, wrap="word", state="disabled", font=("Consolas", 10))
    registro.grid(row=10, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
    marco.columnconfigure(0, weight=1)
    marco.rowconfigure(10, weight=1)

    def escribe(txt):
        registro.configure(state="normal")
        registro.insert("end", txt + "\n")
        registro.see("end")
        registro.configure(state="disabled")

    def vaciar_cola():
        try:
            while True:
                tipo, msg = cola.get_nowait()
                if tipo == "log":
                    escribe(msg)
                elif tipo == "fin":
                    barra.stop()
                    boton.configure(state="normal")
                    estado["ocupado"] = False
                    messagebox.showinfo("Terminado", "Diagnóstico terminado.\n\nInforme: %s\n\nZIP para enviar: %s" % msg)
                elif tipo == "error":
                    barra.stop()
                    boton.configure(state="normal")
                    estado["ocupado"] = False
                    messagebox.showerror("Error", msg)
        except queue.Empty:
            pass
        raiz.after(200, vaciar_cola)

    def hilo():
        try:
            d, z, _ = ejecutar(origen.get(), destino.get(), progreso=lambda m: cola.put(("log", m)), hacer_miniaturas=minis.get(),
                               brutos=brutos.get() if hacer_brutos.get() else None, zip_miniaturas=zip_minis.get())
            cola.put(("fin", (str(d / "informe.html"), str(z))))
        except Exception as e:
            LOG.error("Fallo: %s\n%s", e, traceback.format_exc())  # ejecutar ya lo ha dejado en diagnostico.log
            cola.put(("error", "%s\n\nDetalle en diagnostico.log (si llegó a crearse)." % e))

    def iniciar():
        if estado["ocupado"]:
            return
        if not origen.get() or not Path(origen.get()).exists():
            messagebox.showwarning("Falta el origen", "Elige el disco o la carpeta de Avid que quieres analizar.")
            return
        if not destino.get():
            messagebox.showwarning("Falta el destino", "Elige dónde guardar el informe.")
            return
        if hacer_brutos.get() and not brutos.get():
            messagebox.showwarning("Falta la carpeta de brutos", "Has marcado exportar brutos: elige la carpeta donde guardarlos (un disco con espacio).")
            return
        ff, _ = localizar_ffmpeg()
        if hacer_brutos.get() and not ff:
            messagebox.showerror("Sin ffmpeg", "Para exportar brutos hace falta ffmpeg (carpeta bin o PATH).")
            return
        if not ff and not messagebox.askyesno("Sin ffmpeg", "No se encuentra ffmpeg/ffprobe (carpeta bin o PATH).\nSe analizarán los bins pero no los ficheros de vídeo.\n¿Continuar igualmente?"):
            return
        estado["ocupado"] = True
        boton.configure(state="disabled")
        barra.start(12)
        escribe("— %s —" % ahora_txt())
        threading.Thread(target=hilo, daemon=True).start()

    boton.configure(command=iniciar)
    escribe("Programa de diagnóstico de discos Avid (solo lectura). pyavb: %s. ffmpeg: %s."
            % ("OK" if PYAVB_OK else "NO INSTALADO", "OK" if localizar_ffmpeg()[0] else "no encontrado"))
    raiz.after(200, vaciar_cola)
    raiz.mainloop()


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Diagnóstico de disco Avid (solo lectura).")
    ap.add_argument("--origen", help="Disco o carpeta de Avid a analizar")
    ap.add_argument("--destino", help="Carpeta donde dejar el informe")
    ap.add_argument("--sin-gui", action="store_true", help="No abrir ventana; requiere --origen y --destino")
    ap.add_argument("--sin-miniaturas", action="store_true", help="No generar miniaturas")
    ap.add_argument("--max-miniaturas", type=int, default=0, help="Limitar el número de miniaturas (0 = todas)")
    ap.add_argument("--no-abrir", action="store_true", help="No abrir el informe en el navegador al terminar")
    ap.add_argument("--brutos", help="Carpeta donde exportar un MXF por proyecto (fase 2)")
    ap.add_argument("--zip-sin-miniaturas", action="store_true", help="El zip para enviar no lleva las miniaturas (el informe local sí)")
    args = ap.parse_args()

    if sys.stderr is not None:  # en el .exe sin consola no hay salida estándar
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.sin_gui or (args.origen and args.destino):
        if not (args.origen and args.destino):
            ap.error("--sin-gui requiere --origen y --destino")
        ejecutar(args.origen, args.destino, hacer_miniaturas=not args.sin_miniaturas,
                 max_miniaturas=args.max_miniaturas, abrir=not args.no_abrir, brutos=args.brutos,
                 zip_miniaturas=not args.zip_sin_miniaturas)
        return
    try:
        lanzar_gui()
    except ImportError:
        print("No hay tkinter disponible. Usa: --origen RUTA --destino RUTA --sin-gui")
        sys.exit(2)


if __name__ == "__main__":
    main()
