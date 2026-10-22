# -*- coding: utf-8 -*-
"""
ACABADO PROFESIONAL DE LOS BRUTOS
=================================

- Timecode de las cabeceras GOP del MPEG-2 igual al del fichero (parchear_tc_gop). Al
  unir los segmentos con -c copy cada clip conserva su TC interno desde 00:00:00:00;
  aquí se reescribe en su sitio, sin cambiar el tamaño del fichero. Solo se tocan
  los elementos de imagen del MXF: un 00 00 01 B8 dentro del audio no se toca.
- Ficha técnica: comprueba sobre el fichero exportado (ffprobe) que es XDCAM HD422
  50 Mb/s 1080i25 16:9 con las pistas y el TC pedidos.
- MD5 del bruto, en el formato estándar de md5sum (hash *fichero).
"""

import hashlib
import json
import struct
import subprocess
from pathlib import Path

# Elemento de imagen del contenedor genérico MXF (SMPTE 379M): 06.0e.2b.34.01.02.01.01.0d.01.03.01, tipo 0x15
CLAVE_ESENCIA = bytes.fromhex("060e2b34010201010d010301")
TIPO_IMAGEN = 0x15
INICIO_GOP = b"\x00\x00\x01\xb8"
OP1A = "0d010201.0101"  # patrón operacional 1a en el UL que da ffprobe
BLOQUE = 8 * 1024 * 1024


def _klv(f):
    """Itera (posición del valor, clave, longitud) de todos los KLV del fichero, sin leer los valores."""
    while True:
        cab = f.read(17)
        if len(cab) < 17:
            return
        clave, n = cab[:16], cab[16]
        if n & 0x80:
            extra = f.read(n & 0x7F)
            n = int.from_bytes(extra, "big")
        pos = f.tell()
        yield pos, clave, n
        f.seek(pos + n)


def _tc_bits(frames, fps):
    base = int(round(fps))
    ff, s = frames % base, frames // base
    h, m, s = (s // 3600) % 24, (s // 60) % 60, s % 60
    return (h << 19) | (m << 13) | (1 << 12) | (s << 6) | ff  # drop_frame 0 · horas · minutos · marker · segundos · cuadros


def _tc_texto(bits):
    return "%02d:%02d:%02d:%02d" % ((bits >> 19) & 0x1F, (bits >> 13) & 0x3F, (bits >> 6) & 0x3F, bits & 0x3F)


def _gops(f, maximo=None):
    """(posición de los 4 bytes del TC, imágenes anteriores) de cada cabecera GOP, en orden."""
    imagenes = 0
    for pos, clave, n in _klv(f):
        if clave[:12] != CLAVE_ESENCIA or clave[12] != TIPO_IMAGEN:
            continue
        f.seek(pos)
        cabeza = f.read(min(n, 4096))
        i = cabeza.find(INICIO_GOP)
        if i >= 0 and i + 8 <= len(cabeza):
            yield pos + i + 4, imagenes
            if maximo is not None:
                maximo -= 1
                if maximo <= 0:
                    return
        imagenes += 1  # elemento de imagen con envoltura por cuadro: una imagen por elemento
        f.seek(pos + n)


def parchear_tc_gop(ruta, frames_inicio, fps=25):
    """Reescribe el TC de cada cabecera GOP (25 bits) para que empiece en frames_inicio y siga a la imagen. Conserva
    closed_gop y broken_link. Devuelve cuántos GOP se han parcheado."""
    cambios = []
    with open(ruta, "rb") as f:
        for pos_tc, antes in _gops(f):
            f.seek(pos_tc)
            viejo = struct.unpack(">I", f.read(4))[0]
            cambios.append((pos_tc, (_tc_bits(frames_inicio + antes, fps) << 7) | (viejo & 0x7F)))
    with open(ruta, "r+b") as f:
        for pos_tc, nuevo in cambios:
            f.seek(pos_tc)
            f.write(struct.pack(">I", nuevo))
    return len(cambios)


def leer_tc_gop(ruta, maximo=3):
    """TC de las primeras cabeceras GOP del vídeo."""
    salida = []
    with open(ruta, "rb") as f:
        for pos_tc, _ in _gops(f, maximo):
            f.seek(pos_tc)
            salida.append(_tc_texto(struct.unpack(">I", f.read(4))[0] >> 7))
    return salida


IDENTIFICACION = bytes.fromhex("060e2b34025301010d01010101013000")


def fecha_creacion(ruta):
    """Fecha de creación que guarda el MXF (ModificationDate del conjunto Identification, la que MediaInfo muestra como
    "Encoded date"), "AAAA-MM-DD hh:mm:ss", o None si no hay o es la fecha vacía."""
    with open(ruta, "rb") as f:
        for pos, clave, n in _klv(f):
            if clave[:12] == CLAVE_ESENCIA:
                return None  # ya es la esencia: la cabecera ha terminado
            if clave != IDENTIFICACION:
                continue
            f.seek(pos)
            v = f.read(n)
            i = 0
            while i + 4 <= len(v):
                tag, largo = struct.unpack(">HH", v[i:i + 4])
                if tag == 0x3C06 and largo == 8:
                    y, mo, d, h, mi, se, _ = struct.unpack(">HBBBBBB", v[i + 4:i + 12])
                    return "%04d-%02d-%02d %02d:%02d:%02d" % (y, mo, d, h, mi, se) if y > 1900 else None
                i += 4 + largo
            return None
    return None


def md5_fichero(ruta, parar=None):
    """MD5 por bloques. parar: función que se llama en cada bloque (lanza Detenido si se ha pulsado Detener)."""
    h = hashlib.md5()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(BLOQUE), b""):
            if parar:
                parar()
            h.update(bloque)
    return h.hexdigest()


