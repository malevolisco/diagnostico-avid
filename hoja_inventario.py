"""Hoja de inventario: el programa apunta en una hoja de Google lo que analiza y exporta, y lee lo que decidís vosotros.

La hoja lleva dentro un buzón (hoja_inventario.gs, publicado como aplicación web; ver INSTRUCCIONES.txt). Aquí:

- filas_inventario(): una fila por proyecto del disco con lo que sabe el programa (bins, último cambio, estado, clips de
  cámara, duración, propuesta y brutos exportados). Nada de imágenes ni vídeo: solo texto y cifras.
- actualizar(): las manda a la pestaña del gran proyecto (México, China…). Si no hay conexión, se guardan en
  config_hoja_pendiente.json y se mandan la próxima vez.
- leer(): lo que habéis decidido en la hoja ({proyecto: {estado, decision, notas, quien}}): el selector de proyectos no
  propone los descartados, enseña los HECHOS en verde y los que merecen la pena.

Nunca para el programa: un fallo de la hoja solo se apunta en el registro.
"""

import datetime as dt
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

FICHERO_CONFIG = "config_hoja.json"
FICHERO_PENDIENTE = "config_hoja_pendiente.json"  # config_*: el actualizador lo conserva
TIEMPO = 40  # segundos: Google tarda en arrancar el buzón la primera vez

TRIAJE = {"restaurar": "Restaurar", "poco": "Poco material", "revisar": "Revisar", "sin": "Sin material de cámara"}


class ErrorHoja(Exception):
    pass


# --- configuración: la dirección del buzón y el gran proyecto de cada disco -------------------------------------------

def leer_config(base):
    try:
        datos = json.loads((Path(base) / FICHERO_CONFIG).read_text(encoding="utf-8"))
        return datos if isinstance(datos, dict) else {}
    except (OSError, ValueError):
        return {}


def guardar_config(base, cfg):
    (Path(base) / FICHERO_CONFIG).write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def gran_proyecto(cfg, disco_id):
    """El gran proyecto (la pestaña) de ese disco; uno nuevo, el último que se usó."""
    return (cfg.get("discos") or {}).get(disco_id) or cfg.get("ultimo") or ""


def poner_gran_proyecto(cfg, disco_id, gran):
    cfg = dict(cfg)
    cfg["discos"] = dict(cfg.get("discos") or {}, **{disco_id: gran})
    cfg["ultimo"] = gran
    return cfg


def comprobar_url(url):
    url = (url or "").strip()
    if not url.startswith("https://script.google.com/") and not url.startswith("https://script.googleusercontent.com/"):
        raise ErrorHoja("La dirección del buzón tiene que empezar por https://script.google.com/ (la de la aplicación web, "
                        "terminada en /exec).")
    return url


# --- las filas ----------------------------------------------------------------------------------------------------

