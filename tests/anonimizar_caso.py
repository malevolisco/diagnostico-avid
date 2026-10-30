"""Convierte el análisis de un disco real (la carpeta Diagnostico_... o su zip) en un caso de prueba anonimizado:
tests/casos_reales/NOMBRE/caso.json.gz. Solo texto (clips, cruce, mxf y lecturas.json); nada de vídeo ni miniaturas.

Cada palabra de nombres, rutas y comentarios se cambia por un código (la misma palabra, el mismo código: X001, X002...),
salvo las que el programa necesita para decidir y no dicen nada del contenido: identificadores de cámara (00059S,
C0001), palabras técnicas (Avid MediaFiles, CONTENTS, new, extensiones) y las de PALABRAS. Fechas,
timecodes, duraciones, formatos, UMID y huellas quedan como están.

Después comprueba que el caso anonimizado da las mismas decisiones, clip a clip, que el original. Si no (una palabra
cambiada era la que usaba el programa para decidir, p. ej. un indicativo de agencia), dice qué clips y qué palabras, para
añadirlas a PALABRAS si no son sensibles.

  python tests/anonimizar_caso.py CARPETA_O_ZIP NOMBRE
  python tests/casos_reales.py --ver NOMBRE           revisar el plan
  python tests/casos_reales.py --actualizar NOMBRE    guardarlo como lo esperado
"""

import argparse
import gzip
import hashlib
import io
import json
import re
import sys
import zipfile
from pathlib import Path

import casos_reales

FICHEROS = ("clips", "cruce", "mxf")
PALABRAS = set("""
avid mediafiles mxf omfi omfimediafiles media files creating quarantine cuarentena disco disk volume
contents video audio clip clips proxy icon voice general xdroot bpav m4root private avchd bdmv stream dcim clipr clpr
mp root sub take takes new old nueva bin bins trash papelera sequence sequences secuencia secuencias
mxf mov mp4 m4v mts m2ts avi mkv wav aif aiff bwf mp3 jpg jpeg png tif tiff bmp gif psd avb avp avs xml bim ind ppn mpl
xmp thm lrv srt pdf txt
unc path tape reel camroll source file name names camera cam roll scene shot comment comments
material exchange format technology inc pro composer xpress media symphony newscutter
render rendered renders precompute precomputed title titles matte effect fx
mvi dsc dscf dji gopr gx gh img vid pxl mah p2 xdcam xavc
""".split())
# Valores que se dejan tal cual: formatos, clasificaciones y fechas (no son del contenido y el programa decide con ellos)
TAL_CUAL = {"codec", "codec_video", "codec_audio", "formato", "producto", "marca", "marca_tipo", "origen", "op", "fps",
            "fps_medio", "pix_fmt", "field_order", "perfil", "tipo", "color_primarias", "color_transfer", "color_matriz",
            "color_rango", "timecode", "tc", "fecha", "hora", "modificado", "umid", "file_package_umid", "muestreo"}
# Trozos de 3 letras que no pueden quedar en ningún caso (el nombre de la organización), por su huella SHA-256 para no
# escribirlos aquí. Una palabra que los lleve se cambia siempre por un código.
SENSIBLES = {"c07827a9d3618b537d04f1af85292e34ba9f5e2c199c10cf1b47cf952649d695"}


def sensible(texto):
    """El primer trozo sensible del texto (su huella), o None."""
    t = texto.lower()
    return next((h for h in (hashlib.sha256(t[i:i + 3].encode("utf-8")).hexdigest() for i in range(len(t) - 2)) if h in SENSIBLES), None)


def identificador(w):
    """Un identificador de cámara o de fichero (00059S, C0001, A01, 5CB6A85E): mayúsculas y cifras, con al menos tantas
    cifras como letras. Un nombre con un número (ENTREVISTA3) no lo es."""
    return bool(re.fullmatch(r"[A-Z0-9]+", w)) and sum(ch.isdigit() for ch in w) >= max(1, sum(ch.isalpha() for ch in w))


class Anonimizador:
    def __init__(self):
        self.mapa = {}

    def palabra(self, w):
        if not w or not re.search(r"[^\W_]", w):
            return w
        if not sensible(w) and (w.lower() in PALABRAS or identificador(w)):
            return w
        return self.mapa.setdefault(w.lower(), "X%03d" % (len(self.mapa) + 1))

    def texto(self, s):
        return "".join(self.palabra(w) for w in re.split(r"([\W_]+)", s))

    def valor(self, v, clave=None):
        if isinstance(v, str):
            return v if clave in TAL_CUAL else self.texto(v)
        if isinstance(v, list):
            return [self.valor(x, clave) for x in v]
        if isinstance(v, dict):
            return {(k if re.fullmatch(r"[a-z0-9_]+", k) else self.texto(k)): self.valor(x, k) for k, x in v.items()}
        return v

    def lecturas(self, datos):
        """Las claves de lecturas.json son listas en JSON: ["huella", ruta, índice]."""
        return {json.dumps([self.valor(x) if isinstance(x, str) and i else x for i, x in enumerate(json.loads(k))],
                           ensure_ascii=False): v for k, v in (datos or {}).items()}


