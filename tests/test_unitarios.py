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

try:  # diagnostico_avid necesita pyavb; sin él se saltan sus pruebas
    import diagnostico_avid as da
except Exception:  # pragma: no cover
    da = None

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
        cfg["formato_salida"] = "auto"  # DNxHD iguales: copia
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
    """agrupar=proyecto: brutos por tarjeta, y agencias, importados y piezas aparte."""

    def clip(self, nombre, fecha, fichero_origen=None, origen="Sin determinar", proyectos=("P",), **extra):
        ruta = "MXF/%s.mxf" % nombre
        self.mxf[ruta] = {"ruta": ruta, "modificado": fecha}
        return dict({"clave": "K" + nombre, "nombre": nombre, "ficheros": [ruta], "fichero_video": ruta,
                     "fichero_origen": fichero_origen, "origen": origen, "proyectos": list(proyectos)}, **extra)

    def setUp(self):
        self.mxf = {}
        self.cfg = json.loads(json.dumps(eb.CONFIG_DEFECTO))
        self.cfg.update(agrupar="proyecto", excluir_importados=False)

    def test_carpeta_tarjeta(self):
        self.assertEqual(eb.carpeta_tarjeta(r"D:\TARJETAS\CARD02\CONTENTS\VIDEO\0016Q3.MXF"), "D:/TARJETAS/CARD02")
        self.assertEqual(eb.carpeta_tarjeta(r"E:\CONTENTS\VIDEO\0016Q3.MXF"), "E:")
        self.assertEqual(eb.carpeta_tarjeta("/Volumes/CAM_A/PRIVATE/M4ROOT/CLIP/C0001.MP4"), "Volumes/CAM_A")
        self.assertIsNone(eb.carpeta_tarjeta(r"C:\Users\x\Downloads\video.mp4"))
        self.assertIsNone(eb.carpeta_tarjeta(None))

    def test_partir_por_tandas_de_volcado_y_carpeta(self):
        # Nombres P2 como los del disco real: no indican la tarjeta (00016R, 0002CI, 000328 son seguidos)
        cs = [self.clip("00016R", "2018-12-12 20:19:19", duracion=600), self.clip("0002CI", "2018-12-12 20:19:48", duracion=600),
              self.clip("000328", "2018-12-12 20:19:50", duracion=600),
              self.clip("0001GD", "2018-12-17 18:18:09", duracion=600),  # días después: otra tarjeta
              self.clip("A", "2018-12-12 20:19:00", r"E:\\X\\CARD01\\CONTENTS\\V\\A.MXF", duracion=600),
              self.clip("B", "2018-12-12 20:19:00", r"E:\\X\\CARD02\\CONTENTS\\V\\B.MXF", duracion=600)]
        t = eb.partir_por_tarjeta(cs, self.mxf, self.cfg)
        self.assertEqual(sorted(sorted(c["nombre"] for c in g) for g, _ in t), [["00016R", "0002CI", "000328"], ["0001GD"], ["A"], ["B"]])
        self.assertEqual(t[-1][0][0]["nombre"], "0001GD")  # ordenadas por fecha

    def test_tandas_pequenas_se_juntan(self):
        cs = [self.clip("A", "2018-12-12 10:00:00", duracion=1800), self.clip("B", "2018-12-12 13:00:00", duracion=60),
              self.clip("C", "2018-12-12 20:00:00", duracion=1800), self.clip("D", "2018-12-20 10:00:00", duracion=60)]
        t = eb.partir_por_tarjeta(cs, self.mxf, self.cfg)
        # B (1 min) se junta con la tanda más cercana (A, 3 h antes); D está sola a más de 24 h y se queda aparte
        self.assertEqual([sorted(c["nombre"] for c in g) for g, _ in t], [["A", "B"], ["C"], ["D"]])
        self.cfg["tarjeta_minimo_minutos"] = 0
        self.assertEqual(len(eb.partir_por_tarjeta(cs, self.mxf, self.cfg)), 4)

    def test_agencias_piezas_y_renders_aparte(self):
        self.cfg["renders_en_piezas"] = True
        cruce = {"por_proyecto": [{"proyecto": "P", "secuencias_nombres": ["PIEZA SISMO"]}]}
        cs = [self.clip("0001Q3", "2021-09-15 10:00:00"),
              self.clip("X", "2021-09-15 10:00:00", origen="Agencia Reuters"),
              self.clip("PIEZA SISMO.new.01", "2021-09-15 12:00:00"),
              self.clip("Y", "2021-09-15 12:00:00", fichero_origen=r"C:\export\Pieza Sismo.mov"),
              self.clip("Title: 1", "2021-09-15 12:00:00", proyectos=(), render=True, proyectos_render=["P"]),
              self.clip("VID_0001.mp4", "2021-09-15 12:00:00", fichero_origen=r"C:\\Users\\admin\\Desktop\\videos movil\\VID_0001.mp4"),
              self.clip("B.MXF", "2021-09-15 12:00:00", fichero_origen=r"E:\\CONTENTS\\VIDEO\\B.MXF")]  # tarjeta montada: no es importado
        g = {x["nombre"]: sorted(c["nombre"] for c in x["clips"]) for x in eb.grupos_de_exportacion(cs, cruce, self.mxf, self.cfg)}
        self.assertEqual(g, {"P_T01_2021-09-15": ["0001Q3"], "P_T02_2021-09-15": ["B.MXF"], "P_AGENCIAS": ["X"],
                             "P_IMPORTADOS": ["VID_0001.mp4"], "P_PIEZAS": ["PIEZA SISMO.new.01", "Title: 1", "Y"]})

    def test_sin_partir(self):
        self.cfg.update(partir_por_tarjeta=False, agencias_aparte=False, piezas_aparte=False, renders_en_piezas=False, importados_aparte=False)
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


