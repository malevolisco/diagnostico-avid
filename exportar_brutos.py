# -*- coding: utf-8 -*-
"""
EXPORTACIÓN DE BRUTOS — Fase 2
==============================

A partir del análisis de la fase 1 (clips físicos ya cruzados con los bins),
genera por cada proyecto UN solo fichero MXF OP1a con todos sus clips
encadenados, más un shotlist Excel (timecode de cada clip dentro del bruto)
y una hoja de contactos HTML con las miniaturas.

Reparto: el material de cámara de cada proyecto se parte en un bruto por tarjeta,
por tandas de volcado (fecha en que Avid escribió la media). Aparte, por proyecto:
_AGENCIAS (origen de agencia según el bin), _IMPORTADOS (ficheros importados que
no son de tarjeta) y _PIEZAS (piezas renderizadas y renders de efectos).

Modo de trabajo por bruto:
  - "copia":     todos los clips comparten códec, resolución, fps y formato
                 de píxel → se encadenan sin recomprimir (sin pérdida).
  - "convertir": hay mezcla de formatos o el códec no se puede copiar →
                 se convierte todo al formato de destino de config_brutos.json.

El audio se normaliza siempre a PCM 24 bits 48 kHz mono por pista (sin
pérdida respecto a 16 bits) y todos los clips de un bruto llevan el mismo
número de pistas: las que falten se rellenan con silencio.

Resiliencia: si copiar falla por el códec, el proyecto se repite convirtiendo;
ffmpeg tiene tiempo máximo; brutos_resultado.json se escribe tras cada
proyecto y al relanzar se saltan los ya terminados.

Este módulo lo usa diagnostico_avid.py; no se ejecuta solo.
"""

import datetime as dt
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path

LOG = logging.getLogger("diagnostico")

CONFIG_DEFECTO = {
    "_comentario": "Parámetros de exportación de brutos. Borra este fichero para volver a los valores por defecto.",
    "modo": "auto",
    "_modo": "auto = copia directa si todos los clips del proyecto son iguales, si no convierte | copia | convertir",
    "pistas_audio": "max",
    "_pistas_audio": "max = tantas como el clip que más tenga (tope 8) | un número fijo | 0 = sin audio",
    "timecode_inicio": "00:00:00:00",
    "_timecode_inicio": "TC del primer frame del bruto. Escríbelo siempre hh:mm:ss:ff; si el bruto sale a 29,97 o 59,94 se usa drop-frame (hh:mm:ss;ff)",
    "incluir_huerfanos": True,
    "_incluir_huerfanos": "Los clips que no están en ningún bin van a brutos SIN_PROYECTO (también partidos por tarjeta)",
    "partir_por_tarjeta": True,
    "_partir_por_tarjeta": "true = un bruto por tarjeta de cámara dentro de cada proyecto (_T01, _T02...) | false = un bruto por proyecto",
    "tarjeta_hueco_horas": 2,
    "_tarjeta_hueco_horas": "Un salto de más de estas horas entre clips (fecha en que se escribió la media en el disco de Avid) se considera otra tarjeta",
    "tarjeta_minimo_minutos": 5,
    "_tarjeta_minimo_minutos": "Una tanda con menos de estos minutos de material se junta con la tanda más cercana del mismo origen (si está a menos de 24 h). 0 = no juntar",
    "importados_aparte": True,
    "_importados_aparte": "Los ficheros importados (MP4 de descargas, móvil, agencias no reconocidas...: el bin guarda su ruta y no es una tarjeta) van al bruto _IMPORTADOS del proyecto",
    "agencias_aparte": True,
    "origenes_agencia": ["Agencia"],
    "_origenes_agencia": "Clips cuyo origen (según config_origen.json) empieza por alguno de estos textos van al bruto _AGENCIAS del proyecto",
    "piezas_aparte": True,
    "piezas_regex": "",
    "_piezas_regex": "Van al bruto _PIEZAS los renders de efectos y los clips que se llaman como una secuencia del proyecto (pieza exportada o mixdown). Aquí se puede añadir una expresión regular sobre el nombre del clip, p. ej. (?i)mixdown|emitid",
    "renders_en_piezas": True,
    "_renders_en_piezas": "Los renders de efectos (títulos, fundidos) van al bruto _PIEZAS; false = se descartan",
    "clips_compartidos": "primero",
    "_clips_compartidos": "Un clip que está en varios proyectos: primero = solo en el bruto del primer proyecto (orden alfabético) | todos = en cada bruto",
    "prefijo_fichero": "BRUTO_",
    "convertir": {
        "codec": "mpeg2video",
        "ancho": "auto",
        "alto": "auto",
        "fps": "auto",
        "bitrate": "50M",
        "desentrelazar": "auto",
        "_nota": "Solo se usa cuando un proyecto mezcla formatos. auto = el formato mayoritario del proyecto (no cambia NTSC a PAL ni SD a HD). Códec MPEG-2 4:2:2 (tipo XDCAM/IMX). desentrelazar: auto = solo si hay que reescalar | si | no"
    },
    "verificar_decodificando": False,
    "_verificar_decodificando": "true = tras unir, decodifica el bruto entero para comprobarlo (lento, muy seguro)"
}

PARAR = threading.Event()  # lo activa el botón Detener de la ventana


class Detenido(Exception):
    """El usuario ha pulsado Detener."""


CODECS_COPIABLES = {"dnxhd", "mpeg2video", "dvvideo", "prores", "h264"}
MAX_PISTAS = 8


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def flags_subproceso():
    if os.name == "nt":
        return {"creationflags": 0x08000000}
    return {}


