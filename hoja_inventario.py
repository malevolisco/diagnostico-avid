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
    def existe(r):
        try:
            return r.exists()
        except OSError:  # sin permiso: como si no estuviera
            return False
    ruta = next((r for r in rutas if existe(r)), rutas[0])
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


# --- el programa en la nube: estado, órdenes y datos para revisar -------------------------------------------------
#
# Cada minuto, con la hoja conectada, el programa apunta en la pestaña "Programa" qué hace este PC (latido) y recoge las
# órdenes pendientes de la pestaña "Órdenes". Solo ejecuta órdenes de la lista cerrada ORDENES, y solo si en este PC se
# ha marcado "Aceptar órdenes desde la hoja". Al terminar un análisis o una exportación sube a Drive un zip SOLO DE
# TEXTO (zip_texto): nunca vídeo, miniaturas, capturas ni páginas que las enseñen.

ORDENES = ("Analizar", "Exportar", "Detener", "Marcar HECHO", "Sincronizar")
EXTENSIONES_TEXTO = (".json", ".csv", ".log", ".txt", ".xlsx")
MAX_FICHERO = 25 * 1024 * 1024  # un log enorme se recorta por el final
MAX_ZIP = 30 * 1024 * 1024


def nombre_pc():
    import socket
    return os.environ.get("COMPUTERNAME") or socket.gethostname() or "PC"


def latido(url, estado):
    """Apunta el estado de este PC en la pestaña Programa. Devuelve las órdenes pendientes."""
    return enviar(url, dict(estado, accion="latido"), tiempo=30).get("ordenes") or []


def marcar_orden(url, n, estado, detalle="", pc="", desde=None):
    """Cambia el estado de la orden n. desde: solo si sigue en ese estado (si otro PC la ha cogido, ErrorHoja)."""
    datos = {"accion": "orden", "n": n, "estado": estado, "detalle": detalle[:500], "pc": pc}
    if desde:
        datos["desde"] = desde
    return enviar(url, datos, tiempo=30)


def proyectos_de_orden(texto):
    """'ABORTO; CHAVELA VARGAS' → ['ABORTO', 'CHAVELA VARGAS'] (también con comas o saltos de línea)."""
    import re
    return [p.strip() for p in re.split(r"[;\n]|,(?=\s*[^\s])", texto or "") if p.strip()]


def casar_proyectos(pedidos, disponibles):
    """Los nombres pedidos, con el nombre exacto del disco (sin distinguir mayúsculas). Devuelve (encontrados, faltan)."""
    por_clave = {d.strip().lower(): d for d in disponibles}
    encontrados, faltan = [], []
    for p in pedidos:
        d = por_clave.get(p.strip().lower())
        (encontrados if d else faltan).append(d or p)
    return encontrados, faltan


def _final(ruta, maximo):
    with open(ruta, "rb") as f:
        f.seek(0, 2)
        tam = f.tell()
        f.seek(max(0, tam - maximo))
        return f.read()


def zip_texto(carpetas):
    """Zip en memoria con los ficheros de texto (EXTENSIONES_TEXTO) de primer nivel de esas carpetas: el análisis y la
    carpeta de brutos. Nada de subcarpetas (miniaturas, visor), ni vídeo, ni imágenes, ni .html (enseñan capturas).
    Devuelve (bytes, [nombres])."""
    import io
    import zipfile
    buf, nombres, total = io.BytesIO(), [], 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for etiqueta, carpeta in carpetas:
            if not carpeta or not Path(carpeta).is_dir():
                continue
            for f in sorted(Path(carpeta).iterdir()):
                if not f.is_file() or f.suffix.lower() not in EXTENSIONES_TEXTO:
                    continue
                try:
                    datos = _final(f, MAX_FICHERO) if f.suffix.lower() in (".log", ".txt") else f.read_bytes()
                except OSError:
                    continue
                if len(datos) > MAX_FICHERO or total + len(datos) > MAX_ZIP * 4:
                    continue  # un JSON enorme no cabe: se queda fuera (el resto sigue sirviendo)
                nombre = "%s/%s" % (etiqueta, f.name)
                z.writestr(nombre, datos)
                nombres.append(nombre)
                total += len(datos)
    return buf.getvalue(), nombres


def subir_datos(url, gran, disco, nombre, contenido):
    """Sube el zip de texto a la carpeta de datos de la hoja en Drive (DiagnosticoAvid datos/<gran>/<disco>/nombre)."""
    import base64
    if len(contenido) > MAX_ZIP:
        raise ErrorHoja("Los datos ocupan %d MB: demasiado para subirlos." % (len(contenido) // 2 ** 20))
    return enviar(url, {"accion": "datos", "gran_proyecto": gran, "disco": disco, "nombre": nombre,
                        "zip_base64": base64.b64encode(contenido).decode("ascii")}, tiempo=120)


# Datos que no se han podido subir (sin red, Google caído): se guardan junto al programa y se suben solos al volver la
# conexión, con el latido. El PC solo está encendido en horario de trabajo: lo de hoy no se puede perder.
CARPETA_PENDIENTES = "config_hoja_datos_pendientes"


def subir_o_guardar(url, base, gran, disco, nombre, contenido):
    """Sube los datos; si no se puede, los deja en la cola de pendientes (el mismo nombre sustituye al anterior)."""
    try:
        return subir_datos(url, gran, disco, nombre, contenido)
    except ErrorHoja as e:
        if len(contenido) > MAX_ZIP:
            raise
        carpeta = Path(base) / CARPETA_PENDIENTES
        carpeta.mkdir(exist_ok=True)
        clave = "".join(c if c.isalnum() else "_" for c in "%s_%s_%s" % (gran, disco, nombre))[:150]
        (carpeta / (clave + ".zip")).write_bytes(contenido)
        (carpeta / (clave + ".json")).write_text(json.dumps({"gran": gran, "disco": disco, "nombre": nombre}, ensure_ascii=False),
                                                 encoding="utf-8")
        raise ErrorHoja("%s (se sube solo cuando vuelva la conexión)" % e)


def subir_pendientes(url, base, maximo=2):
    """Sube los datos que quedaron pendientes (los más antiguos primero). Devuelve cuántos ha subido."""
    carpeta = Path(base) / CARPETA_PENDIENTES
    if not carpeta.is_dir():
        return 0
    hechos = 0
    for meta in sorted(carpeta.glob("*.json"), key=lambda f: f.stat().st_mtime)[:maximo]:
        z = meta.with_suffix(".zip")
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
            subir_datos(url, m["gran"], m["disco"], m["nombre"], z.read_bytes())
        except (OSError, ValueError, KeyError):
            meta.unlink(missing_ok=True)  # roto: no se puede subir nunca
            z.unlink(missing_ok=True)
            continue
        z.unlink(missing_ok=True)
        meta.unlink(missing_ok=True)
        hechos += 1
    return hechos