class PorDias(unittest.TestCase):
    """agrupar=dias: todo el disco por días seguidos hasta 3 h, sin separar proyectos."""

    def setUp(self):
        self.mxf = {}
        self.cfg = json.loads(json.dumps(eb.CONFIG_DEFECTO))
        self.cfg.update(agrupar="dias", horas_por_bruto=3, excluir_importados=False)

    def clip(self, nombre, fecha, horas, proyectos=("P",), **extra):
        ruta = "MXF/%s.mxf" % nombre
        if fecha:
            self.mxf[ruta] = {"ruta": ruta, "modificado": fecha}
        return dict({"clave": "K" + nombre, "nombre": nombre, "ficheros": [ruta], "fichero_video": ruta,
                     "duracion": horas * 3600, "origen": "Sin determinar", "proyectos": list(proyectos)}, **extra)

    def nombres(self, cs, cruce=None):
        return {g["nombre"]: [c["nombre"] for c in g["clips"]] for g in eb.grupos_de_exportacion(cs, cruce or {}, self.mxf, self.cfg)}

    def test_dias_seguidos_hasta_tres_horas(self):
        cs = [self.clip("A", "2018-12-01 20:00:00", 1), self.clip("B", "2018-12-02 20:00:00", 1.5),
              self.clip("C", "2018-12-04 20:00:00", 1),  # A+B+C pasaría de 3 h: C abre otro bruto
              self.clip("D", "2018-12-20 20:00:00", 0.5)]
        self.assertEqual(self.nombres(cs), {"2018-12-01_a_2018-12-02": ["A", "B"], "2018-12-04_a_2018-12-20": ["C", "D"]})

    def test_dia_largo_se_parte(self):
        cs = [self.clip("A", "2018-12-12 10:00:00", 2), self.clip("B", "2018-12-12 11:00:00", 2),
              self.clip("C", "2018-12-12 12:00:00", 0.5)]
        self.assertEqual(self.nombres(cs), {"2018-12-12_parte1": ["A"], "2018-12-12_parte2": ["B", "C"]})

    def test_madrugada_cuenta_como_dia_anterior(self):
        cs = [self.clip("A", "2018-12-01 23:00:00", 0.5), self.clip("B", "2018-12-02 02:30:00", 0.5)]
        self.assertEqual(self.nombres(cs), {"2018-12-01": ["A", "B"]})

    def test_proyectos_juntos_sin_repetir_y_aparte_agencias_y_piezas(self):
        cruce = {"por_proyecto": [{"proyecto": "Q", "secuencias_nombres": ["PIEZA SISMO"]}]}
        cs = [self.clip("A", "2018-12-01 20:00:00", 0.5, proyectos=("P", "Q")),  # compartido: una sola vez
              self.clip("B", "2018-12-01 21:00:00", 0.5, proyectos=("Q",)),
              self.clip("H", "2018-12-01 22:00:00", 0.5, proyectos=()),  # huérfano
              self.clip("R", "2018-12-01 20:30:00", 0.5, origen="Agencia Reuters"),
              self.clip("PIEZA SISMO", "2018-12-03 10:00:00", 0.1, proyectos=("Q",)),
              self.clip("X,Dissolve,1", "2018-12-01 20:00:00", 0.01, proyectos=(), render=True),
              self.clip("Title: 1", "2018-12-01 20:00:00", 0.01, proyectos=(), render=True, proyectos_render=["Q"])]
        self.assertEqual(self.nombres(cs, cruce), {"2018-12-01": ["A", "B", "H"], "AGENCIAS_2018-12-01": ["R"],
                                                   "PIEZAS_2018-12-03": ["PIEZA SISMO"]})

    def test_dentro_del_bloque_agrupados_por_proyecto(self):
        cs = [self.clip("P1", "2018-12-01 20:00:00", 0.1, proyectos=("PANAMA",)), self.clip("H1", "2018-12-01 20:05:00", 0.1, proyectos=()),
              self.clip("M1", "2018-12-01 20:10:00", 0.1, proyectos=("MEXICO", "PANAMA")),  # compartido: va con el primero (MEXICO)
              self.clip("P2", "2018-12-01 21:00:00", 0.1, proyectos=("PANAMA",)), self.clip("M2", "2018-12-02 09:00:00", 0.1, proyectos=("MEXICO",))]
        # PANAMA empieza antes que MEXICO; los sin proyecto, al final
        self.assertEqual(self.nombres(cs), {"2018-12-01_a_2018-12-02": ["P1", "P2", "M1", "M2", "H1"]})
        fila = eb._fila_clip(1, "x", cs[2], self.mxf)
        self.assertEqual((fila["proyecto"], fila["proyecto_principal"]), ("MEXICO (también en PANAMA)", "MEXICO"))

    def test_sin_fecha_al_final(self):
        cs = [self.clip("A", "2018-12-01 20:00:00", 0.5), self.clip("Z", None, 0.5)]
        self.assertEqual(self.nombres(cs), {"2018-12-01": ["A"], "SIN_FECHA": ["Z"]})


