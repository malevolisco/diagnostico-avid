"""Lo que el análisis lee del vídeo y del sonido para decidir: dónde está un timecode de cinta dentro de un clip, la huella
(MD5) de un fotograma y dónde está un trozo de sonido dentro de otro clip. Se guarda en lecturas.json junto al análisis
(va en el zip). Nunca imagen ni sonido: solo posiciones y huellas.

Sirve para repetir las decisiones sin el disco (tests/casos_reales.py): con repetir(), lo que se pregunta se contesta
desde lo guardado; lo que no se guardó se trata como un fichero que no se puede leer.
"""

import json
import threading
from contextlib import contextmanager
from pathlib import Path

FICHERO = "lecturas.json"


class Lecturas:
    def __init__(self, previas=None):
        self.datos = {}
        self.previas = previas or {}  # lo leído en análisis anteriores del mismo disco: no se vuelve a leer
        self.reutilizadas = 0
        self.leidas = 0
        self.repitiendo = False
        self.no_grabadas = 0
        self.cerrojo = threading.Lock()

    def leer(self, tipo, clave, calcular):
        """El resultado de calcular() (que lee el disco), recordado como tipo + clave (lista de textos y números; las
        rutas, relativas al origen). Un OSError también se recuerda: al repetir, vuelve a saltar."""
        k = json.dumps([tipo] + list(clave), ensure_ascii=False)
        if self.repitiendo:
            with self.cerrojo:
                if k not in self.datos:
                    self.no_grabadas += 1
                    raise OSError("lectura no grabada: %s" % k)
                v = self.datos[k]
            if isinstance(v, dict) and "error" in v:
                raise OSError(v["error"])
            return v["valor"]
        previa = self.previas.get(k)
        if isinstance(previa, dict) and "valor" in previa:  # un error de antes se vuelve a intentar
            with self.cerrojo:
                self.datos[k] = previa
                self.reutilizadas += 1
            return previa["valor"]
        with self.cerrojo:
            self.leidas += 1
        try:
            valor = calcular()
        except OSError as e:
            with self.cerrojo:
                self.datos[k] = {"error": str(e)}
            raise
        with self.cerrojo:
            self.datos[k] = {"valor": valor}
        return valor

    def guardar(self, carpeta):
        with self.cerrojo:  # con lo de los análisis anteriores que no hizo falta: así cada lecturas.json va completo
            datos = dict(sorted({**{k: v for k, v in self.previas.items() if isinstance(v, dict) and "valor" in v},
                                 **self.datos}.items()))
        (Path(carpeta) / FICHERO).write_text(json.dumps({"_comentario": "Posiciones y huellas leídas del vídeo y del sonido "
                                                         "para decidir (trozos repetidos); sin imagen ni sonido.",
                                                         "lecturas": datos}, ensure_ascii=False, indent=0), encoding="utf-8")


ACTIVAS = Lecturas()


def empezar(previas=None):
    """Un análisis nuevo. previas: lo leído en análisis anteriores del mismo disco ({clave: {"valor"}}, de sus
    lecturas.json): no se vuelve a leer del disco (23/11/2026: con 41 proyectos sumados, el paso 4 rehacía miles de
    comparaciones de sonido ya hechas). Las lecturas que fallaron se vuelven a intentar."""
    global ACTIVAS
    ACTIVAS = Lecturas(previas)
    return ACTIVAS


def de_analisis_anteriores(carpetas):
    """Lo leído en esas carpetas de análisis (sus lecturas.json), sin los errores. La más reciente manda."""
    previas = {}
    for carpeta in reversed(list(carpetas)):  # de la más antigua a la más reciente: la reciente pisa
        try:
            datos = json.loads((Path(carpeta) / FICHERO).read_text(encoding="utf-8")).get("lecturas") or {}
        except (OSError, ValueError, AttributeError):
            continue
        previas.update({k: v for k, v in datos.items() if isinstance(v, dict) and "valor" in v})
    return previas


@contextmanager
def repetir(datos):
    """Contesta desde datos ({clave: {"valor"|"error"}}, lo de lecturas.json) en vez de leer el disco."""
    global ACTIVAS
    antes = ACTIVAS
    ACTIVAS = Lecturas()
    ACTIVAS.datos, ACTIVAS.repitiendo = dict(datos or {}), True
    try:
        yield ACTIVAS
    finally:
        ACTIVAS = antes


def huella(ruta_rel, ruta, indice):
    import mxf_util
    return ACTIVAS.leer("huella", [ruta_rel, indice], lambda: mxf_util.huella_fotograma_dv(ruta, indice))


def situar(ruta_rel, ruta, objetivos, a_fotogramas):
    """mxf_util.situar_tc_dv recordado: {fotograma de cinta: posición en el clip}."""
    import mxf_util
    objetivos = list(objetivos)
    pares = ACTIVAS.leer("situar", [ruta_rel] + objetivos,
                         lambda: sorted(mxf_util.situar_tc_dv(ruta, objetivos, a_fotogramas).items()))
    return {int(k): v for k, v in pares}