def hms(segundos):
    s = int(round(segundos or 0))
    return "%d:%02d:%02d" % (s // 3600, s % 3600 // 60, s % 60)


def texto_propuesta(t):
    if not t:
        return ""
    texto = TRIAJE.get(t.get("veredicto"), t.get("veredicto") or "")
    extra = []
    agencia = (t.get("otros") or {}).get("agencia")
    if agencia:
        extra.append("%d de agencia" % agencia)
    if t.get("dudosos"):
        extra.append("%d dudosos" % t["dudosos"])
    if t.get("sin_audio"):
        extra.append("%d sin audio" % t["sin_audio"])
    return texto + (" (" + ", ".join(extra) + ")" if extra else "")


def entero(r):
    return bool(r) and str(r.get("estado", "")).startswith(("OK", "AVISO")) and not r.get("fallidos")


def texto_brutos(resultados):
    """Los brutos de un proyecto, como salen en la hoja: 'NOVIEMBRE DE 2002 · 78 clips · 0:50:00; …'."""
    partes = []
    for r in resultados:
        texto = "%s · %s clips · %s" % (r.get("proyecto", "?"), r.get("clips", "?"), hms(r.get("segundos")))
        if not entero(r):
            texto += " · INCOMPLETO" + (" (%d de %d clips)" % (r.get("incluidos") or 0, r["clips"]) if r.get("clips") else "")
        partes.append(texto)
    return "; ".join(partes)


def estado_de(analizado, resultados):
    if resultados:
        return "Exportado" if all(entero(r) for r in resultados) else "Exportar de nuevo"
    return "Analizado" if analizado else "Sin analizar"


def disco_de(carpeta, origenes, etiqueta=""):
    """El "Disco" de la hoja: la carpeta en la que está el proyecto, desde la raíz del disco ("DISCO 1"). Así sale lo
    mismo se elija el disco entero o la carpeta DISCO 1. Un proyecto en la raíz del disco: el nombre del disco.
    carpeta: la del proyecto, relativa a uno de los orígenes (el primero en el que exista)."""
    origenes = [o for o in origenes if o]
    if not origenes:
        return etiqueta
    rutas = [Path(os.path.abspath(str(o))) / carpeta for o in origenes]
    ruta = next((r for r in rutas if r.exists()), rutas[0])
    arriba = ruta.parent
    rel = str(arriba)[len(arriba.anchor):].strip("\\/")
    return rel.replace("/", "\\") if rel else (etiqueta or arriba.anchor.strip("\\/:") or "raíz")


def filas_inventario(lista, origenes, triaje=None, resultados=None, hechos=None, quitados_hecho=(), etiqueta="", ahora=None):
    """Las filas para la hoja. lista: los proyectos del disco (listar_proyectos); triaje: triaje.json del análisis;
    resultados: brutos_resultado.json (None si no hay carpeta de brutos: no se manda nada de brutos); hechos: los
    marcados HECHOS en el programa; quitados_hecho: los que se acaban de desmarcar (su estado se pone a la fuerza)."""
    triaje = {t["proyecto"]: t for t in (triaje or [])}
    por_proyecto = {}
    for r in resultados or []:
        por_proyecto.setdefault(r.get("proyecto_avid"), []).append(r)
    hechos = hechos or {}
    ahora = ahora or dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    filas = []
    for p in lista:
        nombre = p["nombre"]
        t = triaje.get(nombre)
        if p.get("sin_avid") and not t and nombre not in por_proyecto:
            continue  # carpetas sueltas sin proyecto de Avid: solo si tienen algo
        res = por_proyecto.get(nombre, [])
        f = {"disco": disco_de(p.get("carpeta") or "", origenes, etiqueta), "proyecto": nombre,
             "bins": "carpeta" if p.get("sin_avid") else p.get("bins"), "ultimo_cambio": (p.get("modificado") or "")[:10],
             "estado": estado_de(t is not None, res), "actualizado": ahora}
        if t:
            f.update({"clips": t.get("clips"), "duracion": hms(t.get("segundos")), "propuesta": texto_propuesta(t)})
        if resultados is not None and res:
            f["brutos"] = texto_brutos(res)
        if nombre in hechos:
            f.update(estado="HECHO", forzar_estado=True)
        elif nombre in quitados_hecho:
            f["forzar_estado"] = True
        filas.append(f)
    return filas


# --- el buzón ----------------------------------------------------------------------------------------------------

def enviar(url, datos, tiempo=TIEMPO):
    """POST con JSON al buzón. Google contesta con una redirección a la respuesta (urllib la sigue con GET)."""
    cuerpo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
    peticion = urllib.request.Request(comprobar_url(url), data=cuerpo, method="POST",
                                      headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": "DiagnosticoAvid"})
    try:
        with urllib.request.urlopen(peticion, timeout=tiempo) as r:
            texto = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise ErrorHoja("El buzón de la hoja ha contestado con un error %d. ¿Está publicado como aplicación web con acceso "
                        "para «Cualquier usuario»?" % e.code)
    except (urllib.error.URLError, OSError) as e:
        raise ErrorHoja("Sin conexión con la hoja: %s" % getattr(e, "reason", e))
    try:
        respuesta = json.loads(texto)
        if not isinstance(respuesta, dict):
            raise ValueError(texto[:80])
    except ValueError:
        raise ErrorHoja("La dirección no es la del buzón de la hoja (no contesta como él). Copia la de la aplicación web, "
                        "terminada en /exec, y que el acceso sea «Cualquier usuario».")
    if not respuesta.get("ok"):
        raise ErrorHoja("La hoja dice: %s" % respuesta.get("error", "error desconocido"))
    return respuesta


def probar(url):
    """Comprueba la conexión. Devuelve las pestañas (los grandes proyectos que ya hay)."""
    return enviar(url, {"accion": "probar"}).get("pestanas") or []


def _pendientes(base):
    try:
        datos = json.loads((Path(base) / FICHERO_PENDIENTE).read_text(encoding="utf-8"))
        return datos if isinstance(datos, dict) else {}
    except (OSError, ValueError):
        return {}


def _guardar_pendientes(base, pend):
    ruta = Path(base) / FICHERO_PENDIENTE
    if pend:
        ruta.write_text(json.dumps(pend, ensure_ascii=False, indent=1), encoding="utf-8")
    elif ruta.exists():
        ruta.unlink()


def actualizar(url, base, gran, filas):
    """Manda las filas a la pestaña gran (con las que quedaron pendientes de otra vez). Devuelve la respuesta del buzón.
    Si falla, las guarda en config_hoja_pendiente.json (la última de cada proyecto) y lanza ErrorHoja."""
    pend = _pendientes(base)
    for f in filas:  # las de ahora van detrás: son las que valen
        pend.setdefault(gran, {})["%s|%s" % (f.get("disco", ""), f["proyecto"])] = f
    respuestas = {}
    try:
        for g in list(pend):
            respuestas[g] = enviar(url, {"accion": "actualizar", "gran_proyecto": g, "filas": list(pend[g].values())})
            del pend[g]
    finally:
        _guardar_pendientes(base, pend)
    return respuestas.get(gran) or {}


def leer(url, gran):
    """{proyecto: {disco, estado, decision, notas, quien}} de la pestaña gran. {} si no hay pestaña aún."""
    filas = enviar(url, {"accion": "leer", "gran_proyecto": gran}).get("filas") or []
    return {f["proyecto"]: f for f in filas if f.get("proyecto")}


def descartado(d):
    return bool(d) and (str(d.get("decision", "")).startswith("Descartar") or d.get("estado") == "Descartado")


def texto_decision(d):
    """Lo que dice el selector de proyectos de lo decidido en la hoja."""
    if not d:
        return ""
    if descartado(d):
        texto = "descartado en la hoja" + (": " + d["decision"].split(":", 1)[1].strip() if ":" in d.get("decision", "") else "")
    elif d.get("estado") == "HECHO":
        texto = "HECHO en la hoja"
    elif d.get("decision") == "Merece la pena":
        texto = "★ merece la pena"
    elif d.get("decision") == "Dudas":
        texto = "dudas en la hoja"
    else:
        return ""
    return texto + (" (%s)" % d["quien"] if d.get("quien") else "")
