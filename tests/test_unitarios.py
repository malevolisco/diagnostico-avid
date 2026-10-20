#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pruebas unitarias (solo biblioteca estándar; no hace falta ffmpeg).

- Timecode drop-frame con valores conocidos.
- Reintento copia -> convertir de exportar_proyecto, sustituyendo ejecutar_ffmpeg
  y duracion_ffprobe por funciones que simulan fallos concretos.
- Botón Detener: corta un proceso en marcha y para la exportación entre proyectos.

Uso:  python -m unittest discover -s tests -v
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import exportar_brutos as eb  # noqa: E402
import actualizar  # noqa: E402

NTSC = 30000 / 1001
DUR = 2.0


class Timecode(unittest.TestCase):
    def test_drop_frame(self):
        self.assertEqual(eb.fmt_tc(1798, NTSC), "00:00:59;28")
        self.assertEqual(eb.fmt_tc(1799, NTSC), "00:00:59;29")
        self.assertEqual(eb.fmt_tc(1800, NTSC), "00:01:00;02")
        self.assertEqual(eb.fmt_tc(17982, NTSC), "00:10:00;00")
        self.assertEqual(eb.fmt_tc(107892, NTSC), "01:00:00;00")

    def test_drop_frame_5994(self):
        self.assertEqual(eb.fmt_tc(3599, 60000 / 1001), "00:00:59;59")
        self.assertEqual(eb.fmt_tc(3600, 60000 / 1001), "00:01:00;04")
        self.assertEqual(eb.fmt_tc(215784, 60000 / 1001), "01:00:00;00")

    def test_non_drop(self):
        self.assertEqual(eb.fmt_tc(90000, 25), "01:00:00:00")
        self.assertEqual(eb.fmt_tc(0, 25), "00:00:00:00")

    def test_es_df(self):
        self.assertIs(eb.es_df(25), False)
        self.assertIs(eb.es_df(30), False)
        self.assertIs(eb.es_df(NTSC), True)
        self.assertIs(eb.es_df(60000 / 1001), True)

    def test_ida_y_vuelta(self):
        for fps in (25, NTSC, 60000 / 1001):
            for frames in list(range(0, 40000, 7)) + [107892, 215784]:
                self.assertEqual(eb.tc_a_frames(eb.fmt_tc(frames, fps), fps), frames, (fps, frames))

    def test_timecode_inicio(self):
        self.assertEqual(eb.timecode_inicio({"timecode_inicio": "01:00:00:00"}, NTSC), "01:00:00;00")
        self.assertEqual(eb.timecode_inicio({"timecode_inicio": "01:00:00;00"}, 25), "01:00:00:00")
        self.assertEqual(eb.timecode_inicio({"timecode_inicio": "basura"}, 25), "00:00:00:00")


