#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pruebas del acabado profesional (mxf_util.py): TC de las cabeceras GOP, ficha técnica y MD5.
Necesitan ffmpeg y ffprobe en el PATH; si no están, se saltan.

Uso:  python -m unittest discover -s tests -v
"""

import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import exportar_brutos as eb  # noqa: E402
import mxf_util  # noqa: E402

FFMPEG, FFPROBE = shutil.which("ffmpeg"), shutil.which("ffprobe")


def audio_mxf(ruta):
    """Bytes de todos los elementos de sonido del MXF, en orden."""
    datos = b""
    with open(ruta, "rb") as f:
        for pos, clave, n in mxf_util._klv(f):
            if clave[:12] == mxf_util.CLAVE_ESENCIA and clave[12] == 0x16:
                f.seek(pos)
                datos += f.read(n)
                f.seek(pos + n)
    return datos


@unittest.skipUnless(FFMPEG and FFPROBE, "sin ffmpeg/ffprobe")
class AcabadoProfesional(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.mxf = self.crear("bruto.mxf")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def crear(self, nombre, tc_encoder=None):
        """2 s de XDCAM HD422 con TC de fichero 10:00:00:00 y 4 pistas; la 1 lleva 00 00 01 B8 dentro (no debe tocarse)."""
        raw = self.tmp / "trampa.s24le"
        raw.write_bytes(b"\x00\x00\x01\xb8\x00\x00" * 48000)
        salida = self.tmp / nombre
        plan = {"modo": "convertir", "destino": {"xdcam": True}}
        video = eb.args_video(plan, {"pistas": [{"tipo": "video", "field_order": "tt"}]}, {"convertir": {}})
        cmd = [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=25:d=2", "-f", "s24le", "-ar", "48000", "-ac", "1",
               "-i", str(raw)]
        for _ in range(3):
            cmd += ["-f", "lavfi", "-i", "sine=f=440:r=48000:d=2"]
        cmd += ["-map", "0:v"] + sum((["-map", "%d:a" % i] for i in range(1, 5)), []) + video
        cmd += ["-c:a", "pcm_s24le", "-ar", "48000", "-ac", "1", "-t", "2", "-timecode", tc_encoder or "10:00:00:00", "-f", "mxf", str(salida)]
        subprocess.run(cmd, check=True, capture_output=True)
        return salida

    def test_tc_gop_y_audio_intacto(self):
        # ffmpeg ya escribe en las GOP el TC que se le pide al codificar; el bruto une segmentos codificados cada uno
        # desde 00:00:00:00, que es lo que hay que reescribir. El parche debe dar lo mismo que habría escrito ffmpeg.
        nativo = mxf_util.leer_tc_gop(self.mxf, 10)
        self.assertEqual(nativo, ["10:00:00:00", "10:00:00:10", "10:00:00:22", "10:00:01:09", "10:00:01:21"])
        cero = self.crear("cero.mxf", tc_encoder="00:00:00:00")
        self.assertEqual(mxf_util.leer_tc_gop(cero, 1), ["00:00:00:00"])
        audio_antes = hashlib.md5(audio_mxf(cero)).hexdigest()
        self.assertIn(b"\x00\x00\x01\xb8", audio_mxf(cero))
        self.assertEqual(mxf_util.parchear_tc_gop(cero, 10 * 3600 * 25, 25), 5)
        self.assertEqual(mxf_util.leer_tc_gop(cero, 10), nativo)
        self.assertEqual(hashlib.md5(audio_mxf(cero)).hexdigest(), audio_antes)
        r = subprocess.run([FFMPEG, "-v", "error", "-i", str(cero), "-f", "null", "-"], capture_output=True)
        self.assertEqual(r.stderr, b"")  # sigue decodificando sin errores

    def test_ficha_tecnica(self):
        ok, texto = mxf_util.ficha_tecnica(FFPROBE, self.mxf, {"pistas": 4, "tc": "10:00:00:00", "duracion": 2.0, "tc_gop": True})
        self.assertTrue(ok, texto)
        self.assertIn("CONFORME", texto)
        ok, texto = mxf_util.ficha_tecnica(FFPROBE, self.mxf, {"pistas": 2, "tc": "10:00:00:00", "duracion": 2.0})
        self.assertFalse(ok)
        self.assertIn("FALLO  2 pistas de audio (4)", texto)

    def test_fecha_de_creacion(self):
        self.assertIsNone(mxf_util.fecha_creacion(self.mxf))  # ffmpeg sin creation_time: fecha vacía
        con = self.tmp / "con_fecha.mxf"
        subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(self.mxf), "-map", "0", "-c", "copy", "-metadata",
                        "creation_time=2026-10-22T18:43:16.000000Z", "-f", "mxf", str(con)], check=True)
        self.assertEqual(mxf_util.fecha_creacion(con), "2026-10-22 18:43:16")

    def test_md5(self):
        h = mxf_util.escribir_md5(self.mxf)
        self.assertEqual(h, hashlib.md5(self.mxf.read_bytes()).hexdigest())
        self.assertEqual(self.mxf.with_suffix(".md5").read_text(encoding="utf-8"), "%s *bruto.mxf\n" % h)


if __name__ == "__main__":
    unittest.main()
