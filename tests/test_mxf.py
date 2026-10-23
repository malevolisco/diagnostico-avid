#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pruebas del acabado profesional (mxf_util.py): TC de las cabeceras GOP, ficha técnica y MD5.
Necesitan ffmpeg y ffprobe en el PATH; si no están, se saltan.

Uso:  python -m unittest discover -s tests -v
"""

import hashlib
import json
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

    def test_dnxhd_1080i50_se_recodifica_sin_tocar_los_campos(self):
        """Como el material de los discos de China (DNxHD 120 1080i50): sale XDCAM conforme sin separar campos."""
        fuente = self.tmp / "dnxhd.mxf"
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=25:d=2", "-vf", "setfield=tff",
                        "-c:v", "dnxhd", "-b:v", "120M", "-pix_fmt", "yuv422p", "-flags", "+ildct+ilme", "-top", "1", "-f", "mxf", str(fuente)],
                       check=True, capture_output=True)
        r = subprocess.run([FFPROBE, "-v", "error", "-show_streams", "-of", "json", str(fuente)], capture_output=True, check=True)
        import json
        s = json.loads(r.stdout)["streams"][0]
        pista = {"tipo": "video", "ancho": s["width"], "alto": s["height"], "fps": s["r_frame_rate"], "field_order": s.get("field_order")}
        self.assertTrue(eb.es_1080i25(pista), pista)
        video = eb.args_video({"modo": "convertir", "destino": {"xdcam": True}}, {"pistas": [pista]}, {"convertir": {}})
        self.assertNotIn("bwdif", " ".join(video))
        salida = self.tmp / "xdcam.mxf"
        raw = self.tmp / "silencio.s24le"
        raw.write_bytes(b"\x00\x00\x00" * 96000)
        cmd = [FFMPEG, "-v", "error", "-y", "-i", str(fuente)] + sum((["-f", "s24le", "-ar", "48000", "-ac", "1", "-i", str(raw)] for _ in range(4)), [])
        cmd += ["-map", "0:v"] + sum((["-map", "%d:a" % i] for i in range(1, 5)), []) + video
        cmd += ["-c:a", "pcm_s24le", "-t", "2", "-timecode", "00:00:00:00", "-f", "mxf", str(salida)]
        subprocess.run(cmd, check=True, capture_output=True)
        ok, texto = mxf_util.ficha_tecnica(FFPROBE, salida, {"pistas": 4, "tc": "00:00:00:00", "duracion": 2.0})
        self.assertTrue(ok, texto)

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

    def test_color_bt709_en_la_cabecera(self):
        self.assertEqual(mxf_util.color_mpeg2(self.mxf), (1, 1, 1))

    def test_md5(self):
        h = mxf_util.escribir_md5(self.mxf)
        self.assertEqual(h, hashlib.md5(self.mxf.read_bytes()).hexdigest())
        self.assertEqual(self.mxf.with_suffix(".md5").read_text(encoding="utf-8"), "%s *bruto.mxf\n" % h)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(FFMPEG and FFPROBE, "sin ffmpeg/ffprobe")
class AudioPorCanales(unittest.TestCase):
    """Cada canal del clip es una pista del bruto (un estéreo en un solo fichero da dos; un 5.1, seis) y todo el audio
    dura lo mismo que el vídeo, para que al unir los clips el sonido no se desplace."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = json.loads(json.dumps(eb.CONFIG_DEFECTO))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ff(self, *args):
        subprocess.run([FFMPEG, "-v", "error", "-y"] + list(args), check=True, capture_output=True)

    def perfil(self, nombre):
        """Lo que guarda el análisis de un fichero (mxf.json): sus flujos con tipo, canales y duración."""
        r = subprocess.run([FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(self.tmp / nombre)],
                           capture_output=True, check=True)
        d = json.loads(r.stdout)
        pistas = [{"tipo": s["codec_type"], "canales": s.get("channels"), "ancho": s.get("width"), "alto": s.get("height"),
                   "fps": s.get("r_frame_rate"), "field_order": s.get("field_order"),
                   "duracion": float(s["duration"]) if s.get("duration") else None} for s in d["streams"]]
        return {"ruta": nombre, "pistas": pistas, "duracion": float(d["format"]["duration"])}

    def segmento(self, ficheros, pistas):
        mxf = {f: self.perfil(f) for f in ficheros}
        clip = {"clave": "K", "nombre": "C", "ficheros": ficheros, "fichero_video": ficheros[0], "duracion": mxf[ficheros[0]]["duracion"]}
        plan = {"modo": "convertir", "pistas_audio": pistas, "destino": {"xdcam": True}, "fps": 25.0}
        info = {}
        seg, err, cmd = eb.hacer_segmento(clip, 1, plan, self.tmp, mxf, self.tmp, FFMPEG, self.cfg, info=info)
        self.assertIsNotNone(seg, err)
        return seg, eb.canales_clip(clip, mxf), info

    def volumen(self, ruta, pista):
        r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(ruta), "-map", "0:a:%d" % pista, "-af", "volumedetect", "-f", "null", "-"],
                           capture_output=True, text=True)
        return float(r.stderr.split("mean_volume:")[1].split("dB")[0])

    def duraciones_audio(self, ruta):
        r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a", "-show_entries", "stream=duration", "-of", "csv=p=0", str(ruta)],
                           capture_output=True, text=True, check=True)
        return [float(x) for x in r.stdout.split()]

    def video_con_estereo(self, nombre, dur=2):
        """Vídeo con un estéreo dentro: el canal izquierdo con tono y el derecho en silencio."""
        self.ff("-f", "lavfi", "-i", "testsrc2=s=1280x720:r=25:d=%d" % dur, "-f", "lavfi", "-i", "sine=f=440:r=48000:d=%d" % dur,
                "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-filter_complex", "[1:a][2:a]join=inputs=2:channel_layout=stereo[a]",
                "-map", "0:v", "-map", "[a]", "-t", str(dur), "-c:v", "mpeg2video", "-c:a", "pcm_s16le", str(self.tmp / nombre))

    def test_estereo_en_un_fichero_son_dos_pistas(self):
        self.video_con_estereo("movil.mov")
        seg, canales, info = self.segmento(["movil.mov"], 4)
        self.assertEqual([(c["flujo"], c["canal"], c["de"]) for c in canales], [(0, 0, 2), (0, 1, 2)])
        vol = [self.volumen(seg, k) for k in range(4)]
        self.assertGreater(vol[0], -40)  # izquierdo: tono
        self.assertLess(vol[1], -80)  # derecho: silencio (antes se perdía y la pista 1 llevaba la mezcla)
        self.assertLess(max(vol[2:]), -80)
        ok, texto = mxf_util.ficha_tecnica(FFPROBE, seg, {"pistas": 4, "tc": "00:00:00:00", "duracion": 2.0})
        self.assertTrue(ok, texto)

    def test_estereo_dentro_y_51_aparte_son_ocho(self):
        self.video_con_estereo("clip.mov")
        self.ff("-f", "lavfi", "-i", "sine=f=1000:r=48000:d=2", "-af", "pan=5.1|c0=c0|c1=c0|c2=c0|c3=c0|c4=c0|c5=c0",
                "-c:a", "pcm_s24le", str(self.tmp / "clip_51.wav"))
        seg, canales, _ = self.segmento(["clip.mov", "clip_51.wav"], 8)
        self.assertEqual([c["ruta"] for c in canales], ["clip.mov"] * 2 + ["clip_51.wav"] * 6)
        vol = [self.volumen(seg, k) for k in range(8)]
        self.assertGreater(vol[0], -40)
        self.assertLess(vol[1], -80)
        self.assertTrue(all(v > -40 for v in vol[2:]), vol)

    def test_audio_mas_corto_que_el_video(self):
        self.ff("-f", "lavfi", "-i", "testsrc2=s=1280x720:r=25:d=2", "-c:v", "mpeg2video", str(self.tmp / "v.mxf"))
        self.ff("-f", "lavfi", "-i", "sine=f=440:r=48000:d=1", "-c:a", "pcm_s16le", str(self.tmp / "a.wav"))
        self.ff("-f", "lavfi", "-i", "sine=f=440:r=48000:d=3", "-c:a", "pcm_s16le", str(self.tmp / "largo.wav"))
        seg, _, _ = self.segmento(["v.mxf", "a.wav", "largo.wav"], 4)
        for d in self.duraciones_audio(seg):
            self.assertAlmostEqual(d, 2.0, delta=0.05)  # rellenado y cortado a la duración del vídeo
