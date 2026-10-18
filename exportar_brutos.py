# -*- coding: utf-8 -*-
"""
EXPORTACIÓN DE BRUTOS — Fase 2
==============================

A partir del análisis de la fase 1 (clips físicos ya cruzados con los bins),
genera por cada proyecto UN solo fichero MXF OP1a con todos sus clips
encadenados, más un shotlist Excel (timecode de cada clip dentro del bruto)
y una hoja de contactos HTML con las miniaturas.

Modo de trabajo por proyecto:
  - "copia":     todos los clips comparten códec, resolución, fps y formato
                 de píxel → se encadenan sin recomprimir (sin pérdida).
  - "convertir": hay mezcla de formatos o el códec no se puede copiar →
                 se convierte todo al formato de destino de config_brutos.json.

El audio se normaliza siempre a PCM 24 bits 48 kHz mono por pista (sin
pérdida respecto a 16 bits) y todos los clips de un bruto llevan el mismo
número de pistas: las que falten se rellenan con silencio.

Este módulo lo usa diagnostico_avid.py; no se ejecuta solo.
"""

import datetime as dt
import json
import logging
import os
import shutil
import subprocess
from collections import Counter
from pathlib import Path

LOG = logging.getLogger("diagnostico")

CONFIG_DEFECTO = {
    "_comentario": "Parámetros de exportación de brutos. Borra este fichero para volver a los valores por defecto.",
    "modo": "auto",
    "_modo": "auto = copia directa si todos los clips del proyecto son iguales, si no convierte | copia | convertir",
    "pistas_audio": "max",
    "_pistas_audio": "max = tantas como el clip que más tenga (tope 8) | un número fijo | 0 = sin audio",
    "timecode_inicio": "00:00:00:00",
    "incluir_huerfanos": True,
    "_incluir_huerfanos": "Los clips que no están en ningún bin van a un bruto llamado SIN_PROYECTO",
    "prefijo_fichero": "BRUTO_",
    "convertir": {
        "codec": "mpeg2video",
        "ancho": 1920,
        "alto": 1080,
        "fps": 25,
        "bitrate": "50M",
        "desentrelazar": "auto",
        "_nota": "Por defecto MPEG-2 4:2:2 50 Mb/s 1080p25 (tipo XDCAM HD422). desentrelazar: auto | si | no"
    },
    "verificar_decodificando": False,
    "_verificar_decodificando": "true = tras unir, decodifica el bruto entero para comprobarlo (lento, muy seguro)"
}

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


def fmt_tc(segundos, fps):
    frames_total = int(round(segundos * fps))
    f = frames_total % int(round(fps))
    s_total = frames_total // int(round(fps))
    h, r = divmod(s_total, 3600)
    m, s = divmod(r, 60)
    return "%02d:%02d:%02d:%02d" % (h, m, s, f)


def fps_num(texto):
    """'25/1' -> 25.0"""
    try:
        if "/" in str(texto):
            a, b = str(texto).split("/")
            return float(a) / float(b)
        return float(texto)
    except Exception:
        return 25.0


def ejecutar_ffmpeg(cmd, timeout=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, **flags_subproceso())
        return r.returncode == 0, (r.stderr or "").strip()[-600:]
    except subprocess.TimeoutExpired:
        return False, "tiempo agotado"
    except Exception as e:
        return False, str(e)