class Reintento(unittest.TestCase):
    """exportar_proyecto con ffmpeg simulado. Tres clips DNxHD iguales (plan: copia)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.clips, self.mxf_por_ruta = [], {}
        for i in range(3):
            v = "MXF/C%dV01.mxf" % i
            self.mxf_por_ruta[v] = {"ruta": v, "pistas": [{"tipo": "video", "codec": "dnxhd", "ancho": 1920, "alto": 1080,
                                                           "fps": "25/1", "pix_fmt": "yuv422p", "field_order": "progressive"}]}
            self.clips.append({"clave": "K%d" % i, "nombre": "CLIP %d" % i, "ficheros": [v], "fichero_video": v,
                               "num_audio": 0, "duracion": DUR, "bytes": 1000, "timecode": None})
        self.llamadas = []
        self._orig = (eb.ejecutar_ffmpeg, eb.duracion_ffprobe)

    def tearDown(self):
        eb.ejecutar_ffmpeg, eb.duracion_ffprobe = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def simular(self, falla):
        """falla(cmd) -> None si el comando va bien, o el texto de error."""
        def ejecutar(cmd, timeout):
            self.llamadas.append((cmd, timeout))
            err = falla(cmd)
            if err:
                return False, err
            Path(cmd[-1]).write_bytes(b"mxf")  # el último argumento es el fichero de salida
            return True, ""

        def duracion(ffprobe, ruta):  # un segmento dura lo que su clip; el bruto, la suma de los incluidos
            nombre = Path(ruta).name
            if nombre.startswith(("seg_", "prueba_")):
                return DUR
            return DUR * self.incluidos_esperados
        eb.ejecutar_ffmpeg, eb.duracion_ffprobe = ejecutar, duracion

    def exportar(self):
        cfg = eb.cargar_config(self.tmp / "config_brutos.json")
        destino = self.tmp / "brutos"
        destino.mkdir()
        return eb.exportar_proyecto("PRUEBA", self.clips, self.mxf_por_ruta, self.tmp, destino, "ffmpeg", "ffprobe",
                                    lambda m: None, cfg)

    def comprobar_timeouts(self):
        self.assertTrue(self.llamadas)
        for cmd, timeout in self.llamadas:
            self.assertIsInstance(timeout, (int, float), cmd)

    def test_codec_no_copiable_reintenta_convirtiendo(self):
        self.incluidos_esperados = 3
        self.simular(lambda cmd: "Invalid data" if "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] == "copy" else None)
        res = self.exportar()
        self.assertTrue(res["estado"].startswith("OK"), res)
        self.assertEqual(res["modo"], "convertir")
        self.assertIs(res["reintento"], True)
        self.assertEqual(res["incluidos"], 3)
        self.assertIn("motivo_reintento", res)
        self.comprobar_timeouts()

    def test_fichero_danado_no_reintenta(self):
        self.incluidos_esperados = 2
        self.simular(lambda cmd: "corrupt" if any("C1V01" in str(x) for x in cmd) else None)
        res = self.exportar()
        self.assertEqual(res["modo"], "copia")
        self.assertIs(res["reintento"], False)
        self.assertEqual((res["incluidos"], res["fallidos"]), (2, 1))
        self.assertEqual([(e["clip"], e["estado"]) for e in res["errores"]], [("CLIP 1", "ERROR al convertir")])
        self.assertTrue(res["estado"].startswith("OK"), res)
        self.comprobar_timeouts()

    def test_tiempo_agotado_no_reintenta(self):
        self.incluidos_esperados = 2
        self.simular(lambda cmd: eb.TIEMPO_AGOTADO if any("C2V01" in str(x) for x in cmd) else None)
        res = self.exportar()
        self.assertEqual(res["modo"], "copia")
        self.assertIs(res["reintento"], False)
        self.assertEqual([(e["clip"], e["estado"]) for e in res["errores"]], [("CLIP 2", "ERROR (tiempo agotado)")])
        # el clip con tiempo agotado no se prueba en convertir
        self.assertEqual(sum(1 for cmd, _ in self.llamadas if any("C2V01" in str(x) for x in cmd)), 1)
        self.comprobar_timeouts()

    def test_union_fallida_reintenta_convirtiendo(self):
        self.incluidos_esperados = 3
        estado = {"copia": True}

        def falla(cmd):
            if "concat" in cmd and estado["copia"]:
                estado["copia"] = False
                return "Could not write header"
            return None
        self.simular(falla)
        res = self.exportar()
        self.assertEqual((res["modo"], res["reintento"]), ("convertir", True))
        self.assertTrue(res["estado"].startswith("OK"), res)

    def test_union_tiempo_agotado_no_reintenta(self):
        self.incluidos_esperados = 3
        self.simular(lambda cmd: eb.TIEMPO_AGOTADO if "concat" in cmd else None)
        res = self.exportar()
        self.assertEqual((res["modo"], res["reintento"]), ("copia", False))
        self.assertEqual(res["estado"], "ERROR al unir (tiempo agotado)")


class Reanudar(unittest.TestCase):
    def test_ya_exportado(self):
        d = Path(tempfile.mkdtemp())
        try:
            (d / "B.mxf").write_bytes(b"12345")
            (d / "B_shotlist.xlsx").write_bytes(b"x")
            previo = {"estado": "OK", "clips": 3, "fichero": "B.mxf", "bytes": 5, "shotlist": "B_shotlist.xlsx"}
            self.assertTrue(eb.ya_exportado(previo, 3, d))
            self.assertTrue(eb.ya_exportado(dict(previo, estado="AVISO: duración"), 3, d))
            self.assertFalse(eb.ya_exportado(previo, 4, d))  # cambió el número de clips
            self.assertFalse(eb.ya_exportado(dict(previo, bytes=6), 3, d))  # fichero a medias o distinto
            self.assertFalse(eb.ya_exportado(dict(previo, estado="ERROR al unir"), 3, d))
            (d / "B_shotlist.xlsx").unlink()
            self.assertFalse(eb.ya_exportado(previo, 3, d))
        finally:
            shutil.rmtree(d, ignore_errors=True)


class Reparto(unittest.TestCase):
    """Brutos por tarjeta, y agencias y piezas aparte."""

    def clip(self, nombre, fecha, fichero_origen=None, origen="Sin determinar", proyectos=("P",), **extra):
        ruta = "MXF/%s.mxf" % nombre
        self.mxf[ruta] = {"ruta": ruta, "modificado": fecha}
        return dict({"clave": "K" + nombre, "nombre": nombre, "ficheros": [ruta], "fichero_video": ruta,
                     "fichero_origen": fichero_origen, "origen": origen, "proyectos": list(proyectos)}, **extra)

    def setUp(self):
        self.mxf = {}
        self.cfg = json.loads(json.dumps(eb.CONFIG_DEFECTO))

    def test_carpeta_tarjeta(self):
        self.assertEqual(eb.carpeta_tarjeta(r"D:\TARJETAS\CARD02\CONTENTS\VIDEO\0016Q3.MXF"), "D:/TARJETAS/CARD02")
        self.assertEqual(eb.carpeta_tarjeta(r"E:\CONTENTS\VIDEO\0016Q3.MXF"), "E:")
        self.assertEqual(eb.carpeta_tarjeta("/Volumes/CAM_A/PRIVATE/M4ROOT/CLIP/C0001.MP4"), "Volumes/CAM_A")
        self.assertIsNone(eb.carpeta_tarjeta(r"C:\Users\x\Downloads\video.mp4"))
        self.assertIsNone(eb.carpeta_tarjeta(None))

    def test_partir_por_fecha_carpeta_y_camara(self):
        cs = [self.clip("0001Q3", "2021-09-15 10:00:00"), self.clip("0002Q3", "2021-09-15 10:30:00"),
              self.clip("0003Q3", "2021-09-15 16:00:00"),  # más de 2 h después: otra tarjeta
              self.clip("0001R7", "2021-09-15 10:05:00"),  # otra cámara P2
              self.clip("A", "2021-09-15 10:00:00", r"E:\X\CARD01\CONTENTS\V\A.MXF"),
              self.clip("B", "2021-09-15 10:00:00", r"E:\X\CARD02\CONTENTS\V\B.MXF")]
        t = eb.partir_por_tarjeta(cs, self.mxf, self.cfg)
        grupos = sorted(sorted(c["nombre"] for c in g) for g, _ in t)
        self.assertEqual(grupos, [["0001Q3", "0002Q3"], ["0001R7"], ["0003Q3"], ["A"], ["B"]])
        self.assertEqual(t[-1][0][0]["nombre"], "0003Q3")  # ordenadas por fecha

    def test_agencias_piezas_y_renders_aparte(self):
        cruce = {"por_proyecto": [{"proyecto": "P", "secuencias_nombres": ["PIEZA SISMO"]}]}
        cs = [self.clip("0001Q3", "2021-09-15 10:00:00"),
              self.clip("X", "2021-09-15 10:00:00", origen="Agencia Reuters"),
              self.clip("PIEZA SISMO.new.01", "2021-09-15 12:00:00"),
              self.clip("Y", "2021-09-15 12:00:00", fichero_origen=r"C:\export\Pieza Sismo.mov"),
              self.clip("Title: 1", "2021-09-15 12:00:00", proyectos=(), render=True, proyectos_render=["P"])]
        g = {x["nombre"]: sorted(c["nombre"] for c in x["clips"]) for x in eb.grupos_de_exportacion(cs, cruce, self.mxf, self.cfg)}
        self.assertEqual(g, {"P_T01_2021-09-15": ["0001Q3"], "P_AGENCIAS": ["X"],
                             "P_PIEZAS": ["PIEZA SISMO.new.01", "Title: 1", "Y"]})

    def test_sin_partir(self):
        self.cfg.update(partir_por_tarjeta=False, agencias_aparte=False, piezas_aparte=False, renders_en_piezas=False)
        cs = [self.clip("A", "2021-09-14 10:00:00"), self.clip("B", "2021-09-15 10:00:00", origen="Agencia Reuters"),
              self.clip("T", "2021-09-15 10:00:00", proyectos=(), render=True, proyectos_render=["P"])]
        g = {x["nombre"]: sorted(c["nombre"] for c in x["clips"]) for x in eb.grupos_de_exportacion(cs, {}, self.mxf, self.cfg)}
        self.assertEqual(g, {"P": ["A", "B"]})

    def test_firma_distinta_no_reanuda(self):
        d = Path(tempfile.mkdtemp())
        try:
            (d / "B.mxf").write_bytes(b"12345")
            (d / "B_shotlist.xlsx").write_bytes(b"x")
            previo = {"estado": "OK", "clips": 1, "fichero": "B.mxf", "bytes": 5, "shotlist": "B_shotlist.xlsx",
                      "firma": eb.firma_clips([{"clave": "K1"}])}
            self.assertTrue(eb.ya_exportado(previo, 1, d, eb.firma_clips([{"clave": "K1"}])))
            self.assertFalse(eb.ya_exportado(previo, 1, d, eb.firma_clips([{"clave": "K2"}])))
        finally:
            shutil.rmtree(d, ignore_errors=True)


class Actualizacion(unittest.TestCase):
    """Autoactualización con una 'release' falsa en ficheros locales (file://). El cambio de carpeta
    lo hace un .bat de Windows: eso se prueba en el workflow de GitHub con el .exe."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._env = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = str(self.tmp / "localappdata")
        self._lanzar = actualizar.lanzar_cambio
        self.lanzados = []
        actualizar.lanzar_cambio = lambda *a, **k: self.lanzados.append((a, k))
        self.app = self.tmp / "Escritorio" / "DiagnosticoAvid"
        self.app.mkdir(parents=True)
        self._url = os.environ.get("DIAGNOSTICO_URL_ACTUALIZACION")
        os.environ["DIAGNOSTICO_URL_ACTUALIZACION"] = "file:prueba"  # permite file:// como en la prueba del workflow

    def tearDown(self):
        actualizar.lanzar_cambio = self._lanzar
        if self._url is None:
            os.environ.pop("DIAGNOSTICO_URL_ACTUALIZACION", None)
        else:
            os.environ["DIAGNOSTICO_URL_ACTUALIZACION"] = self._url
        if self._env is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = self._env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def release(self, tag, con_exe=True, nombre_zip=actualizar.NOMBRE_ZIP):
        z = self.tmp / "zip" / nombre_zip
        z.parent.mkdir(exist_ok=True)
        import zipfile
        with zipfile.ZipFile(z, "w") as f:
            f.writestr("DiagnosticoAvid/_internal/build.txt", tag + " abc 2026-10-20")
            if con_exe:
                f.writestr("DiagnosticoAvid/DiagnosticoAvid.exe", b"MZ")
        datos = {"tag_name": tag, "assets": [{"name": nombre_zip, "browser_download_url": z.as_uri(), "size": z.stat().st_size}]}
        j = self.tmp / "latest.json"
        j.write_text(json.dumps(datos), encoding="utf-8")
        return j.as_uri()

    def test_numero_build(self):
        self.assertEqual(actualizar.numero_build("build-12 abc1234 2026-10-20"), 12)
        self.assertEqual(actualizar.numero_build("build-9"), 9)
        self.assertIsNone(actualizar.numero_build("local"))

    def test_solo_si_es_mas_nueva(self):
        url = self.release("build-10")
        self.assertEqual(actualizar.buscar_version("build-9 x", url)["tag"], "build-10")
        self.assertIsNone(actualizar.buscar_version("build-10 x", url))
        self.assertIsNone(actualizar.buscar_version("build-11 x", url))
        self.assertIsNone(actualizar.buscar_version("local", url))  # ejecutado con Python: nunca

    def test_descarga_comprueba_y_lanza_el_cambio(self):
        url = self.release("build-10")
        self.assertEqual(actualizar.actualizar("build-9 x", self.app, url=url), "cambio_lanzado")
        (app, nueva, trabajo, tag, relanzar), _ = self.lanzados[0]
        self.assertIs(relanzar, True)
        self.assertEqual((app, tag), (self.app, "build-10"))
        self.assertEqual((nueva / "_internal" / "build.txt").read_text(), "build-10 abc 2026-10-20")
        self.assertEqual(trabajo.parent, self.app.parent)  # misma unidad: el cambio es un renombrado

    def test_zip_sin_exe_no_se_instala(self):
        url = self.release("build-10", con_exe=False)
        self.assertTrue(actualizar.actualizar("build-9 x", self.app, url=url).startswith("error"))
        self.assertEqual(self.lanzados, [])
        self.assertFalse((self.app.parent / "DiagnosticoAvid_actualizacion").exists())

    def test_release_sin_zip_no_se_instala(self):
        url = self.release("build-10", nombre_zip="otro.zip")
        self.assertTrue(actualizar.actualizar("build-9 x", self.app, url=url).startswith("error"))
        self.assertEqual(self.lanzados, [])

    def test_sin_internet_sigue(self):
        r = actualizar.actualizar("build-9 x", self.app, url=(self.tmp / "no_existe.json").as_uri())
        self.assertTrue(r.startswith("error"))
        self.assertEqual(self.lanzados, [])

    def test_solo_https_fuera_de_pruebas(self):
        del os.environ["DIAGNOSTICO_URL_ACTUALIZACION"]
        url = self.release("build-10")  # file://
        self.assertTrue(actualizar.actualizar("build-9 x", self.app, url=url).startswith("error: dirección no permitida"))
        self.assertEqual(actualizar.comprobar_url("https://api.github.com/x"), "https://api.github.com/x")

    def test_no_reintenta_una_version_que_fallo(self):
        url = self.release("build-10")
        actualizar.marca_fallo("build-10").write_text("build-10")
        self.assertEqual(actualizar.actualizar("build-9 x", self.app, url=url), "al_dia")
        self.assertEqual(self.lanzados, [])


class Detener(unittest.TestCase):
    """El botón Detener activa PARAR. Se usa un proceso real (python durmiendo) en lugar de ffmpeg."""
    DORMIR = [sys.executable, "-c", "import time; time.sleep(30)"]

    def tearDown(self):
        eb.PARAR.clear()

    def test_corta_el_proceso_en_marcha(self):
        threading.Timer(0.5, eb.PARAR.set).start()
        t0 = time.monotonic()
        with self.assertRaises(eb.Detenido):
            eb.ejecutar_ffmpeg(self.DORMIR, 60)
        self.assertLess(time.monotonic() - t0, 5)

    def test_no_arranca_si_ya_se_ha_pedido(self):
        eb.PARAR.set()
        with self.assertRaises(eb.Detenido):
            eb.ejecutar_ffmpeg(self.DORMIR, 60)

    def test_tiempo_agotado(self):
        t0 = time.monotonic()
        self.assertEqual(eb.ejecutar_ffmpeg(self.DORMIR, 1), (False, eb.TIEMPO_AGOTADO))
        self.assertLess(time.monotonic() - t0, 5)

    def test_exportacion_para_entre_proyectos(self):
        tmp = Path(tempfile.mkdtemp())
        orig = (eb.ejecutar_ffmpeg, eb.duracion_ffprobe)
        try:
            mxf, clips = [], []
            for p in ("A", "B", "C"):
                v = "MXF/%sV01.mxf" % p
                mxf.append({"ruta": v, "pistas": [{"tipo": "video", "codec": "dnxhd", "ancho": 1920, "alto": 1080,
                                                   "fps": "25/1", "pix_fmt": "yuv422p", "field_order": "progressive"}]})
                clips.append({"clave": "K" + p, "nombre": "CLIP " + p, "ficheros": [v], "fichero_video": v, "num_audio": 0,
                              "duracion": DUR, "bytes": 1000, "timecode": None, "proyectos": ["PROY " + p]})

            def ejecutar(cmd, timeout):
                if any("BV01" in str(x) for x in cmd):  # se pulsa Detener mientras se hace el proyecto B
                    eb.PARAR.set()
                    raise eb.Detenido()
                Path(cmd[-1]).write_bytes(b"mxf")
                return True, ""
            eb.ejecutar_ffmpeg, eb.duracion_ffprobe = ejecutar, lambda ffprobe, ruta: DUR
            cfg = eb.cargar_config(tmp / "config_brutos.json")
            destino = tmp / "brutos"
            res = eb.exportar_todo(clips, {}, mxf, tmp, destino, "ffmpeg", "ffprobe", lambda m: None, cfg)
            self.assertEqual([r["proyecto"] for r in res], ["PROY A_T01"])
            guardado = json.loads((destino / "brutos_resultado.json").read_text(encoding="utf-8"))
            self.assertEqual([r["proyecto"] for r in guardado], ["PROY A_T01"])
            self.assertFalse(list(destino.glob("_tmp_*")), "queda la carpeta temporal del proyecto a medias")
            self.assertFalse((destino / "BRUTO_PROY B_T01.mxf").exists())
        finally:
            eb.ejecutar_ffmpeg, eb.duracion_ffprobe = orig
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
