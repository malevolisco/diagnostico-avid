# -*- coding: utf-8 -*-
"""
EXPORTACIÓN DE BRUTOS — Fase 2
==============================

A partir del análisis de la fase 1 (clips físicos ya cruzados con los bins),
genera por cada proyecto UN solo fichero MXF OP1a con todos sus clips
encadenados, más un shotlist Excel (timecode de cada clip dentro del bruto)
y una hoja de contactos HTML con las miniaturas.

Reparto (config "agrupar"):
  - "dias" (por defecto): todo el disco, sin separar proyectos, ordenado por la
    fecha en que Avid escribió la media y cortado en bloques de días seguidos de
    hasta horas_por_bruto (3 h). Dentro de cada bloque los clips van agrupados por
    proyecto (cada proyecto seguido, por orden de fecha). Aparte, también por días:
    AGENCIAS y PIEZAS (piezas renderizadas). El shotlist dice el proyecto de cada clip.
  - "proyecto": cada proyecto por tarjetas (tandas de volcado), con _AGENCIAS,
    _IMPORTADOS y _PIEZAS aparte por proyecto.
Los renders de efectos (fundidos, picture-in-picture, rótulos) no entran.

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
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

LOG = logging.getLogger("diagnostico")

CONFIG_DEFECTO = {
    "_comentario": "Parámetros de exportación de brutos. Borra este fichero para volver a los valores por defecto.",
    "modo": "auto",
    "_modo": "auto = copia directa si todos los clips ya están en el formato de salida, si no convierte | copia | convertir",
    "formato_salida": "xdcam_hd50",
    "_formato_salida": "xdcam_hd50 = siempre XDCAM HD422 50 Mb/s, 1920x1080, 1080i50 (25 imágenes/s, 50 campos/s), 16:9 (lo pide el Archivo; el SD 4:3 va con bandas negras a los lados y el NTSC pasa a 25) | auto = formato mayoritario de cada bruto (ver convertir)",
    "pistas_audio": "max",
    "_pistas_audio": "max = tantas como el clip que más tenga (tope 8) | un número fijo | 0 = sin audio",
    "timecode_inicio": "00:00:00:00",
    "_timecode_inicio": "TC del primer frame del bruto. Escríbelo siempre hh:mm:ss:ff; si el bruto sale a 29,97 o 59,94 se usa drop-frame (hh:mm:ss;ff)",
    "incluir_huerfanos": True,
    "_incluir_huerfanos": "Los clips que no están en ningún bin van a brutos SIN_PROYECTO (también partidos por tarjeta)",
    "agrupar": "proyecto_mes",
    "_agrupar": "proyecto_mes = cada proyecto en orden cronológico, en brutos de hasta horas_por_bruto, con nombre PROYECTO_AAAA-MM | dias = todo el disco por días seguidos, sin separar proyectos | proyecto = cada proyecto por tarjetas",
    "horas_por_bruto": 2,
    "_horas_por_bruto": "Duración máxima de un bruto (proyecto_mes y dias). El clip que la pasaría no se añade: abre el bruto siguiente",
    "separar_si_pasan_dias": 180,
    "_separar_si_pasan_dias": "proyecto_mes: si entre dos clips seguidos del proyecto pasan más de estos días, el siguiente abre un bruto nuevo (material de otra época o de otro corresponsal en el mismo proyecto, p. ej. 2002 y 2019) | 0 = no se separa",
    "excluir_importados": True,
    "_excluir_importados": "true = fuera de los brutos lo que viene de fuera (agencias, descargas, móvil...), decidido por pistas: metadatos del fichero, carpeta de la que se importó, nombre del bin y del fichero",
    "solo_material_de_camara": True,
    "_solo_material_de_camara": "true = en los brutos de material solo entra lo que tiene alguna pista de cámara (nombre de clip P2, carpeta de tarjeta, metadatos o nombre de fichero de cámara) o se marcó 'Entra' en Origen del material; lo dudoso se queda fuera | false = lo dudoso entra marcado 'para revisar'",
    "dia_empieza_hora": 6,
    "_dia_empieza_hora": "agrupar=dias: lo volcado antes de esta hora cuenta como del día anterior (volcados de madrugada)",
    "partir_por_tarjeta": True,
    "_partir_por_tarjeta": "true = un bruto por tarjeta de cámara dentro de cada proyecto (_T01, _T02...) | false = un bruto por proyecto",
    "tarjeta_hueco_horas": 2,
    "_tarjeta_hueco_horas": "Un salto de más de estas horas entre clips (fecha en que se escribió la media en el disco de Avid) se considera otra tarjeta",
    "tarjeta_minimo_minutos": 5,
    "_tarjeta_minimo_minutos": "Una tanda con menos de estos minutos de material se junta con la tanda más cercana del mismo origen (si está a menos de 24 h). 0 = no juntar",
    "importados_aparte": True,
    "_importados_aparte": "Con excluir_importados = false, lo importado de fuera (descargas, móvil...) va al bruto _IMPORTADOS del proyecto. Lo dudoso va con el material",
    "camara_propia_regex": r"^(DJI_\d{3,5}|C\d{4}|MAH\d{5}|MVI_\d{4}|GH\d{6}|GX\d{6}|GOPR\d{4}|GP\d{6}|DSC_?\d{4}|P\d{7}|A\d{3}C\d{3}|\d{8}_C\d{3,4})(\D|$)",
    "_camara_propia_regex": "Ficheros de cámara propia importados desde una carpeta del PC (no desde la tarjeta): son material aunque el bin no guarde una ruta de tarjeta. Se mira el nombre del fichero que guarda el bin (y el del clip). Además de estos, el programa reconoce siempre los de cámaras con tarjeta SD y DJI Osmo (CAMARAS_SD_REGEX, AVCHD 00001.MTS); aquí se añaden otros. Por defecto: dron DJI, Sony (C0001), Canon (MVI_), GoPro, Panasonic (P1000123), Nikon (DSC_), Zoom (MAH), ARRI/Blackmagic (A001C002). Los del móvil (IMG_) siguen siendo importados",
    "bins_agencia_regex": r"agencia|reuters|\bafp\b|\befe\b|\bap\b|eurovisi|\bebu\b|xinhua|\bcctv\b|\bcgtn\b",
    "_bins_agencia_regex": "Bins cuyo nombre indica material de agencia (p. ej. '20260916 agencias.avb'): lo que hay en ellos y no es de cámara cuenta como agencia",
    "triaje_max_clips": 2,
    "triaje_max_minutos": 2,
    "_triaje": "Listado para decidir qué proyectos restaurar (proyectos.html del análisis): un proyecto con hasta triaje_max_clips clips de cámara y menos de triaje_max_minutos en total sale como 'Poco material: ¿descartar?'",
    "nombres_agencia_regex": r"(?<![a-z0-9])(reuters|rtr[a-z0-9]*|afp|efe|aptn|associated[ _-]?press|europa[ _-]?press|eurovisi[oó]n|evn|ebu|xinhua|cctv|cgtn)(?![a-z])",
    "_nombres_agencia_regex": "Indicativos de agencia en el nombre del clip (en el bin o en el MXF) o del fichero importado: Reuters (RTRMADV...), AFP, EFE... Si sale, el clip es de agencia aunque sus metadatos digan cámara (lo elegido a mano en 'Origen del material' sigue mandando). Vacío = no se mira",
    "reglas_origen": [],
    "_reglas_origen": "Decisiones de la pantalla 'Origen del material' marcadas 'Recordar para otros discos', p. ej. [{\"carpeta\": \"Compras\", \"clase\": \"agencia\"}]. carpeta: trozo de la ruta de la que se importó; bin: trozo del nombre del bin; clase: propio | agencia | importado",
    "aprendizaje_origen": "",
    "_aprendizaje_origen": "Dónde se guarda lo aprendido de las decisiones de 'Origen del material' (solo palabras y formas de nombre, nunca vídeo). Vacío = config_aprendizaje_origen.json junto al programa. Una carpeta compartida (p. ej. \\\\servidor\\archivo\\aprendizaje.json) sirve para aprender en equipo",
    "agencias_aparte": True,
    "origenes_agencia": ["Agencia"],
    "_origenes_agencia": "Clips cuyo origen (según config_origen.json) empieza por alguno de estos textos van al bruto _AGENCIAS del proyecto",
    "piezas_aparte": True,
    "piezas_regex": "",
    "_piezas_regex": "Van al bruto _PIEZAS los renders de efectos y los clips que se llaman como una secuencia del proyecto (pieza exportada o mixdown). Aquí se puede añadir una expresión regular sobre el nombre del clip, p. ej. (?i)mixdown|emitid",
    "exportar_piezas": False,
    "_exportar_piezas": "Los brutos de piezas renderizadas se reparten pero no se exportan (en la práctica se descartan). true = se exportan. En la ventana manda la casilla de cada bruto",
    "renders_en_piezas": False,
    "_renders_en_piezas": "Los renders de efectos (fundidos, picture-in-picture, rótulos) no son material: false = no entran en ningún bruto | true = van con las piezas",
    "clips_compartidos": "primero",
    "_clips_compartidos": "Un clip que está en varios proyectos: primero = solo en el bruto del primer proyecto (orden alfabético) | todos = en cada bruto",
    "prefijo_fichero": "",
    "convertir": {
        "codec": "mpeg2video",
        "ancho": "auto",
        "alto": "auto",
        "fps": "auto",
        "bitrate": "50M",
        "desentrelazar": "auto",
        "_nota": "Solo con formato_salida = auto y cuando un bruto mezcla formatos. auto = el formato mayoritario del proyecto (no cambia NTSC a PAL ni SD a HD). Códec MPEG-2 4:2:2 (tipo XDCAM/IMX). desentrelazar: auto = solo si hay que reescalar | si | no"
    },
    "clips_a_la_vez": "auto",
    "_clips_a_la_vez": "Cuántos clips se convierten a la vez (cada uno es un ffmpeg). auto = núcleos del procesador menos uno, hasta 6. 1 = de uno en uno",
    "enrutado_audio": [],
    "_enrutado_audio": "Qué pista del clip va a cada pista del bruto: [2, 1] = la 2 del clip a la 1 y la 1 a la 2. 0 = silencio. Vacío = cada una en la suya",
    "niveles_audio": "sin_tocar",
    "_niveles_audio": "sin_tocar (recomendado: el bruto es documento) | ganancia = ganancia_db fija por pista | r128 = cada clip a -23 LUFS (EBU R128), con tope de +/-12 dB; altera el original",
    "ganancia_db": [],
    "_ganancia_db": "Con niveles_audio = ganancia: dB por pista del bruto, de -20 a 20 (p. ej. [3, 0] sube 3 dB la pista 1)",
    "md5": True,
    "_md5": "Un fichero .md5 por bruto (comprobación de integridad al copiarlo)",
    "rdd9": True,
    "_rdd9": "true = los brutos XDCAM salen empaquetados según SMPTE RDD 9 (el MXF de XDCAM HD422 de Sony): los clips los une bmxtranswrap de la BBC, que va con el programa, en lugar de ffmpeg; no recodifica ni añade tiempo | false = MXF OP1a de ffmpeg",
    "quitar_duplicados": True,
    "_quitar_duplicados": "Clips que son un trozo de otro del mismo proyecto (un subclip o una consolidación de Avid). En DV: misma fecha, hora y timecode de cinta de la cámara y los mismos fotogramas; en cualquier formato: timecode del fichero dentro del otro y el mismo sonido, muestra a muestra: true = no entran en el bruto y quedan en la papelera del montaje, de donde se pueden recuperar | false = entran los dos",
    "ficha_tecnica": True,
    "_ficha_tecnica": "Una ficha .txt por bruto que dice si el fichero exportado es conforme a XDCAM HD422",
    "medidor_audio": True,
    "_medidor_audio": "Mide sonoridad y pico de cada clip y avisa en el shotlist de saturados o casi mudos (no los toca)",
    "verificar_decodificando": False,
    "_verificar_decodificando": "true = tras unir, decodifica el bruto entero para comprobarlo (lento, muy seguro)"
}

PARAR = threading.Event()  # lo activa el botón Detener de la ventana


class Detenido(Exception):
    """El usuario ha pulsado Detener."""


class OrigenPerdido(Detenido):
    """El disco de origen ha desaparecido (desconectado, se ha dormido, cable flojo). Se trata como Detener (lo
    terminado se conserva, lo que estaba a medias se descarta y al volver a exportar se sigue), pero se avisa."""

    def __init__(self, origen, analizando=False):
        super().__init__("Se ha perdido el disco de origen (%s): ¿se ha desconectado? Vuelve a conectarlo con la misma letra y %s"
                         % (origen, "vuelve a analizarlo (el análisis empieza de cero)." if analizando else
                            "pulsa Exportar: los brutos terminados se conservan y se sigue por el que faltaba."))


def origen_disponible(origen):
    """¿Se puede leer la carpeta raíz del disco de origen?"""
    try:
        os.listdir(ruta_larga(origen))
        return True
    except OSError:
        return False


def comprobar_origen(origen, analizando=False):
    if not origen_disponible(origen):
        raise OrigenPerdido(origen, analizando)


def ruta_larga(ruta, siempre=False):
    """En Windows, las rutas de más de 260 caracteres solo se pueden abrir con el prefijo "\\\\?\\" (carpetas de proyecto
    con nombres largos, en chino...). En Linux o con rutas cortas, la misma ruta. ffmpeg (5.1 o más) las admite igual."""
    texto = str(ruta)
    if os.name != "nt" or texto.startswith("\\\\?\\"):
        return texto
    absoluta = os.path.abspath(texto)  # lo que cuenta es la ruta completa: "disco_prueba\\..." puede ser corta y la real no
    if len(absoluta) < 240 and not siempre:
        return texto
    return "\\\\?\\UNC\\" + absoluta[2:] if absoluta.startswith("\\\\") else "\\\\?\\" + absoluta


CODECS_COPIABLES = {"dnxhd", "mpeg2video", "dvvideo", "prores", "h264"}
# XDCAM HD422 50 Mb/s 1080i50 (también se escribe 1080i/25: 25 imágenes entrelazadas = 50 campos por segundo): MPEG-2 4:2:2
# long GOP, entrelazado (primero el campo superior), 16:9.
XDCAM_HD50 = {"codec": "mpeg2video", "ancho": 1920, "alto": 1080, "fps": "25/1", "pix_fmt": "yuv422p"}
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


def ejecutar_ffmpeg(cmd, timeout, cwd=None):
    """Devuelve (ok, últimas líneas de stderr). timeout en segundos: un MXF dañado no debe colgar la exportación.
    Si se pulsa Detener, mata ffmpeg en un segundo como mucho y lanza Detenido. También sirve para bmxtranswrap."""
    if PARAR.is_set():
        raise Detenido()
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, cwd=cwd,
                                **flags_subproceso())
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
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, **flags_subproceso())
        return float(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Reparto en brutos: tarjetas, agencias y piezas
# ---------------------------------------------------------------------------

# Carpetas que cuelgan de la raíz de una tarjeta de cámara (P2, XDCAM/XAVC, AVCHD, foto/vídeo genérico).
# El nombre de un clip P2 (0016Q3) NO sirve para saber la tarjeta: en el disco real de México, clips
# grabados con segundos de diferencia se llaman 00016R, 0002CI, 000328.
MARCAS_TARJETA = {"CONTENTS", "PRIVATE", "DCIM", "XDROOT", "BPAV", "AVCHD", "M4ROOT", "CLIPR", "MP_ROOT"}
# Nombres de fichero de cámaras que graban en tarjeta SD (28/10/2026: Osmo y cámaras con tarjeta SD). Van en el
# código y no solo en config_brutos.json → camara_propia_regex porque ese fichero se escribe al primer uso y no se
# actualiza: así los reconocen también los programas ya instalados. DJI (dron, Osmo Pocket/Action/360: DJI_0001 y
# DJI_20231015123456_0001_D), Sony (C0001, MAH/MAQ00001), Canon (MVI_1234), GoPro, Panasonic (P1000123), Nikon (DSC_),
# Fujifilm (DSCF), Zoom (ZOOM0001), ARRI/Blackmagic/Canon XF (A001C002), Sony con fecha (20231015_C0001)
CAMARAS_SD_REGEX = (r"^(DJI_\d+|C\d{4}|MA[HQ]\d{5}|MVI_\d{4}|GH\d{6}|GX\d{6}|GOPR\d{4}|GP\d{6}|DSC_?\d{4}|DSCF\d{4}|"
                    r"P\d{7}|A\d{3}C\d{3}|\d{8}_C\d{3,4}|ZOOM\d{4}|.+_S\d{3}_S\d{3}_T\d{3})(\D|$)")
# AVCHD (Sony, Panasonic, Canon de consumo): 00000.MTS en PRIVATE/AVCHD/BDMV/STREAM
AVCHD_REGEX = r"^\d{5}\.(mts|m2ts)$"


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


def fecha_grabacion(c):
    """Fecha y hora de grabación (cámara) si el análisis la encontró en una columna del bin o un comentario del MXF."""
    try:
        return dt.datetime.strptime(c["fecha_grabacion"], "%Y-%m-%d %H:%M:%S") if c.get("fecha_grabacion") else None
    except ValueError:
        return None


DESFASE_RELOJ_HORAS = 12  # más diferencia entre la grabación y el volcado se avisa (reloj de la cámara mal puesto)


def fecha_imposible(grabacion, volcado=None, ahora=None, desde=2000):
    """Por qué una fecha de grabación no puede ser buena, o None (docs/diseno_cualquier_disco.md, apartado 5): antes de
    2000, en el futuro, o el 1 de enero lejos del volcado (las cámaras con el reloj sin poner arrancan el 1/1). Un 1 de
    enero cerca del volcado puede ser de verdad (Año Nuevo), así que vale."""
    ahora = ahora or dt.datetime.now()
    if grabacion.year < desde:
        return "anterior a %d" % desde
    if grabacion > ahora + dt.timedelta(days=1):
        return "en el futuro"
    if (grabacion.month, grabacion.day) == (1, 1) and volcado and abs(volcado - grabacion) > dt.timedelta(days=2):
        return "1 de enero lejos del volcado: reloj de la cámara sin poner"
    return None


def fecha_clip(c, mxf_por_ruta):
    """La fecha que manda: la de grabación si consta; si no, la del fichero de media (volcado)."""
    return fecha_grabacion(c) or fecha_media(c, mxf_por_ruta)


def orden_cronologico(c, mxf_por_ruta):
    """Orden estricto: fecha y hora de grabación (o, si no consta, del fichero de media) al segundo; si coinciden, el
    timecode original del clip (en un volcado de tarjeta caen varios clips en el mismo segundo) y después el nombre.
    Sin fecha, al final."""
    f = fecha_clip(c, mxf_por_ruta)
    return (f is None, f or dt.datetime.min, str(c.get("timecode") or "").replace(";", ":"), c["nombre"])


def clips_a_la_vez(cfg):
    n = cfg.get("clips_a_la_vez", "auto")
    if str(n) == "auto":
        return max(1, min(6, (os.cpu_count() or 2) - 1))
    try:
        return max(1, int(n))
    except (TypeError, ValueError):
        return 1


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


def es_fichero_de_camara(fichero, cfg):
    """¿El fichero que guarda el bin se llama como los de una cámara (DJI_0012.MP4, C0380.MP4...)? Para el material
    propio importado desde una carpeta del PC en vez de desde la tarjeta (camara_propia_regex)."""
    nombre = re.split(r"[\\/]+", str(fichero or ""))[-1]
    base = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", nombre)
    if not base:
        return False
    if re.search(CAMARAS_SD_REGEX, base, re.IGNORECASE) or re.search(AVCHD_REGEX, nombre, re.IGNORECASE):
        return True
    rx = cfg.get("camara_propia_regex", CONFIG_DEFECTO["camara_propia_regex"])
    try:
        return bool(rx and re.search(rx, base, re.IGNORECASE))
    except re.error as e:
        LOG.error("camara_propia_regex no válida (%s); no se aplica", e)
        return False


def es_bin_de_agencia(nombre_bin, cfg):
    """¿El nombre del bin dice que es de agencias? ('20260916 agencias.avb')."""
    base = re.sub(r"\.avb$", "", re.split(r"[\\/]+", str(nombre_bin or ""))[-1], flags=re.IGNORECASE)
    rx = cfg.get("bins_agencia_regex", CONFIG_DEFECTO["bins_agencia_regex"])
    try:
        return bool(base and rx and re.search(rx, base, re.IGNORECASE))
    except re.error as e:
        LOG.error("bins_agencia_regex no válida (%s); no se aplica", e)
        return False


# ---------------------------------------------------------------------------
# Origen del material por señales (docs/diseno_cualquier_disco.md, apartado 4)
# ---------------------------------------------------------------------------

CLASES_ORIGEN = {"propio": "Material propio", "agencia": "Agencia", "importado": "Importado de fuera",
                 "efecto": "Efecto de Avid", "dudoso": "Dudoso"}
# Nombres de fichero de móvil: IMG_1234.MOV (iPhone), VID_20210915_101500.mp4 y PXL_... (Android), VID-20210915-WA0001 (WhatsApp)
MOVIL_REGEX = r"^(IMG_\d{4}|IMG_\d{8}_\d{6}|VID_\d{4,}|PXL_\d{8}|VID-\d{8}-WA\d+|.*-WA\d{4})(\D|$)"
# Nombre de clip de una tarjeta P2 de Panasonic (0001AB, 0001AB.new.01), como la regla de config_origen.json; aquí
# también para los clips sin bin, a los que no se aplica esa regla
NOMBRE_P2 = r"^\d{4}[A-Z0-9]{2}(\.new)?(\.\d+)?$"
# Carpetas de las que lo importado viene de fuera: descargas y mensajería
CARPETAS_DE_FUERA = r"(^|[\\/])(downloads|descargas|whatsapp[^\\/]*|telegram[^\\/]*|wechat[^\\/]*)([\\/]|$)"
# Marcas de móvil en los metadatos (make): el resto de marcas es de cámara
MARCAS_MOVIL = ("apple", "samsung", "xiaomi", "huawei", "honor", "google", "oneplus", "oppo", "vivo", "motorola", "realme", "nokia", "lg")
# Grabadores externos (graban la señal de la cámara por HDMI/SDI): el encoder que escriben cuando no ponen make.
# Atomos: Ninja, Shogun, Samurai, Sumo, Shinobi; "Blade" es el sistema de los Ninja/Shogun (BLADE_S001_S001_T001.MOV)
GRABADORES = (("atomos", "Atomos"), ("ninja", "Atomos"), ("shogun", "Atomos"), ("samurai", "Atomos"), ("sumo", "Atomos"),
              ("shinobi", "Atomos"), ("blade", "Atomos"), ("ki pro", "AJA Ki Pro"), ("hyperdeck", "Blackmagic HyperDeck"),
              ("video assist", "Blackmagic Video Assist"), ("pix", "Sound Devices PIX"), ("odyssey", "Convergent Design Odyssey"))
# Quien escribe el fichero y no es una cámara (MXF de Avid, recodificados...)
NO_CAMARA = ("avid", "ffmpeg", "lavf", "adobe", "apple final cut", "telestream", "harmonic", "evs", "blackmagic design davinci")


def marca_de_metadatos(tags_formato, tags_flujos=()):
    """(marca, tipo) según los metadatos del fichero; tipo 'camara', 'movil' o 'internet'. (None, None) si no dicen nada.
    Ejemplos: el make/model de un iPhone o una Panasonic, el handler de GoPro, el XAVC de Sony, la compañía de un MXF de cámara."""
    t = {str(k).lower(): str(v) for k, v in (tags_formato or {}).items()}
    flujos = [{str(k).lower(): str(v) for k, v in (f or {}).items()} for f in tags_flujos or ()]
    make = t.get("com.apple.quicktime.make") or t.get("make") or t.get("com.android.manufacturer") or ""
    model = t.get("com.apple.quicktime.model") or t.get("model") or t.get("com.android.model") or ""
    make, model = make.strip(), model.strip()
    if make:
        marca = model if model.lower().startswith(make.lower()) else " ".join(x for x in (make, model) if x)
        return marca, ("movil" if make.lower().startswith(MARCAS_MOVIL) else "camara")
    if t.get("com.android.version"):
        return "Android", "movil"
    handlers = " ".join(f.get("handler_name", "") for f in flujos) + " " + t.get("handler_name", "")
    if "produced by google" in handlers.lower() or t.get("major_brand", "").lower() == "dash":
        return "YouTube/Google", "internet"
    if "gopro" in handlers.lower() or t.get("firmware", "").upper().startswith(("HD", "H1", "H2")):
        return "GoPro", "camara"
    if "dji" in (handlers + " " + t.get("encoder", "")).lower():
        return "DJI", "camara"
    encoder = t.get("encoder", "").strip()
    grabador = next((m for clave, m in GRABADORES if re.search(r"(?<![a-z])%s(?![a-z])" % clave, encoder.lower())), None)
    if grabador:
        return "%s (%s)" % (grabador, encoder) if grabador.lower() not in encoder.lower() else encoder, "camara"
    if any(k.startswith("com.blackmagic-design.camera") for k in t):
        return " ".join(x for x in ["Blackmagic", t.get("com.blackmagic-design.camera.model", "").strip()] if x), "camara"
    if t.get("major_brand", "").upper().startswith("XAVC") or "xavc" in t.get("compatible_brands", "").lower():
        return "Sony (XAVC)", "camara"
    compania = (t.get("company_name") or "").strip()
    if compania and not compania.lower().startswith(NO_CAMARA):
        return " ".join(x for x in [compania, (t.get("product_name") or "").strip()] if x), "camara"
    return None, None


def _base_fichero(ruta):
    return re.sub(r"\.[A-Za-z0-9]{2,4}$", "", re.split(r"[\\/]+", str(ruta or ""))[-1])


def procedencia(c):
    """De dónde viene el clip, para agrupar en la pantalla de origen y recordar decisiones: (carpeta de la que se
    importó o '', bin). La carpeta, con barras \\ y sin la letra de unidad (cambia de un PC a otro)."""
    ruta = str(c.get("fichero_origen") or "").replace("/", "\\")
    carpeta = ruta.rsplit("\\", 1)[0] if "\\" in ruta else ""
    carpeta = re.sub(r"^[A-Za-z]:", "", carpeta)
    return carpeta, (c.get("bins") or [""])[0]


def decision_de_origen(c, cfg):
    """La clase que alguien eligió para la procedencia del clip: en este disco (origen.json, cfg['decisiones_origen'])
    o como regla para todos (config_brutos.json → reglas_origen). (clase, motivo) o None."""
    carpeta, nbin = procedencia(c)
    for d in cfg.get("decisiones_origen") or ():
        if d.get("clase") in CLASES_ORIGEN and (d.get("carpeta") or "") == carpeta and (d.get("bin") or "") == nbin:
            return d["clase"], "elegido en este disco"
    ruta = str(c.get("fichero_origen") or "").replace("/", "\\").lower()
    for r in cfg.get("reglas_origen") or ():
        if r.get("clase") not in CLASES_ORIGEN or not (r.get("carpeta") or r.get("bin")):
            continue
        if r.get("carpeta") and str(r["carpeta"]).replace("/", "\\").lower() not in ruta:
            continue
        if r.get("bin") and str(r["bin"]).lower() not in nbin.lower():
            continue
        return r["clase"], "regla recordada (%s)" % (r.get("carpeta") or "bin " + r["bin"])
    return None


def nombre_de_agencia(c, cfg):
    """El indicativo de agencia que lleva el nombre del clip (el del MXF o los del bin) o del fichero importado
    ('RTRWNEV', 'Reuters', 'EFE'...), o None (nombres_agencia_regex)."""
    rx = cfg.get("nombres_agencia_regex", CONFIG_DEFECTO["nombres_agencia_regex"])
    if not rx:
        return None
    for texto in [c.get("nombre"), *(c.get("nombres_bin") or ()), _base_fichero(c.get("fichero_origen"))]:
        try:
            m = re.search(rx, str(texto or ""), re.IGNORECASE)
        except re.error as e:
            LOG.error("nombres_agencia_regex no válida (%s); no se aplica", e)
            return None
        if m:
            return m.group(0)
    return None


def senales_de_origen(c, cfg):
    """[(fuerza, clase, motivo)]: lo que dice cada pista. 4 = el indicativo de agencia en el nombre (manda sobre los
    metadatos: Reuters graba con cámaras Sony); 3 = el fichero o la tarjeta; 2 = nombres y carpetas."""
    s = []
    origen = str(c.get("origen") or "")
    fichero = c.get("fichero_origen")
    agencia = nombre_de_agencia(c, cfg)
    if agencia:
        s.append((4, "agencia", "nombre de agencia (%s)" % agencia))
    if origen == "Agencia (bin de agencias)":
        s.append((2, "agencia", "bin de agencias"))
    elif origen.startswith(tuple(cfg.get("origenes_agencia") or ())):
        s.append((3, "agencia", origen))
    if c.get("marca"):
        clase = {"camara": "propio", "movil": "importado", "internet": "importado"}.get(c.get("marca_tipo"))
        if clase:
            que = {"camara": "metadatos", "movil": "móvil, metadatos", "internet": "descargado, metadatos"}[c["marca_tipo"]]
            s.append((3, clase, "%s: %s" % (que, c["marca"])))
    tarjeta = carpeta_tarjeta(fichero)
    if tarjeta is not None:
        marca = next((x for x in re.split(r"[\\/]+", str(fichero)) if x.upper() in MARCAS_TARJETA), "")
        s.append((3, "propio", "carpeta de tarjeta (%s)" % marca))
    if es_fichero_de_camara(fichero, cfg) or (origen.startswith("Cámara") and not tarjeta):
        s.append((2, "propio", "nombre de fichero de cámara" if fichero else origen))
    else:
        nombres = [str(n or "").strip() for n in [c.get("nombre"), *(c.get("nombres_bin") or ())]]
        if not tarjeta and any(re.match(NOMBRE_P2, n, re.IGNORECASE) for n in nombres):
            s.append((2, "propio", "nombre de clip P2"))
        elif not tarjeta and any(es_fichero_de_camara(n, cfg) for n in nombres if n):  # Osmo, SD... con el nombre del fichero
            s.append((2, "propio", "nombre de clip de cámara"))
    if fichero and re.search(MOVIL_REGEX, _base_fichero(fichero), re.IGNORECASE):
        s.append((2, "importado", "nombre de fichero de móvil"))
    m = re.search(CARPETAS_DE_FUERA, str(fichero or ""), re.IGNORECASE)
    if m:
        s.append((2, "importado", "carpeta %s" % m.group(2)))
    return s


def clasificar(c, cfg):
    """Origen de un clip por señales: (clase, motivo, seguro). clase: propio | agencia | importado | efecto | dudoso.
    Manda lo que se eligió a mano; si no, la pista más fuerte. Si las más fuertes se contradicen (propio frente a fuera),
    dudoso. Un fichero importado sin ninguna pista, también dudoso. Sin ruta de importación ni pistas (capturado, creado
    en Avid), dudoso con solo_material_de_camara (27/10/2026); si no, propio.
    Lo dudoso se queda fuera de los brutos con solo_material_de_camara; si no, entra marcado "para revisar"."""
    if c.get("render"):
        return "efecto", "efecto de Avid", True
    elegido = decision_de_origen(c, cfg)
    if elegido:
        return elegido[0], elegido[1], True
    senales = senales_de_origen(c, cfg)
    if not senales:
        if c.get("fichero_origen"):
            carpeta = procedencia(c)[0] or "?"
            return "dudoso", "importado de %s, sin pistas de origen" % carpeta, False
        if str(c.get("origen") or "").startswith("Fichero importado"):
            return "dudoso", "importado (según el bin), sin ruta ni pistas de origen", False
        if cfg.get("solo_material_de_camara", True):
            return "dudoso", "sin pistas de cámara ni ruta de importación (¿capturado o creado en Avid?)", False
        return "propio", "sin ruta de importación (capturado o de tarjeta)", True
    fuerza = max(f for f, _, _ in senales)
    arriba = [(cl, m) for f, cl, m in senales if f == fuerza]
    clases = {cl for cl, _ in arriba}
    motivos = "; ".join(dict.fromkeys(m for _, m in arriba))
    if len(clases) == 1:
        return clases.pop(), motivos, True
    if clases <= {"agencia", "importado"}:
        return "agencia", motivos, True
    return "dudoso", "pistas contrarias: " + motivos, False


TRIAJE = {"restaurar": "Restaurar", "poco": "Poco material: ¿descartar?", "revisar": "Revisar: vídeos sin pistas de cámara",
          "sin": "Sin material de cámara"}


def triaje_proyectos(clips, cfg, proyectos=()):
    """Para decidir qué proyectos restaurar antes del montaje (28/10/2026): separar los que
    tienen material de los que solo tienen uno o dos clips cortos. Por proyecto, sus clips de cámara (clasificar: propio; sin agencias, importados,
    dudosos ni efectos de Avid), cuántos son y cuánto duran, y un veredicto: sin (ninguno), poco (hasta
    triaje_max_clips y menos de triaje_max_minutos en total) o restaurar; y revisar cuando hay vídeos dudosos (MP4 o
    MOV sin nombre ni datos de cámara, 28/10/2026) que pueden ser el
    material y no hay de cámara (o hay poco y lo dudoso dura más). Un clip en varios proyectos cuenta en todos;
    uno sin bin, en SIN_PROYECTO. proyectos: los del disco, para que salgan también los que no tienen ningún clip.
    Devuelve una lista ordenada (restaurar, poco, revisar, sin; dentro, por nombre) de dicts con proyecto, veredicto,
    camara (clips, por nombre), dudosos (los vídeos dudosos, por nombre), sin_audio (los de cámara sin ninguna pista de
    sonido), segundos, mas_largo y otros (Counter de las demás clases)."""
    max_clips = int(cfg.get("triaje_max_clips", CONFIG_DEFECTO["triaje_max_clips"]))
    max_seg = float(cfg.get("triaje_max_minutos", CONFIG_DEFECTO["triaje_max_minutos"])) * 60
    nuevo = lambda p: {"proyecto": p, "camara": [], "dudosos": [], "otros": Counter()}
    por = {p: nuevo(p) for p in proyectos}
    for c in con_video(clips):  # lo que solo tiene sonido no se cuenta ni se enseña
        if c.get("render"):
            continue
        clase = clasificar(c, cfg)[0]
        for p in c.get("proyectos") or ["SIN_PROYECTO"]:
            t = por.setdefault(p, nuevo(p))
            if clase == "propio" and c.get("fichero_video"):
                t["camara"].append(c)
            elif clase == "dudoso" and c.get("fichero_video"):
                t["dudosos"].append(c)
                t["otros"]["dudoso"] += 1
            else:
                t["otros"][clase] += 1
    orden = list(TRIAJE)
    for t in por.values():
        t["camara"].sort(key=lambda c: c["nombre"])
        t["dudosos"].sort(key=lambda c: c["nombre"])
        t["sin_audio"] = [c for c in t["camara"] if not c.get("num_audio")]
        duraciones = [c.get("duracion") or 0 for c in t["camara"]]
        t["segundos"], t["mas_largo"] = sum(duraciones), max(duraciones, default=0)
        dudoso_seg = sum(c.get("duracion") or 0 for c in t["dudosos"])
        if not t["camara"]:
            t["veredicto"] = "revisar" if t["dudosos"] else "sin"
        elif len(t["camara"]) <= max_clips and t["segundos"] < max_seg:
            t["veredicto"] = "revisar" if dudoso_seg >= max_seg else "poco"
        else:
            t["veredicto"] = "restaurar"
    return sorted(por.values(), key=lambda t: (orden.index(t["veredicto"]), t["proyecto"].lower()))


def texto_origen(clase, motivo, seguro):
    """Lo que sale en el shotlist y en la ventana: 'Material propio', 'Dudoso · para revisar', 'Agencia · revisado'."""
    if not clase:
        return ""
    if not seguro:
        return CLASES_ORIGEN[clase] + " · para revisar"
    return CLASES_ORIGEN[clase] + (" · revisado" if motivo in ("elegido en este disco",) or str(motivo).startswith("regla recordada") else "")


def anotar_origen(clips, cfg):
    """Pone en cada clip origen_clase, origen_motivo, origen_seguro y origen_texto (clasificar)."""
    for c in clips:
        clase, motivo, seguro = clasificar(c, cfg)
        c["origen_clase"], c["origen_motivo"], c["origen_seguro"] = clase, motivo, seguro
        c["origen_texto"] = texto_origen(clase, motivo, seguro)


def leer_decisiones_origen(carpeta_brutos):
    """Lo elegido en la pantalla 'Origen del material' para este disco (origen.json en la carpeta de brutos)."""
    ruta = Path(carpeta_brutos) / "origen.json"
    if not ruta.exists():
        return []
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        return [d for d in datos.get("decisiones", []) if isinstance(d, dict) and d.get("clase") in CLASES_ORIGEN]
    except Exception as e:
        LOG.warning("origen.json ilegible (%s); no se aplica", e)
        return []


# Lo que se puede elegir en la pantalla "Origen del material" (None = como propone el programa)
ELECCIONES_ORIGEN = [("propio", "Entra"), ("agencia", "Fuera: agencia"), ("importado", "Fuera: importado"),
                     (None, "Como propone el programa")]


def grupos_de_origen(clips, cfg):
    """Para la pantalla "Origen del material": los clips agrupados por procedencia (carpeta de la que se importaron y
    bin), con lo que propone el programa, el motivo más repetido, unos ejemplos y lo elegido para este disco
    (cfg['decisiones_origen']). Los efectos de Avid no salen. Primero los grupos con dudosos; luego, los más largos."""
    sin_elegir = dict(cfg, decisiones_origen=[])
    elegido = {((d.get("carpeta") or ""), (d.get("bin") or "")): d["clase"] for d in cfg.get("decisiones_origen") or ()}
    grupos = {}
    for c in clips:
        if c.get("render"):
            continue
        clave = procedencia(c)
        g = grupos.setdefault(clave, {"carpeta": clave[0], "bin": clave[1], "clips": [], "segundos": 0.0,
                                      "clases": Counter(), "motivos": Counter(), "elegido": elegido.get(clave)})
        clase, motivo, seguro = clasificar(c, sin_elegir)
        g["clips"].append(c)
        g["segundos"] += c.get("duracion") or 0
        g["clases"][clase if seguro else "dudoso"] += 1
        g["motivos"][motivo] += 1
    for g in grupos.values():
        g["dudosos"] = g["clases"].get("dudoso", 0)
        g["propuesta"] = " · ".join("%s %d" % (CLASES_ORIGEN[k], n) for k, n in g["clases"].most_common()) \
            if len(g["clases"]) > 1 else CLASES_ORIGEN[next(iter(g["clases"]))]
        g["motivo"] = g["motivos"].most_common(1)[0][0]
        g["ejemplos"] = [c["nombre"] for c in g["clips"][:3]]
    return sorted(grupos.values(), key=lambda g: (g["dudosos"] == 0, -g["segundos"], g["carpeta"], g["bin"]))


def guardar_decisiones_origen(carpeta_brutos, decisiones):
    """Escribe origen.json en la carpeta de brutos: [{"carpeta", "bin", "clase"}] (solo las que no son None)."""
    datos = {"version": 1,
             "_comentario": "Origen del material elegido a mano para este disco (pantalla Montaje → Origen del material). "
                            "carpeta: de dónde se importó, sin letra de unidad; bin; clase: propio | agencia | importado",
             "decisiones": [d for d in decisiones if d.get("clase") in CLASES_ORIGEN]}
    ruta = Path(carpeta_brutos) / "origen.json"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    return ruta


def regla_de_origen(carpeta, nbin, clase):
    """La regla para config_brutos.json → reglas_origen ("Recordar para otros discos"): por carpeta si se importó de
    una; si no (capturado), por el nombre del bin sin la extensión."""
    if carpeta:
        return {"carpeta": carpeta, "clase": clase}
    return {"bin": re.sub(r"\.avb$", "", nbin or "", flags=re.IGNORECASE), "clase": clase}


def es_de_camara(c, cfg):
    """Material de cámara según el bin: ruta de tarjeta (CONTENTS, PRIVATE, DCIM...), nombre de fichero de cámara
    o un origen de cámara en config_origen.json (p. ej. los nombres de clip de P2)."""
    return bool(carpeta_tarjeta(c.get("fichero_origen")) or es_fichero_de_camara(c.get("fichero_origen"), cfg)
                or str(c.get("origen") or "").startswith("Cámara"))


def es_importado(c, cfg):
    """De fuera (agencia, internet, móvil...) según clasificar. Lo dudoso no: eso lo decide excluido."""
    return clasificar(c, cfg)[0] in ("agencia", "importado")


def excluido(c, cfg):
    """Fuera de los brutos de material: lo de fuera y, con solo_material_de_camara, también lo dudoso."""
    if not cfg.get("excluir_importados", True):
        return False
    clase = clasificar(c, cfg)[0]
    return clase in ("agencia", "importado") or (clase == "dudoso" and bool(cfg.get("solo_material_de_camara", True)))


def tc_cinta(texto, fps):
    """Timecode de cinta de un subcódigo DV ('hh:mm:ss:ff', o 'hh:mm:ss;ff' si es drop-frame) -> fotogramas desde 0."""
    return tc_a_frames(texto, 30000 / 1001 if ";" in str(texto) else int(round(fps_num(fps) or 25)))


def tramo_dv(c):
    """Tramo de grabación de un clip DV según el subcódigo que escribe la cámara en cada fotograma (c["dv"]):
    (inicio, fin) como datetime de la fecha y hora de grabación y (tc_inicio, tc_fin) en fotogramas del timecode de
    cinta, o None si no lo lleva. None si el clip no trae una fecha de grabación creíble."""
    dv = c.get("dv") or {}
    ini, fin = dv.get("inicio") or {}, dv.get("fin") or {}

    def cuando(d):
        try:
            return dt.datetime.strptime("%s %s" % (d["fecha"], d["hora"]), "%Y-%m-%d %H:%M:%S")
        except (KeyError, TypeError, ValueError):
            return None
    t0 = cuando(ini)
    if t0 is None or not 1990 <= t0.year <= dt.date.today().year + 1:
        return None
    t1 = cuando(fin) or t0 + dt.timedelta(seconds=c.get("duracion") or 0)
    if t1 < t0:
        return None
    tc = None
    try:
        if ini.get("tc") and fin.get("tc"):
            tc = (tc_cinta(ini["tc"], c.get("fps")), tc_cinta(fin["tc"], c.get("fps")) + int(fin.get("atras") or 0))
        elif ini.get("tc") and dv.get("fotogramas"):  # sin el último fotograma: el primero más los que tiene el clip
            tc0 = tc_cinta(ini["tc"], c.get("fps"))
            tc = (tc0, tc0 + int(dv["fotogramas"]) - 1)
    except ValueError:
        tc = None
    if tc and tc[1] < tc[0]:
        tc = None
    return t0, t1, tc


def duplicados_dv(clips, margen=1.0):
    """Clips DV cuyo tramo de grabación (fecha y hora de la cámara, y timecode de cinta si lo hay) cae entero dentro
    del de otro: candidatos a ser un trozo copiado (un subclip o una consolidación de Avid crean otro fichero, con otra
    identidad, con los mismos fotogramas). Devuelve ([{"corto", "largo", "desde"}], [(a, b)]): desde es el fotograma
    del largo donde empezaría el corto; la segunda lista, los que solo se solapan en parte. Hay que confirmarlo con
    los fotogramas (diagnostico_avid.marcar_duplicados): dos cámaras grabando a la vez dan tramos que se solapan."""
    # a igual inicio, primero el más largo (se queda), contado en fotogramas (la hora del último va en segundos enteros) y,
    # si duran lo mismo, el que tiene más sonido: de dos copias del mismo clip, una muda y otra con sonido, la que se queda
    # en el bruto es la que tiene sonido
    def fotogramas(c):
        return (c.get("dv") or {}).get("fotogramas") or int(round((c.get("duracion") or 0) * (fps_num(c.get("fps")) or 25)))
    tramos = sorted(((t, c) for c in clips if c.get("fichero_video") for t in [tramo_dv(c)] if t),
                    key=lambda x: (x[0][0], -fotogramas(x[1]), -(x[1].get("num_audio") or 0), str(x[1].get("clave"))))
    holgura = dt.timedelta(seconds=margen)
    candidatos, solapes = [], []
    for i, (ta, a) in enumerate(tramos):
        for tb, b in tramos[i + 1:]:
            if tb[0] > ta[1] + holgura:
                break  # ordenados por inicio: los siguientes empiezan aún después
            if tb[1] <= ta[1] + holgura:  # b cae dentro de a (a empieza antes o a la vez y dura más o igual)
                largo, tl, corto, tco = a, ta, b, tb
            elif tb[0] <= ta[0] + holgura and ta[1] <= tb[1] + holgura:
                largo, tl, corto, tco = b, tb, a, ta
            else:
                if min(ta[1], tb[1]) - max(ta[0], tb[0]) > dt.timedelta(seconds=2):
                    solapes.append((a, b))
                continue
            fps = fps_num(largo.get("fps")) or 25
            por_hora = int(round((tco[0] - tl[0]).total_seconds() * fps))  # la hora va en segundos enteros: ±1 s
            if tl[2] and tco[2]:  # con timecode de cinta: tiene que caer dentro y cuadrar con la hora
                if not (tl[2][0] <= tco[2][0] and tco[2][1] <= tl[2][1]):
                    continue
                if abs((tco[2][0] - tl[2][0]) / fps - (tco[0] - tl[0]).total_seconds()) > 2:
                    continue
                desde = tco[2][0] - tl[2][0]
            else:
                desde = por_hora
            candidatos.append({"corto": corto, "largo": largo, "desde": max(0, desde), "por_tc": bool(tl[2] and tco[2]),
                               "desde_hora": max(0, por_hora)})
    return candidatos, solapes


def duplicados_tc(clips, tope=10):
    """Clips de cualquier formato cuyo tramo de timecode (el del fichero, que Avid conserva al consolidar o al hacer un
    subclip) cae entero dentro del de otro con los mismos fps: candidatos a ser un trozo copiado, que hay que confirmar
    con el sonido (diagnostico_avid.marcar_duplicados). Solo clips con vídeo, sonido y timecode, y no marcados ya.
    Muchas cámaras empiezan cada clip en 00:00:00:00: de cada corto se miran como mucho tope largos, primero los de su
    proyecto. Devuelve [{"corto", "largo", "desde"}]: desde es el fotograma del largo donde empezaría el corto."""
    grupos = defaultdict(list)
    for c in clips:
        fps = fps_num(c.get("fps"))
        if not (c.get("fichero_video") and c.get("timecode") and c.get("num_audio") and c.get("duracion") and fps) or c.get("contenido_en"):
            continue
        try:
            t0 = tc_cinta(c["timecode"], c.get("fps"))
        except ValueError:
            continue
        grupos[c.get("fps")].append((t0, t0 + int(round(c["duracion"] * fps)), c))
    import heapq
    candidatos = []
    for tramos in grupos.values():
        tramos.sort(key=lambda x: (x[0], -(x[1] - x[0]), -(x[2].get("num_audio") or 0), str(x[2].get("clave"))))
        for k, (b0, b1, corto) in enumerate(tramos):
            # los de antes empiezan antes o a la vez (y, si a la vez, duran más o igual): valen los que acaban después
            # (un fotograma de holgura por el redondeo de la duración). Sin guardarlos todos: con miles de clips en
            # 00:00:00:00 serían millones de parejas.
            suyos = set(corto.get("proyectos") or ())
            mejores = heapq.nsmallest(tope, ((not (suyos & set(largo.get("proyectos") or ())), largo.get("duracion") or 0, n, largo, b0 - a0)
                                             for n, (a0, a1, largo) in enumerate(tramos[:k]) if b1 <= a1 + 1),
                                      key=lambda x: x[:3])
            candidatos += [{"corto": corto, "largo": x[3], "desde": x[4]} for x in mejores]
    return candidatos


def muestras_sonido(ffmpeg, ruta, canal, inicio, dur):
    """Muestras de un canal de audio (canal: lo que da canales_clip), tal cual en PCM de 16 bits, sin remuestrear, desde
    inicio durante dur segundos. b"" si no se puede leer."""
    cmd = [ffmpeg, "-v", "error", "-ss", "%.3f" % max(0.0, inicio), "-i", str(ruta), "-t", "%.3f" % dur, "-map", "0:a:%d" % canal["flujo"]]
    if canal.get("de", 1) > 1:
        cmd += ["-af", "pan=mono|c0=c%d" % canal["canal"]]
    cmd += ["-c:a", "pcm_s16le", "-f", "s16le", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=120, **flags_subproceso())
    except Exception:
        return b""
    return r.stdout[:len(r.stdout) // 2 * 2] if r.returncode == 0 else b""


def sonido_con_forma(muestras, bloque=400):
    """¿El sonido cambia lo bastante para servir de huella? Un tono de referencia o un silencio son iguales en cualquier
    sitio y no prueban nada; la voz o el ambiente, sí. Mira el volumen por bloques de 400 muestras (8 ms a 48 kHz):
    tiene que haber algo por encima de -50 dBFS y una variación (desviación típica) de 3 dB o más."""
    import array
    import math
    import statistics
    m = array.array("h")
    m.frombytes(muestras)
    if sys.byteorder == "big":
        m.byteswap()
    niveles = []
    for i in range(0, len(m) - bloque + 1, bloque):
        e = sum(x * x for x in m[i:i + bloque]) / bloque
        niveles.append(max(-60.0, 10 * math.log10(e / 32768.0 ** 2)) if e > 0 else -60.0)
    return len(niveles) >= 10 and max(niveles) >= -50 and statistics.pstdev(niveles) >= 3


def buscar_muestras(trozo, donde):
    """Posición (en muestras) en la que trozo aparece idéntico, byte a byte, dentro de donde; None si no está."""
    if not trozo or len(trozo) > len(donde):
        return None
    i = donde.find(trozo)
    while i != -1 and i % 2:  # muestras de 2 bytes: solo valen las posiciones pares
        i = donde.find(trozo, i + 1)
    return None if i == -1 else i // 2


ID_P2 = re.compile(r"^(\d{4}[A-Z0-9]{2})(\.new(\.\d+)?)?$", re.IGNORECASE)


def mismo_clip_de_camara(clips):
    """Grupos de clips con el mismo identificador de clip P2 (0008AY, 00059S.new.04: Avid añade .new.NN al hacer un
    subclip o consolidar) que comparten proyecto: [(largo, [cortos])], el largo primero (más fotogramas; a igual, más
    sonido). Son candidatos: que el corto esté dentro del largo se comprueba con el timecode (marcar_duplicados)."""
    grupos = defaultdict(list)
    for c in con_video(clips):
        m = ID_P2.match(str(c.get("nombre") or ""))
        if m and not c.get("contenido_en"):
            for p in c.get("proyectos") or ["SIN_PROYECTO"]:
                grupos[(m.group(1).upper(), p)].append(c)
    fotogramas = lambda c: (c.get("dv") or {}).get("fotogramas") or int(round((c.get("duracion") or 0) * (fps_num(c.get("fps")) or 25)))
    salida, vistos = [], set()
    for cs in grupos.values():
        cs = [c for c in cs if id(c) not in vistos]
        if len(cs) < 2:
            continue
        cs.sort(key=lambda c: (-fotogramas(c), -(c.get("num_audio") or 0), str(c.get("clave"))))
        salida.append((cs[0], cs[1:]))
        vistos.update(id(c) for c in cs[1:])
    return salida


def es_duplicado(c, cfg):
    """¿Es un trozo repetido de otro clip del mismo proyecto (marcar_duplicados)? Entonces no entra en el bruto: en el
    montaje queda en la papelera, de donde se puede recuperar (quitar_duplicados en config_brutos.json)."""
    d = c.get("contenido_en") or {}
    return bool(cfg.get("quitar_duplicados", True) and d.get("mismo_proyecto"))


def texto_duplicado(c):
    """'parte de ENTREVISTA, del 2:37,6 al 2:51,3' para el montaje, el informe y el log."""
    d = c.get("contenido_en") or {}
    if not d:
        return ""
    t = lambda s: ("%d:%04.1f" % (s // 60, s % 60)).replace(".", ",")
    if d.get("desde") is None:
        return "parte de %s (mismo clip de cámara)" % d.get("nombre")
    return "parte de %s, del %s al %s" % (d.get("nombre"), t(d.get("desde") or 0), t(d.get("hasta") or 0))


def motivo_descarte(c, cfg):
    """Por qué un clip no entra en ningún bruto ("efecto de Avid", "agencia", "importado", "dudoso"), o None si entra."""
    if c.get("render") and not cfg.get("renders_en_piezas", False):
        return "efecto de Avid"
    if excluido(c, cfg):
        return clasificar(c, cfg)[0]
    return None


def con_video(clips):
    """Los clips con vídeo. Los que solo tienen sonido (ficheros de audio sin su vídeo) no van a ningún bruto y no se
    enseñan en el montaje, ni siquiera como descartados (30/10/2026)."""
    return [c for c in clips if c.get("fichero_video")]


def descartados_por_proyecto(clips, cruce, mxf_por_ruta, cfg):
    """{proyecto: [(clip, motivo)]} de lo que se queda fuera de los brutos (para la pantalla de montaje), con las mismas
    reglas que listas_por_proyecto. Un clip en varios proyectos cuenta con el primero, como allí."""
    salida = defaultdict(list)
    for c in con_video(clips):
        motivo = motivo_descarte(c, cfg)
        if not motivo:
            continue
        proys = sorted(c.get("proyectos_render" if c.get("render") else "proyectos") or []) or ["SIN_PROYECTO"]
        salida[proys[0]].append((c, motivo))
    for lista in salida.values():
        lista.sort(key=lambda x: orden_cronologico(x[0], mxf_por_ruta))
    return dict(salida)


def grupos_de_exportacion(clips, cruce, mxf_por_ruta, cfg, cortes=None):
    """Reparte los clips en brutos. Devuelve [{nombre, base, proyecto, tipo, contenido, clips}].
    cortes (solo proyecto_mes): {proyecto: [claves de los clips donde empieza cada bruto]} de la pantalla de cortes."""
    agrupar = cfg.get("agrupar", "proyecto_mes")
    if agrupar == "dias":
        return grupos_por_dias(clips, cruce, mxf_por_ruta, cfg)
    if agrupar == "proyecto":
        return grupos_por_proyecto(clips, cruce, mxf_por_ruta, cfg)
    return grupos_por_proyecto_mes(clips, cruce, mxf_por_ruta, cfg, cortes)


def trozos_hasta(cs, tope):
    """Parte una lista ya ordenada en trozos seguidos de hasta tope segundos (solo cuenta el vídeo). El clip que
    pasaría del tope no se añade: abre el trozo siguiente. Un clip que por sí solo lo pasa va solo."""
    duracion = lambda lista: sum(c.get("duracion") or 0 for c in lista if c.get("fichero_video"))
    trozos, trozo = [], []
    for c in cs:
        if trozo and duracion(trozo) + duracion([c]) > tope:
            trozos.append(trozo)
            trozo = []
        trozo.append(c)
    if trozo:
        trozos.append(trozo)
    return trozos


def listas_por_proyecto(clips, cruce, mxf_por_ruta, cfg):
    """agrupar=proyecto_mes: {proyecto: {"material": [...], "piezas": [...]}} con los clips de cada proyecto en orden
    cronológico estricto. Fuera: renders de efectos e importados (excluir_importados). Un clip en varios proyectos,
    solo en el primero (clips_compartidos = primero); los que no están en ningún bin, en SIN_PROYECTO."""
    politica = cfg.get("clips_compartidos", "primero")
    secuencias = {p["proyecto"]: {_normal(n) for n in p.get("secuencias_nombres", [])} for p in (cruce or {}).get("por_proyecto", [])}
    rx = re.compile(cfg["piezas_regex"]) if cfg.get("piezas_regex") else None
    por_proyecto = defaultdict(lambda: {"material": [], "piezas": [], "duplicados": []})
    for c in con_video(clips):
        render = bool(c.get("render"))
        if render and not cfg.get("renders_en_piezas", False):
            continue
        proys = sorted(c.get("proyectos_render" if render else "proyectos") or [])
        if politica == "primero" and proys:
            proys = proys[:1]
        if not proys:
            if not cfg.get("incluir_huerfanos", True):
                continue
            proys = ["SIN_PROYECTO"]
        for p in proys:
            if render or (cfg.get("piezas_aparte", True) and es_pieza(c, secuencias.get(p, set()), rx)):
                por_proyecto[p]["piezas"].append(c)
            elif not excluido(c, cfg):
                por_proyecto[p]["material"].append(c)
                if es_duplicado(c, cfg):  # en su sitio del orden, pero fuera del reparto (la papelera del montaje)
                    por_proyecto[p]["duplicados"].append(c)
    for listas in por_proyecto.values():
        for familia in listas:
            listas[familia].sort(key=lambda c: orden_cronologico(c, mxf_por_ruta))
    return dict(por_proyecto)


SALTO_DIAS = 180  # separar_si_pasan_dias por defecto


def salto_dias(cfg):
    try:
        return max(0.0, float((cfg or {}).get("separar_si_pasan_dias", SALTO_DIAS)))
    except (TypeError, ValueError):
        return SALTO_DIAS


def inicios_auto(cs, tope, desde=0, fecha=None, salto=0):
    """Posiciones (en cs) donde empieza cada bruto a partir de la posición desde, llenando hasta tope segundos. Con
    fecha (clip -> datetime) y salto (días), también donde entre un clip y el anterior pasan más de salto días: material
    de otra época en el mismo proyecto (separar_si_pasan_dias)."""
    duracion = lambda c: (c.get("duracion") or 0) if c.get("fichero_video") else 0
    hueco = dt.timedelta(days=salto) if fecha and salto else None
    inicios, acumulado = [], 0
    for i in range(desde, len(cs)):
        lejos = False
        if hueco and i > desde:
            a, b = fecha(cs[i - 1]), fecha(cs[i])
            lejos = bool(a and b and b - a > hueco)
        if i > desde and (lejos or acumulado + duracion(cs[i]) > tope):
            inicios.append(i)
            acumulado = 0
        acumulado += duracion(cs[i])
    return inicios


def partir_con_cortes(cs, tope, cortes=None, fecha=None, salto=0):
    """Trozos de cs. cortes: claves de los clips donde empieza cada bruto, elegidas en la pantalla de cortes; si
    alguna ya no está (otro análisis), se ignoran y se corta automáticamente hasta tope."""
    posicion = {c.get("clave"): i for i, c in enumerate(cs)}
    if cortes and all(k in posicion for k in cortes):
        inicios = sorted({posicion[k] for k in cortes} - {0})
    else:
        if cortes:
            LOG.warning("Cortes guardados que ya no cuadran con el análisis: se corta automáticamente")
        inicios = inicios_auto(cs, tope, fecha=fecha, salto=salto)
    bordes = [0] + inicios + [len(cs)]
    return [cs[a:b] for a, b in zip(bordes, bordes[1:]) if b > a]


def grupos_por_proyecto_mes(clips, cruce, mxf_por_ruta, cfg, cortes=None):
    """agrupar=proyecto_mes (por defecto, pedido por el Archivo en oct. 2026): cada proyecto en orden cronológico,
    en brutos de hasta horas_por_bruto (o donde se eligió en la pantalla de cortes: cortes = {proyecto: [claves]}),
    llamados "PROYECTO MES DE AÑO" (nombre_bruto; " 1", " 2"... si se repite el nombre). Piezas renderizadas aparte
    ("PROYECTO PIEZAS MES DE AÑO"). Lo que no tiene fecha va al final, con el nombre del proyecto solo."""
    tope = 3600 * float(cfg.get("horas_por_bruto", 2))
    fecha = lambda c: fecha_clip(c, mxf_por_ruta)
    grupos = []
    listas = listas_por_proyecto(clips, cruce, mxf_por_ruta, cfg)
    for proyecto in sorted(listas):
        elegidos = (cortes or {}).get(proyecto)
        trozos = trozos_proyecto(listas[proyecto], fecha, tope, elegidos, salto_dias(cfg))
        for familia in ("material", "piezas"):
            de_familia = [t for f, t in trozos if f == familia]
            nombres = numerar([nombre_bruto(proyecto, [fecha(c) for c in t], familia == "piezas") for t in de_familia])
            for nombre, t in zip(nombres, de_familia):
                grupos.append(grupo_bruto(proyecto, familia, nombre, t, fecha, " · corte elegido a mano" if elegidos and familia == "material" else ""))
    return grupos


def trozos_proyecto(listas_proyecto, fecha, tope, cortes=None, salto=SALTO_DIAS):
    """Reparto automático de un proyecto: [(familia, clips)], material y luego piezas; en cada familia, lo que tiene
    fecha en brutos de hasta tope (o con los cortes elegidos, solo en material) y al final lo que no tiene fecha."""
    trozos = []
    fuera = {c.get("clave") for c in listas_proyecto.get("duplicados") or []}
    for familia in ("material", "piezas"):
        cs = [c for c in listas_proyecto.get(familia) or [] if c.get("clave") not in fuera]
        con_fecha = [c for c in cs if fecha(c)]
        sin_fecha = [c for c in cs if not fecha(c)]
        elegidos = cortes if familia == "material" else None
        trozos += [(familia, t) for t in partir_con_cortes(con_fecha, tope, elegidos, fecha, salto) + trozos_hasta(sin_fecha, tope)]
    return trozos


def grupo_bruto(proyecto, familia, nombre, t, fecha, nota=""):
    """Un bruto para exportar_todo: nombre, base (nombre de fichero), proyecto, tipo, contenido, clips."""
    fechas = sorted(fecha(c) for c in t if fecha(c))
    periodo = (fechas[0].strftime("%d/%m/%Y") + ("" if fechas[-1].date() == fechas[0].date() else " – " + fechas[-1].strftime("%d/%m/%Y"))) if fechas else "sin fecha"
    contenido = "%s%s · %d clips%s" % ("piezas renderizadas · " if familia == "piezas" else "", periodo, len(t), nota)
    return {"nombre": nombre, "base": nombre_seguro(nombre, 120), "proyecto": proyecto, "tipo": familia, "contenido": contenido,
            "clips": t, "ordenado": True}


MESES = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]


def nombre_proyecto(proyecto):
    """Proyecto para un nombre de fichero: en mayúsculas, sin los caracteres que Windows no admite."""
    texto = "SIN PROYECTO" if proyecto == "SIN_PROYECTO" else str(proyecto)
    texto = re.sub(r'[\\/:*?"<>|]+', "", texto.upper())
    return re.sub(r"\s+", " ", texto).strip(" .") or "SIN NOMBRE"


def nombre_bruto(proyecto, fechas, piezas=False):
    """Nombre por defecto de un bruto: PROYECTO [PIEZAS] MES DE AÑO, en letra y sin guiones:
    "VENEZUELA SEPTIEMBRE DE 2021", "... SEPTIEMBRE A OCTUBRE DE 2021", "... DICIEMBRE DE 2021 A ENERO DE 2022".
    Sin fechas, solo el proyecto. La numeración de los repetidos (" 1", " 2") la pone quien reparte."""
    partes = [nombre_proyecto(proyecto)] + (["PIEZAS"] if piezas else [])
    fechas = sorted(f for f in fechas if f)
    if fechas:
        a, b = fechas[0], fechas[-1]
        if (a.year, a.month) == (b.year, b.month):
            partes.append("%s DE %d" % (MESES[a.month - 1], a.year))
        elif a.year == b.year:
            partes.append("%s A %s DE %d" % (MESES[a.month - 1], MESES[b.month - 1], a.year))
        else:
            partes.append("%s DE %d A %s DE %d" % (MESES[a.month - 1], a.year, MESES[b.month - 1], b.year))
    return " ".join(partes)


def numerar(nombres):
    """Añade " 1", " 2"... a los nombres repetidos, en el orden en que salen."""
    repetidos, vistos, salida = Counter(nombres), Counter(), []
    for n in nombres:
        vistos[n] += 1
        salida.append(n + (" %d" % vistos[n] if repetidos[n] > 1 else ""))
    return salida


def _dia(fecha, cfg):
    """Día al que pertenece un volcado: lo de antes de dia_empieza_hora cuenta como del día anterior."""
    return (fecha - dt.timedelta(hours=float(cfg.get("dia_empieza_hora", 6)))).date() if fecha else None


def bloques_por_dias(cs, mxf_por_ruta, cfg):
    """Ordena los clips por fecha de la media y los junta por días seguidos mientras el bloque no pase de
    horas_por_bruto. Un día que por sí solo la pase se parte en trozos (entre clips, nunca dentro de uno).
    Devuelve [(clips, primer_dia, ultimo_dia, parte o None)]."""
    tope = 3600 * float(cfg.get("horas_por_bruto", 3))
    duracion = lambda lista: sum(c.get("duracion") or 0 for c in lista if c.get("fichero_video"))
    por_dia = defaultdict(list)
    for c in cs:
        por_dia[_dia(fecha_media(c, mxf_por_ruta), cfg)].append(c)
    bloques, actual, dias = [], [], []
    for dia in sorted(por_dia, key=lambda d: (d is None, d or dt.date.min)):  # sin fecha, al final
        del_dia = sorted(por_dia[dia], key=lambda c: (fecha_media(c, mxf_por_ruta) or dt.datetime.min, c["nombre"]))
        if dia is None and actual:  # lo que no tiene fecha va siempre en su propio bruto
            bloques.append((actual, dias[0], dias[-1], None))
            actual, dias = [], []
        if duracion(del_dia) > tope:
            if actual:
                bloques.append((actual, dias[0], dias[-1], None))
                actual, dias = [], []
            trozos, trozo = [], []
            for c in del_dia:
                if trozo and duracion(trozo) + duracion([c]) > tope:
                    trozos.append(trozo)
                    trozo = []
                trozo.append(c)
            trozos.append(trozo)
            bloques += [(t, dia, dia, n if len(trozos) > 1 else None) for n, t in enumerate(trozos, 1)]
            continue
        if actual and duracion(actual) + duracion(del_dia) > tope:
            bloques.append((actual, dias[0], dias[-1], None))
            actual, dias = [], []
        actual += del_dia
        dias.append(dia)
    if actual:
        bloques.append((actual, dias[0], dias[-1], None))
    return bloques


def proyecto_principal(c):
    """Proyecto con el que un clip se ordena dentro de un bloque por días (el primero si está en varios)."""
    return sorted(c.get("proyectos") or [])[0] if c.get("proyectos") else None


def ordenar_por_proyecto(cs, mxf_por_ruta):
    """Clips agrupados por proyecto: los proyectos por orden de su primer clip, y dentro de cada uno por fecha.
    Los que no tienen proyecto, al final."""
    fecha = lambda c: fecha_media(c, mxf_por_ruta) or dt.datetime.min
    primero = {}
    for c in cs:
        p = proyecto_principal(c)
        primero[p] = min(primero.get(p, dt.datetime.max), fecha(c))
    return sorted(cs, key=lambda c: (proyecto_principal(c) is None, primero[proyecto_principal(c)], proyecto_principal(c) or "",
                                     fecha(c), c["nombre"], c.get("timecode") or ""))


def grupos_por_dias(clips, cruce, mxf_por_ruta, cfg):
    """agrupar=dias: todo el disco por días, sin separar proyectos; agencias y piezas aparte (también por días).
    Cada clip va una sola vez aunque esté en varios proyectos."""
    secuencias = {p["proyecto"]: {_normal(n) for n in p.get("secuencias_nombres", [])} for p in (cruce or {}).get("por_proyecto", [])}
    rx = re.compile(cfg["piezas_regex"]) if cfg.get("piezas_regex") else None
    familias = {"material": [], "agencias": [], "piezas": []}
    for c in con_video(clips):
        if c.get("render"):
            if cfg.get("renders_en_piezas", False):
                familias["piezas"].append(c)
            continue
        if not c.get("proyectos") and not cfg.get("incluir_huerfanos", True):
            continue
        secs = set().union(*(secuencias.get(p, set()) for p in c.get("proyectos") or [])) if c.get("proyectos") else set()
        if cfg.get("piezas_aparte", True) and es_pieza(c, secs, rx):
            familias["piezas"].append(c)
        elif excluido(c, cfg) or es_duplicado(c, cfg):
            continue
        elif cfg.get("agencias_aparte", True) and clasificar(c, cfg)[0] == "agencia":
            familias["agencias"].append(c)
        else:
            familias["material"].append(c)
    grupos = []
    for familia, cs in familias.items():
        prefijo = "" if familia == "material" else familia.upper() + "_"
        for bloque, desde, hasta, parte in bloques_por_dias(cs, mxf_por_ruta, cfg):
            if desde is None:
                fechas = "SIN_FECHA"
            else:
                fechas = desde.isoformat() + ("" if hasta == desde else "_a_" + hasta.isoformat())
            nombre = prefijo + fechas + ("_parte%d" % parte if parte else "")
            proyectos = Counter(p for c in bloque for p in (c.get("proyectos") or ["SIN_PROYECTO"]))
            dias = (desde.strftime("%d/%m/%Y") + ("" if hasta == desde else " – " + hasta.strftime("%d/%m/%Y"))) if desde else "sin fecha"
            lista = ", ".join("%s %d" % kv for kv in proyectos.most_common(4))
            if len(proyectos) > 4:
                lista += " y %d proyectos más" % (len(proyectos) - 4)
            etiqueta = {"material": "", "agencias": "agencias · ", "piezas": "piezas renderizadas · "}[familia]
            contenido = "%s%s%s · %d clips · %s" % (etiqueta, dias, " (parte %d)" % parte if parte else "", len(bloque), lista)
            grupos.append({"nombre": nombre, "base": nombre_seguro(nombre), "proyecto": ", ".join(sorted(proyectos)),
                           "tipo": familia, "contenido": contenido, "clips": ordenar_por_proyecto(bloque, mxf_por_ruta),
                           "ordenado": True})
    return grupos


def grupos_por_proyecto(clips, cruce, mxf_por_ruta, cfg):
    """agrupar=proyecto: cada proyecto por tarjetas, con agencias, importados y piezas aparte."""
    politica = cfg.get("clips_compartidos", "primero")
    huerfanos = cfg.get("incluir_huerfanos", True)
    por_proyecto = defaultdict(lambda: {"normales": [], "renders": []})
    for c in con_video(clips):
        render = bool(c.get("render"))
        if render and not cfg.get("renders_en_piezas", False):
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
    grupos = []
    for proyecto in sorted(por_proyecto):
        normales, renders = por_proyecto[proyecto]["normales"], por_proyecto[proyecto]["renders"]
        base = nombre_seguro(proyecto, 50)
        piezas = [c for c in normales if cfg.get("piezas_aparte", True) and es_pieza(c, secuencias.get(proyecto, set()), rx)]
        resto = [c for c in normales if c not in piezas and not excluido(c, cfg) and not es_duplicado(c, cfg)]
        agencias = [c for c in resto if cfg.get("agencias_aparte", True) and clasificar(c, cfg)[0] == "agencia"]
        resto = [c for c in resto if c not in agencias]
        importados = [c for c in resto if cfg.get("importados_aparte", True) and es_importado(c, cfg)]
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


OPCIONES_FIRMA = ("formato_salida", "modo", "convertir", "timecode_inicio", "pistas_audio", "enrutado_audio", "niveles_audio", "ganancia_db")


def opciones_bruto(cfg):
    """Las opciones que cambian el fichero exportado (van a la firma y a brutos_resultado.json)."""
    return {k: cfg.get(k, CONFIG_DEFECTO.get(k)) for k in OPCIONES_FIRMA}


def firma_bruto(cs, cfg):
    """Identifica un bruto: sus clips en orden y las opciones que cambian el fichero. Si cambia, no se reanuda: se rehace."""
    texto = "\n".join(str(c.get("clave")) for c in cs) + "\n" + json.dumps(opciones_bruto(cfg), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Opciones de exportación (panel)
# ---------------------------------------------------------------------------

NIVELES_AUDIO = ("sin_tocar", "ganancia", "r128")
PROHIBIDOS = '\\/:*?"<>|'
R128_OBJETIVO, R128_TOPE, GANANCIA_MAX = -23.0, 12.0, 20.0


def validar_opciones(cfg):
    """Errores legibles de las opciones del panel ([] si todo está bien). La ventana no deja exportar con errores."""
    errores = []
    tc = str(cfg.get("timecode_inicio", ""))
    m = re.fullmatch(r"(\d\d):(\d\d):(\d\d)[:;](\d\d)", tc)
    fps_max = 25 if cfg.get("formato_salida", "xdcam_hd50") == "xdcam_hd50" else 30
    if not m:
        errores.append("TC de inicio: escribe hh:mm:ss:ff, por ejemplo 00:00:00:00")
    else:
        h, mi, se, ff = map(int, m.groups())
        if h > 23 or mi > 59 or se > 59 or ff >= fps_max:
            errores.append("TC de inicio %s: horas 00-23, minutos y segundos 00-59, fotogramas 00-%02d" % (tc, fps_max - 1))
    pa = cfg.get("pistas_audio", "max")
    if pa != "max" and not (isinstance(pa, int) and 0 <= pa <= MAX_PISTAS):
        errores.append("Pistas de audio: 'max' o un número de 0 a %d" % MAX_PISTAS)
    enr = cfg.get("enrutado_audio") or []
    if not isinstance(enr, list) or len(enr) > MAX_PISTAS or any(not isinstance(x, int) or not 0 <= x <= MAX_PISTAS for x in enr):
        errores.append("Enrutado de audio: para cada pista, una pista del clip de 1 a %d o 0 (silencio)" % MAX_PISTAS)
    if cfg.get("niveles_audio", "sin_tocar") not in NIVELES_AUDIO:
        errores.append("Niveles de audio: sin_tocar, ganancia o r128")
    gan = cfg.get("ganancia_db") or []
    if not isinstance(gan, list) or len(gan) > MAX_PISTAS or any(not isinstance(x, (int, float)) or abs(x) > GANANCIA_MAX for x in gan):
        errores.append("Ganancia: de -%d a %d dB por pista" % (GANANCIA_MAX, GANANCIA_MAX))
    if any(ch in str(cfg.get("prefijo_fichero") or "") for ch in PROHIBIDOS):
        errores.append("Prefijo: no puede llevar %s" % " ".join(PROHIBIDOS))
    return errores


def validar_nombres(nombres):
    """Errores de los nombres de los brutos (vacíos, con caracteres que Windows no admite o repetidos)."""
    errores = []
    for n in nombres:
        if not str(n).strip():
            errores.append("Hay un bruto sin nombre")
        elif any(ch in n for ch in PROHIBIDOS) or n.strip(" .") != n:
            errores.append("%s: no puede llevar %s ni acabar en punto o espacio" % (n, " ".join(PROHIBIDOS)))
    repetidos = sorted(n for n, k in Counter(x.upper() for x in nombres if str(x).strip()).items() if k > 1)
    if repetidos:
        errores.append("Nombres repetidos: %s" % "; ".join(repetidos))
    return errores


def pistas_salida(n_max, cfg):
    """Pistas de audio del bruto: las que más tenga un clip (pistas_audio = max) o las fijadas, hasta 8. En XDCAM HD422
    solo valen 4 u 8: se redondea hacia arriba y las que sobran van en silencio (0 sigue siendo sin audio)."""
    pa = cfg.get("pistas_audio", "max")
    n = n_max if pa == "max" else int(pa)
    n = max(0, min(MAX_PISTAS, n))
    if n and cfg.get("formato_salida", "xdcam_hd50") == "xdcam_hd50":
        n = 4 if n <= 4 else 8
    return n


def ganancia_r128(lufs):
    """dB para llevar un clip a -23 LUFS, con tope de +/-12 dB (para no disparar el ruido de un clip casi mudo)."""
    if lufs is None:
        return 0
    return round(max(-R128_TOPE, min(R128_TOPE, R128_OBJETIVO - lufs)), 1)


def canales_clip(clip, mxf_por_ruta):
    """Cada canal de audio del clip, en orden: [{"ruta", "flujo", "canal", "de"}]. Primero el audio que vaya dentro del
    fichero de vídeo (un MOV o un MXF OP1a de cámara), luego los ficheros de audio; en cada fichero, sus flujos de audio, y
    en cada flujo, sus canales. En la media de Avid (un fichero mono por pista) es una pista por fichero, como siempre; un
    estéreo en un solo fichero da dos y un 5.1, seis."""
    salida = []
    video = clip.get("fichero_video")
    for ruta in [video] + [r for r in clip.get("ficheros") or [] if r != video]:
        flujos = [x for x in (mxf_por_ruta.get(ruta) or {}).get("pistas", []) if x.get("tipo") == "audio"] if ruta else []
        for i, flujo in enumerate(flujos):
            n = max(1, int(flujo.get("canales") or 1))
            salida += [{"ruta": ruta, "flujo": i, "canal": c, "de": n} for c in range(n)]
    return salida


def duracion_video(clip, mxf_por_ruta):
    """Duración del vídeo del clip (la del flujo de vídeo; si no consta, la del fichero o la del clip)."""
    m = mxf_por_ruta.get(clip.get("fichero_video")) or {}
    pv = next((x for x in m.get("pistas", []) if x.get("tipo") == "video"), {})
    return pv.get("duracion") or m.get("duracion") or clip.get("duracion")


def medir_audio(ffmpeg, ruta, flujo=0, canal=None):
    """Sonoridad integrada (LUFS) y pico (dBFS) de un canal de audio de un fichero (flujo de audio y canal dentro de él;
    sin canal, el flujo entero). None si no se puede."""
    filtro = ("pan=mono|c0=c%d," % canal if canal is not None else "") + "ebur128=peak=sample"
    cmd = [ffmpeg, "-nostats", "-hide_banner", "-i", str(ruta), "-map", "0:a:%d" % flujo, "-af", filtro, "-f", "null", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=600, **flags_subproceso())
    except Exception as e:
        LOG.warning("Medidor de audio: %s (%s)", ruta, e)
        return {"lufs": None, "pico": None}
    texto = r.stderr.decode("utf-8", "replace")
    resumen = texto[texto.rfind("Summary:"):] if "Summary:" in texto else ""
    lufs = re.search(r"I:\s*(-?[\d.]+|-inf) LUFS", resumen)
    pico = re.search(r"Peak:\s*(-?[\d.]+|-inf) dBFS", resumen)
    num = lambda m: None if not m else (-120.0 if m.group(1) == "-inf" else float(m.group(1)))
    return {"lufs": num(lufs), "pico": num(pico)}


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
    xdcam = cfg.get("formato_salida", "xdcam_hd50") == "xdcam_hd50"
    if xdcam:  # solo se copia lo que ya está en XDCAM HD 50 1080i50
        x = XDCAM_HD50
        copiable = uniforme and next(iter(firmas)) == (x["codec"], x["ancho"], x["alto"], x["fps"], x["pix_fmt"])
    else:
        copiable = uniforme and codec in CODECS_COPIABLES
    modo = cfg.get("modo", "auto")
    if modo == "auto":
        modo = "copia" if copiable else "convertir"
    elif modo == "copia" and not copiable:
        LOG.warning("Se pidió copia pero los clips no son uniformes o el códec no es copiable; se convierte.")
        modo = "convertir"
    if modo_forzado:
        modo = modo_forzado

    pistas = pistas_salida(max((c.get("num_audio", 0) for c in clips), default=0), cfg)

    mayoritaria = firmas.most_common(1)[0][0] if firmas else (None, 1920, 1080, "25/1", None)
    cv = cfg["convertir"]
    if xdcam:
        destino = {"ancho": XDCAM_HD50["ancho"], "alto": XDCAM_HD50["alto"], "fps_txt": "25", "xdcam": True}
        return {"modo": modo, "pistas_audio": pistas, "fps": 25.0, "destino": destino,
                "firmas": [{"codec": k[0], "resolucion": "%sx%s" % (k[1], k[2]), "fps": k[3], "clips": v} for k, v in firmas.items()],
                "clips_video": len(con_video), "clips_solo_audio": len(clips) - len(con_video)}
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


# ffmpeg 7.1 toma el color de los fotogramas y no de -color_primaries/-color_trc: sin esto, la cabecera MPEG-2 sale con
# primarias y transferencia "sin especificar" (lo detectó la ficha técnica en el Windows de GitHub)
COLOR_BT709 = "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"


def es_1080i25(pista_video):
    """¿El vídeo ya es 1920x1080 a 25 imágenes/s entrelazado con el campo superior primero (p. ej. el DNxHD 120
    1080i50 de los discos de China)? Entonces no hace falta separar campos ni volver a entrelazar."""
    pv = pista_video or {}
    return (pv.get("ancho"), pv.get("alto")) == (1920, 1080) and abs(fps_num(pv.get("fps")) - 25) < 0.01 and pv.get("field_order") == "tt"


TRANSFER_HDR = {"arib-std-b67": "HLG", "smpte2084": "PQ"}
_FILTROS = {}  # ffmpeg -> nombres de los filtros que trae (se mira una vez)


def tiene_filtro(ffmpeg, nombre):
    """¿Trae este ffmpeg el filtro? (zscale depende de cómo se compiló)."""
    if ffmpeg not in _FILTROS:
        try:
            r = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True, timeout=30, **flags_subproceso())
            _FILTROS[ffmpeg] = {linea.split()[1] for linea in r.stdout.decode("utf-8", "replace").splitlines() if len(linea.split()) > 2}
        except Exception:
            _FILTROS[ffmpeg] = set()
    return nombre in _FILTROS[ffmpeg]


def color_de(pista_video):
    """'HLG', 'PQ', 'BT.2020' (gama amplia sin HDR) o None (BT.709 / SD / sin datos)."""
    pv = pista_video or {}
    if pv.get("color_transfer") in TRANSFER_HDR:
        return TRANSFER_HDR[pv["color_transfer"]]
    return "BT.2020" if str(pv.get("color_primarias") or "").startswith("bt2020") else None


def filtro_color(color):
    """Conversión directa a BT.709 (zscale) del HDR y la gama amplia. Medido con una imagen de prueba pasada a HLG y a PQ y
    vuelta (oct. 2026): ~30 dB de PSNR frente a la original, cuando sin convertir sale ~14 dB (colores lavados o
    apagados) y con el mapeo de tonos clásico (hable) ~16 dB. HLG se diseñó para verse bien en una tele normal y se convierte
    tal cual; en PQ el blanco de referencia (203 nits, ITU-R BT.2408) pasa a ser el blanco: solo se recortan los brillos
    por encima de él (reflejos, sol)."""
    return "zscale=t=bt709:p=bt709:m=bt709:r=tv" + (":npl=203" if color == "PQ" else "") + ",format=yuv422p10le"


def detectar_campos(ffmpeg, ruta, segundos=8):
    """Si el fichero no dice si es entrelazado (field_order desconocido), lo mira ffmpeg con idet en los primeros
    segundos: 'tt', 'bb' o 'progressive'."""
    cmd = [ffmpeg, "-hide_banner", "-nostats", "-t", str(segundos), "-i", str(ruta), "-map", "0:v:0", "-vf", "idet", "-f", "null", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=300, **flags_subproceso())
        texto = r.stderr.decode("utf-8", "replace")
        # ffmpeg escribe dos resúmenes y el primero sale siempre a cero: vale el último
        m = re.findall(r"Multi frame detection:\s*TFF:\s*(\d+)\s*BFF:\s*(\d+)\s*Progressive:\s*(\d+)", texto)
    except Exception:
        m = None
    if not m:
        return "progressive"
    tff, bff, prog = (int(x) for x in m[-1])
    if max(tff, bff) <= prog:
        return "progressive"
    return "tt" if tff >= bff else "bb"


def matriz_entrada(pista_video):
    """Matriz de color con la que leer el vídeo: la que diga el fichero; si no dice nada, la de su norma (HD, desde 720
    líneas: BT.709; SD: BT.601). Sin esto ffmpeg supone BT.601 y el HD sin etiqueta salía con los colores algo
    corridos (medido: 24,7 dB de PSNR frente al original; con BT.709, idéntico)."""
    pv = pista_video or {}
    if pv.get("color_matriz") and pv["color_matriz"] not in ("unknown", "reserved"):
        return "auto"
    return "bt709" if (pv.get("alto") or 0) >= 720 else "bt601"


def entrada_de_color(pista_video):
    """Opciones de entrada del filtro scale para el vídeo sin etiqueta de color: la matriz (matriz_entrada) y SIEMPRE el
    rango con ella (legal, salvo que el fichero diga "pc", como algunos móviles). Con la matriz sola, ffmpeg 7 corre los
    colores igual que sin ella (medido en 6.1 y 7.0 contra zscale: 24,7 dB; con matriz y rango, idéntico)."""
    matriz = matriz_entrada(pista_video)
    if matriz == "auto":
        return ""
    rango = "pc" if (pista_video or {}).get("color_rango") in ("pc", "jpeg", "full") else "tv"
    return ":in_color_matrix=%s:in_range=%s" % (matriz, rango)


def filtros_xdcam(entrelazado, nativo=False, color=None, entrada=""):
    """A 1920x1080 1080i50 16:9 (25 imágenes entrelazadas, 50 campos) desde cualquier origen: desentrelaza a campos (59,94 o 50 imágenes/s), pasa a 50,
    encaja sin deformar según la relación de aspecto de pantalla (el SD 4:3 queda 1440x1080 con bandas negras),
    convierte el color de SD (BT.601) a HD (BT.709) y vuelve a entrelazar, primero el campo superior.
    nativo: el origen ya es 1080i50 con el campo superior primero (es_1080i25): solo se recodifica, sin tocar los
    campos (más rápido y sin la pérdida de separar y volver a juntar).
    color: 'HLG', 'PQ' o 'BT.2020' (color_de) para pasarlo a BT.709 con filtro_color; None si ya es BT.709 o SD. El giro de
    los vídeos de móvil (rotación en los metadatos) lo aplica ffmpeg solo al leerlos: el vertical sale derecho y con bandas."""
    if nativo and not color:
        return ["setfield=tff", "setsar=1", COLOR_BT709]
    f = ["bwdif=mode=send_field:deint=all"] if entrelazado else []
    if color:
        f.append(filtro_color(color))
    f += ["fps=50",
          "scale=w='trunc(min(1920,1080*dar)/2)*2':h='trunc(min(1080,1920/dar)/2)*2':flags=lanczos:out_color_matrix=bt709:out_range=tv"
          + ("" if color else entrada),
          "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black", "setsar=1", "interlace=scan=tff:lowpass=linear", "setfield=tff", COLOR_BT709]
    return f


def args_video(plan, mxf_video, cfg, convertir_color=True):
    """convertir_color=False si el ffmpeg no trae zscale: el HDR se convierte sin ese paso (y el clip lleva aviso)."""
    if plan["modo"] == "copia":
        return ["-c:v", "copy"]
    pv = next((p for p in mxf_video.get("pistas", []) if p["tipo"] == "video"), {})
    if plan["destino"].get("xdcam"):
        entrelazado = (pv.get("field_order") or "progressive") not in ("progressive", "unknown", None)
        color = color_de(pv) if convertir_color else None
        return ["-vf", ",".join(filtros_xdcam(entrelazado, es_1080i25(pv), color, entrada_de_color(pv))),
                "-c:v", "mpeg2video", "-pix_fmt", "yuv422p", "-profile:v", "0", "-level:v", "2",
                "-b:v", "50M", "-minrate", "50M", "-maxrate", "50M", "-bufsize", "17825792", "-rc_init_occupancy", "17825792",
                "-g", "12", "-bf", "2", "-flags", "+ilme+ildct", "-top", "1", "-dc", "10", "-intra_vlc", "1", "-non_linear_quant", "1",
                "-qmin", "1", "-qmax", "12", "-sc_threshold", "1000000000", "-aspect", "16:9",
                "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]
    cv = cfg["convertir"]
    d = plan["destino"]
    filtros = []
    des = cv.get("desentrelazar", "auto")
    entrelazado = (pv.get("field_order") or "progressive") not in ("progressive", "unknown", None)
    reescala = (pv.get("ancho"), pv.get("alto")) != (d["ancho"], d["alto"])
    if des == "si" or (des == "auto" and entrelazado and reescala):
        filtros.append("bwdif=mode=0")
    color = color_de(pv) if convertir_color else None
    if color:  # HDR o BT.2020: a BT.709, como en XDCAM
        filtros.append(filtro_color(color))
    w, h = int(d["ancho"]), int(d["alto"])  # encajado sin deformar (un vertical de móvil, un 4:3), como en XDCAM
    filtros += ["scale=w='trunc(min(%d,%d*dar)/2)*2':h='trunc(min(%d,%d/dar)/2)*2':flags=lanczos:out_color_matrix=%s%s"
                % (w, h, h, w, "bt709" if h >= 720 else "bt601", "" if color else entrada_de_color(pv)),
                "pad=%d:%d:(ow-iw)/2:(oh-ih)/2:black" % (w, h), "fps=%s" % d["fps_txt"], "setsar=1"]
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


CON_SONIDO_DB = -60  # pico por debajo: el canal está en silencio (o casi)


def picos_canales(ffmpeg, origen, audios):
    """Pico (dBFS) de cada canal de audio del clip ({número de canal 1..n: dB}; -120 si está en silencio, sin el canal si
    no se ha podido medir). Una pasada por fichero, con todos sus flujos a la vez (astats)."""
    picos = {}
    for ruta in dict.fromkeys(ch["ruta"] for ch in audios):
        flujos = sorted({ch["flujo"] for ch in audios if ch["ruta"] == ruta})
        grafo = ";".join("[0:a:%d]astats=measure_overall=none:measure_perchannel=Peak_level[o%d]" % (f, i) for i, f in enumerate(flujos))
        cmd = [ffmpeg, "-nostats", "-hide_banner", "-i", str(ruta_larga(Path(origen) / ruta)), "-filter_complex", grafo]
        cmd += sum((["-map", "[o%d]" % i] for i in range(len(flujos))), []) + ["-f", "null", "-"]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=600, **flags_subproceso())
        except Exception as e:
            LOG.warning("Picos de audio: %s (%s)", ruta, e)
            continue
        canal = None
        for linea in r.stderr.decode("utf-8", "replace").splitlines():
            m = re.search(r"Parsed_astats_(\d+) .*Channel: (\d+)", linea)
            if m:
                canal = (flujos[int(m.group(1))] if int(m.group(1)) < len(flujos) else None, int(m.group(2)) - 1)
                continue
            m = re.search(r"Peak level dB: (-?[\d.]+|-inf)", linea)
            if m and canal:
                for k, ch in enumerate(audios, 1):
                    if (ch["ruta"], ch["flujo"], ch["canal"]) == (ruta, canal[0], canal[1]):
                        picos[k] = -120.0 if m.group(1) == "-inf" else float(m.group(1))
                canal = None
    return picos


def fuentes_con_sonido(fuentes, picos, n_canales):
    """Si el clip trae más canales que pistas tiene el bruto (p. ej. 12 y el bruto 8), entran primero los que tienen
    sonido, en su orden, para que el bruto no se quede sin audio porque el sonido va en los canales 9 a 12. Si no se ha
    podido medir ninguno con sonido, se queda como estaba."""
    sonando = [k for k in sorted(picos) if picos[k] >= CON_SONIDO_DB]
    if not sonando:
        return fuentes
    resto = [k for k in range(1, n_canales + 1) if k not in sonando]
    return (sonando + resto)[:len(fuentes)]


# ---------------------------------------------------------------------------
# Segmentos y unión
# ---------------------------------------------------------------------------

def hacer_segmento(clip, idx, plan, origen, mxf_por_ruta, tmp, ffmpeg, cfg, nombre=None, info=None):
    """Convierte un clip (V + sus audios) en un MXF OP1a intermedio homogéneo, con las pistas de audio del bruto según
    enrutado_audio (las que el clip no tiene, en silencio) y los niveles pedidos. Cada pista del bruto es un canal del clip
    (canales_clip): un estéreo da dos pistas. Todo el audio dura lo mismo que el vídeo (el corto se rellena con silencio y
    el largo se corta), para que al unir los clips el sonido no se desplace. info (dict) recibe lo medido para el
    shotlist: lufs, pico, ganancia, aviso_audio. Devuelve (ruta | None, error, comando)."""
    info = {} if info is None else info
    salida = tmp / (nombre or "seg_%05d.mxf" % idx)
    video = ruta_larga(Path(origen) / clip["fichero_video"])
    audios = canales_clip(clip, mxf_por_ruta)  # "pista del clip" = canal
    n = plan["pistas_audio"]
    enrutado = list(cfg.get("enrutado_audio") or [])
    fuentes = [(enrutado[k] if k < len(enrutado) else k + 1) for k in range(n)]  # pista del clip (1..8) o 0 = silencio
    elegidas = []
    if not enrutado and 0 < n < len(audios):  # más canales que pistas: van los que suenan, no los n primeros sin más
        nuevas = fuentes_con_sonido(fuentes, picos_canales(ffmpeg, origen, audios), len(audios))
        if nuevas != fuentes:
            fuentes = nuevas
            elegidas = ["%d canales en el clip: entran los que tienen sonido (%s)" % (len(audios), ", ".join(map(str, fuentes)))]
    # Con el enrutado por defecto, que al clip le falten pistas es lo normal (se rellenan con silencio): solo se avisa
    # si el enrutado elegido a mano pide una pista que el clip no tiene, o si se pierden pistas del clip.
    avisos = ["pista %d del clip no existe (silencio)" % r for r in sorted({r for r in fuentes if r > len(audios)})] if enrutado else []
    if not audios:  # el clip no trae sonido: se avisa, aunque en el bruto vaya en silencio
        avisos.insert(0, "SIN AUDIO")
    avisos += elegidas
    perdidas = [r for r in range(1, len(audios) + 1) if r not in fuentes]
    if perdidas:
        avisos.append("no entran las pistas %s del clip" % ", ".join(map(str, perdidas)))
    usadas = sorted({r for r in fuentes if 0 < r <= len(audios)})

    niveles = cfg.get("niveles_audio", "sin_tocar")
    medidas = {}
    if usadas and (cfg.get("medidor_audio", True) or niveles == "r128"):
        for r in usadas:
            ch = audios[r - 1]
            medidas[r] = medir_audio(ffmpeg, ruta_larga(Path(origen) / ch["ruta"]), ch["flujo"], ch["canal"] if ch["de"] > 1 else None)
    ganancias = {}
    for k, r in enumerate(fuentes):
        if not 0 < r <= len(audios):
            continue
        if niveles == "ganancia":
            g = (cfg.get("ganancia_db") or [])[k] if k < len(cfg.get("ganancia_db") or []) else 0
        elif niveles == "r128":
            g = ganancia_r128((medidas.get(r) or {}).get("lufs"))
            if abs(g) >= R128_TOPE:
                avisos.append("R128 al tope en la pista %d (%+.0f dB)" % (k + 1, g))
        else:
            g = 0
        if g:
            ganancias[k] = float(g)
    if medidas:
        lufs = [m["lufs"] for m in medidas.values() if m.get("lufs") is not None]
        picos = [m["pico"] for m in medidas.values() if m.get("pico") is not None]
        info["lufs"] = max(lufs) if lufs else None
        info["pico"] = max(picos) if picos else None
        if info["pico"] is not None and info["pico"] >= -0.1:
            avisos.insert(0, "saturado")
        if info["lufs"] is None or info["lufs"] < -60:
            avisos.insert(0, "casi mudo")
    if ganancias:
        info["ganancia"] = " ".join("%d: %+.1f" % (k + 1, g) for k, g in sorted(ganancias.items()))
    if avisos:
        info["aviso_audio"] = "; ".join(avisos)

    cmd = [ffmpeg, "-v", "error", "-y", "-i", str(video)]
    entradas = {clip["fichero_video"]: 0}  # fichero -> número de entrada de ffmpeg
    for r in usadas:
        if audios[r - 1]["ruta"] not in entradas:
            entradas[audios[r - 1]["ruta"]] = len(entradas)
            cmd += ["-i", ruta_larga(Path(origen) / audios[r - 1]["ruta"])]
    grafo = []
    for k, r in enumerate(fuentes):
        if 0 < r <= len(audios):  # un canal concreto del clip, a mono y 48 kHz
            ch = audios[r - 1]
            cadena = "[%d:a:%d]pan=mono|c0=c%d,aresample=48000" % (entradas[ch["ruta"]], ch["flujo"], ch["canal"])
        else:
            cadena = "anullsrc=r=48000:cl=mono"
        if k in ganancias:
            cadena += ",volume=%.1fdB" % ganancias[k]
        grafo.append(cadena + ",apad[a%d]" % k)  # apad: el audio corto se alarga con silencio hasta el final del vídeo
    if grafo:
        cmd += ["-filter_complex", ";".join(grafo)]
    cmd += ["-map", "0:v:0"] + sum((["-map", "[a%d]" % k] for k in range(len(fuentes))), [])
    m_video = mxf_por_ruta.get(clip["fichero_video"], {})
    pv = next((x for x in m_video.get("pistas", []) if x.get("tipo") == "video"), {})
    convertir_color = True
    if plan["modo"] != "copia" and plan["destino"].get("xdcam"):
        if pv.get("field_order") in (None, "unknown"):  # el fichero no lo dice: se mira la imagen
            pv = dict(pv, field_order=detectar_campos(ffmpeg, video))
            m_video = dict(m_video, pistas=[pv] + [x for x in m_video.get("pistas", []) if x.get("tipo") != "video"])
            info["campos"] = pv["field_order"]
        if color_de(pv):
            convertir_color = tiene_filtro(ffmpeg, "zscale")
            info["color"] = color_de(pv)
            if not convertir_color:
                info["aviso_video"] = "%s sin ajustar a BT.709 (este ffmpeg no trae zscale)" % color_de(pv)
    cmd += args_video(plan, m_video, cfg, convertir_color)
    if n:
        cmd += ["-c:a", "pcm_s24le", "-ar", "48000", "-ac", "1"]
    dur = duracion_video(clip, mxf_por_ruta)
    cmd += ["-t", "%.3f" % dur] if dur else ["-shortest"]  # todo, también el audio largo, a la duración del vídeo
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
    creado = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z")  # si no, el MXF sale sin fecha de creación
    cmd = [ffmpeg, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lista), "-map", "0", "-c", "copy",
           "-timecode", timecode_inicio(cfg, plan["fps"]), "-metadata", "creation_time=" + creado, "-f", "mxf", str(salida)]
    ok, err = ejecutar_ffmpeg(cmd, timeout_union(duracion_total))
    lista.unlink(missing_ok=True)
    return ok, err, cmd


def buscar_bmx(ffprobe):
    """bmxtranswrap (bmx de la BBC, licencia BSD): junto a ffprobe (bin/ del programa) o en el PATH; None si no está."""
    exe = "bmxtranswrap.exe" if os.name == "nt" else "bmxtranswrap"
    junto = Path(str(ffprobe or "")).parent / exe
    return str(junto) if ffprobe and junto.exists() else shutil.which("bmxtranswrap")


def pistas_ffprobe(ffprobe, ruta):
    """Tipos de las pistas del fichero, en orden ("video", "audio", ...); [] si ffprobe no lo lee."""
    cmd = [ffprobe, "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(ruta)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, **flags_subproceso())
        return [x.strip() for x in r.stdout.splitlines() if x.strip()]
    except Exception:
        return []


def unir_rdd9(bmx, segmentos, salida, cfg, plan, duracion_total, ffprobe):
    """Une los segmentos con bmxtranswrap -t rdd9: el bruto sale empaquetado según SMPTE RDD 9 en la misma pasada que
    la unión con ffmpeg (bmx lee los MXF en orden y escribe uno, sin recodificar). bmx en Windows abre los ficheros con
    la página de códigos del sistema ("北京" le llega como "??"): trabaja en la carpeta de los segmentos, que es su
    directorio actual, con sus nombres (seg_00001.mxf...), y el resultado se mueve al nombre del bruto. bmx deja fuera
    sin error una pista que no puede unir (p. ej. un audio más corto): se comprueba que el bruto tiene las mismas pistas
    que el primer segmento. bmx: lista con el ejecutable (y lo que vaya delante, para las pruebas).
    Devuelve (ok, error, comando); si no sale bien, no deja nada."""
    carpeta = Path(segmentos[0]).parent
    nombres = [Path(s).name for s in segmentos]
    hecho = carpeta / "union_rdd9.mxf"
    cmd = [*bmx, "-t", "rdd9", "--no-reorder", "-y", timecode_inicio(cfg, plan["fps"]), "-o", hecho.name, *nombres]
    if any(Path(s).parent != carpeta for s in segmentos) or not all(n.isascii() for n in nombres):
        return False, "segmentos fuera de la carpeta de trabajo o con nombres no ASCII", cmd
    ok, err = ejecutar_ffmpeg(cmd, timeout_union(duracion_total), cwd=carpeta)
    if ok and not hecho.exists():
        ok, err = False, "bmxtranswrap no ha escrito el fichero. " + err
    if ok:
        esperadas, salen = pistas_ffprobe(ffprobe, segmentos[0]), pistas_ffprobe(ffprobe, hecho)
        if not salen or salen != esperadas:
            ok, err = False, "pistas %s, esperadas %s. %s" % (salen, esperadas, err)
    if not ok:
        hecho.unlink(missing_ok=True)
        return False, err, cmd
    os.replace(hecho, salida)
    return True, err, cmd


# ---------------------------------------------------------------------------
# Shotlist y hoja de contactos
# ---------------------------------------------------------------------------

COLUMNAS_SHOTLIST = [  # (cabecera, clave de la fila, ancho)
    ("Nº", "n", 5), ("Proyecto", "proyecto", 24), ("Clip", "clip", 34), ("TC inicio (bruto)", "tc_in", 14), ("TC fin (bruto)", "tc_out", 14),
    ("Duración", "duracion", 11), ("TC original", "tc_original", 14), ("Fecha fichero", "fecha", 18), ("Fecha grabación", "fecha_grabacion", 18),
    ("Montaje", "montaje", 14), ("Códec origen", "codec", 12), ("Resolución origen", "resolucion", 14), ("Pistas audio", "pistas", 8),
    ("Sonoridad (LUFS)", "lufs", 10), ("Pico (dBFS)", "pico", 10), ("Ganancia (dB)", "ganancia", 10), ("Aviso audio", "aviso_audio", 26),
    ("Color origen", "color", 10), ("Aviso vídeo", "aviso_video", 26), ("Aviso fecha", "aviso_fecha", 30),
    ("Estado", "estado", 14), ("Ficheros origen", "ficheros", 60), ("Fichero importado (según bin)", "fichero_origen", 50),
    ("Disco de origen (según bin)", "volumenes", 24), ("Origen", "origen_texto", 24), ("Por qué", "origen_motivo", 40),
    ("Origen (reglas del bin)", "origen", 20), ("Referencia agencia", "ref_agencia", 18), ("Slug", "slug", 40)]


def escribir_shotlist(ruta, proyecto, filas, quitados=None):
    """Shotlist del bruto (xlsx; csv si falta openpyxl). quitados: clips del proyecto quitados en la pantalla de montaje,
    en una hoja aparte para que conste que no entraron a propósito."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
        from openpyxl.utils import get_column_letter
    except ImportError:
        import csv
        with open(ruta.with_suffix(".csv"), "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow([c for c, _, _ in COLUMNAS_SHOTLIST])
            w.writerows([[fila.get(k) for _, k, _ in COLUMNAS_SHOTLIST] for fila in filas])
        return ruta.with_suffix(".csv")
    wb = Workbook()
    ws = wb.active
    ws.title = "Shotlist"
    ws.append([c for c, _, _ in COLUMNAS_SHOTLIST])
    for c in ws[1]:
        c.font = Font(bold=True)
    anterior = None
    for fila in filas:
        ws.append([fila.get(k) for _, k, _ in COLUMNAS_SHOTLIST])
        if fila.get("proyecto_principal") != anterior:  # donde empieza otro proyecto, en negrita
            ws.cell(row=ws.max_row, column=2).font = Font(bold=True)
            anterior = fila.get("proyecto_principal")
    for i, (_, _, a) in enumerate(COLUMNAS_SHOTLIST, 1):
        ws.column_dimensions[get_column_letter(i)].width = a
    ws.freeze_panes = "A2"
    if quitados:
        wq = wb.create_sheet("Quitados")
        wq.append(["Clip", "TC original", "Duración (s)", "Ficheros origen"])
        for c in wq[1]:
            c.font = Font(bold=True)
        for c in quitados:
            wq.append([c.get("nombre"), c.get("timecode"), c.get("duracion"), "; ".join(c.get("ficheros") or [])])
        for col, a in zip("ABCD", (34, 14, 12, 60)):
            wq.column_dimensions[col].width = a
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
    otros = sorted(c.get("proyectos") or [])[1:]
    texto = (proyecto_principal(c) or "SIN_PROYECTO") + (" (también en %s)" % ", ".join(otros) if otros else "")
    return {"n": i, "proyecto": texto, "proyecto_principal": proyecto_principal(c) or "SIN_PROYECTO", "clip": c["nombre"], "tc_original": c.get("timecode"),
            "fecha": mxf_por_ruta.get(c.get("fichero_video") or c["ficheros"][0], {}).get("modificado"),
            "fecha_grabacion": c.get("fecha_grabacion"), "aviso_fecha": c.get("aviso_fecha"),
            "aviso_audio": "SIN AUDIO" if c.get("fichero_video") and not c.get("num_audio") else None,
            "codec": c.get("codec_video"), "resolucion": c.get("resolucion"), "pistas": c.get("num_audio"),
            "ficheros": "; ".join(c["ficheros"]), "miniatura": c.get("miniatura"), "tc_in": None, "tc_out": None, "duracion": None,
            "fichero_origen": c.get("fichero_origen"), "volumenes": ", ".join(c.get("volumenes") or []),
            "origen": c.get("origen"), "origen_texto": c.get("origen_texto"), "origen_motivo": c.get("origen_motivo"), "ref_agencia": (c.get("origen_detalle") or {}).get("id"), "slug": (c.get("origen_detalle") or {}).get("slug")}


def exportar_proyecto(proyecto, cs, mxf_por_ruta, origen, destino, ffmpeg, ffprobe, progreso, cfg, miniaturas_dir=None,
                      etiqueta="", modo_forzado=None, timeouts=None, motivo_reintento=None, base_fichero=None, ordenado=False,
                      movidos=None, quitados=None, evento=None):
    """Exporta el bruto de un proyecto. Devuelve el resultado (dict) para brutos_resultado.json.

    En modo copia, si un segmento o la unión fallan por algo que no sea tiempo agotado, se repite
    el proyecto entero una sola vez en modo convertir (modo_forzado). Para no convertir todo por
    un fichero dañado, ante un segmento fallido se prueba antes ese mismo clip en convertir: si
    también falla, el problema es el fichero y el proyecto sigue en copia sin ese clip.
    timeouts: claves de clips que ya agotaron el tiempo (no se reintentan).
    proyecto es el nombre del bruto (p. ej. "MEXICO_T02_2021-09-15"); base_fichero, el nombre de
    fichero sin prefijo (por defecto, el nombre del bruto saneado). movidos (claves movidas a mano) y quitados (clips
    quitados del proyecto) vienen de la pantalla de montaje y solo van al shotlist. evento(dict) recibe el avance:
    {"bruto", "estado": empezando | convirtiendo | uniendo | comprobando | terminado, "clip", "total"}; el de
    terminado lleva además "resultado" (el dict de brutos_resultado.json)."""
    movidos = set(movidos or ())
    avisar = (lambda estado, **kw: evento(dict(kw, bruto=proyecto, estado=estado))) if evento else (lambda estado, **kw: None)
    base_fichero = base_fichero or nombre_seguro(proyecto)
    destino = Path(destino)
    timeouts = set(timeouts or ())
    if not ordenado:  # en agrupar=dias el reparto ya los da agrupados por proyecto
        cs = sorted(cs, key=lambda c: orden_cronologico(c, mxf_por_ruta))
    plan = plan_proyecto(cs, mxf_por_ruta, cfg, modo_forzado)
    pref = cfg.get("prefijo_fichero", "BRUTO_")
    res = {"proyecto": proyecto, "modo": plan["modo"], "reintento": modo_forzado is not None, "pistas_audio": plan["pistas_audio"],
           "clips": len(cs), "incluidos": 0, "fallidos": 0, "solo_audio": plan["clips_solo_audio"], "fichero": None, "bytes": 0,
           "segundos": 0.0, "estado": "", "shotlist": None, "contactos": None, "errores": [], "firma": firma_bruto(cs, cfg),
           "opciones": opciones_bruto(cfg)}
    if motivo_reintento:
        res["motivo_reintento"] = motivo_reintento
    progreso("Bruto %s: %s (%d clips, modo %s%s)" % (etiqueta, proyecto, len(cs), plan["modo"], ", reintento" if modo_forzado else ""))
    avisar("empezando", total=len(cs))

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
        avisar("terminado", resultado=res)
        return res

    def reintentar(motivo):
        LOG.warning("%s: falla en modo copia (%s); se repite el proyecto convirtiendo", proyecto, motivo)
        progreso("  %s: no se puede copiar sin recomprimir; se repite convirtiendo" % proyecto)
        shutil.rmtree(tmp, ignore_errors=True)
        return exportar_proyecto(proyecto, cs, mxf_por_ruta, origen, destino, ffmpeg, ffprobe, progreso, cfg, miniaturas_dir,
                                 etiqueta, modo_forzado="convertir", timeouts=timeouts, motivo_reintento=motivo, base_fichero=base_fichero,
                                 ordenado=True, movidos=movidos, quitados=quitados, evento=evento)

    puede_reintentar = plan["modo"] == "copia" and modo_forzado is None
    tmp = destino / ("_tmp_" + base_fichero)
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    salida = destino / ("%s%s.mxf" % (pref, base_fichero))
    uniendo = False
    pool = None
    try:
        # Convirtiendo, varios clips a la vez (cada segmento es independiente; se unen después en orden). Copiando,
        # de uno en uno: es rápido y el reintento copia -> convertir decide clip a clip.
        futuros, infos = {}, {}
        n_pool = clips_a_la_vez(cfg) if plan["modo"] == "convertir" else 1
        if n_pool > 1:
            pool = ThreadPoolExecutor(n_pool)
            futuros = {i: pool.submit(hacer_segmento, c, i, plan, origen, mxf_por_ruta, tmp, ffmpeg, cfg, None, infos.setdefault(i, {}))
                       for i, c in enumerate(cs, 1) if c.get("fichero_video") and c.get("clave") not in timeouts}
        segmentos, filas = [], []
        frames = 0  # se acumulan frames, no segundos, para no arrastrar redondeos en el shotlist
        inicio = tc_a_frames(timecode_inicio(cfg, plan["fps"]), plan["fps"])
        for i, c in enumerate(cs, 1):
            fila = _fila_clip(i, proyecto, c, mxf_por_ruta)
            fila["montaje"] = "movido a mano" if c.get("clave") in movidos else "automático"
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
            avisar("convirtiendo", clip=i, total=len(cs))
            seg, err, cmd = futuros[i].result() if i in futuros else hacer_segmento(c, i, plan, origen, mxf_por_ruta, tmp, ffmpeg, cfg,
                                                                                    None, infos.setdefault(i, {}))
            fila.update(infos.get(i, {}))
            if not seg:
                comprobar_origen(origen)  # si es que se ha ido el disco, no se sigue haciendo un bruto con huecos
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

        if pool:
            pool.shutdown(wait=True)
        acumulado = frames / plan["fps"]
        if segmentos:
            progreso("  %s: uniendo %d clips..." % (proyecto, len(segmentos)))
            avisar("uniendo")
            uniendo = True
            ok = False
            bmx = buscar_bmx(ffprobe) if plan["destino"].get("xdcam") and cfg.get("rdd9", True) else None
            if bmx:
                ok, err, cmd = unir_rdd9([bmx], segmentos, salida, cfg, plan, acumulado, ffprobe)
                res["rdd9"] = ok
                if not ok:  # se une con ffmpeg; la ficha técnica lo marca no conforme
                    LOG.error("%s: bmx no ha podido unir en RDD 9 (%s); se une con ffmpeg, en MXF OP1a", proyecto, (err or "")[-300:])
                    res["errores"].append(detalle_error(None, "RDD 9", cmd, err))
            elif plan["destino"].get("xdcam") and cfg.get("rdd9", True):
                LOG.warning("%s: no está bmxtranswrap; el bruto sale en MXF OP1a de ffmpeg, sin RDD 9", proyecto)
            if not ok:
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
                if res["estado"] == "OK":
                    avisar("comprobando")
                    acabado_profesional(res, salida, plan, cfg, ffprobe, inicio, acumulado, progreso, proyecto)
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
        if pool:  # PARAR está activado: los ffmpeg en marcha mueren en un segundo y los pendientes no arrancan
            pool.shutdown(wait=True, cancel_futures=True)
        shutil.rmtree(tmp, ignore_errors=True)
        if uniendo:
            salida.unlink(missing_ok=True)
            salida.with_name(salida.stem + "_ficha.txt").unlink(missing_ok=True)
        raise

    base = destino / ("%s%s" % (pref, base_fichero))
    try:
        res["shotlist"] = escribir_shotlist(base.with_name(base.name + "_shotlist.xlsx"), proyecto, filas, quitados).name
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
    avisar("terminado", resultado=res)
    return res


def comprobar_parar():
    if PARAR.is_set():
        raise Detenido()


def acabado_profesional(res, salida, plan, cfg, ffprobe, inicio, segundos, progreso, proyecto):
    """Tras unir: TC interno del vídeo igual al del fichero (solo lo convertido), ficha técnica XDCAM (con la envoltura
    RDD 9) y MD5.
    Un bruto no conforme queda en AVISO. Actualiza res (tc_gop, conforme, ficha, md5)."""
    import mxf_util
    xdcam = bool(plan["destino"].get("xdcam"))
    if res.get("rdd9") is False and not cfg.get("ficha_tecnica", True):  # con ficha, la ficha lo marca no conforme
        res["estado"] = "AVISO: sin RDD 9 (bmx no ha podido unir los clips)"
    if xdcam and plan["modo"] == "convertir":
        try:
            res["tc_gop"] = mxf_util.parchear_tc_gop(salida, inicio, plan["fps"])
        except Exception as e:
            LOG.error("%s: no se pudo reescribir el TC interno (%s)", proyecto, e)
            res["errores"].append(detalle_error(None, "TC interno", [], str(e)))
    if xdcam and cfg.get("ficha_tecnica", True):
        progreso("  %s: comprobando el formato..." % proyecto)
        esperado = {"pistas": plan["pistas_audio"], "tc": fmt_tc(inicio, plan["fps"]), "duracion": segundos, "tc_gop": bool(res.get("tc_gop")),
                    "rdd9": res.get("rdd9")}
        try:
            ok, texto = mxf_util.ficha_tecnica(ffprobe, salida, esperado, flags_subproceso())
        except Exception as e:
            ok, texto = False, "No se pudo hacer la ficha técnica: %s\n" % e
        ficha = salida.with_name(salida.stem + "_ficha.txt")
        ficha.write_text(texto, encoding="utf-8")
        res.update(conforme=ok, ficha=ficha.name)
        if not ok:
            fallos = [l[7:] for l in texto.splitlines() if l.startswith("FALLO")]
            res["estado"] = "AVISO: no conforme con XDCAM HD422 (%s)" % "; ".join(fallos)[:300]
            LOG.warning("%s: %s", proyecto, res["estado"])
    if cfg.get("md5", True):
        progreso("  %s: calculando MD5..." % proyecto)
        res["md5"] = mxf_util.escribir_md5(salida, comprobar_parar)


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


def de_proyectos(c, elegidos):
    """¿El clip es de alguno de los proyectos elegidos? Los que no están en ningún bin cuentan como SIN_PROYECTO."""
    return bool(set(c.get("proyectos_render" if c.get("render") else "proyectos") or ["SIN_PROYECTO"]) & set(elegidos))


FICHERO_CORTES = "cortes.json"


def cargar_cortes(carpeta_brutos):
    """Cortes elegidos en la pantalla de cortes: {proyecto: [claves de los clips donde empieza cada bruto]}."""
    try:
        datos = json.loads((Path(carpeta_brutos) / FICHERO_CORTES).read_text(encoding="utf-8"))
        return {k: list(v) for k, v in (datos.get("proyectos") or {}).items()}
    except FileNotFoundError:
        return {}
    except Exception as e:
        LOG.warning("%s ilegible (%s): se corta automáticamente", FICHERO_CORTES, e)
        return {}


def guardar_cortes(carpeta_brutos, cortes):
    Path(carpeta_brutos).mkdir(parents=True, exist_ok=True)
    datos = {"_comentario": "Dónde empieza cada bruto, elegido en 'Revisar cortes'. Borra un proyecto (o el fichero) para volver al corte automático.",
             "proyectos": {k: v for k, v in sorted(cortes.items()) if v}}
    (Path(carpeta_brutos) / FICHERO_CORTES).write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")


def grupos_con_montaje(clips, cruce, mxf, mxf_por_ruta, cfg, destino, progreso, montaje=None):
    """Reparto final: el automático, salvo en los proyectos tocados en la pantalla de montaje (montaje.json de la
    carpeta de brutos, o el montaje que se pase), cuyos brutos son exactamente los del montaje."""
    import montaje as mj
    agrupar = cfg.get("agrupar", "proyecto_mes")
    if agrupar != "proyecto_mes":
        if montaje is not None or (Path(destino) / mj.FICHERO).exists():
            LOG.warning("Hay montaje pero agrupar = %s: el montaje solo se aplica con proyecto_mes; se ignora", agrupar)
            progreso("Aviso: el montaje solo se aplica con agrupar = proyecto_mes; se ignora.")
        return grupos_de_exportacion(clips, cruce, mxf_por_ruta, cfg)
    if montaje is None:
        montaje = mj.Montaje.desde_analisis(clips, cruce, mxf, cfg, destino)
    for a in montaje.avisos:
        LOG.warning("Montaje: %s", a)
        progreso("Aviso del montaje: %s" % a)
    tocados = set(montaje.estados)
    if tocados:
        LOG.info("Montaje a mano en %d proyectos (%s): %s", len(tocados), mj.FICHERO, "; ".join(sorted(tocados)))
    grupos = [g for g in grupos_de_exportacion(clips, cruce, mxf_por_ruta, cfg) if g["proyecto"] not in tocados] + montaje.grupos()
    return sorted(grupos, key=lambda g: g["proyecto"])  # estable: dentro de cada proyecto, el orden del montaje


def exportar_todo(clips, cruce, mxf, origen, destino, ffmpeg, ffprobe, progreso, cfg, miniaturas_dir=None, proyectos=None, cortes=None,
                  montaje=None, solo_brutos=None, evento=None):
    """Devuelve una lista con el resultado por proyecto (para el informe). brutos_resultado.json se
    reescribe tras cada proyecto, y al relanzar sobre la misma carpeta se saltan los ya terminados.
    Si se pulsa Detener, devuelve los proyectos terminados hasta ese momento (PARAR queda activado).
    proyectos: nombres de los proyectos elegidos (None = todos; "SIN_PROYECTO" = clips sin bin). Los brutos de
    proyectos no elegidos que ya estaban en brutos_resultado.json se conservan, para hacer el disco en varias veces.
    cortes: cortes a mano al estilo antiguo (pruebas); si no se pasan, manda el montaje (montaje.json de la carpeta
    de brutos, o el que se pase en montaje; un cortes.json antiguo se convierte).
    solo_brutos: nombres de los brutos marcados en la cola de la ventana (None = todos, salvo los de piezas si
    exportar_piezas es false). evento: función que recibe el avance de cada bruto (ver exportar_proyecto)."""
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

    cfg = dict(cfg, decisiones_origen=leer_decisiones_origen(destino))
    anotar_origen(clips, cfg)
    dudosos = [c for c in clips if c.get("origen_clase") == "dudoso"]
    if dudosos:
        LOG.info("Origen dudoso (%s): %d clips, p. ej. %s",
                 "fuera de los brutos" if cfg.get("solo_material_de_camara", True) else "entran marcados 'para revisar'", len(dudosos),
                 "; ".join("%s (%s)" % (c["nombre"], c["origen_motivo"]) for c in dudosos[:5]))
    if cortes:
        LOG.info("Cortes elegidos a mano en %d proyectos", len(cortes))
    if proyectos is not None and cfg.get("agrupar", "proyecto_mes") == "dias":  # los bloques por días mezclan proyectos
        clips = [c for c in clips if de_proyectos(c, set(proyectos))]
    if cortes is not None:
        grupos = grupos_de_exportacion(clips, cruce, mxf_por_ruta, cfg, cortes)
    else:
        grupos = grupos_con_montaje(clips, cruce, mxf, mxf_por_ruta, cfg, destino, progreso, montaje)
    if proyectos is not None:
        elegidos = set(proyectos)
        grupos = [g for g in grupos if cfg.get("agrupar", "proyecto_mes") == "dias" or g["proyecto"] in elegidos]
        LOG.info("Proyectos elegidos (%d): %s", len(elegidos), "; ".join(sorted(elegidos)))
    if solo_brutos is not None:
        grupos = [g for g in grupos if g["nombre"] in set(solo_brutos)]
    elif not cfg.get("exportar_piezas", False):
        piezas = [g["nombre"] for g in grupos if g["tipo"] == "piezas"]
        if piezas:
            LOG.info("Brutos de piezas que no se exportan (exportar_piezas = false): %s", "; ".join(piezas))
        grupos = [g for g in grupos if g["tipo"] != "piezas"]
    de_esta_vez = {g["nombre"] for g in grupos}
    proyectos_de_esta_vez = {g["proyecto"] for g in grupos}
    # De otras ejecuciones: se conservan los de otros proyectos. Los de estos proyectos que ya no salen (se movieron
    # los cortes o cambió el análisis) se olvidan; su .mxf se queda en la carpeta y se avisa en el log.
    otros = [r for p, r in previos.items() if p not in de_esta_vez and r.get("proyecto_avid") not in proyectos_de_esta_vez]
    for p, r in previos.items():
        if p not in de_esta_vez and r.get("proyecto_avid") in proyectos_de_esta_vez and r.get("fichero"):
            LOG.warning("%s es de una exportación anterior con otros cortes; ya no corresponde y se puede borrar", r["fichero"])
            progreso("Aviso: %s es de una exportación anterior con otros cortes; se puede borrar." % r["fichero"])

    resultados = []
    LOG.info("Brutos previstos: %d (%s)", len(grupos), "; ".join("%s: %d clips" % (g["nombre"], len(g["clips"])) for g in grupos))
    for n_proy, g in enumerate(grupos, 1):
        proyecto, cs = g["nombre"], g["clips"]
        etiqueta = "%d/%d" % (n_proy, len(grupos))
        try:
            if PARAR.is_set():
                raise Detenido()
            comprobar_origen(origen)
            if ya_exportado(previos.get(proyecto), len(cs), destino, firma_bruto(cs, cfg)):
                res = dict(previos[proyecto], reanudado=True)
                if evento:
                    evento({"bruto": proyecto, "estado": "reanudado", "resultado": res})
                progreso("Bruto %s: %s ya estaba exportado, se salta (reanudado)" % (etiqueta, proyecto))
                LOG.info("Bruto %s: reanudado (ya exportado en una ejecución anterior)", proyecto)
            else:
                res = exportar_proyecto(proyecto, cs, mxf_por_ruta, origen, destino, ffmpeg, ffprobe, progreso, cfg, miniaturas_dir,
                                        etiqueta, base_fichero=g["base"], ordenado=g.get("ordenado", False),
                                        movidos=g.get("movidos"), quitados=g.get("quitados"), evento=evento)
                res["reanudado"] = False
            res.update({"proyecto_avid": g["proyecto"], "tipo": g["tipo"], "contenido": g["contenido"]})
        except Detenido as e:
            faltan = [x["nombre"] for x in grupos[n_proy - 1:]]
            perdido = isinstance(e, OrigenPerdido)
            progreso("%s Terminados: %d de %d brutos. Al relanzar con las mismas carpetas sigue por %s."
                     % (e if perdido else "Exportación detenida.", len(resultados), len(grupos), faltan[0]))
            LOG.warning("Exportación %s en %s; quedan %d proyectos", "parada: se ha perdido el disco de origen" if perdido
                        else "detenida por el usuario", faltan[0], len(faltan))
            # los de una ejecución anterior a los que no se ha llegado se conservan para poder reanudarlos
            with open(ruta_json, "w", encoding="utf-8") as f:
                json.dump(otros + resultados + [previos[p] for p in faltan if p in previos], f, ensure_ascii=False, indent=1)
            if perdido:  # que la ventana lo diga claro; lo hecho ya está guardado
                raise
            return resultados
        resultados.append(res)
        with open(ruta_json, "w", encoding="utf-8") as f:  # tras cada proyecto: un corte no pierde lo ya hecho
            json.dump(otros + resultados, f, ensure_ascii=False, indent=1)
    return resultados
