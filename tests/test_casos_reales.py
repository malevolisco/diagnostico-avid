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
