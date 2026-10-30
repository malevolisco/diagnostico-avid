# -*- coding: utf-8 -*-
"""
ACABADO PROFESIONAL DE LOS BRUTOS
=================================

- Timecode de las cabeceras GOP del MPEG-2 igual al del fichero (parchear_tc_gop). Al
  unir los segmentos con -c copy cada clip conserva su TC interno desde 00:00:00:00;
  aquí se reescribe en su sitio, sin cambiar el tamaño del fichero. Solo se tocan
  los elementos de imagen del MXF: un 00 00 01 B8 dentro del audio no se toca.
- Ficha técnica: comprueba sobre el fichero exportado (ffprobe) que es XDCAM HD422
  50 Mb/s 1080i50 16:9 con las pistas y el TC pedidos.
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


# Vídeo DV (DV25, DVCPRO50, DVCPRO HD). Cada fotograma son secuencias DIF de 150 bloques de 80 bytes (12.000 bytes):
# 10 por canal en 525/60 (NTSC) y 12 en 625/50 (PAL); DV25 lleva un canal, DVCPRO50 dos y DVCPRO HD cuatro. Los bloques
# 1-2 de cada secuencia son de subcódigo (timecode de cinta, pack 0x13) y los 3-5 de VAUX (fecha y hora de grabación,
# packs 0x62 y 0x63): la cámara los escribe en cada fotograma y Avid los conserva al importar, consolidar o copiar un
# trozo a otro fichero. Ojo: el formato "dv" de ffmpeg los reescribe al reempaquetar (fecha 2070-01-01); aquí se leen
# del MXF tal cual.
SECUENCIA_DIF = 12000
TIPOS_DV = (0x15, 0x18)  # elemento de imagen o compuesto (DV con el audio dentro) del contenedor genérico


def _bcd(b):
    return (b >> 4) * 10 + (b & 0x0F)


def _packs_dv(fotograma, nseq):
    """Packs de subcódigo y VAUX del primer canal de un fotograma DV."""
    packs = []
    for s in range(nseq):
        base = s * SECUENCIA_DIF
        for blk in range(1, 6):
            o = base + blk * 80
            if o + 80 > len(fotograma):
                return packs
            sct = fotograma[o] >> 5
            if sct == 1:  # subcódigo: 6 bloques de sincronismo de 8 bytes (2 de identificación, 1 de reserva, el pack)
                packs += [fotograma[o + 3 + i * 8 + 3:o + 3 + i * 8 + 8] for i in range(6)]
            elif sct == 2:  # VAUX: 15 packs de 5 bytes
                packs += [fotograma[o + 3 + i * 5:o + 3 + i * 5 + 5] for i in range(15)]
    return packs


def _datos_dv(fotograma, nseq):
    """{"tc", "fecha", "hora"} del fotograma: el valor que más se repite en sus packs (la cámara los escribe varias
    veces); None en lo que no lleve."""
    from collections import Counter
    tc, fecha, hora = Counter(), Counter(), Counter()
    for p in _packs_dv(fotograma, nseq):
        if len(p) < 5 or p[1:5] == b"\xff\xff\xff\xff":
            continue
        if p[0] == 0x13:  # el bit 6 del primer byte dice si el timecode es drop-frame (entonces se escribe con ;)
            tc["%02d:%02d:%02d%s%02d" % (_bcd(p[4] & 0x3F), _bcd(p[3] & 0x7F), _bcd(p[2] & 0x7F), ";" if p[1] & 0x40 else ":",
                                          _bcd(p[1] & 0x3F))] += 1
        elif p[0] == 0x62:
            aa, mm, dd = _bcd(p[4]), _bcd(p[3] & 0x1F), _bcd(p[2] & 0x3F)
            if 1 <= mm <= 12 and 1 <= dd <= 31:
                fecha["%04d-%02d-%02d" % (aa + (1900 if aa >= 70 else 2000), mm, dd)] += 1
        elif p[0] == 0x63:
            h, m, s = _bcd(p[4] & 0x3F), _bcd(p[3] & 0x7F), _bcd(p[2] & 0x7F)
            if h < 24 and m < 60 and s < 60:
                hora["%02d:%02d:%02d" % (h, m, s)] += 1
    mas = lambda c: c.most_common(1)[0][0] if c else None
    return {"tc": mas(tc), "fecha": mas(fecha), "hora": mas(hora)}


def _es_inicio_dv(cab):
    """¿Empieza aquí un fotograma DV? Bloque de cabecera (SCT 0) de la secuencia 0, canal 0, bloque 0."""
    return len(cab) >= 3 and cab[0] >> 5 == 0 and cab[1] >> 3 == 0 and cab[2] == 0


def _esencia_dv(f):
    """(posición, clave, longitud, secuencias por canal, tamaño del fotograma) del primer elemento DV, o None."""
    for pos, clave, n in _klv(f):
        if clave[:12] == CLAVE_ESENCIA and clave[12] in TIPOS_DV:
            break
    else:
        return None
    f.seek(pos)
    cab = f.read(4)
    if not _es_inicio_dv(cab):
        return None
    nseq = 12 if cab[3] & 0x80 else 10
    canal = nseq * SECUENCIA_DIF
    for k in (1, 2, 4):  # DV25, DVCPRO50, DVCPRO HD: el fotograma siguiente empieza tras k canales
        if n == k * canal:
            return pos, clave, n, nseq, n
        if n > k * canal:
            f.seek(pos + k * canal)
            if _es_inicio_dv(f.read(3)):
                return pos, clave, n, nseq, k * canal
    return None


def _posicion_fotograma(f, esencia, indice):
    """Dónde empieza el fotograma número indice (desde 0), o None. En un solo bloque, a tamaño fijo; con un bloque por
    fotograma se cuentan los bloques de imagen (entre ellos puede haber relleno o particiones)."""
    pos, clave, n, nseq, tam = esencia
    if n > tam:
        return pos + indice * tam if (indice + 1) * tam <= n else None
    f.seek(0)
    cuenta = 0
    for p, c, largo in _klv(f):
        if c == clave:
            if cuenta == indice:
                return p
            cuenta += 1
    return None


def subcodigo_dv(ruta, cola=8 * 1024 * 1024):
    """Timecode de cinta y fecha y hora de grabación del primer y del último fotograma DV de un MXF, leyendo solo esos
    dos fotogramas (unos 100 KB aunque el clip dure una hora). {"inicio": {...}, "fin": {...}, "fotograma": bytes,
    "fotogramas": cuántos o None} o
    None si no es DV. Vale para la esencia en un solo bloque (OP-Atom de Avid) y para un bloque por fotograma: entonces
    el último se busca en la cola del fichero."""
    with open(ruta, "rb") as f:
        esencia = _esencia_dv(f)
        if esencia is None:
            return None
        pos, clave, n, nseq, tam = esencia
        canal = nseq * SECUENCIA_DIF
        f.seek(pos)
        inicio = _datos_dv(f.read(canal), nseq)
        ultimo = None
        if n > tam:  # un solo bloque con todos los fotogramas: el último entero (Avid puede dejar bytes de más al final)
            ultimo = pos + (n // tam - 1) * tam
        else:  # un bloque por fotograma: el último elemento de imagen, cerca del final (antes del índice y del pie)
            f.seek(0, 2)
            final = f.tell()
            desde = max(pos, final - cola - tam)
            f.seek(desde)
            trozo = f.read(final - desde)
            i = trozo.rfind(clave)
            if i >= 0 and i + 17 <= len(trozo):
                extra = (trozo[i + 16] & 0x7F) if trozo[i + 16] & 0x80 else 0
                ultimo = desde + i + 17 + extra
        fin = None
        for atras in range(30 if n > tam else 1):  # si el último no es un fotograma DV entero, el anterior que lo sea
            if ultimo is None or ultimo - atras * tam < pos:
                break
            f.seek(ultimo - atras * tam)
            dato = f.read(canal)
            if _es_inicio_dv(dato):
                fin = _datos_dv(dato, nseq)
                if atras and fin.get("tc"):  # el timecode del último, contando los que faltan
                    fin["atras"] = atras
                break
        # fotogramas: exacto con la esencia en un bloque; con un bloque por fotograma no se cuentan (None)
        return {"inicio": inicio, "fin": fin, "fotograma": tam, "fotogramas": n // tam if n > tam else None}


def huella_fotograma_dv(ruta, indice):
    """MD5 del fotograma DV número indice (desde 0) de un MXF, o None si no se encuentra. Dos copias del mismo
    fotograma (un trozo consolidado en Avid) dan la misma huella; dos cámaras distintas, no."""
    with open(ruta, "rb") as f:
        esencia = _esencia_dv(f)
        if esencia is None or indice < 0:
            return None
        sitio = _posicion_fotograma(f, esencia, indice)
        if sitio is None:
            return None
        f.seek(sitio)
        dato = f.read(esencia[4])
        if len(dato) < esencia[4] or not _es_inicio_dv(dato):
            return None
        return hashlib.md5(dato).hexdigest()


def leer_tc_gop(ruta, maximo=3):
    """TC de las primeras cabeceras GOP del vídeo."""
    salida = []
    with open(ruta, "rb") as f:
        for pos_tc, _ in _gops(f, maximo):
            f.seek(pos_tc)
            salida.append(_tc_texto(struct.unpack(">I", f.read(4))[0] >> 7))
    return salida


def color_mpeg2(ruta):
    """(primarias, transferencia, matriz) de la extensión de pantalla de la secuencia MPEG-2 (00 00 01 B5, id 2) del
    primer elemento de imagen, o None. 1, 1, 1 = BT.709. Se lee del propio vídeo porque ffprobe 7.1 no lo da en MXF."""
    with open(ruta, "rb") as f:
        for pos, clave, n in _klv(f):
            if clave[:12] != CLAVE_ESENCIA or clave[12] != TIPO_IMAGEN:
                continue
            f.seek(pos)
            cabeza = f.read(min(n, 4096))
            i = cabeza.find(b"\x00\x00\x01\xb5")
            while 0 <= i and i + 8 <= len(cabeza):
                b = cabeza[i + 4]
                if b >> 4 == 2:  # sequence_display_extension
                    return (cabeza[i + 5], cabeza[i + 6], cabeza[i + 7]) if b & 1 else None
                i = cabeza.find(b"\x00\x00\x01\xb5", i + 4)
            return None
    return None


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


PAQUETE_MATERIAL = bytes.fromhex("060e2b34025301010d01010101013600")
PAQUETE_FUENTE = bytes.fromhex("060e2b34025301010d01010101013700")
TROZO_FUENTE = bytes.fromhex("060e2b34025301010d01010101011100")  # SourceClip: apunta a un paquete de fichero (0x1101)
PARTICION = bytes.fromhex("060e2b34020501010d01020101")  # + 02 cabecera, 03 cuerpo, 04 pie


def _es(clave, ref):
    """Misma clave SMPTE sin mirar el byte de versión del registro (el 8.º), que cambia según quién escribió el MXF."""
    return clave[:7] == ref[:7] and clave[8:len(ref)] == ref[8:]


def paquetes(ruta, limite=256 * 1024 * 1024):
    """Lo que identifica un MXF sin ffprobe, leyendo solo su cabecera: los UMID (hex en mayúsculas, como los da ffprobe)
    de sus paquetes de material y de fichero (y de los que apuntan sus pistas, que es lo que da ffprobe como
    file_package_umid en los MXF de varias pistas de Avid), y los nombres de los de material. {"umids": set, "nombres": set}, o None si
    no se puede leer o no es la cabecera esperada (entonces hay que analizarlo entero con ffprobe)."""
    umids, nombres = set(), set()
    try:
        with open(ruta, "rb") as f:
            for i, (pos, clave, n) in enumerate(_klv(f)):
                if i == 0 and not _es(clave, PARTICION):
                    return None  # no empieza por una partición de cabecera: no es un MXF normal
                if pos > limite or clave[:12] == CLAVE_ESENCIA or (_es(clave, PARTICION) and clave[13] in (3, 4)):
                    break  # ya ha terminado la cabecera
                es_material = _es(clave, PAQUETE_MATERIAL)
                if not (es_material or _es(clave, PAQUETE_FUENTE) or _es(clave, TROZO_FUENTE)) or n > 1024 * 1024:
                    continue
                f.seek(pos)
                v = f.read(n)
                j = 0
                while j + 4 <= len(v):
                    tag, largo = struct.unpack(">HH", v[j:j + 4])
                    dato = v[j + 4:j + 4 + largo]
                    if tag in (0x4401, 0x1101) and largo == 32 and any(dato):
                        umids.add(dato.hex().upper())
                    elif tag == 0x4402 and es_material and dato:
                        nombres.add(dato.decode("utf-16-be", "replace").rstrip("\x00"))
                    j += 4 + largo
    except (OSError, struct.error, ValueError):
        return None
    return {"umids": umids, "nombres": nombres} if umids else None


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
    """Comprueba el bruto exportado contra el perfil XDCAM HD422 50 Mb/s 1080i50 16:9 (25 imágenes/s, 50 campos/s). esperado: {"pistas": n,
    "tc": "hh:mm:ss:ff", "duracion": segundos, "tc_gop": True si se ha reescrito el TC interno, "rdd9": True si lo ha unido bmx en
    RDD 9, False si bmx ha fallado, None si no se ha usado}.
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
    color = color_mpeg2(ruta)
    ver(color == (1, 1, 1), "Color BT.709 en la cabecera del vídeo (primarias, transferencia, matriz: %s)" % (color,))
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
    if esperado.get("rdd9") is None:
        lineas.append("—      Envoltura MXF OP1a de ffmpeg, sin RDD 9 (no se ha encontrado bmxtranswrap o está desactivado)")
    else:
        bmx = tags.get("company_name") == "BBC" and str(tags.get("product_name", "")).startswith("bmx")
        ver(bool(esperado["rdd9"]) and bmx, "Envoltura SMPTE RDD 9 (%s)" % (" ".join(str(tags.get(k)) for k in ("company_name", "product_name", "product_version") if tags.get(k)) or "sin identificar"))
    lineas.append("Creado: %s" % (fecha_creacion(ruta) or "sin fecha"))
    cab = "Ficha técnica de %s\nPerfil: XDCAM HD422 50 Mb/s 1080i50 16:9 (25 imágenes/s entrelazadas = 50 campos/s, campo superior primero)\nVeredicto: %s\n\n" % (Path(ruta).name, "CONFORME" if conforme else "NO CONFORME")
    return conforme, cab + "\n".join(lineas) + "\n"