class ProyectoMes(unittest.TestCase):
    """agrupar=proyecto_mes (por defecto, correo del Archivo de oct. 2026): sin importados, nombre PROYECTO_AAAA-MM,
    hasta 2 h y en orden cronológico estricto."""

    def setUp(self):
        self.mxf = {}
        self.cfg = json.loads(json.dumps(eb.CONFIG_DEFECTO))

    def clip(self, nombre, fecha, minutos, proyectos=("P",), **extra):
        ruta = "MXF/%s.mxf" % nombre
        if fecha:
            self.mxf[ruta] = {"ruta": ruta, "modificado": fecha}
        return dict({"clave": "K" + nombre, "nombre": nombre, "ficheros": [ruta], "fichero_video": ruta,
                     "duracion": minutos * 60, "origen": "Cámara (tarjeta P2)", "proyectos": list(proyectos)}, **extra)

    def nombres(self, cs, cruce=None):
        return {g["nombre"]: [c["nombre"] for c in g["clips"]] for g in eb.grupos_de_exportacion(cs, cruce or {}, self.mxf, self.cfg)}

    def test_por_defecto(self):
        self.assertEqual((eb.CONFIG_DEFECTO["agrupar"], eb.CONFIG_DEFECTO["horas_por_bruto"], eb.CONFIG_DEFECTO["formato_salida"],
                          eb.CONFIG_DEFECTO["excluir_importados"], eb.CONFIG_DEFECTO["prefijo_fichero"]),
                         ("proyecto_mes", 2, "xdcam_hd50", True, ""))

    def test_importados_y_agencias_fuera(self):
        cs = [self.clip("0016Q3", "2019-09-16 10:52:26", 1),
              self.clip("VID_0001", "2019-09-16 11:00:00", 1, origen="Fichero importado",
                        fichero_origen=r"D:\Documents and Settings\rtve1\Mis documentos\Mis vídeos\VID_0001.mp4"),
              self.clip("SIN RUTA", "2019-09-16 11:00:00", 1, origen="Fichero importado"),
              self.clip("REUTERS", "2019-09-16 11:00:00", 1, origen="Agencia Reuters", fichero_origen=r"C:\Downloads\x_RTRMADV_y.MP4"),
              # MXF importado de una tarjeta P2: el origen dice "Fichero importado" pero la ruta es de tarjeta
              self.clip("0001AB", "2019-09-16 12:00:00", 1, origen="Fichero importado", fichero_origen=r"E:\CARD01\CONTENTS\VIDEO\0001AB.MXF"),
              self.clip("X,Dissolve,1", "2019-09-16 12:00:00", 0.1, proyectos=(), render=True)]
        self.assertEqual(self.nombres(cs), {"P_2019-09": ["0016Q3", "0001AB"]})
        self.cfg["excluir_importados"] = False
        self.assertEqual(len(self.nombres(cs)["P_2019-09"]), 5)

    def test_orden_cronologico_estricto(self):
        # Las fechas de la foto del correo, que salieron desordenadas
        fechas = ["2019-10-14 20:55:14", "2019-10-09 17:40:36", "2019-09-16 20:26:20", "2019-09-18 17:43:26", "2019-09-04 21:12:18"]
        cs = [self.clip("C%d" % i, f, 1) for i, f in enumerate(fechas)]
        g = eb.grupos_de_exportacion(cs, {}, self.mxf, self.cfg)
        self.assertEqual(len(g), 1)
        self.assertEqual(g[0]["nombre"], "P_2019-09_a_2019-10")
        orden = [self.mxf[c["fichero_video"]]["modificado"] for c in g[0]["clips"]]
        self.assertEqual(orden, sorted(fechas))
        self.assertTrue(g[0]["ordenado"])

    def test_tope_de_dos_horas(self):
        # A+B = 110 min; C (20 min) pasaría de 2 h: no se añade, abre el bruto siguiente
        cs = [self.clip("A", "2019-09-01 10:00:00", 60), self.clip("B", "2019-09-02 10:00:00", 50),
              self.clip("C", "2019-09-03 10:00:00", 20), self.clip("D", "2019-09-04 10:00:00", 30),
              self.clip("E", "2019-10-01 10:00:00", 150)]  # más de 2 h por sí solo: va solo
        self.assertEqual(self.nombres(cs), {"P_2019-09_1": ["A", "B"], "P_2019-09_2": ["C", "D"], "P_2019-10": ["E"]})

    def test_proyectos_piezas_compartidos_y_sin_fecha(self):
        cruce = {"por_proyecto": [{"proyecto": "Q", "secuencias_nombres": ["PIEZA SISMO"]}]}
        cs = [self.clip("A", "2018-12-01 20:00:00", 1, proyectos=("Q", "P")),  # compartido: solo con el primero (P)
              self.clip("B", "2018-12-01 21:00:00", 1, proyectos=("Q",)), self.clip("H", "2018-12-02 09:00:00", 1, proyectos=()),
              self.clip("PIEZA SISMO", "2018-12-03 10:00:00", 1, proyectos=("Q",)), self.clip("Z", None, 1)]
        self.assertEqual(self.nombres(cs, cruce), {"P_2018-12": ["A"], "P_SIN_FECHA": ["Z"], "Q_2018-12": ["B"],
                                                   "Q_PIEZAS_2018-12": ["PIEZA SISMO"], "SIN_PROYECTO_2018-12": ["H"]})


