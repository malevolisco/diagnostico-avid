"""Discos reales (anonimizados, solo texto): las decisiones de ahora tienen que ser las de esperado.json. Ver casos_reales.py."""

import gzip
import json
import unittest

import anonimizar_caso
import casos_reales


class CasosReales(unittest.TestCase):
    def test_decide_como_se_espera(self):
        nombres = casos_reales.casos()
        self.assertTrue(nombres, "no hay casos en tests/casos_reales")
        for nombre in nombres:
            with self.subTest(caso=nombre):
                esperado = casos_reales.esperado(nombre)
                self.assertIsNotNone(esperado, "%s: falta esperado.json (python tests/casos_reales.py --actualizar %s)" % (nombre, nombre))
                lineas = casos_reales.diferencias(esperado, casos_reales.plan(casos_reales.cargar(nombre)))
                self.assertFalse(lineas, "%s decide distinto que lo esperado. Si el cambio es el que se buscaba, revisa y "
                                         "actualiza: python tests/casos_reales.py --actualizar %s\n  %s"
                                 % (nombre, nombre, "\n  ".join(lineas)))

    def test_mismo_clip_de_camara_en_disco_dv(self):
        """Disco DV de 2002/2019, analizado antes de que hubiera trozos repetidos por el nombre: un clip de cámara más corto
        con el mismo identificador (0008AY.new.01 de otro bin) va a la papelera si su timecode de cinta está dentro del largo;
        si no (00059S.new.04 después del final del largo, los 0013OJ.new.NN de otros momentos), es otro plano y se queda.
        Dónde está cada timecode dentro del largo se lee del disco (lecturas.situar); aquí, con el timecode seguido."""
        import copy
        from unittest import mock
        import diagnostico_avid as da
        import exportar_brutos as eb
        clips = copy.deepcopy(casos_reales.cargar("disco_dv_2002_2019")["clips"])

        def situar(ruta_rel, ruta, objetivos, a_f):
            largo = next(c for c in clips if c.get("fichero_video") == ruta_rel)
            l0, n = a_f(largo["dv"]["inicio"]["tc"]), largo["dv"]["fotogramas"]
            return {o: o - l0 for o in objetivos if 0 <= o - l0 < n}

        with mock.patch("lecturas.situar", situar):
            marcados = da.repetidos_por_nombre(clips, "(disco)")
        self.assertEqual(sorted(c["nombre"] for c in marcados),
                         ["0008AY.new.01", "00118A.new.01", "0027WK.new.01", "0035WV.new.02", "0043F6.new.02"])
        self.assertEqual([eb.texto_duplicado(c) for c in marcados if c["nombre"] == "0027WK.new.01"],
                         ["parte de 0027WK.new.01, del 0:13,2 al 0:22,0"])
        quedan = {c["nombre"] for c in clips if not c.get("contenido_en")}
        self.assertLessEqual({"00059S.new.04", "00059S.new.07", "0013OJ.new.02", "0013OJ.new.13", "0013OJ.new.20"}, quedan)

    def test_sin_nada_sensible(self):
        for nombre in casos_reales.casos():
            for f in sorted((casos_reales.CARPETA / nombre).iterdir()):
                datos = f.read_bytes()
                texto = (gzip.decompress(datos) if f.suffix == ".gz" else datos).decode("utf-8")
                with self.subTest(fichero=f.name, caso=nombre):
                    self.assertIsNone(anonimizar_caso.sensible(texto))
                    if f.suffix == ".gz":
                        self.assertLessEqual(set(json.loads(texto)), {"clips", "cruce", "lecturas", "mxf", "config"})


if __name__ == "__main__":
    unittest.main()
