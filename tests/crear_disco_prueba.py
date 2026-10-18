#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Crea un disco Avid simulado para probar el programa sin un disco real.

Reproduce la estructura de un disco de Media Composer: carpetas de proyecto
con .avp y bins .avb (escritos con pyavb), y Avid MediaFiles/MXF/101 con
ficheros MXF OP-Atom (vídeo DNxHD y audio PCM en ficheros separados, que
comparten el identificador UMID como hace Avid). Incluye casos límite:
proyecto uniforme, proyecto con mezcla de formatos, clip sin audio, clip
solo audio, clip huérfano, clip offline, bin corrupto, bin en papelera y
fichero en cuarentena.

Uso:  python tests/crear_disco_prueba.py CARPETA_DESTINO [--ffmpeg RUTA_FFMPEG --ffprobe RUTA_FFPROBE]
"""

import argparse
import json
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

    def mkv(nombre, out, dur, size="1920x1080", br="120M"):
        subprocess.run([args.ffmpeg, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=%s:rate=25" % size,
                        "-t", str(dur), "-c:v", "dnxhd", "-b:v", br, "-pix_fmt", "yuv422p", "-f", "mxf_opatom",
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
        v = M / ("%sV01.%s.mxf" % (nombre, suf))
        mkv(nombre, v, dur, **kw)
        u = umid(v)
        for i in range(audios):
            mka(nombre, M / ("%sA0%d.%s%d.mxf" % (nombre, i + 1, suf, i)), dur, 440 * (i + 1), u)
        return u

    def bin_con(path, clips, secs=()):
        f = avb.file.AVBFile()
        for nombre, u in clips:
            m = f.create.Composition(mob_type="MasterMob")
            m.name = nombre
            m.length = 75
            m.mob_id = avb.mobid.MobID(bytes_le=bytes.fromhex(u))
            f.content.add_mob(m)
        for s in secs:
            c = f.create.Composition(mob_type="CompositionMob")
            c.name = s
            c.length = 1250
            f.content.add_mob(c)
        path.parent.mkdir(parents=True, exist_ok=True)
        f.write(str(path))

    # Proyecto uniforme (1080p DNxHD): copia directa. Un clip sin audio y uno offline.
    u1 = clip("2017 07 21", "5C0095C009487", 3)
    u2 = clip("2017 07 13", "5C0095C00948C", 2)
    u3 = clip("2017 07 21", "5C0095C009517", 2, audios=0)
    P = D / "15 SEPT CENTROAMERICA"
    P.mkdir()
    (P / "15 SEPT CENTROAMERICA.avp").write_bytes(b"avp")
    (P / "SearchData").mkdir()
    bin_con(P / "15 SEPT CENTROAMERICA Bin.avb",
            [("2017 07 21", u1), ("2017 07 13", u2), ("2017 07 21", u3),
             ("CLIP OFFLINE", "060A2B340101010501010D00130000000000000000000000000000000000FFFF")],
            ["PIEZA 15 SEPT"])
    bin_con(P / "Trash" / "viejo.avb", [("2017 07 21", u1)])

    # Proyecto con mezcla 720p/1080p: conversión. Un bin corrupto.
    u4 = clip("2018-10-30T013549Z", "5BFD8590", 2, size="1280x720", br="60M")
    u5 = clip("2018_21043491-c", "5C06EDB3", 2, audios=1)
    P = D / "ABORTO"
    P.mkdir()
    (P / "ABORTO.avp").write_bytes(b"avp")
    bin_con(P / "EDICION.avb", [("2018-10-30T013549Z", u4), ("2018_21043491-c", u5)], ["ABORTO PIEZA", "ABORTO TOTAL"])
    (P / "Bin corrupto.avb").write_bytes(b"Domain\x00basura" * 40)

    # Huérfano con vídeo, solo-audio huérfano y fichero en cuarentena.
    clip("HUERFANO", "AAAA0001", 2, audios=1)
    mka("SOLOAUDIO", M / "SOLOAUDIOA01.BBBB.mxf", 2, 300, "060A2B340101010501010D0013000000000000000000000000000000000000AA")
    mkv("CUARENTENA", M / "Quarantined Files" / "xV01.CCCC.mxf", 1)
    (M / "msmMMOB.mdb").write_bytes(b"\0" * 100)
    (M / "msmFMID.pmr").write_bytes(b"\0" * 100)
    print("Disco de prueba creado en %s (%d ficheros)" % (D, sum(1 for _ in D.rglob("*") if _.is_file())))


if __name__ == "__main__":
    main()