def cargar_config(ruta):
    ruta = Path(ruta)
    if not ruta.exists():
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(CONFIG_DEFECTO, f, ensure_ascii=False, indent=2)
        return json.loads(json.dumps(CONFIG_DEFECTO))
    try:
        with open(ruta, encoding="utf-8") as f:
            cfg = json.load(f)
        base = json.loads(json.dumps(CONFIG_DEFECTO))
        base.update({k: v for k, v in cfg.items() if k != "convertir"})
        base["convertir"].update(cfg.get("convertir", {}))
        return base
    except Exception as e:
        LOG.error("config_brutos.json ilegible (%s); se usan valores por defecto", e)
        return json.loads(json.dumps(CONFIG_DEFECTO))


def nombre_seguro(texto, maximo=60):
    import re
    texto = re.sub(r'[\\/:*?"<>|]+', "_", str(texto)).strip().strip(".")
    return (texto or "sin_nombre")[:maximo]


def es_df(fps):
    """Drop-frame solo con fps de salida NTSC (29,97 o 59,94). ffmpeg aborta si se pide DF con otros fps."""
    try:
        return any(abs(float(fps) - x) < 0.01 for x in (30000 / 1001, 60000 / 1001))
    except (TypeError, ValueError):
        return False


def _base_tc(fps):
    """(fps nominal, frames que se saltan por minuto en DF)."""
    nominal = int(round(fps))
    return nominal, (2 * nominal // 30 if es_df(fps) else 0)


def fmt_tc(frames, fps):
    """Frames desde 0 -> timecode. DF (29,97/59,94): hh:mm:ss;ff, la única sintaxis que acepta ffmpeg; si no, hh:mm:ss:ff.
    Ajuste DF igual que av_timecode_adjust_ntsc_framenum2 de ffmpeg."""
    frames = int(frames)
    nominal, salto = _base_tc(fps)
    if salto:
        diez_min = 17982 * nominal // 30
        d, m = divmod(frames, diez_min)
        frames += 9 * salto * d + salto * max(0, (m - salto) // (diez_min // 10))
    f = frames % nominal
    s_total = frames // nominal
    h, r = divmod(s_total, 3600)
    m, s = divmod(r, 60)
    return "%02d:%02d:%02d%s%02d" % (h, m, s, ";" if salto else ":", f)


def tc_a_frames(texto, fps):
    """Timecode hh:mm:ss:ff (o ;ff) -> frames desde 0, con la misma convención que fmt_tc."""
    t = str(texto).strip().replace(";", ":").replace(".", ":").split(":")
    if len(t) != 4 or not all(x.isdigit() for x in t):
        raise ValueError("timecode no válido: %r" % texto)
    h, m, s, f = (int(x) for x in t)
    nominal, salto = _base_tc(fps)
    minutos = 60 * h + m
    return (3600 * h + 60 * m + s) * nominal + f - salto * (minutos - minutos // 10)


def fps_num(texto):
    """'25/1' -> 25.0"""
    try:
        if "/" in str(texto):
            a, b = str(texto).split("/")
            return float(a) / float(b)
        return float(texto)
    except Exception:
        return 25.0


TIEMPO_AGOTADO = "tiempo agotado"


def ejecutar_ffmpeg(cmd, timeout):
    """Devuelve (ok, últimas líneas de stderr). timeout en segundos: un MXF dañado no debe colgar la exportación.
    Si se pulsa Detener, mata ffmpeg en un segundo como mucho y lanza Detenido."""
    if PARAR.is_set():
        raise Detenido()
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, **flags_subproceso())
    except Exception as e:
        return False, str(e)
    limite = time.monotonic() + timeout
    while True:
        try:
            _, err = proc.communicate(timeout=1)
            return proc.returncode == 0, (err or b"").decode("utf-8", "replace").strip()[-600:]
        except subprocess.TimeoutExpired:
            if PARAR.is_set() or time.monotonic() > limite:
                proc.kill()
                proc.communicate()
                if PARAR.is_set():
                    raise Detenido()
                return False, TIEMPO_AGOTADO


def timeout_segmento(duracion):
    return max(600, 30 * (duracion or 0))


def timeout_union(duracion_total):
    return max(1800, 10 * (duracion_total or 0))


def detalle_error(clip, fase, cmd, err):
    """Lo que se guarda en brutos_resultado.json de cada error: comando truncado y final de stderr."""
    return {"clip": clip, "fase": fase, "comando": " ".join(str(x) for x in cmd)[:300],
            "error": "\n".join((err or "").splitlines()[-5:])}


def duracion_ffprobe(ffprobe, ruta):
    cmd = [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(ruta)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, **flags_subproceso())
        return float(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Reparto en brutos: tarjetas, agencias y piezas
# ---------------------------------------------------------------------------

# Carpetas que cuelgan de la raíz de una tarjeta de cámara (P2, XDCAM/XAVC, AVCHD, foto/vídeo genérico).
# El nombre de un clip P2 (0016Q3) NO sirve para saber la tarjeta: en el disco real de México, clips
# grabados con segundos de diferencia se llaman 00016R, 0002CI, 000328.
MARCAS_TARJETA = {"CONTENTS", "PRIVATE", "DCIM", "XDROOT", "BPAV", "AVCHD", "M4ROOT", "CLIPR"}


def carpeta_tarjeta(ruta):
    """Raíz de la tarjeta según la ruta del fichero importado que guarda el bin: la carpeta que
    contiene CONTENTS, PRIVATE, DCIM... ('E:' si se importó con la tarjeta montada). None si no consta."""
    partes = [x for x in re.split(r"[\\/]+", str(ruta or "")) if x]
    for i, x in enumerate(partes):
        if i and x.upper() in MARCAS_TARJETA:
            return "/".join(partes[:i])
    return None


def fecha_media(c, mxf_por_ruta):
    """Fecha del fichero de media (la de vídeo si hay): cuándo se capturó o importó en Avid."""
    for r in [c.get("fichero_video")] + list(c.get("ficheros") or []):
        texto = (mxf_por_ruta.get(r) or {}).get("modificado") if r else None
        if texto:
            try:
                return dt.datetime.strptime(texto, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                pass
    return None


def _juntar_pequenas(tandas, minimo_seg, max_seg=24 * 3600):
    """Junta cada tanda con menos de minimo_seg de material con la tanda vecina más cercana en el
    tiempo, si está a menos de max_seg. tandas: listas de (fecha, clip) ordenadas por fecha."""
    duracion = lambda t: sum(c.get("duracion") or 0 for _, c in t)
    tandas = [list(t) for t in tandas]
    i = 0
    while minimo_seg and len(tandas) > 1 and i < len(tandas):
        if duracion(tandas[i]) >= minimo_seg:
            i += 1
            continue
        vecinas = []
        if i > 0 and tandas[i][0][0] and tandas[i - 1][-1][0]:
            vecinas.append(((tandas[i][0][0] - tandas[i - 1][-1][0]).total_seconds(), i - 1))
        if i + 1 < len(tandas) and tandas[i + 1][0][0] and tandas[i][-1][0]:
            vecinas.append(((tandas[i + 1][0][0] - tandas[i][-1][0]).total_seconds(), i + 1))
        vecinas = [v for v in vecinas if v[0] <= max_seg]
        if not vecinas:
            i += 1
            continue
        a, b = sorted((i, min(vecinas)[1]))
        tandas[a] += tandas.pop(b)
        i = 0
    return tandas


def partir_por_tarjeta(cs, mxf_por_ruta, cfg):
    """Agrupa los clips por tarjeta. Lo que hay en el disco real: los clips P2 no guardan en el bin la
    ruta de la tarjeta y su nombre no la indica, así que se usa la fecha en que Avid escribió la media
    (cada volcado de tarjeta es una tanda): un salto de más de tarjeta_hueco_horas abre otra tarjeta, y
    las tandas de menos de tarjeta_minimo_minutos se juntan con la más cercana. Si el bin sí guarda la
    carpeta de la tarjeta (CONTENTS, PRIVATE...), se separa también por ella.
    Devuelve [(clips, info)] por fecha; info: carpeta, desde, hasta."""
    hueco = dt.timedelta(hours=float(cfg.get("tarjeta_hueco_horas", 2)))
    minimo = 60 * float(cfg.get("tarjeta_minimo_minutos", 5))
    por_carpeta = defaultdict(list)
    for c in cs:
        por_carpeta[carpeta_tarjeta(c.get("fichero_origen"))].append(c)
    salida = []
    for carpeta, grupo in por_carpeta.items():
        fechados = sorted(((fecha_media(c, mxf_por_ruta), c) for c in grupo), key=lambda t: (t[0] or dt.datetime.min, t[1]["nombre"]))
        tandas, actual = [], []
        for f, c in fechados:
            previa = actual[-1][0] if actual else None
            if actual and f and previa and f - previa > hueco:
                tandas.append(actual)
                actual = []
            actual.append((f, c))
        tandas.append(actual)
        for t in _juntar_pequenas(tandas, minimo):
            fechas = [f for f, _ in t if f]
            salida.append(([c for _, c in t], {"carpeta": carpeta, "desde": min(fechas) if fechas else None,
                                               "hasta": max(fechas) if fechas else None}))
    salida.sort(key=lambda t: (t[1]["desde"] or dt.datetime.max, t[1]["carpeta"] or ""))
    return salida


def _normal(texto):
    """Nombre comparable: sin extensión, sin .new.01 ni 'mixdown' al final, minúsculas y espacios colapsados."""
    t = re.sub(r"\.(mxf|mov|mp4|m4v|avi|mts|wav|aif+)$", "", str(texto or "").strip(), flags=re.IGNORECASE)
    t = re.sub(r"(\.new)?(\.\d+)?$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"[\s._-]*(video\s*|audio\s*)?mixdown$", "", t, flags=re.IGNORECASE)
    return " ".join(t.lower().split())


def es_pieza(c, secuencias, rx=None):
    """Pieza renderizada: el clip (o el fichero importado) se llama como una secuencia del proyecto."""
    if rx and rx.search(c.get("nombre") or ""):
        return True
    fichero = str(c.get("fichero_origen") or "").replace("\\", "/").rsplit("/", 1)[-1]
    return bool(secuencias) and bool({_normal(c.get("nombre")), _normal(fichero)} & secuencias - {""})


def grupos_de_exportacion(clips, cruce, mxf_por_ruta, cfg):
    """Reparte los clips en brutos. Devuelve [{nombre, base, proyecto, tipo, contenido, clips}]."""
    politica = cfg.get("clips_compartidos", "primero")
    huerfanos = cfg.get("incluir_huerfanos", True)
    por_proyecto = defaultdict(lambda: {"normales": [], "renders": []})
    for c in clips:
        render = bool(c.get("render"))
        if render and not cfg.get("renders_en_piezas", True):
            continue
        proys = sorted(c.get("proyectos_render" if render else "proyectos") or [])
        if politica == "primero" and proys:
            proys = proys[:1]
        if not proys:
            if not huerfanos:
                continue
            proys = ["SIN_PROYECTO"]
        for p in proys:
            por_proyecto[p]["renders" if render else "normales"].append(c)

    secuencias = {p["proyecto"]: {_normal(n) for n in p.get("secuencias_nombres", [])} for p in (cruce or {}).get("por_proyecto", [])}
    rx = re.compile(cfg["piezas_regex"]) if cfg.get("piezas_regex") else None
    prefijos = tuple(cfg.get("origenes_agencia") or ())
    grupos = []
    for proyecto in sorted(por_proyecto):
        normales, renders = por_proyecto[proyecto]["normales"], por_proyecto[proyecto]["renders"]
        base = nombre_seguro(proyecto, 50)
        piezas = [c for c in normales if cfg.get("piezas_aparte", True) and es_pieza(c, secuencias.get(proyecto, set()), rx)]
        resto = [c for c in normales if c not in piezas]
        agencias = [c for c in resto if cfg.get("agencias_aparte", True) and prefijos and str(c.get("origen") or "").startswith(prefijos)]
        resto = [c for c in resto if c not in agencias]
        importados = [c for c in resto if cfg.get("importados_aparte", True) and c.get("fichero_origen")
                      and not carpeta_tarjeta(c["fichero_origen"])]
        resto = [c for c in resto if c not in importados]
        if resto and cfg.get("partir_por_tarjeta", True):
            tarjetas = partir_por_tarjeta(resto, mxf_por_ruta, cfg)
            for n, (cs, info) in enumerate(tarjetas, 1):
                fecha = info["desde"].strftime("%Y-%m-%d") if info["desde"] else ""
                sufijo = "_T%02d" % n + ("_" + fecha if fecha else "")
                partes = ["tarjeta %d de %d" % (n, len(tarjetas))]
                if info["desde"]:
                    partes.append(info["desde"].strftime("%d/%m/%Y %H:%M") + (" – " + info["hasta"].strftime("%d/%m/%Y %H:%M") if info["hasta"] != info["desde"] else ""))
                partes.append("carpeta " + info["carpeta"].rsplit("/", 1)[-1] if info["carpeta"] else "volcado")
                grupos.append({"nombre": proyecto + sufijo, "base": base + sufijo, "proyecto": proyecto, "tipo": "tarjeta",
                               "contenido": " · ".join(partes), "clips": cs})
        elif resto:
            grupos.append({"nombre": proyecto, "base": base, "proyecto": proyecto, "tipo": "proyecto", "contenido": "proyecto entero", "clips": resto})
        if agencias:
            cuenta = Counter(c.get("origen") for c in agencias)
            grupos.append({"nombre": proyecto + "_AGENCIAS", "base": base + "_AGENCIAS", "proyecto": proyecto, "tipo": "agencias",
                           "contenido": "agencias: " + ", ".join("%s %d" % kv for kv in cuenta.most_common()), "clips": agencias})
        if importados:
            carpetas = Counter(str(c["fichero_origen"]).replace("\\", "/").rsplit("/", 2)[-2] if "/" in str(c["fichero_origen"]).replace("\\", "/") else "?"
                               for c in importados)
            grupos.append({"nombre": proyecto + "_IMPORTADOS", "base": base + "_IMPORTADOS", "proyecto": proyecto, "tipo": "importados",
                           "contenido": "ficheros importados %d (carpetas: %s)" % (len(importados), ", ".join("%s %d" % kv for kv in carpetas.most_common(4))),
                           "clips": importados})
        if piezas or renders:
            grupos.append({"nombre": proyecto + "_PIEZAS", "base": base + "_PIEZAS", "proyecto": proyecto, "tipo": "piezas",
                           "contenido": "piezas renderizadas %d, renders de efectos %d" % (len(piezas), len(renders)),
                           "clips": piezas + renders})
    return grupos


def firma_clips(cs):
    """Identifica el conjunto de clips de un bruto: si cambia, el bruto no se reanuda, se rehace."""
    return hashlib.sha256("\n".join(sorted(str(c.get("clave")) for c in cs)).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Plan por proyecto
# ---------------------------------------------------------------------------

def plan_proyecto(clips, mxf_por_ruta, cfg, modo_forzado=None):
    """Decide copia o conversión y el número de pistas de audio. modo_forzado lo usa el reintento en convertir."""
    con_video = [c for c in clips if c.get("fichero_video")]
    firmas = Counter()
    for c in con_video:
        m = mxf_por_ruta.get(c["fichero_video"], {})
        pv = next((p for p in m.get("pistas", []) if p["tipo"] == "video"), {})
        firmas[(pv.get("codec"), pv.get("ancho"), pv.get("alto"), pv.get("fps"), pv.get("pix_fmt"))] += 1

    uniforme = len(firmas) == 1
    codec = next(iter(firmas))[0] if firmas else None
    modo = cfg.get("modo", "auto")
    if modo == "auto":
        modo = "copia" if (uniforme and codec in CODECS_COPIABLES) else "convertir"
    elif modo == "copia" and not (uniforme and codec in CODECS_COPIABLES):
        LOG.warning("Se pidió copia pero los clips no son uniformes o el códec no es copiable; se convierte.")
        modo = "convertir"
    if modo_forzado:
        modo = modo_forzado

    pa = cfg.get("pistas_audio", "max")
    if pa == "max":
        pistas = min(MAX_PISTAS, max((c.get("num_audio", 0) for c in clips), default=0))
    else:
        pistas = max(0, min(MAX_PISTAS, int(pa)))

    mayoritaria = firmas.most_common(1)[0][0] if firmas else (None, 1920, 1080, "25/1", None)
    cv = cfg["convertir"]
    destino = {
        "ancho": int(cv["ancho"]) if str(cv.get("ancho", "auto")) != "auto" else int(mayoritaria[1] or 1920),
        "alto": int(cv["alto"]) if str(cv.get("alto", "auto")) != "auto" else int(mayoritaria[2] or 1080),
        "fps_txt": str(cv["fps"]) if str(cv.get("fps", "auto")) != "auto" else str(mayoritaria[3] or "25"),
    }
    if modo == "copia":
        fps = fps_num(mayoritaria[3]) if firmas else 25.0
    else:
        fps = fps_num(destino["fps_txt"])
    return {"modo": modo, "pistas_audio": pistas, "fps": fps, "destino": destino,
            "firmas": [{"codec": k[0], "resolucion": "%sx%s" % (k[1], k[2]), "fps": k[3], "clips": v} for k, v in firmas.items()],
            "clips_video": len(con_video), "clips_solo_audio": len(clips) - len(con_video)}


def args_video(plan, mxf_video, cfg):
    if plan["modo"] == "copia":
        return ["-c:v", "copy"]
    cv = cfg["convertir"]
    d = plan["destino"]
    pv = next((p for p in mxf_video.get("pistas", []) if p["tipo"] == "video"), {})
    filtros = []
    des = cv.get("desentrelazar", "auto")
    entrelazado = (pv.get("field_order") or "progressive") not in ("progressive", "unknown", None)
    reescala = (pv.get("ancho"), pv.get("alto")) != (d["ancho"], d["alto"])
    if des == "si" or (des == "auto" and entrelazado and reescala):
        filtros.append("bwdif=mode=0")
    filtros += ["scale=%d:%d:flags=lanczos" % (d["ancho"], d["alto"]), "fps=%s" % d["fps_txt"], "setsar=1"]
    args = ["-vf", ",".join(filtros)]
    if cv["codec"] == "mpeg2video":
        br = str(cv["bitrate"])
        args += ["-c:v", "mpeg2video", "-pix_fmt", "yuv422p", "-profile:v", "0", "-level:v", "2",
                 "-b:v", br, "-minrate", br, "-maxrate", br, "-bufsize", "17825792", "-g", "12", "-bf", "2"]
    elif cv["codec"] == "dnxhd":
        args += ["-c:v", "dnxhd", "-pix_fmt", "yuv422p", "-b:v", str(cv["bitrate"])]
    else:
        args += ["-c:v", cv["codec"], "-pix_fmt", "yuv422p", "-b:v", str(cv["bitrate"])]
    return args


# ---------------------------------------------------------------------------
# Segmentos y unión
# ---------------------------------------------------------------------------

def hacer_segmento(clip, idx, plan, origen, mxf_por_ruta, tmp, ffmpeg, cfg, nombre=None):
    """Convierte un clip (V + sus audios) en un MXF OP1a intermedio homogéneo.
    Devuelve (ruta | None, error, comando)."""
    salida = tmp / (nombre or "seg_%05d.mxf" % idx)
    video = Path(origen) / clip["fichero_video"]
    audios = [Path(origen) / r for r in clip["ficheros"] if r != clip["fichero_video"]
              and any(p["tipo"] == "audio" for p in mxf_por_ruta.get(r, {}).get("pistas", []))]
    audios = audios[:plan["pistas_audio"]]
    n_silencio = plan["pistas_audio"] - len(audios)

    cmd = [ffmpeg, "-v", "error", "-y", "-i", str(video)]
    for a in audios:
        cmd += ["-i", str(a)]
    dur = clip.get("duracion")
    for _ in range(n_silencio):  # silencio de la duración del clip: con -shortest se recortaría el vídeo a la pista más corta
        cmd += (["-t", "%.3f" % dur] if dur else []) + ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono"]
    cmd += ["-map", "0:v:0"]
    for i in range(plan["pistas_audio"]):
        cmd += ["-map", "%d:a:0" % (i + 1)]
    cmd += args_video(plan, mxf_por_ruta.get(clip["fichero_video"], {}), cfg)
    if plan["pistas_audio"]:
        cmd += ["-c:a", "pcm_s24le", "-ar", "48000", "-ac", "1"]
    if n_silencio and not dur:
        cmd += ["-shortest"]
    cmd += ["-f", "mxf", str(salida)]
    ok, err = ejecutar_ffmpeg(cmd, timeout_segmento(clip.get("duracion")))
    if not ok:
        LOG.error("Segmento fallido %s (%s): %s", clip["nombre"], plan["modo"], err)
        return None, err, cmd
    return salida, None, cmd


def timecode_inicio(cfg, fps):
    """timecode_inicio del config con el separador que corresponde a los fps de salida (;ff en DF)."""
    texto = cfg.get("timecode_inicio", "00:00:00:00")
    try:
        return fmt_tc(tc_a_frames(texto, fps), fps)
    except ValueError:
        LOG.warning("timecode_inicio no válido en config_brutos.json (%r); se usa 00:00:00:00", texto)
        return fmt_tc(0, fps)


def unir_segmentos(segmentos, salida, ffmpeg, cfg, plan, duracion_total):
    """Devuelve (ok, error, comando)."""
    lista = salida.parent / (salida.stem + "_lista.txt")
    with open(lista, "w", encoding="utf-8") as f:
        for s in segmentos:  # rutas absolutas: el demuxer concat resuelve las relativas respecto a la lista
            f.write("file '%s'\n" % str(Path(s).resolve()).replace("'", "'\\''"))
    cmd = [ffmpeg, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lista), "-map", "0", "-c", "copy",
           "-timecode", timecode_inicio(cfg, plan["fps"]), "-f", "mxf", str(salida)]
    ok, err = ejecutar_ffmpeg(cmd, timeout_union(duracion_total))
    lista.unlink(missing_ok=True)
    return ok, err, cmd


# ---------------------------------------------------------------------------
# Shotlist y hoja de contactos
# ---------------------------------------------------------------------------

def escribir_shotlist(ruta, proyecto, filas):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
    except ImportError:
        import csv
        with open(ruta.with_suffix(".csv"), "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(filas[0].keys()) if filas else ["clip"], delimiter=";")
            w.writeheader()
            w.writerows(filas)
        return ruta.with_suffix(".csv")
    wb = Workbook()
    ws = wb.active
    ws.title = "Shotlist"
    cab = ["Nº", "Proyecto", "Clip", "TC inicio (bruto)", "TC fin (bruto)", "Duración", "TC original", "Fecha fichero",
           "Códec origen", "Resolución origen", "Pistas audio", "Estado", "Ficheros origen", "Fichero importado (según bin)", "Disco de origen (según bin)", "Origen", "Referencia agencia", "Slug"]
    ws.append(cab)
    for c in ws[1]:
        c.font = Font(bold=True)
    for fila in filas:
        ws.append([fila.get(k) for k in ["n", "proyecto", "clip", "tc_in", "tc_out", "duracion", "tc_original", "fecha",
                                          "codec", "resolucion", "pistas", "estado", "ficheros", "fichero_origen", "volumenes", "origen", "ref_agencia", "slug"]])
    anchos = [5, 24, 34, 14, 14, 11, 14, 18, 12, 14, 8, 14, 60, 50, 24, 20, 18, 40]
    for i, a in enumerate(anchos):
        ws.column_dimensions[chr(65 + i)].width = a
    ws.freeze_panes = "A2"
    wb.save(ruta)
    return ruta


def escribir_contactos(ruta, proyecto, filas, miniaturas_dir):
    def esc(t):
        return (str(t) if t is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    h = ["<!doctype html><html lang='es'><head><meta charset='utf-8'><title>Contactos %s</title><style>" % esc(proyecto),
         "body{font:14px system-ui,sans-serif;margin:24px;color:#1d2330}h1{font-size:20px}p{color:#6b7280}",
         ".g{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px}",
         ".c{border:1px solid #e3e6eb;border-radius:6px;overflow:hidden;background:#fff}.c img{width:100%;aspect-ratio:16/9;object-fit:cover;display:block;background:#222}",
         ".c .s{aspect-ratio:16/9;background:#2a2f3a;color:#9aa3b2;display:flex;align-items:center;justify-content:center;font-size:12px}",
         ".c div.t{padding:6px 8px;font-size:12px}.c b{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.c span{color:#6b7280}",
         "</style></head><body><h1>Bruto %s</h1><p>%d clips · hoja de contactos generada el %s</p><div class='g'>"
         % (esc(proyecto), len(filas), dt.datetime.now().strftime("%Y-%m-%d %H:%M"))]
    for f in filas:
        mini = f.get("miniatura")
        if mini and miniaturas_dir and (Path(miniaturas_dir) / Path(mini).name).exists():
            img = "<img loading='lazy' src='miniaturas/%s' alt=''>" % esc(Path(mini).name)
        else:
            img = "<div class='s'>%s</div>" % ("sin miniatura" if str(f.get("estado", "")).startswith("incluido") else esc(f.get("estado")))
        h.append("<div class='c'>%s<div class='t'><b title='%s'>%d · %s</b><span>%s → %s · %s</span></div></div>"
                 % (img, esc(f["clip"]), f["n"], esc(f["clip"]), esc(f.get("tc_in") or "—"), esc(f.get("tc_out") or "—"), esc(f.get("duracion") or "")))
    h.append("</div></body></html>")
    ruta.write_text("\n".join(h), encoding="utf-8")


# ---------------------------------------------------------------------------
# Exportación completa
# ---------------------------------------------------------------------------

def _fila_clip(i, proyecto, c, mxf_por_ruta):
    return {"n": i, "proyecto": proyecto, "clip": c["nombre"], "tc_original": c.get("timecode"),
            "fecha": mxf_por_ruta.get(c.get("fichero_video") or c["ficheros"][0], {}).get("modificado"),
            "codec": c.get("codec_video"), "resolucion": c.get("resolucion"), "pistas": c.get("num_audio"),
            "ficheros": "; ".join(c["ficheros"]), "miniatura": c.get("miniatura"), "tc_in": None, "tc_out": None, "duracion": None,
            "fichero_origen": c.get("fichero_origen"), "volumenes": ", ".join(c.get("volumenes") or []),
            "origen": c.get("origen"), "ref_agencia": (c.get("origen_detalle") or {}).get("id"), "slug": (c.get("origen_detalle") or {}).get("slug")}


def exportar_proyecto(proyecto, cs, mxf_por_ruta, origen, destino, ffmpeg, ffprobe, progreso, cfg, miniaturas_dir=None,
                      etiqueta="", modo_forzado=None, timeouts=None, motivo_reintento=None, base_fichero=None):
    """Exporta el bruto de un proyecto. Devuelve el resultado (dict) para brutos_resultado.json.

    En modo copia, si un segmento o la unión fallan por algo que no sea tiempo agotado, se repite
    el proyecto entero una sola vez en modo convertir (modo_forzado). Para no convertir todo por
    un fichero dañado, ante un segmento fallido se prueba antes ese mismo clip en convertir: si
    también falla, el problema es el fichero y el proyecto sigue en copia sin ese clip.
    timeouts: claves de clips que ya agotaron el tiempo (no se reintentan).
    proyecto es el nombre del bruto (p. ej. "MEXICO_T02_2021-09-15"); base_fichero, el nombre de
    fichero sin prefijo (por defecto, el nombre del bruto saneado)."""
    base_fichero = base_fichero or nombre_seguro(proyecto)
    destino = Path(destino)
    timeouts = set(timeouts or ())
    cs = sorted(cs, key=lambda c: (c["nombre"], c.get("timecode") or ""))
    plan = plan_proyecto(cs, mxf_por_ruta, cfg, modo_forzado)
    pref = cfg.get("prefijo_fichero", "BRUTO_")
    res = {"proyecto": proyecto, "modo": plan["modo"], "reintento": modo_forzado is not None, "pistas_audio": plan["pistas_audio"],
           "clips": len(cs), "incluidos": 0, "fallidos": 0, "solo_audio": plan["clips_solo_audio"], "fichero": None, "bytes": 0,
           "segundos": 0.0, "estado": "", "shotlist": None, "contactos": None, "errores": [], "firma": firma_clips(cs)}
    if motivo_reintento:
        res["motivo_reintento"] = motivo_reintento
    progreso("Bruto %s: %s (%d clips, modo %s%s)" % (etiqueta, proyecto, len(cs), plan["modo"], ", reintento" if modo_forzado else ""))

    # Espacio: estimación conservadora
    bytes_origen = sum(c["bytes"] for c in cs)
    segundos_est = sum(c.get("duracion") or 0 for c in cs)
    if plan["modo"] == "copia":
        necesario = bytes_origen * 2.2  # intermedios + final
    else:
        necesario = segundos_est * 7e6 * 2.2  # ~50 Mb/s + audio
    libre = shutil.disk_usage(destino).free
    if necesario > libre:
        res["estado"] = "SIN ESPACIO: hacen falta ~%.1f GB y hay %.1f GB libres" % (necesario / 1e9, libre / 1e9)
        LOG.error("%s: %s", proyecto, res["estado"])
        return res

    def reintentar(motivo):
        LOG.warning("%s: falla en modo copia (%s); se repite el proyecto convirtiendo", proyecto, motivo)
        progreso("  %s: no se puede copiar sin recomprimir; se repite convirtiendo" % proyecto)
        shutil.rmtree(tmp, ignore_errors=True)
        return exportar_proyecto(proyecto, cs, mxf_por_ruta, origen, destino, ffmpeg, ffprobe, progreso, cfg, miniaturas_dir,
                                 etiqueta, modo_forzado="convertir", timeouts=timeouts, motivo_reintento=motivo, base_fichero=base_fichero)

    puede_reintentar = plan["modo"] == "copia" and modo_forzado is None
    tmp = destino / ("_tmp_" + base_fichero)
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    salida = destino / ("%s%s.mxf" % (pref, base_fichero))
    uniendo = False
    try:
        segmentos, filas = [], []
        frames = 0  # se acumulan frames, no segundos, para no arrastrar redondeos en el shotlist
        inicio = tc_a_frames(timecode_inicio(cfg, plan["fps"]), plan["fps"])
        for i, c in enumerate(cs, 1):
            fila = _fila_clip(i, proyecto, c, mxf_por_ruta)
            if not c.get("fichero_video"):
                fila["estado"] = "solo audio, no incluido"
                filas.append(fila)
                continue
            if c.get("clave") in timeouts:
                fila["estado"] = "ERROR (tiempo agotado)"
                res["fallidos"] += 1
                filas.append(fila)
                continue
            progreso("  %s: clip %d/%d  %s" % (proyecto, i, len(cs), c["nombre"]))
            seg, err, cmd = hacer_segmento(c, i, plan, origen, mxf_por_ruta, tmp, ffmpeg, cfg)
            if not seg:
                if err == TIEMPO_AGOTADO:
                    timeouts.add(c.get("clave"))
                    fila["estado"] = "ERROR (tiempo agotado)"
                else:
                    if puede_reintentar:
                        prueba, _, _ = hacer_segmento(c, i, dict(plan, modo="convertir"), origen, mxf_por_ruta, tmp, ffmpeg, cfg,
                                                      nombre="prueba_%05d.mxf" % i)
                        if prueba:  # convirtiendo sí sale: el problema es el códec, no el fichero
                            return reintentar("segmento %s: %s" % (c["nombre"], (err or "")[-200:]))
                    fila["estado"] = "ERROR al convertir"
                res["errores"].append(dict(detalle_error(c["nombre"], "segmento", cmd, err), estado=fila["estado"]))
                res["fallidos"] += 1
                filas.append(fila)
                continue
            dur = duracion_ffprobe(ffprobe, seg) or (c.get("duracion") or 0)
            n = int(round(dur * plan["fps"]))
            fila.update({"estado": "incluido (cuarentena)" if c.get("en_cuarentena") else "incluido",
                         "tc_in": fmt_tc(inicio + frames, plan["fps"]), "tc_out": fmt_tc(inicio + frames + n, plan["fps"]),
                         "duracion": fmt_tc(n, plan["fps"])})
            frames += n
            segmentos.append(seg)
            res["incluidos"] += 1
            filas.append(fila)

        acumulado = frames / plan["fps"]
        if segmentos:
            progreso("  %s: uniendo %d clips..." % (proyecto, len(segmentos)))
            uniendo = True
            ok, err, cmd = unir_segmentos(segmentos, salida, ffmpeg, cfg, plan, acumulado)
            if not ok and puede_reintentar and err != TIEMPO_AGOTADO:
                return reintentar("unión: %s" % (err or "")[-200:])
            if ok and salida.exists():
                dur_final = duracion_ffprobe(ffprobe, salida)
                res.update({"fichero": salida.name, "bytes": salida.stat().st_size, "segundos": round(dur_final or 0, 2)})
                if dur_final is None:
                    res["estado"] = "ERROR: el bruto no se puede leer"
                elif abs(dur_final - acumulado) > 1.0:
                    res["estado"] = "AVISO: duración %.1fs, esperada %.1fs" % (dur_final, acumulado)
                else:
                    res["estado"] = "OK"
                if cfg.get("verificar_decodificando") and res["estado"] == "OK":
                    progreso("  %s: verificando decodificación completa..." % proyecto)
                    cmd2 = [ffmpeg, "-v", "error", "-i", str(salida), "-f", "null", "-"]
                    ok2, err2 = ejecutar_ffmpeg(cmd2, timeout_union(acumulado))
                    res["estado"] = "OK (decodificación verificada)" if ok2 else "ERROR al decodificar: " + err2[-200:]
                    if not ok2:
                        res["errores"].append(detalle_error(None, "verificación", cmd2, err2))
            else:
                res["estado"] = "ERROR al unir (tiempo agotado)" if err == TIEMPO_AGOTADO else "ERROR al unir: " + (err or "")[-200:]
                res["errores"].append(detalle_error(None, "unión", cmd, err))
                LOG.error("%s: %s", proyecto, res["estado"])
        else:
            res["estado"] = "sin clips de vídeo exportables"
    except Detenido:  # el proyecto a medias se descarta: al relanzar se rehace entero
        shutil.rmtree(tmp, ignore_errors=True)
        if uniendo:
            salida.unlink(missing_ok=True)
        raise

    base = destino / ("%s%s" % (pref, base_fichero))
    try:
        res["shotlist"] = escribir_shotlist(base.with_name(base.name + "_shotlist.xlsx"), proyecto, filas).name
    except Exception as e:
        LOG.error("Shotlist %s: %s", proyecto, e)
    try:
        if miniaturas_dir:
            (destino / "miniaturas").mkdir(exist_ok=True)
            for f in filas:
                if f.get("miniatura"):
                    src = Path(miniaturas_dir) / Path(f["miniatura"]).name
                    if src.exists():
                        shutil.copy2(src, destino / "miniaturas" / src.name)
        escribir_contactos(base.with_name(base.name + "_contactos.html"), proyecto, filas, destino / "miniaturas")
        res["contactos"] = base.name + "_contactos.html"
    except Exception as e:
        LOG.error("Contactos %s: %s", proyecto, e)
    shutil.rmtree(tmp, ignore_errors=True)
    LOG.info("Bruto %s: %s (%d incluidos, %d fallidos, %.1f s, modo %s%s)", proyecto, res["estado"], res["incluidos"], res["fallidos"],
             res["segundos"], res["modo"], ", reintento" if res["reintento"] else "")
    return res


def ya_exportado(previo, n_clips, destino, firma=None):
    """Un bruto de una ejecución anterior se salta si terminó bien, sus ficheros siguen ahí con el
    mismo tamaño y tiene los mismos clips (si cambió el análisis o el config, se rehace)."""
    if not previo or not (str(previo.get("estado", "")).startswith(("OK", "AVISO"))):
        return False
    if previo.get("clips") != n_clips or not previo.get("fichero") or not previo.get("shotlist"):
        return False
    if firma and previo.get("firma") and previo["firma"] != firma:
        return False
    mxf = Path(destino) / previo["fichero"]
    return mxf.exists() and mxf.stat().st_size == previo.get("bytes") and (Path(destino) / previo["shotlist"]).exists()


def exportar_todo(clips, cruce, mxf, origen, destino, ffmpeg, ffprobe, progreso, cfg, miniaturas_dir=None):
    """Devuelve una lista con el resultado por proyecto (para el informe). brutos_resultado.json se
    reescribe tras cada proyecto, y al relanzar sobre la misma carpeta se saltan los ya terminados.
    Si se pulsa Detener, devuelve los proyectos terminados hasta ese momento (PARAR queda activado)."""
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    mxf_por_ruta = {m["ruta"]: m for m in mxf}
    ruta_json = destino / "brutos_resultado.json"

    previos = {}
    if ruta_json.exists():
        try:
            previos = {r["proyecto"]: r for r in json.loads(ruta_json.read_text(encoding="utf-8"))}
        except Exception as e:
            LOG.warning("brutos_resultado.json anterior ilegible (%s); se exporta todo", e)

    grupos = grupos_de_exportacion(clips, cruce, mxf_por_ruta, cfg)

    resultados = []
    LOG.info("Brutos previstos: %d (%s)", len(grupos), "; ".join("%s: %d clips" % (g["nombre"], len(g["clips"])) for g in grupos))
    for n_proy, g in enumerate(grupos, 1):
        proyecto, cs = g["nombre"], g["clips"]
        etiqueta = "%d/%d" % (n_proy, len(grupos))
        try:
            if PARAR.is_set():
                raise Detenido()
            if ya_exportado(previos.get(proyecto), len(cs), destino, firma_clips(cs)):
                res = dict(previos[proyecto], reanudado=True)
                progreso("Bruto %s: %s ya estaba exportado, se salta (reanudado)" % (etiqueta, proyecto))
                LOG.info("Bruto %s: reanudado (ya exportado en una ejecución anterior)", proyecto)
            else:
                res = exportar_proyecto(proyecto, cs, mxf_por_ruta, origen, destino, ffmpeg, ffprobe, progreso, cfg, miniaturas_dir,
                                        etiqueta, base_fichero=g["base"])
                res["reanudado"] = False
            res.update({"proyecto_avid": g["proyecto"], "tipo": g["tipo"], "contenido": g["contenido"]})
        except Detenido:
            faltan = [x["nombre"] for x in grupos[n_proy - 1:]]
            progreso("Exportación detenida. Terminados: %d de %d brutos. Al relanzar con las mismas carpetas sigue por %s."
                     % (len(resultados), len(grupos), faltan[0]))
            LOG.warning("Exportación detenida por el usuario en %s; quedan %d proyectos", faltan[0], len(faltan))
            # los de una ejecución anterior a los que no se ha llegado se conservan para poder reanudarlos
            with open(ruta_json, "w", encoding="utf-8") as f:
                json.dump(resultados + [previos[p] for p in faltan if p in previos], f, ensure_ascii=False, indent=1)
            return resultados
        resultados.append(res)
        with open(ruta_json, "w", encoding="utf-8") as f:  # tras cada proyecto: un corte no pierde lo ya hecho
            json.dump(resultados, f, ensure_ascii=False, indent=1)
    return resultados