def duracion_ffprobe(ffprobe, ruta):
    cmd = [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(ruta)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, **flags_subproceso())
        return float(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Plan por proyecto
# ---------------------------------------------------------------------------

def plan_proyecto(clips, mxf_por_ruta, cfg):
    """Decide copia o conversión y el número de pistas de audio."""
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

    pa = cfg.get("pistas_audio", "max")
    if pa == "max":
        pistas = min(MAX_PISTAS, max((c.get("num_audio", 0) for c in clips), default=0))
    else:
        pistas = max(0, min(MAX_PISTAS, int(pa)))

    if modo == "copia":
        fps = fps_num(next(iter(firmas))[3]) if firmas else 25.0
    else:
        fps = float(cfg["convertir"].get("fps", 25))
    return {"modo": modo, "pistas_audio": pistas, "fps": fps, "firmas": [{"codec": k[0], "resolucion": "%sx%s" % (k[1], k[2]), "fps": k[3], "clips": v} for k, v in firmas.items()],
            "clips_video": len(con_video), "clips_solo_audio": len(clips) - len(con_video)}


def args_video(plan, mxf_video, cfg):
    if plan["modo"] == "copia":
        return ["-c:v", "copy"]
    cv = cfg["convertir"]
    pv = next((p for p in mxf_video.get("pistas", []) if p["tipo"] == "video"), {})
    filtros = []
    des = cv.get("desentrelazar", "auto")
    entrelazado = (pv.get("field_order") or "progressive") not in ("progressive", "unknown", None)
    if des == "si" or (des == "auto" and entrelazado):
        filtros.append("bwdif=mode=0")
    filtros += ["scale=%d:%d:flags=lanczos" % (cv["ancho"], cv["alto"]), "fps=%s" % cv["fps"], "setsar=1"]
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

def hacer_segmento(clip, idx, plan, origen, mxf_por_ruta, tmp, ffmpeg, cfg):
    """Convierte un clip (V + sus audios) en un MXF OP1a intermedio homogéneo."""
    salida = tmp / ("seg_%05d.mxf" % idx)
    video = Path(origen) / clip["fichero_video"]
    audios = [Path(origen) / r for r in clip["ficheros"] if r != clip["fichero_video"]
              and any(p["tipo"] == "audio" for p in mxf_por_ruta.get(r, {}).get("pistas", []))]
    audios = audios[:plan["pistas_audio"]]
    n_silencio = plan["pistas_audio"] - len(audios)

    cmd = [ffmpeg, "-v", "error", "-y", "-i", str(video)]
    for a in audios:
        cmd += ["-i", str(a)]
    for _ in range(n_silencio):
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono"]
    cmd += ["-map", "0:v:0"]
    for i in range(plan["pistas_audio"]):
        cmd += ["-map", "%d:a:0" % (i + 1)]
    cmd += args_video(plan, mxf_por_ruta.get(clip["fichero_video"], {}), cfg)
    if plan["pistas_audio"]:
        cmd += ["-c:a", "pcm_s24le", "-ar", "48000", "-ac", "1"]
    if n_silencio:
        cmd += ["-shortest"]
    cmd += ["-f", "mxf", str(salida)]
    ok, err = ejecutar_ffmpeg(cmd)
    if not ok:
        LOG.error("Segmento fallido %s: %s", clip["nombre"], err)
        return None
    return salida


def unir_segmentos(segmentos, salida, ffmpeg, cfg):
    lista = salida.parent / (salida.stem + "_lista.txt")
    with open(lista, "w", encoding="utf-8") as f:
        for s in segmentos:  # rutas absolutas: el demuxer concat resuelve las relativas respecto a la lista
            f.write("file '%s'\n" % str(Path(s).resolve()).replace("'", "'\\''"))
    cmd = [ffmpeg, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lista), "-map", "0", "-c", "copy",
           "-timecode", cfg.get("timecode_inicio", "00:00:00:00"), "-f", "mxf", str(salida)]
    ok, err = ejecutar_ffmpeg(cmd)
    lista.unlink(missing_ok=True)
    return ok, err


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
           "Códec origen", "Resolución origen", "Pistas audio", "Estado", "Ficheros origen"]
    ws.append(cab)
    for c in ws[1]:
        c.font = Font(bold=True)
    for fila in filas:
        ws.append([fila.get(k) for k in ["n", "proyecto", "clip", "tc_in", "tc_out", "duracion", "tc_original", "fecha",
                                          "codec", "resolucion", "pistas", "estado", "ficheros"]])
    anchos = [5, 24, 34, 14, 14, 11, 14, 18, 12, 14, 8, 14, 60]
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