class OrdenYParalelo(unittest.TestCase):
    """Orden estricto por fecha y hora (desempate por timecode), varios clips a la vez y selección de proyectos."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._orig = (eb.ejecutar_ffmpeg, eb.duracion_ffprobe)

    def tearDown(self):
        eb.ejecutar_ffmpeg, eb.duracion_ffprobe = self._orig
        eb.PARAR.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_mismo_segundo_desempata_por_timecode(self):
        mxf = {"a": {"modificado": "2019-09-16 10:52:26"}, "b": {"modificado": "2019-09-16 10:52:26"}, "c": {"modificado": "2019-09-16 10:52:25"}}
        cs = [{"nombre": "0016Q3", "fichero_video": "a", "ficheros": ["a"], "timecode": "14:02:10:00"},
              {"nombre": "00016R", "fichero_video": "b", "ficheros": ["b"], "timecode": "13:58:00;12"},
              {"nombre": "ZZZ", "fichero_video": "c", "ficheros": ["c"], "timecode": "23:00:00:00"}]
        self.assertEqual([c["nombre"] for c in sorted(cs, key=lambda c: eb.orden_cronologico(c, mxf))], ["ZZZ", "00016R", "0016Q3"])

    def test_clips_a_la_vez(self):
        self.assertGreaterEqual(eb.clips_a_la_vez({"clips_a_la_vez": "auto"}), 1)
        self.assertLessEqual(eb.clips_a_la_vez({"clips_a_la_vez": "auto"}), 6)
        self.assertEqual(eb.clips_a_la_vez({"clips_a_la_vez": 3}), 3)
        self.assertEqual(eb.clips_a_la_vez({"clips_a_la_vez": "x"}), 1)

    def preparar(self, n, falla=None):
        mxf, clips = {}, []
        for i in range(n):
            v = "MXF/C%02dV01.mxf" % i
            mxf[v] = {"ruta": v, "modificado": "2019-09-16 10:%02d:00" % i,
                      "pistas": [{"tipo": "video", "codec": "dvvideo", "ancho": 720, "alto": 480, "fps": "30000/1001",
                                  "pix_fmt": "yuv411p", "field_order": "bb"}]}
            clips.append({"clave": "K%d" % i, "nombre": "CLIP %02d" % i, "ficheros": [v], "fichero_video": v, "num_audio": 0,
                          "duracion": DUR, "bytes": 1000, "timecode": None, "proyectos": ["P%d" % (i % 2)]})
        self.en_marcha, self.maximo = 0, 0
        cerrojo = threading.Lock()

        def ejecutar(cmd, timeout):
            with cerrojo:
                self.en_marcha += 1
                self.maximo = max(self.maximo, self.en_marcha)
            try:
                time.sleep(0.05)
                if falla:
                    falla(cmd)
                if eb.PARAR.is_set():
                    raise eb.Detenido()
                Path(cmd[-1]).write_bytes(b"mxf")
                return True, ""
            finally:
                with cerrojo:
                    self.en_marcha -= 1
        eb.ejecutar_ffmpeg = ejecutar
        eb.duracion_ffprobe = lambda ffprobe, ruta: DUR if Path(ruta).name.startswith("seg_") else DUR * n
        return mxf, clips

    def test_convierte_varios_a_la_vez_y_une_en_orden(self):
        mxf, clips = self.preparar(6)
        cfg = json.loads(json.dumps(eb.CONFIG_DEFECTO))
        cfg["clips_a_la_vez"] = 3
        destino = self.tmp / "brutos"
        destino.mkdir()
        unidos = []
        orig_unir = eb.unir_segmentos
        eb.unir_segmentos = lambda segs, salida, *a: (unidos.extend(Path(s).name for s in segs), salida.write_bytes(b"x"), (True, None, []))[2]
        try:
            res = eb.exportar_proyecto("P", list(reversed(clips)), mxf, self.tmp, destino, "ffmpeg", "ffprobe", lambda m: None, cfg)
        finally:
            eb.unir_segmentos = orig_unir
        self.assertEqual((res["modo"], res["incluidos"], res["estado"]), ("convertir", 6, "OK"))
        self.assertEqual(self.maximo, 3)  # tres ffmpeg a la vez
        self.assertEqual(unidos, ["seg_%05d.mxf" % i for i in range(1, 7)])  # en orden cronológico, no en el de llegada

    def test_detener_con_varios_a_la_vez(self):
        def falla(cmd):
            if "C02V01" in " ".join(map(str, cmd)):
                eb.PARAR.set()
        mxf, clips = self.preparar(8, falla)
        cfg = json.loads(json.dumps(eb.CONFIG_DEFECTO))
        cfg["clips_a_la_vez"] = 3
        destino = self.tmp / "brutos"
        destino.mkdir()
        with self.assertRaises(eb.Detenido):
            eb.exportar_proyecto("P", clips, mxf, self.tmp, destino, "ffmpeg", "ffprobe", lambda m: None, cfg)
        self.assertFalse(list(destino.glob("_tmp_*")))
        self.assertEqual(self.en_marcha, 0)

    def test_solo_proyectos_elegidos_y_se_conservan_los_de_antes(self):
        mxf, clips = self.preparar(4)
        cfg = json.loads(json.dumps(eb.CONFIG_DEFECTO))
        destino = self.tmp / "brutos"
        eb.unir_segmentos, orig = (lambda segs, salida, *a: (salida.write_bytes(b"x"), (True, None, []))[1]), eb.unir_segmentos
        try:
            eb.duracion_ffprobe = lambda ffprobe, ruta: DUR if Path(ruta).name.startswith("seg_") else DUR * 2
            res = eb.exportar_todo(clips, {}, list(mxf.values()), self.tmp, destino, "ffmpeg", "ffprobe", lambda m: None, cfg, proyectos=["P1"])
            self.assertEqual([r["proyecto"] for r in res], ["P1_2019-09"])
            res = eb.exportar_todo(clips, {}, list(mxf.values()), self.tmp, destino, "ffmpeg", "ffprobe", lambda m: None, cfg, proyectos=["P0"])
            self.assertEqual([r["proyecto"] for r in res], ["P0_2019-09"])
        finally:
            eb.unir_segmentos = orig
        guardado = json.loads((destino / "brutos_resultado.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(r["proyecto"] for r in guardado), ["P0_2019-09", "P1_2019-09"])

    @unittest.skipIf(da is None, "sin pyavb")
    def test_lista_rapida_de_proyectos(self):
        for f in ["MEXICO/MEXICO.avp", "MEXICO/MEXICO Bin.avb", "MEXICO/Trash/viejo.avb", "PANAMA 2019/PANAMA 2019.avp",
                  "PANAMA 2019/b/c.avb", "suelto.avb", "Avid MediaFiles/MXF/1/no.avp"]:
            (self.tmp / f).parent.mkdir(parents=True, exist_ok=True)
            (self.tmp / f).write_bytes(b"")
        lista = da.listar_proyectos(self.tmp)
        self.assertEqual([(p["nombre"], p["bins"]) for p in lista], [("(bins sin proyecto)", 1), ("MEXICO", 2), ("PANAMA 2019", 1)])


class CortesYGrabacion(unittest.TestCase):
    """Pantalla de cortes (cortes.json), hora de grabación y exportar con un análisis ya hecho."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.mxf = {}
        self.cfg = json.loads(json.dumps(eb.CONFIG_DEFECTO))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def clip(self, nombre, fecha, minutos, **extra):
        ruta = "MXF/%s.mxf" % nombre
        self.mxf[ruta] = {"ruta": ruta, "modificado": fecha}
        return dict({"clave": "K" + nombre, "nombre": nombre, "ficheros": [ruta], "fichero_video": ruta, "duracion": minutos * 60,
                     "origen": "Cámara (tarjeta P2)", "proyectos": ["P"]}, **extra)

    def test_manda_la_hora_de_grabacion(self):
        # Volcados en el mismo segundo; según la cámara B se grabó antes que A. C no tiene hora de grabación.
        cs = [self.clip("A", "2019-09-16 20:00:00", 1, fecha_grabacion="2019-09-16 18:00:00"),
              self.clip("B", "2019-09-16 20:00:00", 1, fecha_grabacion="2019-09-16 09:00:00"),
              self.clip("C", "2019-09-16 12:00:00", 1)]
        orden = [c["nombre"] for c in sorted(cs, key=lambda c: eb.orden_cronologico(c, self.mxf))]
        self.assertEqual(orden, ["B", "C", "A"])

    def test_inicios_auto_y_cortes_a_mano(self):
        cs = [self.clip(n, "2019-09-%02d 10:00:00" % d, m) for n, d, m in [("A", 1, 60), ("B", 2, 50), ("C", 3, 20), ("D", 4, 30), ("E", 5, 40)]]
        self.assertEqual(eb.inicios_auto(cs, 7200), [2])  # A+B = 110 min; C+D+E = 90 min
        self.assertEqual(eb.inicios_auto(cs, 7200, desde=3), [])
        # A mano: el segundo bruto empieza en D (A+B+C = 130 min: pasa de 2 h, se respeta)
        self.assertEqual([[c["nombre"] for c in t] for t in eb.partir_con_cortes(cs, 7200, ["KD"])], [["A", "B", "C"], ["D", "E"]])
        # Un corte que ya no está en el análisis: automático
        self.assertEqual([[c["nombre"] for c in t] for t in eb.partir_con_cortes(cs, 7200, ["KZ"])], [["A", "B"], ["C", "D", "E"]])

    def test_cortes_en_el_reparto_y_en_disco(self):
        cs = [self.clip(n, "2019-09-%02d 10:00:00" % d, m) for n, d, m in [("A", 1, 60), ("B", 2, 50), ("C", 3, 20)]]
        eb.guardar_cortes(self.tmp, {"P": ["KB"], "Q": []})
        cortes = eb.cargar_cortes(self.tmp)
        self.assertEqual(cortes, {"P": ["KB"]})
        g = {x["nombre"]: [c["nombre"] for c in x["clips"]] for x in eb.grupos_de_exportacion(cs, {}, self.mxf, self.cfg, cortes)}
        self.assertEqual(g, {"P_2019-09_1": ["A"], "P_2019-09_2": ["B", "C"]})
        self.assertEqual(eb.cargar_cortes(self.tmp / "no_existe"), {})

    @unittest.skipIf(da is None, "sin pyavb")
    def test_leer_fecha_de_grabacion(self):
        b = da.buscar_fecha_grabacion
        self.assertEqual(b({"columnas": {"Shoot Date": "15/09/2021", "Shoot Time": "09:15:00"}})[0], eb.dt.datetime(2021, 9, 15, 9, 15))
        self.assertEqual(b({"comentarios": {"Record Date": "2021-09-15T07:00:01Z"}})[0], eb.dt.datetime(2021, 9, 15, 7, 0, 1))
        self.assertEqual(b({"columnas": {"Fecha de grabación": "2019:09:16 14:02:10"}})[0], eb.dt.datetime(2019, 9, 16, 14, 2, 10))
        self.assertEqual(b({"columnas": {"Shoot Date": "15/09/2021"}}), (None, "bin: Shoot Date (sin hora)"))  # sin hora no vale
        self.assertEqual(b({"columnas": {"Comments": "entrevista 15/09"}}), (None, None))
        clips = [{"columnas": {"Shoot Date": "09/16/2019", "Shoot Time": "14:02:10:12"}}, {}]
        r = da.fijar_fechas_grabacion(clips)
        self.assertEqual((r["clips"], clips[0]["fecha_grabacion"]), (1, "2019-09-16 14:02:10"))

    @unittest.skipIf(da is None, "sin pyavb")
    def test_buscar_el_ultimo_analisis_del_disco(self):
        for nombre, origen, fecha in [("Diagnostico_X_20261020_0900", "F:/DISCO 1", "2026-10-20 09:00"),
                                      ("Diagnostico_X_20261021_0900", "F:/DISCO 1", "2026-10-21 09:00"),
                                      ("Diagnostico_Y_20261022_0900", "G:/OTRO", "2026-10-22 09:00")]:
            d = self.tmp / nombre
            d.mkdir()
            for f in ("clips.json", "cruce.json", "mxf.json"):
                (d / f).write_text("[]", encoding="utf-8")
            (d / "resumen.json").write_text(json.dumps({"origen": origen, "fecha": fecha}), encoding="utf-8")
        self.assertEqual(da.buscar_analisis(self.tmp, "F:/DISCO 1").name, "Diagnostico_X_20261021_0900")
        self.assertIsNone(da.buscar_analisis(self.tmp, "H:/NADA"))


