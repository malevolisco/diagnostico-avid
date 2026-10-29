#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Crea un disco Avid simulado para probar el programa sin un disco real.

Reproduce la estructura de un disco de Media Composer: carpetas de proyecto
con .avp y bins .avb (escritos con pyavb), y Avid MediaFiles/MXF/101 con
ficheros MXF OP-Atom (vídeo DNxHD y audio PCM en ficheros separados, que
comparten el identificador UMID como hace Avid). Incluye casos límite:
proyecto uniforme, proyecto con mezcla de formatos, clip sin audio, clip
solo audio, clip huérfano, clip offline, bin corrupto, bin en papelera,
fichero en cuarentena, render de título, clip compartido entre proyectos,
ruta con ñ, proyecto NTSC 29,97 en MPEG-2 (copia y timecode drop-frame),
proyecto cuyo bin no coincide por identificador (cruce aproximado por
nombre y duración, con dos clips ambiguos), dos tarjetas por fecha y dos por
carpeta de tarjeta, un clip de agencia (Reuters), una pieza renderizada, un
vídeo de móvil importado y un render de fundido que no está en ningún bin
(como los 806 del disco real de México). Y un proyecto como los de la
Corresponsalía de Pekín: nombres en chino, DNxHD 1080i50, material propio
importado desde una carpeta del PC (DJI_, C0380), un bin de agencias y una
carpeta de bins con una ruta de más de 260 caracteres.
Las fechas de la media se fijan (15/09/2021 10:00, y un clip el día 10) para
que los nombres de los brutos sean siempre los mismos.

Además, un proyecto ENLACES con media enlazada (AMA) fuera de Avid MediaFiles: un vídeo
vertical de móvil con estéreo (tarjeta XDROOT) y un HDR HLG de GoPro (DCIM), y un clip
cuya media de Avid está en otra carpeta (CARPETA_DESTINO_media2), como si estuviera en
otro disco: solo se encuentra si se añade esa carpeta de media (--media-extra).

