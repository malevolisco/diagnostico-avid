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
class SubcodigoDV(unittest.TestCase):
    """Fecha, hora y timecode de cinta de los fotogramas DV, y su huella, para detectar trozos repetidos."""

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import dv_prueba
        self.tmp = Path(tempfile.mkdtemp())
        self.largo = self.tmp / "largo.mxf"
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=720x480:r=30000/1001:d=6", "-vf", "setfield=bff",
                        "-c:v", "dvvideo", "-pix_fmt", "yuv422p", "-f", "mxf_opatom", str(self.largo)], check=True, capture_output=True)
        dv_prueba.poner_subcodigo(self.largo, "17:35:48:11", __import__("datetime").datetime(2002, 11, 13, 23, 27, 26))
        self.corto = self.tmp / "corto.mxf"  # 40 fotogramas copiados desde el 60, como un trozo consolidado en Avid
        subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", "2.0", "-i", str(self.largo), "-frames:v", "40", "-c", "copy",
                        "-f", "mxf_opatom", str(self.corto)], check=True, capture_output=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_subcodigo(self):
        largo, corto = mxf_util.subcodigo_dv(self.largo), mxf_util.subcodigo_dv(self.corto)
        self.assertEqual(largo["inicio"], {"tc": "17:35:48:11", "fecha": "2002-11-13", "hora": "23:27:26"})
        self.assertEqual(largo["fin"]["tc"], "17:35:54:10")
        self.assertEqual(largo["fotograma"], 240000)  # DVCPRO50 NTSC: dos canales de 10 secuencias
        self.assertEqual((corto["inicio"]["tc"], corto["fin"]["tc"], corto["inicio"]["hora"]), ("17:35:50:11", "17:35:51:20", "23:27:28"))

    def test_huella_de_fotograma(self):
        h = mxf_util.huella_fotograma_dv
        self.assertEqual(h(self.corto, 0), h(self.largo, 60))
        self.assertEqual(h(self.corto, 39), h(self.largo, 99))
        self.assertNotEqual(h(self.corto, 0), h(self.largo, 61))
        self.assertIsNone(h(self.largo, 180))  # fuera del clip

    def test_bytes_de_mas_al_final_de_la_esencia(self):
        """En ficheros de Avid reales el bloque de la esencia puede llevar bytes de más tras el último fotograma: el último
        se busca por número de fotogramas, no restando un fotograma al final del bloque (antes salía "fin": None)."""
        datos = self.largo.read_bytes()
        with open(self.largo, "rb") as f:
            pos, clave, n = next((p, c, k) for p, c, k in mxf_util._klv(f) if c[:12] == mxf_util.CLAVE_ESENCIA)
        inicio_ber = datos.rfind(clave, 0, pos) + 16
        largo_ber = pos - inicio_ber - 1
        ber = bytes([0x80 | largo_ber]) + (n + 1000).to_bytes(largo_ber, "big")
        raro = self.tmp / "avid.mxf"
        raro.write_bytes(datos[:inicio_ber] + ber + datos[pos:pos + n] + b"\x00" * 1000 + datos[pos + n:])
        r = mxf_util.subcodigo_dv(raro)
        self.assertEqual((r["fin"]["tc"], r["fotogramas"]), ("17:35:54:10", 180))
        self.assertEqual(mxf_util.huella_fotograma_dv(raro, 179), mxf_util.huella_fotograma_dv(self.largo, 179))

    def test_trozo_sin_timecode_en_el_largo(self):
        """Si del largo solo se sabe la hora de grabación (en segundos enteros), el trozo se busca en ±1 s."""
        try:
            import diagnostico_avid as da
        except ImportError:
            self.skipTest("sin pyavb")
        dv_largo, dv_corto = mxf_util.subcodigo_dv(self.largo), mxf_util.subcodigo_dv(self.corto)
        dv_largo = dict(dv_largo, inicio=dict(dv_largo["inicio"], tc=None, hora="23:27:27"), fin=None)  # 1 s de desfase
        clip = lambda clave, dv, dur: {"clave": clave, "nombre": clave, "fichero_video": clave + ".mxf", "fps": "30000/1001",
                                       "duracion": dur, "proyectos": ["P"], "dv": dv}
        largo, corto = clip("largo", dv_largo, 6.006), clip("corto", dv_corto, 40 / (30000 / 1001))
        self.assertEqual([c["clave"] for c in da.marcar_duplicados([largo, corto], self.tmp)], ["corto"])
        self.assertAlmostEqual(corto["contenido_en"]["desde"], 2.0, delta=0.05)  # fotograma 60

    def test_trozo_tras_un_corte_de_grabacion(self):
        """Disco real (00059S): la cámara paró y volvió a grabar dentro del largo y su timecode salta 6 minutos; el trozo
        copiado de después del salto se situaba por la diferencia de timecodes, fuera del largo, y seguía en el bruto."""
        try:
            import diagnostico_avid as da
        except ImportError:
            self.skipTest("sin pyavb")
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import dv_prueba
        import exportar_brutos as eb
        dt = __import__("datetime")
        dv_prueba.poner_subcodigo(self.largo, "17:35:48:11", dt.datetime(2002, 11, 13, 23, 27, 26), salto=(90, 360))
        tras = self.tmp / "tras_el_corte.mxf"  # 40 fotogramas desde el 120 (después del salto)
        subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", "4.004", "-i", str(self.largo), "-frames:v", "40", "-c", "copy",
                        "-f", "mxf_opatom", str(tras)], check=True, capture_output=True)
        dv_largo, dv_tras = mxf_util.subcodigo_dv(self.largo), mxf_util.subcodigo_dv(tras)
        self.assertEqual((dv_largo["fin"]["tc"], dv_tras["inicio"]["tc"]), ("17:41:54:10", "17:41:52:11"))
        a_f = lambda x: eb.tc_cinta(x, "30000/1001")
        self.assertEqual(mxf_util.situar_tc_dv(self.largo, [a_f("17:41:52:11"), a_f("17:35:49:11"), a_f("17:40:00:00")], a_f),
                         {a_f("17:41:52:11"): 120, a_f("17:35:49:11"): 30, a_f("17:40:00:00"): None})
        clip = lambda clave, dv, dur: {"clave": clave, "nombre": clave, "fichero_video": clave + ".mxf", "fps": "30000/1001",
                                       "duracion": dur, "proyectos": ["P"], "dv": dv}
        largo, corto = clip("largo", dv_largo, 6.006), clip("tras_el_corte", dv_tras, 40 / (30000 / 1001))
        self.assertEqual([c["clave"] for c in da.marcar_duplicados([largo, corto], self.tmp)], ["tras_el_corte"])
        self.assertAlmostEqual(corto["contenido_en"]["desde"], 4.0, delta=0.05)  # fotograma 120 del largo

    def test_mismo_clip_de_camara_dentro_y_fuera(self):
        """Por el nombre de clip P2 (30/10/2026): el trozo de después de un corte de grabación, con el mismo identificador
        que el largo, va a la papelera aunque sus fotogramas no se hayan podido comparar; otro trozo con el mismo
        identificador pero de otro momento (timecode que no está en el largo) se queda."""
        try:
            import diagnostico_avid as da
        except ImportError:
            self.skipTest("sin pyavb")
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import dv_prueba
        dt = __import__("datetime")
        dv_prueba.poner_subcodigo(self.largo, "17:35:48:11", dt.datetime(2002, 11, 13, 23, 27, 26), salto=(90, 360))
        tras = self.tmp / "tras.mxf"
        subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", "4.004", "-i", str(self.largo), "-frames:v", "40", "-c", "copy",
                        "-f", "mxf_opatom", str(tras)], check=True, capture_output=True)
        otro = self.tmp / "otro.mxf"
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=720x480:r=30000/1001:d=1", "-vf", "setfield=bff",
                        "-c:v", "dvvideo", "-pix_fmt", "yuv422p", "-f", "mxf_opatom", str(otro)], check=True, capture_output=True)
        dv_prueba.poner_subcodigo(otro, "18:30:00:00", dt.datetime(2002, 11, 13, 23, 50, 0))
        clip = lambda nombre, fichero, dur: {"clave": fichero, "nombre": nombre, "fichero_video": fichero, "fps": "30000/1001",
                                             "duracion": dur, "proyectos": ["P"], "dv": mxf_util.subcodigo_dv(self.tmp / fichero)}
        largo, corto, distinto = clip("00059S.new.01", "largo.mxf", 6.006), clip("00059S.new.04", "tras.mxf", 1.335), clip("00059S.new.09", "otro.mxf", 1.0)
        self.assertEqual([c["clave"] for c in da.repetidos_por_nombre([largo, corto, distinto], self.tmp)], ["tras.mxf"])
        self.assertEqual(corto["contenido_en"]["por"], "nombre")
        self.assertAlmostEqual(corto["contenido_en"]["desde"], 4.0, delta=0.05)
        self.assertNotIn("contenido_en", distinto)

    def test_un_bloque_por_fotograma_y_no_dv(self):
        pal = self.tmp / "pal.mxf"  # DV25 PAL en OP1a: un elemento por fotograma; sin subcódigo (ffmpeg no lo escribe)
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=720x576:r=25:d=3", "-c:v", "dvvideo",
                        "-pix_fmt", "yuv420p", "-f", "mxf", str(pal)], check=True, capture_output=True)
        r = mxf_util.subcodigo_dv(pal)
        self.assertEqual((r["fotograma"], r["inicio"]["tc"], r["fin"] is not None), (144000, None, True))
        self.assertIsNotNone(mxf_util.huella_fotograma_dv(pal, 74))
        self.assertIsNone(mxf_util.huella_fotograma_dv(pal, 75))
        self.assertIsNone(mxf_util.subcodigo_dv(self.tmp / "nada.mxf") if (self.tmp / "nada.mxf").write_bytes(b"x" * 100) else None)


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

    def test_ficha_y_rdd9(self):
        """Sin bmx (rdd9 None) la ficha lo dice y sigue conforme; si se pidió RDD 9 y el fichero no lo escribió bmx, falla."""
        esperado = {"pistas": 4, "tc": "10:00:00:00", "duracion": 2.0, "tc_gop": True}
        ok, texto = mxf_util.ficha_tecnica(FFPROBE, self.mxf, dict(esperado, rdd9=None))
        self.assertTrue(ok, texto)
        self.assertIn("sin RDD 9", texto)
        ok, texto = mxf_util.ficha_tecnica(FFPROBE, self.mxf, dict(esperado, rdd9=True))
        self.assertFalse(ok)
        self.assertIn("FALLO  Envoltura SMPTE RDD 9 (FFmpeg", texto)

    def test_fecha_de_creacion(self):
        self.assertIsNone(mxf_util.fecha_creacion(self.mxf))  # ffmpeg sin creation_time: fecha vacía
        con = self.tmp / "con_fecha.mxf"
        subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(self.mxf), "-map", "0", "-c", "copy", "-metadata",
                        "creation_time=2026-10-22T18:43:16.000000Z", "-f", "mxf", str(con)], check=True)
        self.assertEqual(mxf_util.fecha_creacion(con), "2026-10-22 18:43:16")

    def test_color_bt709_en_la_cabecera(self):
        self.assertEqual(mxf_util.color_mpeg2(self.mxf), (1, 1, 1))

    def test_paquetes_de_la_cabecera_sin_ffprobe(self):
        # lo mismo que da ffprobe (material_package_umid y file_package_umid de cada pista), leyendo solo la cabecera
        datos = json.loads(subprocess.run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(self.mxf)],
                                          capture_output=True, text=True, check=True).stdout)
        limpio = lambda t: (t or "").upper().replace("0X", "")
        esperado = {limpio(datos["format"]["tags"]["material_package_umid"])}
        esperado |= {limpio((s.get("tags") or {}).get("file_package_umid")) for s in datos["streams"]} - {""}
        p = mxf_util.paquetes(self.mxf)
        self.assertTrue(esperado <= p["umids"], (esperado, p))
        otro = self.tmp / "no_es_mxf.mxf"
        otro.write_bytes(b"\x00" * 4096)
        self.assertIsNone(mxf_util.paquetes(otro))  # no se sabe: hay que analizarlo entero
        self.assertIsNone(mxf_util.paquetes(self.tmp / "no_existe.mxf"))

    def test_solo_los_mxf_del_proyecto(self):
        import diagnostico_avid as da
        umid = next(iter(u for u in mxf_util.paquetes(self.mxf)["umids"]))
        ajeno = self.tmp / "ajeno.mxf"
        ajeno.write_bytes(b"\x00" * 4096)  # ilegible: entra por si acaso
        sobra = self.tmp / "sobra.mxf"
        shutil.copy(self.mxf, sobra)
        ficheros = [{"ruta": n, "ext": ".mxf"} for n in ("bruto.mxf", "ajeno.mxf")]
        bins = [{"ok": True, "clips": [{"nombre": "X", "mob_id": {"le": umid, "be": None}}]}]
        elegidos, total = da.mxf_de_proyectos(ficheros, self.tmp, bins, lambda m: None)
        self.assertEqual(([f["ruta"] for f in elegidos], total), (["ajeno.mxf", "bruto.mxf"], 2))
        bins = [{"ok": True, "clips": [{"nombre": "OTRO", "mob_id": {"le": "00" * 32}}]}]
        elegidos, _ = da.mxf_de_proyectos([{"ruta": "sobra.mxf", "ext": ".mxf"}], self.tmp, bins, lambda m: None)
        self.assertEqual(elegidos, [])  # ni su UMID ni su nombre: es de otro proyecto

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
                   "fps": s.get("r_frame_rate"), "field_order": s.get("field_order"), "color_primarias": s.get("color_primaries"),
                   "color_transfer": s.get("color_transfer"), "color_matriz": s.get("color_space"), "color_rango": s.get("color_range"),
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

    def test_doce_canales_entran_los_que_suenan(self):
        """Un audio de 12 canales con el sonido en el 9 al 12 y un bruto de 8 pistas: entran los que suenan, no los 8
        primeros (que dejarían el bruto sin sonido)."""
        self.ff("-f", "lavfi", "-i", "testsrc2=s=1280x720:r=25:d=2", "-c:v", "mpeg2video", str(self.tmp / "v.mxf"))
        entradas = []
        for k in range(12):
            entradas += ["-f", "lavfi", "-i", ("sine=f=%d:r=48000:d=2" % (300 + 100 * k)) if k >= 8 else "anullsrc=r=48000:cl=mono:d=2"]
        self.ff(*entradas, "-filter_complex", "".join("[%d:a]" % k for k in range(12)) + "amerge=inputs=12[a]", "-map", "[a]",
                "-t", "2", "-c:a", "pcm_s24le", str(self.tmp / "doce.wav"))
        seg, canales, info = self.segmento(["v.mxf", "doce.wav"], 8)
        self.assertEqual(len(canales), 12)
        vol = [self.volumen(seg, k) for k in range(8)]
        self.assertTrue(all(v > -40 for v in vol[:4]), vol)  # los canales 9 a 12
        self.assertTrue(all(v < -80 for v in vol[4:]), vol)
        self.assertIn("entran los que tienen sonido (9, 10, 11, 12, 1, 2, 3, 4)", info["aviso_audio"])
        self.assertIn("no entran las pistas 5, 6, 7, 8 del clip", info["aviso_audio"])

    def test_audio_mas_corto_que_el_video(self):
        self.ff("-f", "lavfi", "-i", "testsrc2=s=1280x720:r=25:d=2", "-c:v", "mpeg2video", str(self.tmp / "v.mxf"))
        self.ff("-f", "lavfi", "-i", "sine=f=440:r=48000:d=1", "-c:a", "pcm_s16le", str(self.tmp / "a.wav"))
        self.ff("-f", "lavfi", "-i", "sine=f=440:r=48000:d=3", "-c:a", "pcm_s16le", str(self.tmp / "largo.wav"))
        seg, _, _ = self.segmento(["v.mxf", "a.wav", "largo.wav"], 4)
        for d in self.duraciones_audio(seg):
            self.assertAlmostEqual(d, 2.0, delta=0.05)  # rellenado y cortado a la duración del vídeo


