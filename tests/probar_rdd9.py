#!/usr/bin/env python3
"""Comprueba que unir los clips con bmx (bruto en SMPTE RDD 9) no cambia nada frente a unirlos con ffmpeg (MXF OP1a):
compara, bruto a bruto, los de una exportación normal con los de otra con "rdd9": false de los mismos proyectos.

- vídeo idéntico bit a bit (MD5 del flujo MPEG-2 sin decodificar: ni bmx ni ffmpeg recodifican al unir);
- audio idéntico (MD5 del PCM de cada pista) y mismo número de pistas;
- misma duración, timecode del fichero y timecode de las cabeceras GOP;
- la ficha técnica del bruto de bmx es CONFORME y dice RDD 9.

Deja en el log lo que dice mxf2raw de los dos (patrón operacional, particiones), si se le da.

Uso:
  python tests/probar_rdd9.py BRUTOS_RDD9 BRUTOS_FFMPEG --ffmpeg bin/ffmpeg.exe --ffprobe bin/ffprobe.exe [--mxf2raw RUTA]
Sale con 1 si algún bruto no supera la comprobación.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import mxf_util  # noqa: E402


def ejecutar(args, timeout=600):
    r = subprocess.run([str(a) for a in args], capture_output=True, timeout=timeout)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def sonda(ffprobe, ruta):
    _, out, _ = ejecutar([ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", ruta])
    return json.loads(out)


def md5_flujo(ffmpeg, ruta, mapa, copiar):
    codec = ["-c", "copy"] if copiar else ["-c:a", "pcm_s24le"]
    rc, out, err = ejecutar([ffmpeg, "-v", "error", "-i", ruta, "-map", mapa, *codec, "-f", "md5", "-"])
    return out.strip() if rc == 0 else "error: " + err[-200:]


def brutos(carpeta):
    datos = json.loads((Path(carpeta) / "brutos_resultado.json").read_text(encoding="utf-8"))
    return {b["fichero"]: b for b in datos if str(b.get("fichero", "")).lower().endswith(".mxf") and b.get("conforme") is not None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rdd9")
    ap.add_argument("ffmpeg_brutos")
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--ffprobe", default="ffprobe")
    ap.add_argument("--mxf2raw")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # la consola de Windows (cp1252) no escribe "北京"
    con, sin = brutos(a.rdd9), brutos(a.ffmpeg_brutos)
    comunes = sorted(set(con) & set(sin))
    if not comunes:
        raise SystemExit("No hay brutos XDCAM comunes entre %s y %s" % (a.rdd9, a.ffmpeg_brutos))
    fallos = 0
    for nombre in comunes:
        r9, ff = Path(a.rdd9) / nombre, Path(a.ffmpeg_brutos) / nombre
        print("\n=== %s" % nombre)
        p1, p2 = sonda(a.ffprobe, ff), sonda(a.ffprobe, r9)
        n1 = sum(1 for s in p1["streams"] if s["codec_type"] == "audio")
        n2 = sum(1 for s in p2["streams"] if s["codec_type"] == "audio")
        tc1, tc2 = p1["format"].get("tags", {}).get("timecode"), p2["format"].get("tags", {}).get("timecode")
        d1, d2 = float(p1["format"]["duration"]), float(p2["format"]["duration"])
        ficha = (Path(a.rdd9) / str(con[nombre].get("ficha"))).read_text(encoding="utf-8")
        comprobaciones = [
            ("unido con bmx (rdd9 %r) y con ffmpeg (rdd9 %r)" % (con[nombre].get("rdd9"), sin[nombre].get("rdd9")),
             con[nombre].get("rdd9") is True and sin[nombre].get("rdd9") is None),
            ("vídeo idéntico (MD5 del MPEG-2 sin decodificar)",
             md5_flujo(a.ffmpeg, ff, "0:v:0", True) == md5_flujo(a.ffmpeg, r9, "0:v:0", True)),
            ("mismas pistas de audio (%d / %d)" % (n1, n2), n1 == n2),
            ("misma duración (%.3f / %.3f s)" % (d1, d2), abs(d1 - d2) <= 0.04),
            ("mismo timecode (%s / %s)" % (tc1, tc2), tc1 == tc2),
            ("mismo TC en las cabeceras GOP", mxf_util.leer_tc_gop(ff, 3) == mxf_util.leer_tc_gop(r9, 3)),
            ("ficha CONFORME y en RDD 9", con[nombre].get("conforme") is True and "OK     Envoltura SMPTE RDD 9 (BBC bmx" in ficha),
        ]
        for i in range(min(n1, n2)):
            comprobaciones.append(("audio %d idéntico (MD5 del PCM)" % (i + 1),
                                   md5_flujo(a.ffmpeg, ff, "0:a:%d" % i, False) == md5_flujo(a.ffmpeg, r9, "0:a:%d" % i, False)))
        for texto, ok in comprobaciones:
            print("%s  %s" % ("OK   " if ok else "FALLO", texto))
        if not all(ok for _, ok in comprobaciones):
            fallos += 1
            print(ficha)
        print("tamaño: ffmpeg %d, bmx %d bytes" % (ff.stat().st_size, r9.stat().st_size))
        if a.mxf2raw and nombre.isascii():  # bmx en Windows no abre nombres fuera de la página de códigos
            for ruta in (ff, r9):
                r = subprocess.run([a.mxf2raw, "--info", "--check-complete", ruta.name], cwd=ruta.parent, capture_output=True, timeout=600)
                print("--- mxf2raw %s (código %d)\n%s%s" % (ruta.parent.name, r.returncode, r.stdout.decode("utf-8", "replace")[:2500],
                                                         r.stderr.decode("utf-8", "replace")[-800:]))
    print("\nRDD 9: %d de %d brutos unidos con bmx iguales a los de ffmpeg" % (len(comunes) - fallos, len(comunes)))
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    main()
