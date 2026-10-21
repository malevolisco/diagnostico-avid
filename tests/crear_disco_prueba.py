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
(como los 806 del disco real de México).
Las fechas de la media se fijan (15/09/2021 10:00, y un clip el día 10) para
que los nombres de los brutos sean siempre los mismos.

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
    M = D / "Avid MediaFiles" / "MXF" / "101"
    M.mkdir(parents=True)
    (M / "Quarantined Files").mkdir()
    (M / "Creating").mkdir()

    def umid(f):
        out = subprocess.run([args.ffprobe, "-v", "error", "-print_format", "json", "-show_format", str(f)],
                             capture_output=True, text=True, check=True).stdout
        return json.loads(out)["format"]["tags"]["material_package_umid"][2:]

    def mkv(nombre, out, dur, size="1920x1080", br="120M", fps="25", codec="dnxhd"):
        extra = ["-profile:v", "0", "-level:v", "2"] if codec == "mpeg2video" else []  # MPEG-2 4:2:2 (tipo IMX)
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

    def clip(nombre, suf, dur, audios=2, **kw):
        fichero = nombre.replace(":", "_")  # Windows no admite ':' en nombres de fichero (Avid tampoco los usa)
        v = M / ("%sV01.%s.mxf" % (fichero, suf))
        mkv(nombre, v, dur, **kw)
        u = umid(v)
        for i in range(audios):
            mka(nombre, M / ("%sA0%d.%s%d.mxf" % (fichero, i + 1, suf, i)), dur, 440 * (i + 1), u)
        return u

    def bin_con(path, clips, secs=(), renders=()):
        """clips: (nombre, umid[, frames[, fichero_origen[, columnas]]]). columnas: columnas de usuario del bin
        (p. ej. "Shoot Date"), como las que Avid guarda en _USER. Sin fichero_origen los mobs no llevan
        pistas, así que el programa asume 25 fps al calcular los segundos del bin. Con fichero_origen
        el master clip apunta a un source mob cuyo localizador guarda la ruta del fichero importado,
        como hace Avid (de ahí salen la carpeta de la tarjeta y el origen, p. ej. Reuters)."""
        f = avb.file.AVBFile()
        for nombre, u, *extra in clips:
            m = f.create.Composition(mob_type="MasterMob")
            m.name = nombre
            m.length = extra[0] if extra else 75
            if len(extra) > 1:
                src = f.create.Composition(mob_type="SourceMob")
                src.name, src.length, src.mob_id, src.tracks = nombre, m.length, avb.mobid.MobID.new(), []
                d = f.create.CDCIDescriptor()
                loc = f.create.WinFileLocator()
                loc.path = loc.path_utf8 = extra[1]
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
        path.parent.mkdir(parents=True, exist_ok=True)
        f.write(str(path))

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
    bin_con(P / "NTSC Bin.avb", [("NTSC 1", n1, 60, r"E:\TARJETAS\NTSC\CARD01\CONTENTS\VIDEO\0001AB.MXF",
                                  {"Shoot Date": "15/09/2021", "Shoot Time": "18:30:00"}),
                                 ("NTSC 2", n2, 60, r"E:\TARJETAS\NTSC\CARD02\CONTENTS\VIDEO\0002AB.MXF",
                                  {"Shoot Date": "15/09/2021", "Shoot Time": "09:15:00"})])

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

    # Huérfano con vídeo, solo-audio huérfano y fichero en cuarentena.
    clip("HUERFANO", "AAAA0001", 2, audios=1)
    mka("SOLOAUDIO", M / "SOLOAUDIOA01.BBBB.mxf", 2, 300, "060A2B340101010501010D0013000000000000000000000000000000000000AA")
    mkv("CUARENTENA", M / "Quarantined Files" / "xV01.CCCC.mxf", 1)
    # Fechas fijas de la media: todo el 15/09/2021 a las 10:00 salvo el clip "2017 07 13" (día 10), que
    # es otra tarjeta del proyecto 15 SEPT (a más de 24 h, para que no se junte aunque dure poco).
    # Los brutos se llaman por la fecha de su tarjeta.
    dia15 = dt.datetime(2021, 9, 15, 10, 0).timestamp()
    dia10 = dt.datetime(2021, 9, 10, 10, 0).timestamp()
    for f in M.rglob("*.mxf"):
        t = dia10 if f.name.startswith("2017 07 13") else dia15
        os.utime(f, (t, t))
    (M / "msmMMOB.mdb").write_bytes(b"\0" * 100)
    (M / "msmFMID.pmr").write_bytes(b"\0" * 100)
    print("Disco de prueba creado en %s (%d ficheros)" % (D, sum(1 for _ in D.rglob("*") if _.is_file())))


if __name__ == "__main__":
    main()