Uso:  python tests/crear_disco_prueba.py CARPETA_DESTINO [--ffmpeg RUTA_FFMPEG --ffprobe RUTA_FFPROBE]
"""

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import avb


def larga(ruta):
    """En Windows, las rutas de más de 260 caracteres necesitan el prefijo \\\\?\\ para crearlas."""
    texto = os.path.abspath(str(ruta))
    return "\\\\?\\" + texto if os.name == "nt" and not texto.startswith("\\\\?\\") else texto


def en_bin(texto):
    """pyavb escribe los nombres en Mac Roman, que no tiene chino. Un Avid moderno guarda UTF-8: se escriben los bytes
    UTF-8 tal cual (el programa los reconoce al leer el bin)."""
    return texto.encode("utf-8").decode("mac_roman") if not texto.isascii() and not all(ord(c) < 256 for c in texto) else texto


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("destino")
    ap.add_argument("--ffmpeg", default=shutil.which("ffmpeg"))
    ap.add_argument("--ffprobe", default=shutil.which("ffprobe"))
    args = ap.parse_args()
    if not args.ffmpeg or not args.ffprobe:
        sys.exit("Hace falta ffmpeg y ffprobe (en el PATH o con --ffmpeg/--ffprobe)")

    D = Path(args.destino)
    if D.exists():
        shutil.rmtree(D)
    D2 = Path(args.destino + "_media2")  # "otro disco" con más media de Avid
    if D2.exists():
        shutil.rmtree(D2)
    M2 = D2 / "Avid MediaFiles" / "MXF" / "1"
    M2.mkdir(parents=True)
    M = D / "Avid MediaFiles" / "MXF" / "101"
    M.mkdir(parents=True)
    (M / "Quarantined Files").mkdir()
    (M / "Creating").mkdir()

    def umid(f):
        out = subprocess.run([args.ffprobe, "-v", "error", "-print_format", "json", "-show_format", str(f)],
                             capture_output=True, text=True, check=True).stdout
        return json.loads(out)["format"]["tags"]["material_package_umid"][2:]

    def mkv(nombre, out, dur, size="1920x1080", br="120M", fps="25", codec="dnxhd", entrelazado=False):
        extra = ["-profile:v", "0", "-level:v", "2"] if codec == "mpeg2video" else []  # MPEG-2 4:2:2 (tipo IMX)
        if entrelazado:  # 1080i50, primero el campo superior (como el DNxHD 120 de los discos de China)
            extra += ["-vf", "setfield=tff", "-flags", "+ildct+ilme", "-top", "1"]
        subprocess.run([args.ffmpeg, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=%s:rate=%s" % (size, fps),
                        "-t", str(dur), "-c:v", codec, "-b:v", br, "-pix_fmt", "yuv422p"] + extra + ["-f", "mxf_opatom",
                        "-metadata", "material_package_name=%s" % nombre, str(out)], check=True)

    def mka(nombre, out, dur, freq, umid_v):
        subprocess.run([args.ffmpeg, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=%d:sample_rate=48000" % freq,
                        "-t", str(dur), "-c:a", "pcm_s16le", "-f", "mxf_opatom",
                        "-metadata", "material_package_name=%s" % nombre, str(out)], check=True)
        # Avid: V y A del mismo clip comparten UMID. ffmpeg genera uno distinto por fichero; se sustituye.
        datos = out.read_bytes()
        datos = datos.replace(bytes.fromhex(umid(out)), bytes.fromhex(umid_v))
        out.write_bytes(datos)

    def clip(nombre, suf, dur, audios=2, carpeta=None, **kw):
        carpeta = carpeta or M
        fichero = nombre.replace(":", "_")  # Windows no admite ':' en nombres de fichero (Avid tampoco los usa)
        v = carpeta / ("%sV01.%s.mxf" % (fichero, suf))
        mkv(nombre, v, dur, **kw)
        u = umid(v)
        for i in range(audios):
            mka(nombre, carpeta / ("%sA0%d.%s%d.mxf" % (fichero, i + 1, suf, i)), dur, 440 * (i + 1), u)
        return u

    def ff(*a):
        subprocess.run([args.ffmpeg, "-loglevel", "error", "-y"] + list(a), check=True)

    def bin_con(path, clips, secs=(), renders=()):
        """clips: (nombre, umid[, frames[, fichero_origen[, columnas]]]). columnas: columnas de usuario del bin
        (p. ej. "Shoot Date"), como las que Avid guarda en _USER. Sin fichero_origen los mobs no llevan
        pistas, así que el programa asume 25 fps al calcular los segundos del bin. Con fichero_origen
        el master clip apunta a un source mob cuyo localizador guarda la ruta del fichero importado,
        como hace Avid (de ahí salen la carpeta de la tarjeta y el origen, p. ej. Reuters)."""
        f = avb.file.AVBFile()
        for nombre, u, *extra in clips:
            m = f.create.Composition(mob_type="MasterMob")
            m.name = en_bin(nombre)
            m.length = extra[0] if extra else 75
            if len(extra) > 1:
                src = f.create.Composition(mob_type="SourceMob")
                src.name, src.length, src.mob_id, src.tracks = en_bin(nombre), m.length, avb.mobid.MobID.new(), []
                d = f.create.CDCIDescriptor()
                loc = f.create.WinFileLocator()
                loc.path, loc.path_utf8 = en_bin(extra[1]), extra[1]
                d.locator = loc
                src.descriptor = d
                f.content.add_mob(src)
                t, sc = f.create.Track(), f.create.SourceClip()
                sc.mob_id, sc.media_kind, sc.length, sc.start_time, sc.track_id = src.mob_id, "picture", m.length, 0, 1
                t.component = sc
                m.tracks = [t]
            if len(extra) > 2:
                u_attr = f.create.Attributes()
                for k, v in extra[2].items():
                    u_attr[k] = v
                m.attributes["_USER"] = u_attr
            m.mob_id = avb.mobid.MobID(bytes_le=bytes.fromhex(u))
            f.content.add_mob(m)
        for nombre, u in renders:  # precompute = render de efecto (título, fundido)
            m = f.create.Composition(mob_type="MasterMob")
            m.name = nombre
            m.length = 75
            m.usage_code = 1
            m.mob_id = avb.mobid.MobID(bytes_le=bytes.fromhex(u))
            f.content.add_mob(m)
        for s in secs:
            c = f.create.Composition(mob_type="CompositionMob")
            c.name = s
            c.length = 1250
            f.content.add_mob(c)
        os.makedirs(larga(path.parent), exist_ok=True)
        f.write(larga(path))

    # Proyecto uniforme (1080p DNxHD): copia directa. Un clip sin audio, uno offline
    # y un render de título (precompute) con media física, que no debe ir al bruto.
    u1 = clip("2017 07 21", "5C0095C009487", 3)
    u2 = clip("2017 07 13", "5C0095C00948C", 2)
    u3 = clip("2017 07 21 ESPAÑA", "5C0095C009517", 2, audios=0)  # ruta con ñ
    ur = clip("Title: 1", "5C0095C00TITL", 1, audios=0)
    ua = clip("SISMO 15 SEPT", "5C0095C00A9E1", 2)  # agencia: lo dice la ruta del fichero importado que guarda el bin
    up = clip("PIEZA 15 SEPT", "5C0095C00B1E7", 2)  # pieza renderizada: se llama como la secuencia
    um = clip("MOVIL 15 SEPT", "5C0095C00E0B1", 2)  # importado: el bin guarda la ruta de un MP4 que no es de tarjeta
    clip("PIEZA 15 SEPT,Dissolve,1", "5C0095C00D155", 1, audios=0)  # render de fundido sin bin: así los nombra Avid
    P = D / "15 SEPT CENTROAMERICA"
    P.mkdir()
    (P / "15 SEPT CENTROAMERICA.avp").write_bytes(b"avp")
    (P / "SearchData").mkdir()
    bin_con(P / "15 SEPT CENTROAMERICA Bin.avb",
            [("2017 07 21", u1), ("2017 07 13", u2), ("2017 07 21 ESPAÑA", u3),
             ("CLIP OFFLINE", "060A2B340101010501010D00130000000000000000000000000000000000FFFF"),
             ("SISMO 15 SEPT", ua, 50, r"C:\Users\corresponsal\Downloads\2021-09-15T120000Z_1_LWD1234ABC_RTRMADV_A_1-MEXICO-QUAKE.MP4"),
             ("PIEZA 15 SEPT", up, 50),
             ("MOVIL 15 SEPT", um, 50, r"C:\Users\admin\Desktop\videos movil\VID_20210915_101500.mp4")],
            ["PIEZA 15 SEPT"], renders=[("Title: 1", ur)])
    bin_con(P / "Trash" / "viejo.avb", [("2017 07 21", u1)])

    # Proyecto con mezcla 720p/1080p: conversión. Un bin corrupto.
    u4 = clip("2018-10-30T013549Z", "5BFD8590", 2, size="1280x720", br="60M")
    u5 = clip("2018_21043491-c", "5C06EDB3", 2, audios=1)
    P = D / "ABORTO"
    P.mkdir()
    (P / "ABORTO.avp").write_bytes(b"avp")
    # u2 también está aquí: clip compartido entre dos proyectos.
    bin_con(P / "EDICION.avb", [("2018-10-30T013549Z", u4), ("2018_21043491-c", u5), ("2017 07 13", u2)], ["ABORTO PIEZA", "ABORTO TOTAL"])
    (P / "Bin corrupto.avb").write_bytes(b"Domain\x00basura" * 40)

    # NTSC 29,97 MPEG-2 4:2:2 SD: copia directa y timecode drop-frame en el bruto.
    n1 = clip("NTSC 1", "5D00A001", 2, size="720x480", br="30M", fps="30000/1001", codec="mpeg2video")
    n2 = clip("NTSC 2", "5D00A002", 2, audios=1, size="720x480", br="30M", fps="30000/1001", codec="mpeg2video")
    P = D / "NTSC"
    P.mkdir()
    (P / "NTSC.avp").write_bytes(b"avp")
    # Mismo día, pero el bin dice que vienen de dos tarjetas distintas (carpetas CARD01 y CARD02). Columnas de
    # usuario con la hora de grabación: NTSC 2 se grabó antes que NTSC 1 (los dos se volcaron en el mismo segundo).
    # NTSC 2 lleva además "Camroll", que los clips de antes no tienen: en un disco real (26/10/2026) una columna nueva
    # en un clip que no es el primero rompía el análisis en el paso 4/6.
    bin_con(P / "NTSC Bin.avb", [("NTSC 1", n1, 60, r"E:\TARJETAS\NTSC\CARD01\CONTENTS\VIDEO\0001AB.MXF",
                                  {"Shoot Date": "15/09/2021", "Shoot Time": "18:30:00"}),
                                 ("NTSC 2", n2, 60, r"E:\TARJETAS\NTSC\CARD02\CONTENTS\VIDEO\0002AB.MXF",
                                  {"Shoot Date": "15/09/2021", "Shoot Time": "09:15:00", "Camroll": "A002"})])

    # Identificadores que no coinciden con ningún MXF: cruce aproximado por nombre y duración.
    # "APROX A" es único (se empareja); los dos "APROX B" son iguales (ambiguos: quedan sin proyecto).
    clip("APROX A", "5E00B001", 2)
    clip("APROX B", "5E00B002", 2)
    clip("APROX B", "5E00B003", 2)
    P = D / "APROX"
    P.mkdir()
    (P / "APROX.avp").write_bytes(b"avp")
    falso = "060A2B340101010501010D001300000000000000000000000000000000000%03X"
    bin_con(P / "APROX Bin.avb", [("APROX A", falso % 0xA01, 50), ("APROX B", falso % 0xB01, 50), ("APROX B", falso % 0xB02, 50)])

    # Corresponsalía de Pekín (disco real de oct. 2026): proyecto con nombre en chino, todo DNxHD 1080i50 (se recodifica
    # sin separar campos). La entrevista viene de una tarjeta Sony (XDROOT); el dron y la Sony A7 se copiaron a una
    # carpeta del PC antes de importarlos (son material propio por el nombre del fichero). En el bin de agencias, un
    # vídeo de la televisión china (fuera de los brutos). El bin de rodaje está en una ruta de más de 260 caracteres.
    i50 = dict(entrelazado=True)
    c1 = clip("entrevista 北京", "5F00C001", 2, **i50)
    c2 = clip("DJI_0012", "5F00C002", 2, audios=0, **i50)
    c3 = clip("C0380", "5F00C003", 2, **i50)
    c4 = clip("新闻 CCTV 0916", "5F00C004", 2, **i50)
    P = D / "20260916_China 北京"
    P.mkdir()
    (P / "20260916_China 北京.avp").write_bytes(b"avp")
    for cache in ("SearchData", "Statistics", "WaveformCache"):
        (P / cache).mkdir()
    hondo = P / "Bins de rodaje de la corresponsalía en Pekín 北京 entrevistas recursos y planos de dron del 16 de septiembre" \
        / "Material de cámara propia volcado desde la tarjeta y desde el ordenador portátil 北京 0916 segunda jornada de rodaje"
    bin_con(hondo / "20260916 rodaje 北京.avb",
            [("entrevista 北京", c1, 50, r"J:\XDROOT\Clip\C0001.MXF"),
             ("DJI_0012", c2, 50, r"C:\Users\corresponsal\Desktop\China 0916\dron\DJI_0012.MP4"),
             ("C0380", c3, 50, r"D:\北京\A7III\C0380.MP4")])
    bin_con(P / "20260916 agencias.avb", [("新闻 CCTV 0916", c4, 50, r"C:\Users\corresponsal\Downloads\CCTV 新闻 0916.mp4")])

    # Media enlazada (AMA): ficheros de cámara fuera de Avid MediaFiles que el bin enlaza con otra letra de unidad (E:).
    # Un vertical de móvil (rotación 90) con estéreo dentro, y un HDR HLG de GoPro en HEVC de 10 bits con estéreo.
    R = D / "RODAJE"
    (R / "XDROOT" / "Clip").mkdir(parents=True)
    (R / "DCIM" / "100GOPRO").mkdir(parents=True)
    tmp_v = D / "tumbado.mp4"
    # Estéreo con el izquierdo mudo y el derecho con tono: en el bruto, la pista 1 muda y la 2 con sonido (si se mezclara
    # a mono sonaría la 1; si se ignorara el audio de dentro del vídeo, ninguna)
    estereo = ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-f", "lavfi", "-i", "sine=f=880:r=48000:d=2",
               "-filter_complex", "[1:a][2:a]join=inputs=2:channel_layout=stereo[a]", "-map", "0:v", "-map", "[a]", "-t", "2"]
    ff("-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=25:d=2", *estereo, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(tmp_v))
    ff("-display_rotation:v:0", "90", "-i", str(tmp_v), "-c", "copy", str(R / "XDROOT" / "Clip" / "C0001.MP4"))
    tmp_v.unlink()
    # Las etiquetas de color van en las imágenes (setparams) y dentro del HEVC (x265-params): con solo las opciones de salida,
    # el ffmpeg 7.1 de Windows escribía la matriz pero no las primarias ni la transferencia (y el clip no parecía HDR).
    hlg = "color_primaries=bt2020:color_trc=arib-std-b67:colorspace=bt2020nc:range=tv"
    estereo_hlg = list(estereo)
    i = estereo_hlg.index("-filter_complex") + 1
    estereo_hlg[i] = "[0:v]setparams=%s[v];%s" % (hlg, estereo_hlg[i])
    estereo_hlg[estereo_hlg.index("0:v")] = "[v]"
    ff("-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=25:d=2", *estereo_hlg, "-c:v", "libx265", "-pix_fmt", "yuv420p10le",
       "-x265-params", "log-level=error:colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc:range=limited",
       "-color_primaries", "bt2020", "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc",
       "-c:a", "aac", str(R / "DCIM" / "100GOPRO" / "GX010045.MP4"))
    # Y un clip cuya media de Avid está en otro disco (la carpeta _media2).
    uo = clip("OTRO DISCO", "6A00D001", 2, carpeta=M2)
    P = D / "ENLACES"
    P.mkdir()
    (P / "ENLACES.avp").write_bytes(b"avp")
    sin_media = "060A2B340101010501010D001300000000000000000000000000000000000%03X"
    bin_con(P / "ENLACES Bin.avb", [("C0001", sin_media % 0xE01, 50, r"E:\RODAJE\XDROOT\Clip\C0001.MP4"),
                                     ("GX010045", sin_media % 0xE02, 50, r"E:\RODAJE\DCIM\100GOPRO\GX010045.MP4"),
                                     ("OTRO DISCO", uo, 50)])

    # Origen por señales (parte 4 del plan). Un MOV enlazado de una Panasonic con un nombre que no está en ninguna
    # lista: material propio por los metadatos. Un clip importado de una carpeta sin pistas y otro de un bin "compras":
    # dudosos, entran en el bruto marcados "para revisar" (ante la duda, entra).
    (D / "material").mkdir()
    ff("-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=25:d=2", "-f", "lavfi", "-i", "sine=r=48000:d=2", "-c:v", "libx264",
       "-pix_fmt", "yuv420p", "-c:a", "aac", "-metadata", "make=Panasonic", "-metadata", "model=DC-GH5",
       str(D / "material" / "entrevista alcalde.mov"))
    ur = clip("RECURSO SIN PISTAS", "7B00E001", 2)
    uc = clip("VISTA AEREA", "7B00E002", 2)
    P = D / "ORIGEN"
    P.mkdir()
    (P / "ORIGEN.avp").write_bytes(b"avp")
    # Fechas (parte 5): la Panasonic con el reloj sin poner (1/1/2000 de fábrica: se descarta y se ordena por el volcado)
    # y el importado sin pistas con el reloj un día adelantado (26 h tras el volcado: se usa, con aviso)
    bin_con(P / "ORIGEN Bin.avb", [("ENTREVISTA ALCALDE", sin_media % 0xE03, 50, r"E:\material\entrevista alcalde.mov",
                                     {"Shoot Date": "01/01/2000", "Shoot Time": "00:03:00"}),
                                    ("RECURSO SIN PISTAS", ur, 50, r"D:\varios\plano recurso.mp4",
                                     {"Shoot Date": "16/09/2021", "Shoot Time": "12:00:00"}),
                                    # Media OMF de un Avid antiguo: ffmpeg no la lee; sale offline, marcado "media en OMF"
                                    ("VIEJO OMF", sin_media % 0xE04)])
    (D / "OMFI MediaFiles").mkdir()
    (D / "OMFI MediaFiles" / "VIEJO OMFV01.5A1B2C.omf").write_bytes(b"OMF" * 100)
    bin_con(P / "compras.avb", [("VISTA AEREA", uc, 50, r"C:\material\compras\vista aerea.mp4")])

    # Trozo repetido (29/10/2026): una entrevista DV con el subcódigo de la cámara (fecha, hora y timecode de cinta) y un
    # trozo suyo copiado por Avid a otro fichero, con otra identidad y los mismos fotogramas. En el bruto, solo la
    # entrevista; el trozo, en la papelera del montaje.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import dv_prueba
    ue = clip("ENTREVISTA DV", "6D00F001", 6, audios=1, size="720x480", fps="30000/1001", codec="dvvideo")
    v_ent = next(M.glob("ENTREVISTA DVV01.6D00F001.mxf"))
    dv_prueba.poner_subcodigo(v_ent, "17:35:48:11", dt.datetime(2002, 11, 13, 23, 27, 26))
    v_trozo = M / "ENTREVISTA DV TROZOV01.6D00F002.mxf"
    subprocess.run([args.ffmpeg, "-loglevel", "error", "-y", "-ss", "2.0", "-i", str(v_ent), "-frames:v", "40", "-c", "copy",
                    "-metadata", "material_package_name=ENTREVISTA DV TROZO", "-f", "mxf_opatom", str(v_trozo)], check=True)
    ut = umid(v_trozo)
    mka("ENTREVISTA DV TROZO", M / "ENTREVISTA DV TROZOA01.6D00F0020.mxf", 40 / (30000 / 1001), 440, ut)
    P = D / "DUPLICADO"
    P.mkdir()
    (P / "DUPLICADO.avp").write_bytes(b"avp")
    bin_con(P / "DUPLICADO Bin.avb", [("ENTREVISTA DV", ue), ("ENTREVISTA DV TROZO", ut)])

    # Huérfano con vídeo, solo-audio huérfano y fichero en cuarentena.
    clip("HUERFANO", "AAAA0001", 2, audios=1)
    mka("SOLOAUDIO", M / "SOLOAUDIOA01.BBBB.mxf", 2, 300, "060A2B340101010501010D0013000000000000000000000000000000000000AA")
    mkv("CUARENTENA", M / "Quarantined Files" / "xV01.CCCC.mxf", 1)
    # Fechas fijas de la media: todo el 15/09/2021 a las 10:00 salvo el clip "2017 07 13" (día 10), que
    # es otra tarjeta del proyecto 15 SEPT (a más de 24 h, para que no se junte aunque dure poco).
    # Los brutos se llaman por la fecha de su tarjeta.
    dia15 = dt.datetime(2021, 9, 15, 10, 0).timestamp()
    dia10 = dt.datetime(2021, 9, 10, 10, 0).timestamp()
    for f in list(M.rglob("*.mxf")) + list(M2.rglob("*.mxf")) + list(R.rglob("*.MP4")) + list((D / "material").rglob("*.mov")):
        t = dia10 if f.name.startswith("2017 07 13") else dia15
        os.utime(f, (t, t))
    (M / "msmMMOB.mdb").write_bytes(b"\0" * 100)
    (M / "msmFMID.pmr").write_bytes(b"\0" * 100)
    print("Disco de prueba creado en %s (%d ficheros)" % (D, sum(1 for _ in D.rglob("*") if _.is_file())))


if __name__ == "__main__":
    main()