class XdcamHD50(unittest.TestCase):
    """formato_salida=xdcam_hd50 (por defecto): MPEG-2 4:2:2 50 Mb/s 1920x1080 25i 16:9."""

    def plan(self, firmas, field_order="bff"):
        mxf = {}
        cs = []
        for i, (codec, ancho, alto, fps) in enumerate(firmas):
            v = "MXF/C%d.mxf" % i
            mxf[v] = {"ruta": v, "pistas": [{"tipo": "video", "codec": codec, "ancho": ancho, "alto": alto, "fps": fps,
                                              "pix_fmt": "yuv422p" if codec == "mpeg2video" else "yuv411p", "field_order": field_order}]}
            cs.append({"clave": i, "nombre": "C%d" % i, "ficheros": [v], "fichero_video": v, "num_audio": 2})
        cfg = json.loads(json.dumps(eb.CONFIG_DEFECTO))
        return eb.plan_proyecto(cs, mxf, cfg), mxf, cfg

    def test_ntsc_dv_se_convierte_a_25i(self):
        p, mxf, cfg = self.plan([("dvvideo", 720, 480, "30000/1001")] * 2)
        self.assertEqual((p["modo"], p["fps"]), ("convertir", 25.0))  # 25 fps: timecode sin drop-frame
        args = eb.args_video(p, mxf["MXF/C0.mxf"], cfg)
        vf = args[args.index("-vf") + 1]
        self.assertTrue(vf.startswith("bwdif=mode=send_field"))  # entrelazado: se separa en campos antes de cambiar de fps
        for x in ["fps=50", "pad=1920:1080", "interlace=scan=tff", "out_color_matrix=bt709"]:
            self.assertIn(x, vf)
        for a, b in [("-c:v", "mpeg2video"), ("-pix_fmt", "yuv422p"), ("-b:v", "50M"), ("-top", "1"), ("-aspect", "16:9")]:
            self.assertEqual(args[args.index(a) + 1], b)
        self.assertIn("+ilme+ildct", args)

    def test_progresivo_no_desentrelaza(self):
        p, mxf, cfg = self.plan([("h264", 1920, 1080, "30000/1001")], field_order="progressive")
        self.assertFalse(eb.args_video(p, mxf["MXF/C0.mxf"], cfg)[1].startswith("bwdif"))

    def test_solo_copia_si_ya_es_xdcam(self):
        self.assertEqual(self.plan([("mpeg2video", 1920, 1080, "25/1")] * 2)[0]["modo"], "copia")
        self.assertEqual(self.plan([("dnxhd", 1920, 1080, "25/1")] * 2)[0]["modo"], "convertir")  # antes se copiaba