def escribir_md5(ruta, parar=None):
    """Escribe NOMBRE.md5 junto al fichero (formato md5sum binario) y devuelve el hash."""
    ruta = Path(ruta)
    hash_ = md5_fichero(ruta, parar)
    ruta.with_suffix(".md5").write_text("%s *%s\n" % (hash_, ruta.name), encoding="utf-8")
    return hash_


def ficha_tecnica(ffprobe, ruta, esperado, flags=None):
    """Comprueba el bruto exportado contra el perfil XDCAM HD422 50 Mb/s 1080i25 16:9. esperado: {"pistas": n,
    "tc": "hh:mm:ss:ff", "duracion": segundos, "tc_gop": True si se ha reescrito el TC interno}.
    Devuelve (conforme, texto de la ficha)."""
    r = subprocess.run([ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(ruta)],
                       capture_output=True, timeout=300, **(flags or {}))
    try:
        datos = json.loads(r.stdout.decode("utf-8", "replace"))
    except ValueError:
        return False, "No se puede leer el fichero con ffprobe: %s" % r.stderr.decode("utf-8", "replace")[-300:]
    fmt = datos.get("format", {})
    tags = fmt.get("tags", {})
    vs = [s for s in datos.get("streams", []) if s.get("codec_type") == "video"]
    aus = [s for s in datos.get("streams", []) if s.get("codec_type") == "audio"]
    v = vs[0] if vs else {}
    dur = float(fmt.get("duration") or 0)
    tamano = Path(ruta).stat().st_size
    mbps = tamano * 8 / dur / 1e6 if dur else 0
    pistas = esperado.get("pistas", 0)
    maximo = 50 + pistas * 1.152 + 6  # vídeo + audio + envoltura MXF

    lineas, conforme = [], True

    def ver(ok, texto):
        nonlocal conforme
        conforme = conforme and ok
        lineas.append("%s  %s" % ("OK   " if ok else "FALLO", texto))

    ver(fmt.get("format_name") == "mxf" and OP1A in tags.get("operational_pattern_ul", ""),
        "Contenedor MXF OP1a (%s, %s)" % (fmt.get("format_name"), tags.get("operational_pattern_ul")))
    ver(len(vs) == 1, "Una pista de vídeo (%d)" % len(vs))
    ver(v.get("codec_name") == "mpeg2video" and v.get("profile") == "4:2:2" and v.get("pix_fmt") == "yuv422p",
        "MPEG-2 4:2:2 (%s, %s, %s)" % (v.get("codec_name"), v.get("profile"), v.get("pix_fmt")))
    ver((v.get("width"), v.get("height")) == (1920, 1080), "1920x1080 (%sx%s)" % (v.get("width"), v.get("height")))
    ver(v.get("r_frame_rate") == "25/1" and v.get("field_order") == "tt",
        "25 imágenes/s entrelazado, primero el campo superior (%s, %s)" % (v.get("r_frame_rate"), v.get("field_order")))
    ver(v.get("display_aspect_ratio") == "16:9", "Aspecto 16:9 (%s)" % v.get("display_aspect_ratio"))
    ver(v.get("color_primaries") == "bt709", "Color BT.709 (%s)" % v.get("color_primaries"))
    ver(48 <= mbps <= maximo, "Unos 50 Mb/s de vídeo (%.1f Mb/s en total con audio y envoltura)" % mbps)
    ver(len(aus) == pistas, "%d pistas de audio (%d)" % (pistas, len(aus)))
    malas = [i + 1 for i, a in enumerate(aus) if (a.get("codec_name"), a.get("sample_rate"), a.get("channels")) != ("pcm_s24le", "48000", 1)]
    ver(not malas, "Audio PCM 24 bits 48 kHz mono%s" % (" (no lo son las pistas %s)" % malas if malas else ""))
    ver(tags.get("timecode") == esperado.get("tc"), "TC de inicio %s (%s)" % (esperado.get("tc"), tags.get("timecode")))
    if esperado.get("duracion") is not None:
        ver(abs(dur - esperado["duracion"]) <= 1.0, "Duración %.2f s (esperada %.2f s)" % (dur, esperado["duracion"]))
    if esperado.get("tc_gop"):
        gop = leer_tc_gop(ruta, 1)
        ver(gop[:1] == [esperado.get("tc")], "TC interno del vídeo igual al del fichero (%s)" % (gop[0] if gop else "sin GOP"))
    else:
        lineas.append("—      TC interno del vídeo: se conserva el del material copiado sin recomprimir")
    lineas.append("Creado: %s" % (fecha_creacion(ruta) or "sin fecha"))
    cab = "Ficha técnica de %s\nPerfil: XDCAM HD422 50 Mb/s 1080i25 16:9\nVeredicto: %s\n\n" % (Path(ruta).name, "CONFORME" if conforme else "NO CONFORME")
    return conforme, cab + "\n".join(lineas) + "\n"
