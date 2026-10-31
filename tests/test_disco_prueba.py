"""El disco de prueba (botón "Disco de prueba"): solo se rehace una carpeta que creó él; nunca se borra otra."""

import shutil
import tempfile
import unittest
from pathlib import Path

import crear_disco_prueba as cdp


class BorrarSoloLoDePrueba(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_toca_una_carpeta_de_verdad(self):
        real = self.tmp / "real"
        real.mkdir()
        (real / "importante.txt").write_text("x")
        with self.assertRaises(ValueError):
            cdp.borrar_si_es_de_prueba(real)
        self.assertTrue((real / "importante.txt").exists())

    def test_rehace_la_suya_o_una_vacia(self):
        suya = self.tmp / "Disco de prueba"
        suya.mkdir()
        (suya / cdp.MARCA).write_text("x")
        (suya / "algo.mxf").write_bytes(b"")
        cdp.borrar_si_es_de_prueba(suya)
        self.assertFalse(suya.exists())
        vacia = self.tmp / "vacia"
        vacia.mkdir()
        cdp.borrar_si_es_de_prueba(vacia)
        self.assertFalse(vacia.exists())
        cdp.borrar_si_es_de_prueba(self.tmp / "no_existe")


if __name__ == "__main__":
    unittest.main()