def exportar_todo(clips, cruce, mxf, origen, destino, ffmpeg, ffprobe, progreso, cfg, miniaturas_dir=None):
    """Devuelve una lista con el resultado por proyecto (para el informe)."""
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    mxf_por_ruta = {m["ruta"]: m for m in mxf}
    pref = cfg.get("prefijo_fichero", "BRUTO_")

    grupos = {}
    for c in clips:
        for p in c.get("proyectos", []):
            grupos.setdefault(p, []).append(c)
    if cfg.get("incluir_huerfanos", True):
        huer = [c for c in clips if not c.get("proyectos")]
        if huer:
            grupos["SIN_PROYECTO"] = huer

    resultados = []
    for n_proy, (proyecto, cs) in enumerate(sorted(grupos.items()), 1):
        cs = sorted(cs, key=lambda c: (c["nombre"], c.get("timecode") or ""))
        plan = plan_proyecto(cs, mxf_por_ruta, cfg)
        res = {"proyecto": proyecto, "modo": plan["modo"], "pistas_audio": plan["pistas_audio"], "clips": len(cs),
               "incluidos": 0, "fallidos": 0, "solo_audio": plan["clips_solo_audio"], "fichero": None, "bytes": 0,
               "segundos": 0.0, "estado": "", "shotlist": None, "contactos": None}
        progreso("Bruto %d/%d: %s (%d clips, modo %s)" % (n_proy, len(grupos), proyecto, len(cs), plan["modo"]))

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
            resultados.append(res)
            continue

        tmp = destino / ("_tmp_" + nombre_seguro(proyecto, 30))
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        segmentos, filas = [], []
        acumulado = 0.0
        for i, c in enumerate(cs, 1):
            fila = {"n": i, "proyecto": proyecto, "clip": c["nombre"], "tc_original": c.get("timecode"),
                    "fecha": mxf_por_ruta.get(c.get("fichero_video") or c["ficheros"][0], {}).get("modificado"),
                    "codec": c.get("codec_video"), "resolucion": c.get("resolucion"), "pistas": c.get("num_audio"),
                    "ficheros": "; ".join(c["ficheros"]), "miniatura": c.get("miniatura"), "tc_in": None, "tc_out": None, "duracion": None}
            if not c.get("fichero_video"):
                fila["estado"] = "solo audio, no incluido"
                filas.append(fila)
                continue
            progreso("  %s: clip %d/%d  %s" % (proyecto, i, len(cs), c["nombre"]))
            seg = hacer_segmento(c, i, plan, origen, mxf_por_ruta, tmp, ffmpeg, cfg)
            if not seg:
                fila["estado"] = "ERROR al convertir"
                res["fallidos"] += 1
                filas.append(fila)
                continue
            dur = duracion_ffprobe(ffprobe, seg) or (c.get("duracion") or 0)
            fila.update({"estado": "incluido (cuarentena)" if c.get("en_cuarentena") else "incluido", "tc_in": fmt_tc(acumulado, plan["fps"]), "tc_out": fmt_tc(acumulado + dur, plan["fps"]),
                         "duracion": fmt_tc(dur, plan["fps"])})
            acumulado += dur
            segmentos.append(seg)
            res["incluidos"] += 1
            filas.append(fila)

        if segmentos:
            salida = destino / ("%s%s.mxf" % (pref, nombre_seguro(proyecto)))
            progreso("  %s: uniendo %d clips..." % (proyecto, len(segmentos)))
            ok, err = unir_segmentos(segmentos, salida, ffmpeg, cfg)
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
                    ok2, err2 = ejecutar_ffmpeg([ffmpeg, "-v", "error", "-i", str(salida), "-f", "null", "-"])
                    res["estado"] = "OK (decodificación verificada)" if ok2 else "ERROR al decodificar: " + err2[-200:]
            else:
                res["estado"] = "ERROR al unir: " + err[-200:]
                LOG.error("%s: %s", proyecto, res["estado"])
        else:
            res["estado"] = "sin clips de vídeo exportables"

        base = destino / ("%s%s" % (pref, nombre_seguro(proyecto)))
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
        LOG.info("Bruto %s: %s (%d incluidos, %d fallidos, %.1f s)", proyecto, res["estado"], res["incluidos"], res["fallidos"], res["segundos"])
        resultados.append(res)

    with open(destino / "brutos_resultado.json", "w", encoding="utf-8") as f:
        json.dump(resultados, f, ensure_ascii=False, indent=1)
    return resultados
