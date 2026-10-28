#!/usr/bin/env python3
"""Prueba de viabilidad de RDD 9 (SMPTE RDD 9, MXF de XDCAM HD422 de Sony): reempaqueta con bmxtranswrap -t rdd9 los
brutos XDCAM exportados y comprueba que el reempaquetado no cambia nada de lo que ya se verifica:

- vídeo idéntico bit a bit (MD5 del flujo MPEG-2 sin decodificar: bmx no recodifica);
- audio idéntico (MD5 del PCM decodificado de cada pista) y mismo número de pistas;
- misma duración, timecode del fichero y timecode de las cabeceras GOP;
- la ficha técnica (mxf_util.ficha_tecnica) sigue dando CONFORME.

Además deja en el log lo que dice mxf2raw del original y del reempaquetado (patrón operacional, tipos de esencia,
particiones), para ver en qué se diferencian.

Uso:
  python tests/probar_rdd9.py CARPETA_BRUTOS CARPETA_SALIDA --bmx CARPETA_BMX --ffmpeg bin/ffmpeg.exe --ffprobe bin/ffprobe.exe
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


def herramienta(carpeta, nombre):
    for f in Path(carpeta).rglob(nombre + "*"):
        if f.is_file() and f.stem.lower() == nombre:
            return f
    raise SystemExit("No se encuentra %s en %s" % (nombre, carpeta))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("brutos")
    ap.add_argument("salida")
    ap.add_argument("--bmx", required=True)
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--ffprobe", default="ffprobe")
    a = ap.parse_args()
    transwrap, mxf2raw = herramienta(a.bmx, "bmxtranswrap"), herramienta(a.bmx, "mxf2raw")
    print(ejecutar([transwrap, "-v"])[1].strip() or ejecutar([transwrap, "-v"])[2].strip())
    salida = Path(a.salida)
    salida.mkdir(parents=True, exist_ok=True)
    resultado = json.loads((Path(a.brutos) / "brutos_resultado.json").read_text(encoding="utf-8"))
    brutos = [b for b in resultado if str(b.get("fichero", "")).lower().endswith(".mxf") and b.get("conforme") is not None]
    if not brutos:
        raise SystemExit("No hay brutos XDCAM con ficha técnica en %s" % a.brutos)
    fallos = 0
    for b in brutos:
        orig = Path(a.brutos) / b["fichero"]
        rdd9 = salida / orig.name
        print("\n=== %s" % orig.name)
        rc, out, err = ejecutar([transwrap, "-t", "rdd9", "-o", rdd9, orig])
        if rc != 0 or not rdd9.exists():
            print("FALLO  bmxtranswrap -t rdd9 (código %d):\n%s%s" % (rc, out[-1500:], err[-1500:]))
            fallos += 1
            continue
        p1, p2 = sonda(a.ffprobe, orig), sonda(a.ffprobe, rdd9)
        v1 = [s for s in p1["streams"] if s["codec_type"] == "video"]
        v2 = [s for s in p2["streams"] if s["codec_type"] == "video"]
        n1 = sum(1 for s in p1["streams"] if s["codec_type"] == "audio")
        n2 = sum(1 for s in p2["streams"] if s["codec_type"] == "audio")
        tc1, tc2 = p1["format"].get("tags", {}).get("timecode"), p2["format"].get("tags", {}).get("timecode")
        d1, d2 = float(p1["format"]["duration"]), float(p2["format"]["duration"])
        comprobaciones = [
            ("vídeo idéntico (MD5 del MPEG-2 sin decodificar)",
             md5_flujo(a.ffmpeg, orig, "0:v:0", True) == md5_flujo(a.ffmpeg, rdd9, "0:v:0", True)),
            ("mismas pistas de audio (%d / %d)" % (n1, n2), n1 == n2),
            ("mismo códec y orden de campos (%s %s / %s %s)" % (v1[0].get("codec_name"), v1[0].get("field_order"),
                                                               v2[0].get("codec_name"), v2[0].get("field_order")),
             (v1[0].get("codec_name"), v1[0].get("field_order")) == (v2[0].get("codec_name"), v2[0].get("field_order"))),
            ("misma duración (%.3f / %.3f s)" % (d1, d2), abs(d1 - d2) <= 0.04),
            ("mismo timecode (%s / %s)" % (tc1, tc2), tc1 == tc2),
            ("mismo TC en las cabeceras GOP", mxf_util.leer_tc_gop(orig, 3) == mxf_util.leer_tc_gop(rdd9, 3)),
        ]
        for i in range(min(n1, n2)):
            comprobaciones.append(("audio %d idéntico (MD5 del PCM)" % (i + 1),
                                   md5_flujo(a.ffmpeg, orig, "0:a:%d" % i, False) == md5_flujo(a.ffmpeg, rdd9, "0:a:%d" % i, False)))
        conforme, ficha = mxf_util.ficha_tecnica(a.ffprobe, rdd9, {"pistas": n1, "tc": tc1, "duracion": d1, "tc_gop": True})
        comprobaciones.append(("ficha técnica CONFORME", conforme))
        for texto, ok in comprobaciones:
            print("%s  %s" % ("OK   " if ok else "FALLO", texto))
        if not conforme:
            print(ficha)
        if not all(ok for _, ok in comprobaciones):
            fallos += 1
        print("tamaño: %d -> %d bytes" % (orig.stat().st_size, rdd9.stat().st_size))
        for ruta in (orig, rdd9):
            rc, out, err = ejecutar([mxf2raw, "--info", "--check-complete", ruta])
            print("--- mxf2raw %s (código %d)\n%s%s" % (ruta.parent.name + "/" + ruta.name, rc, out[:2500], err[-800:]))
    print("\nRDD 9: %d de %d brutos reempaquetados sin cambios" % (len(brutos) - fallos, len(brutos)))
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    main()