@unittest.skipUnless(FFMPEG and FFPROBE, "sin ffmpeg/ffprobe")
class VideoDeCualquierOrigen(AudioPorCanales):
    """Cualquier vídeo sale XDCAM conforme, derecho, sin deformar y con su duración real: vertical de móvil, HDR, velocidad
    variable, 4K 50p, 23,976p y SD anamórfico."""

    def convertir(self, nombre, pistas=4):
        seg, _, info = self.segmento([nombre], pistas)
        dur = self.perfil(nombre)["duracion"]
        ok, texto = mxf_util.ficha_tecnica(FFPROBE, seg, {"pistas": pistas, "tc": "00:00:00:00", "duracion": round(dur * 25) / 25})
        self.assertTrue(ok, texto)
        return seg, info

    def ancho_de_imagen(self, ruta):
        """Ancho de la imagen sin las bandas negras (cropdetect)."""
        r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(ruta), "-vf", "cropdetect=limit=24:round=2:reset=0", "-f", "null", "-"],
                           capture_output=True, text=True)
        return int(r.stderr.rsplit("crop=", 1)[1].split(":")[0])

    def test_movil_vertical_sale_derecho_con_bandas(self):
        self.ff("-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=2", "-f", "lavfi", "-i", "sine=r=48000:d=2", "-c:v", "libx264",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(self.tmp / "tumbado.mp4"))
        self.ff("-display_rotation:v:0", "90", "-i", str(self.tmp / "tumbado.mp4"), "-c", "copy", str(self.tmp / "vertical.mp4"))
        seg, _ = self.convertir("vertical.mp4")
        self.assertAlmostEqual(self.ancho_de_imagen(seg), 608, delta=8)  # 1080 de alto x 9/16: vertical, centrado entre bandas

    def psnr(self, a, b):
        """PSNR del bruto `a` frente a la referencia `b`, que siempre es BT.709 sin etiquetar. Se etiqueta aquí: si no, ffmpeg 7
        la toma por BT.601 al compararla con el bruto (que sí va etiquetado) y la convierte, y sale ~24 dB aunque sean iguales."""
        ref = "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv,format=yuv422p"
        r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(a), "-i", str(b), "-lavfi", "[0:v]format=yuv422p[x];[1:v]%s[y];[x][y]psnr" % ref,
                            "-f", "null", "-"], capture_output=True, text=True)
        return float(r.stderr.split("average:")[1].split()[0])

    def hdr(self, transfer, nombre, npl):
        """Una imagen normal (BT.709) pasada a HDR como haría una cámara: al convertirla al bruto debe parecerse a la original."""
        self.ff("-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=25:d=2", "-vf", "format=yuv422p", "-c:v", "ffv1", str(self.tmp / "ref.mkv"))
        self.ff("-i", str(self.tmp / "ref.mkv"), "-vf", "zscale=tin=bt709:pin=bt709:min=bt709:t=%s:p=bt2020:m=bt2020nc:npl=%d,format=yuv420p10le"
                % (transfer, npl), "-c:v", "libx265", "-x265-params", "log-level=error", "-color_primaries", "bt2020", "-color_trc", transfer,
                "-colorspace", "bt2020nc", str(self.tmp / nombre))
        seg, info = self.convertir(nombre)
        self.assertNotIn("aviso_video", info)
        return self.psnr(seg, self.tmp / "ref.mkv"), info

    # Umbrales medidos (oct. 2026): sin convertir el HDR sale ~14-15 dB frente a la original y con el mapeo de tonos hable
    # ~16; con la conversión, ~30 con ffmpeg 6.1 y 7.0 (los ~24 que salían en Windows eran de la comparación, no del bruto).
    def test_hdr_hlg_se_pasa_a_bt709(self):
        psnr, info = self.hdr("arib-std-b67", "hlg.mov", 100)
        self.assertEqual(info["color"], "HLG")
        self.assertGreater(psnr, 27, "PSNR %.1f dB" % psnr)

    def test_hdr_pq_se_pasa_a_bt709(self):
        psnr, info = self.hdr("smpte2084", "pq.mov", 203)
        self.assertEqual(info["color"], "PQ")
        self.assertGreater(psnr, 27, "PSNR %.1f dB" % psnr)

    def test_hd_sin_etiqueta_de_color_no_cambia_de_color(self):
        """HD sin la matriz de color en el fichero: es BT.709 (ffmpeg suponía BT.601 y corría los colores: 24,7 dB)."""
        self.ff("-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=25:d=2", "-vf", "format=yuv422p", "-c:v", "ffv1", str(self.tmp / "sin_etiqueta.mkv"))
        seg, _ = self.convertir("sin_etiqueta.mkv")
        psnr = self.psnr(seg, self.tmp / "sin_etiqueta.mkv")
        self.assertGreater(psnr, 35, "PSNR %.1f dB" % psnr)

    def test_entrelazado_sin_marcar_se_detecta(self):
        """Un fichero entrelazado que no lo dice: idet lo ve y se separan los campos como debe."""
        self.ff("-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=50:d=2", "-vf", "tinterlace=interleave_top,setfield=prog", "-c:v", "ffv1",
                str(self.tmp / "sin_marca.mkv"))
        self.assertEqual(eb.detectar_campos(FFMPEG, self.tmp / "sin_marca.mkv"), "tt")
        self.ff("-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=25:d=2", "-c:v", "ffv1", str(self.tmp / "progresivo.mkv"))
        self.assertEqual(eb.detectar_campos(FFMPEG, self.tmp / "progresivo.mkv"), "progressive")

    def test_velocidad_variable_conserva_duracion_y_audio(self):
        self.ff("-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=3", "-f", "lavfi", "-i", "sine=r=48000:d=3",
                "-vf", "setpts='(N+0.4*mod(N\\,3))/30/TB'", "-fps_mode", "passthrough", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", str(self.tmp / "vfr.mp4"))
        seg, _ = self.convertir("vfr.mp4")
        dur_video = self.perfil("vfr.mp4")["duracion"]
        for d in self.duraciones_audio(seg):
            self.assertAlmostEqual(d, dur_video, delta=0.1)

    def test_4k_50p(self):
        self.ff("-f", "lavfi", "-i", "testsrc2=s=3840x2160:r=50:d=1", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                str(self.tmp / "4k.mp4"))
        seg, _ = self.convertir("4k.mp4")
        self.assertGreaterEqual(self.ancho_de_imagen(seg), 1900)

    def test_23976_conserva_la_duracion(self):
        self.ff("-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=24000/1001:d=2", "-c:v", "prores_ks", "-profile:v", "1", str(self.tmp / "cine.mov"))
        seg, _ = self.convertir("cine.mov")
        r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(seg)], capture_output=True, text=True)
        self.assertAlmostEqual(float(r.stdout), self.perfil("cine.mov")["duracion"], delta=0.08)  # misma duración: no se acelera

    def test_sd_anamorfico_16_9_ocupa_todo(self):
        self.ff("-f", "lavfi", "-i", "testsrc2=s=720x576:r=25:d=2", "-vf", "setsar=64/45", "-c:v", "mpeg2video", "-flags", "+ildct+ilme",
                "-top", "1", str(self.tmp / "sd169.mxf"))
        seg, _ = self.convertir("sd169.mxf")
        self.assertGreaterEqual(self.ancho_de_imagen(seg), 1900)
