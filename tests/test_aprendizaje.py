#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pruebas de aprendizaje.py: aprender el origen del material de las decisiones de cada disco."""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import aprendizaje as ap  # noqa: E402


def grupo(carpeta, nbin="material.avb", nombres=("a.mp4",), marca=None):
    clips = [{"nombre": n, "fichero_origen": "C:" + carpeta + "\\" + n, "marca": marca} for n in nombres]
    return {"carpeta": carpeta, "bin": nbin, "clips": clips, "ejemplos": list(nombres)[:3]}


class Rasgos(unittest.TestCase):
    def test_formas_de_nombre(self):
        self.assertEqual(ap.forma_de_nombre(r"C:\x\IMG_1234.MOV"), "IMG_####")
        self.assertEqual(ap.forma_de_nombre("VID_20210915_101500.mp4"), "VID_########_######")
        self.assertEqual(ap.forma_de_nombre("entrevista alcalde.mov"), "texto")
        self.assertIsNone(ap.forma_de_nombre(""))

    def test_pistas_de_una_procedencia(self):
        r = ap.rasgos(grupo(r"\Users\admin\Mis vídeos\Compras", "Compras 2021.avb", ("IMG_0001.MOV",), "Apple iPhone 13"))
        self.assertEqual(r, {"carpeta:admin", "carpeta:videos", "carpeta:compras", "bin:compras",  # los números no cuentan
                             "nombre:IMG_####", "ext:.mov", "marca:apple"})
        self.assertIn("sin_ruta", ap.rasgos({"carpeta": "", "bin": "capturas.avb", "clips": []}))


class Aprender(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.ruta = self.d / "config_aprendizaje_origen.json"

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_aprende_y_propone(self):
        ap.registrar(self.ruta, [(grupo(r"\Mis vídeos\Compras", "compras.avb", ("x1.mp4",)), "agencia"),
                                 (grupo(r"\Escritorio\Compras CNN", "compras.avb", ("x2.mp4",)), "agencia"),
                                 (grupo(r"\RODAJE\Clip", "material.avb", ("C0001.MP4",)), "propio"),
                                 (grupo(r"\Escritorio\DRON", "material.avb", ("DJI_0001.MP4",)), "propio")])
        decisiones = ap.cargar(self.ruta)
        self.assertEqual(len(decisiones), 4)
        s = ap.sugerir(decisiones, grupo(r"\Otro disco\Compras", "compras.avb", ("vista.mp4",)))
        self.assertEqual(s["clase"], "agencia")
        self.assertGreaterEqual(s["confianza"], ap.MINIMA_CONFIANZA)
        self.assertIn("compras", s["pistas"])
        self.assertEqual(ap.texto_sugerencia(s, {"agencia": "Agencia"}), "Agencia %d %% · 2 decisiones" % round(s["confianza"] * 100))

    def test_no_propone_sin_saber(self):
        self.assertIsNone(ap.sugerir([], grupo(r"\x")))
        ap.registrar(self.ruta, [(grupo(r"\A"), "agencia"), (grupo(r"\B", "otro.avb"), "propio"), (grupo(r"\C", "tercero.avb"), "importado")])
        # nada en común con lo aprendido (ni carpeta, ni bin, ni forma de nombre)
        self.assertIsNone(ap.sugerir(ap.cargar(self.ruta), {"carpeta": r"\Zeta", "bin": "nuevo.avb", "clips": []}))
        self.assertEqual(ap.texto_sugerencia(None, {}), "—")

    def test_guardar_dos_veces_no_suma_y_none_quita(self):
        g = grupo(r"\Compras", "compras.avb")
        ap.registrar(self.ruta, [(g, "agencia")])
        ap.registrar(self.ruta, [(g, "propio")])  # cambio de opinión: se sustituye
        self.assertEqual([d["clase"] for d in ap.cargar(self.ruta)], ["propio"])
        ap.registrar(self.ruta, [(g, None)])  # "Como propone el programa": se olvida
        self.assertEqual(ap.cargar(self.ruta), [])
        self.assertFalse(list(self.d.glob("*.tmp")))

    def test_fichero_roto_o_ajeno(self):
        self.assertEqual(ap.cargar(self.d / "no_existe.json"), [])
        self.ruta.write_text("{roto", encoding="utf-8")
        self.assertEqual(ap.cargar(self.ruta), [])
        self.ruta.write_text(json.dumps({"decisiones": [{"clase": "inventada", "rasgos": ["x"]}]}), encoding="utf-8")
        self.assertEqual(ap.cargar(self.ruta), [])
        self.assertEqual(ap.registrar(self.ruta, [(grupo(r"\X"), "propio")]), 1)  # y se puede volver a escribir


if __name__ == "__main__":
    unittest.main()