@unittest.skipIf(da is None, "sin pyavb")
class DatosReales(unittest.TestCase):
    """Casos sacados del disco real de la Corresponsalía de México (oct. 2026)."""

    def test_rutas_con_acentos_mal_leidos(self):
        self.assertEqual(da.reparar_ruta("D//Documents and Settings/rtve1/Mis documentos/Mis vÌdeos/a.mp4"),
                         "D//Documents and Settings/rtve1/Mis documentos/Mis vídeos/a.mp4")
        self.assertEqual(da.reparar_ruta("P//notas IÒigo/a.mov"), "P//notas Iñigo/a.mov")
        self.assertEqual(da.reparar_ruta("/Users/x/Mis vídeos/a.mov"), "/Users/x/Mis vídeos/a.mov")  # Mac: no se toca
        self.assertIsNone(da.reparar_ruta(None))

    def test_nombre_de_render(self):
        for n in ["  ROBOT ABORTO,Audio Dissolve,10", "PIEZA ACTIVISTAS TD2,Dissolve,1",
                  "PIEZA_DARIEN_SELVA_TD1.Copy.01_updated.01,Dissolve,2.new.01"]:
            self.assertTrue(da.RE_NOMBRE_RENDER.search(n), n)
        for n in ["0006M4.new.01", "HONDURAS, DIA 2", "2019 09 29T150951Z_1_LWD0016J7GF9J_RTRWNEV_D_7121 YEMEN.mp4"]:
            self.assertFalse(da.RE_NOMBRE_RENDER.search(n), n)

    def test_renders_sin_bin_no_son_huerfanos(self):
        clips = [{"clave": "K1", "nombre": "INFORME SEMANAL,Dissolve,21", "ficheros": ["a"], "duracion": 1, "bytes": 1,
                  "codec_video": None, "resolucion": None, "fps": None, "proyectos": []},
                 {"clave": "K2", "nombre": "0006M4.new.01", "ficheros": ["b"], "duracion": 1, "bytes": 1,
                  "codec_video": None, "resolucion": None, "fps": None, "proyectos": []}]
        cruce = da.cruzar([], [], clips)
        self.assertEqual([h["nombre"] for h in cruce["huerfanos"]], ["0006M4.new.01"])
        self.assertEqual(cruce["renders"], 1)


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
            cfg["agrupar"] = "proyecto"
            destino = tmp / "brutos"
            res = eb.exportar_todo(clips, {}, mxf, tmp, destino, "ffmpeg", "ffprobe", lambda m: None, cfg)
            self.assertEqual([r["proyecto"] for r in res], ["PROY A_T01"])
            guardado = json.loads((destino / "brutos_resultado.json").read_text(encoding="utf-8"))
            self.assertEqual([r["proyecto"] for r in guardado], ["PROY A_T01"])
            self.assertFalse(list(destino.glob("_tmp_*")), "queda la carpeta temporal del proyecto a medias")
            self.assertFalse((destino / "PROY B_T01.mxf").exists())
        finally:
            eb.ejecutar_ffmpeg, eb.duracion_ffprobe = orig
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