def leer_analisis(origen):
    """{clips, cruce, mxf, lecturas} de una carpeta de análisis o de su zip."""
    origen = Path(origen)
    if origen.suffix.lower() == ".zip":
        with zipfile.ZipFile(origen) as z:
            nombres = {Path(n).name: n for n in z.namelist()}
            leer = lambda f: json.load(io.TextIOWrapper(z.open(nombres[f]), encoding="utf-8")) if f in nombres else None
            caso = {k: leer(k + ".json") for k in FICHEROS}
            lect = leer("lecturas.json")
    else:
        leer = lambda f: json.loads((origen / f).read_text(encoding="utf-8")) if (origen / f).exists() else None
        caso = {k: leer(k + ".json") for k in FICHEROS}
        lect = leer("lecturas.json")
    faltan = [k for k in FICHEROS if caso[k] is None]
    if faltan:
        raise SystemExit("Faltan en %s: %s" % (origen, ", ".join(f + ".json" for f in faltan)))
    caso["lecturas"] = (lect or {}).get("lecturas", {})
    return caso


def anonimizar(caso):
    a = Anonimizador()
    anon = {k: a.valor(caso[k]) for k in FICHEROS}
    anon["lecturas"] = a.lecturas(caso.get("lecturas"))
    return anon, a


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("analisis", help="Carpeta Diagnostico_... o su zip")
    ap.add_argument("nombre", help="Nombre del caso (carpeta en tests/casos_reales)")
    ap.add_argument("--config", help="config_brutos.json con el que se analizó, si no era el de por defecto (se guarda en el caso)")
    ap.add_argument("--comprobar", action="store_true",
                    help="No guarda nada: comprueba que el caso se repite entero (sin lecturas que falten) y decide igual")
    ap.add_argument("--brutos", help="Con --comprobar: carpeta de brutos exportados de ese análisis; cada bruto tiene que tener "
                                     "los clips que dice el plan")
    args = ap.parse_args()
    if not re.fullmatch(r"[a-z0-9_]+", args.nombre):
        raise SystemExit("El nombre del caso: minúsculas, cifras y _ (no el del disco ni el de un proyecto)")
    caso = leer_analisis(args.analisis)
    if args.config:
        cfg = json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
        caso["config"] = {k: v for k, v in cfg.items() if not k.startswith("_")}
    anon, a = anonimizar(caso)
    if caso.get("config"):
        anon["config"] = caso["config"]
    texto = json.dumps(anon, ensure_ascii=False)
    if sensible(texto):
        raise SystemExit("Ha quedado algo sensible en el caso")

    antes = {a.texto(k) if isinstance(k, str) else k: v for k, v in casos_reales.por_clave(caso).items()}
    despues = casos_reales.por_clave(anon)
    distintos = sorted(k for k in set(antes) | set(despues) if antes.get(k) != despues.get(k))
    if distintos:
        inverso = {v: k for k, v in a.mapa.items()}
        print("El caso anonimizado no decide igual en %d clips. Palabras cambiadas en ellos (añádelas a PALABRAS si no son "
              "sensibles):" % len(distintos))
        por_codigo = {c.get("clave"): c for c in anon["clips"]}
        for k in distintos[:30]:
            c = por_codigo.get(k) or {}
            codigos = sorted(set(re.findall(r"X\d{3}", json.dumps(c, ensure_ascii=False))))
            print("  %s: antes %s, ahora %s; palabras: %s" % (c.get("nombre"), antes.get(k), despues.get(k),
                                                             ", ".join(inverso.get(x, x) for x in codigos)))
        return 1
    if args.comprobar:
        plan = casos_reales.plan(caso)
        if plan["lecturas_no_grabadas"]:
            print("Faltan %d lecturas en lecturas.json: el caso no se puede repetir entero" % plan["lecturas_no_grabadas"])
            return 1
        if args.brutos:
            hechos = {b["proyecto"]: b.get("clips") for b in
                      json.loads((Path(args.brutos) / "brutos_resultado.json").read_text(encoding="utf-8"))}
            del_plan = {b["nombre"]: len(b["clips"]) for p in plan["proyectos"].values() for b in p["brutos"]}
            distintos = sorted(n for n in hechos if hechos[n] != del_plan.get(n))
            if distintos:
                print("Los brutos exportados no tienen los clips del plan: %s" % ["%s: %s / %s" % (n, hechos[n], del_plan.get(n))
                                                                                  for n in distintos])
                return 1
        print("El análisis se repite entero sin el disco (%d lecturas) y el caso anonimizado decide igual%s."
              % (len(anon["lecturas"]), "; los brutos exportados son los del plan" if args.brutos else ""))
        return 0
    carpeta = casos_reales.CARPETA / args.nombre
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / "caso.json.gz").write_bytes(gzip.compress(texto.encode("utf-8"), mtime=0))
    print("Guardado %s (%d clips, %d MXF, %d lecturas, %d palabras cambiadas por códigos). Decide igual que el original."
          % (carpeta / "caso.json.gz", len(anon["clips"]), len(anon["mxf"]), len(anon["lecturas"]), len(a.mapa)))
    print("Ahora: python tests/casos_reales.py --ver %s  y, si está bien, --actualizar %s" % (args.nombre, args.nombre))
    return 0


if __name__ == "__main__":
    sys.exit(main())
