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
                      "firma": eb.firma_bruto([{"clave": "K1"}], {})}
            self.assertTrue(eb.ya_exportado(previo, 1, d, eb.firma_bruto([{"clave": "K1"}], {})))
            self.assertFalse(eb.ya_exportado(previo, 1, d, eb.firma_bruto([{"clave": "K2"}], {})))
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

    def nombres(self, cs, cruce=None, cfg=None):
        return {g["nombre"]: [c["nombre"] for c in g["clips"]] for g in eb.grupos_de_exportacion(cs, cruce or {}, self.mxf, cfg or self.cfg)}

    def test_por_defecto(self):
        self.assertEqual((eb.CONFIG_DEFECTO["agrupar"], eb.CONFIG_DEFECTO["horas_por_bruto"], eb.CONFIG_DEFECTO["formato_salida"],
                          eb.CONFIG_DEFECTO["excluir_importados"], eb.CONFIG_DEFECTO["prefijo_fichero"]),
                         ("proyecto_mes", 2, "xdcam_hd50", True, ""))

    def test_importados_y_agencias_fuera(self):
        cs = [self.clip("0016Q3", "2019-09-16 10:52:26", 1),
              self.clip("VID_0001", "2019-09-16 11:00:00", 1, origen="Fichero importado",
                        fichero_origen=r"D:\Documents and Settings\usuario1\Mis documentos\Mis vídeos\VID_0001.mp4"),
              self.clip("SIN RUTA", "2019-09-16 11:00:00", 1, origen="Fichero importado"),
              self.clip("REUTERS", "2019-09-16 11:00:00", 1, origen="Agencia Reuters", fichero_origen=r"C:\Downloads\x_RTRMADV_y.MP4"),
              # MXF importado de una tarjeta P2: el origen dice "Fichero importado" pero la ruta es de tarjeta
              self.clip("0001AB", "2019-09-16 12:00:00", 1, origen="Fichero importado", fichero_origen=r"E:\CARD01\CONTENTS\VIDEO\0001AB.MXF"),
              self.clip("X,Dissolve,1", "2019-09-16 12:00:00", 0.1, proyectos=(), render=True)]
        # VID_0001 (nombre de móvil) y la agencia, fuera. SIN RUTA es importado según el bin, pero sin ninguna pista de
        # dónde viene: dudoso, fuera (solo_material_de_camara)
        self.assertEqual(self.nombres(cs), {"P SEPTIEMBRE DE 2019": ["0016Q3", "0001AB"]})
        self.assertEqual(eb.clasificar(cs[2], self.cfg)[0], "dudoso")
        self.assertEqual(eb.clasificar(cs[0], self.cfg)[0], "propio")
        # Con solo_material_de_camara = false, lo dudoso entra marcado "para revisar" (como hasta el 27/10/2026)
        self.assertEqual(self.nombres(cs, cfg=dict(self.cfg, solo_material_de_camara=False)),
                         {"P SEPTIEMBRE DE 2019": ["0016Q3", "SIN RUTA", "0001AB"]})
        self.cfg["excluir_importados"] = False
        self.assertEqual(len(self.nombres(cs)["P SEPTIEMBRE DE 2019"]), 5)

    def test_orden_cronologico_estricto(self):
        # Las fechas de la foto del correo, que salieron desordenadas
        fechas = ["2019-10-14 20:55:14", "2019-10-09 17:40:36", "2019-09-16 20:26:20", "2019-09-18 17:43:26", "2019-09-04 21:12:18"]
        cs = [self.clip("C%d" % i, f, 1) for i, f in enumerate(fechas)]
        g = eb.grupos_de_exportacion(cs, {}, self.mxf, self.cfg)
        self.assertEqual(len(g), 1)
        self.assertEqual(g[0]["nombre"], "P SEPTIEMBRE A OCTUBRE DE 2019")
        orden = [self.mxf[c["fichero_video"]]["modificado"] for c in g[0]["clips"]]
        self.assertEqual(orden, sorted(fechas))
        self.assertTrue(g[0]["ordenado"])

    def test_tope_de_dos_horas(self):
        # A+B = 110 min; C (20 min) pasaría de 2 h: no se añade, abre el bruto siguiente
        cs = [self.clip("A", "2019-09-01 10:00:00", 60), self.clip("B", "2019-09-02 10:00:00", 50),
              self.clip("C", "2019-09-03 10:00:00", 20), self.clip("D", "2019-09-04 10:00:00", 30),
              self.clip("E", "2019-10-01 10:00:00", 150)]  # más de 2 h por sí solo: va solo
        self.assertEqual(self.nombres(cs), {"P SEPTIEMBRE DE 2019 1": ["A", "B"], "P SEPTIEMBRE DE 2019 2": ["C", "D"], "P OCTUBRE DE 2019": ["E"]})

    def test_descartados_y_su_motivo(self):
        cs = [self.clip("0016Q3", "2019-09-16 10:52:26", 1),
              self.clip("VID_0001", "2019-09-16 11:00:00", 1, origen="Fichero importado", fichero_origen=r"D:\Descargas\VID_0001.mp4"),
              self.clip("REUTERS", "2019-09-16 11:00:00", 1, origen="Agencia Reuters"),
              self.clip("0001AB", "2019-09-16 12:00:00", 1, origen="Fichero importado", fichero_origen=r"E:\CARD01\CONTENTS\VIDEO\0001AB.MXF"),
              self.clip("X,Dissolve,1", "2019-09-16 12:00:00", 0.1, proyectos=(), proyectos_render=["P"], render=True)]
        d = eb.descartados_por_proyecto(cs, {}, self.mxf, self.cfg)
        self.assertEqual(sorted((c["nombre"], m) for c, m in d["P"]),
                         [("REUTERS", "agencia"), ("VID_0001", "importado"), ("X,Dissolve,1", "efecto de Avid")])

    def test_proyectos_piezas_compartidos_y_sin_fecha(self):
        cruce = {"por_proyecto": [{"proyecto": "Q", "secuencias_nombres": ["PIEZA SISMO"]}]}
        cs = [self.clip("A", "2018-12-01 20:00:00", 1, proyectos=("Q", "P")),  # compartido: solo con el primero (P)
              self.clip("B", "2018-12-01 21:00:00", 1, proyectos=("Q",)), self.clip("H", "2018-12-02 09:00:00", 1, proyectos=()),
              self.clip("PIEZA SISMO", "2018-12-03 10:00:00", 1, proyectos=("Q",)), self.clip("Z", None, 1)]
        self.assertEqual(self.nombres(cs, cruce), {"P DICIEMBRE DE 2018": ["A"], "P": ["Z"], "Q DICIEMBRE DE 2018": ["B"],
                                                   "Q PIEZAS DICIEMBRE DE 2018": ["PIEZA SISMO"], "SIN PROYECTO DICIEMBRE DE 2018": ["H"]})


class NombresDeBruto(unittest.TestCase):
    """Nombre por defecto: proyecto en mayúsculas y mes y año en letra, sin guiones."""

    def f(self, texto):
        import datetime as dt
        return dt.datetime.strptime(texto, "%Y-%m-%d")

    def test_nombres_de_bruto(self):
        f = self.f
        self.assertEqual(eb.nombre_bruto("Venezuela", [f("2021-09-19"), f("2021-09-30")]), "VENEZUELA SEPTIEMBRE DE 2021")
        self.assertEqual(eb.nombre_bruto("VENEZUELA", [f("2021-09-19"), f("2021-10-02")]), "VENEZUELA SEPTIEMBRE A OCTUBRE DE 2021")
        self.assertEqual(eb.nombre_bruto("VENEZUELA", [f("2021-12-30"), f("2022-01-02")]), "VENEZUELA DICIEMBRE DE 2021 A ENERO DE 2022")
        self.assertEqual(eb.nombre_bruto("VENEZUELA", []), "VENEZUELA")
        self.assertEqual(eb.nombre_bruto("VENEZUELA", [f("2021-09-19")], piezas=True), "VENEZUELA PIEZAS SEPTIEMBRE DE 2021")
        self.assertEqual(eb.nombre_bruto("VENEZUELA", [], piezas=True), "VENEZUELA PIEZAS")
        self.assertEqual(eb.nombre_bruto("Méx/ico: 2", [f("2021-01-05")]), "MÉXICO 2 ENERO DE 2021")
        self.assertEqual(eb.nombre_bruto("SIN_PROYECTO", [f("2021-05-05")]), "SIN PROYECTO MAYO DE 2021")
        # Las fechas pueden venir desordenadas (bruto montado a mano): manda la más antigua y la más moderna
        self.assertEqual(eb.nombre_bruto("P", [f("2021-10-02"), f("2021-09-19")]), "P SEPTIEMBRE A OCTUBRE DE 2021")


def audio_del_comando(cmd):
    """[(fichero o "anullsrc=…", cadena de filtros)] de cada pista de audio de un comando de hacer_segmento, en orden."""
    entradas = [cmd[i + 1] for i, x in enumerate(cmd) if x == "-i"]
    grafo = cmd[cmd.index("-filter_complex") + 1].split(";") if "-filter_complex" in cmd else []
    salida = []
    for cadena in grafo:
        if cadena.startswith("["):
            salida.append((Path(entradas[int(cadena[1:].split(":")[0])]).name, cadena))
        else:
            salida.append(("anullsrc=r=48000:cl=mono", cadena))
    return salida


class OpcionesExportacion(unittest.TestCase):
    """Panel de opciones: validación, pistas en XDCAM, enrutado, ganancias, R128, medidor y firma."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = json.loads(json.dumps(eb.CONFIG_DEFECTO))
        self._orig = (eb.ejecutar_ffmpeg, eb.medir_audio)
        self.cmds = []

        def ejecutar(cmd, timeout):
            self.cmds.append(cmd)
            Path(cmd[-1]).write_bytes(b"mxf")
            return True, ""
        eb.ejecutar_ffmpeg = ejecutar
        self.medidas = {}
        eb.medir_audio = lambda ffmpeg, ruta, *canal: self.medidas.get(Path(ruta).name, {"lufs": -23.0, "pico": -6.0})

    def tearDown(self):
        eb.ejecutar_ffmpeg, eb.medir_audio = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_por_defecto(self):
        d = eb.CONFIG_DEFECTO
        self.assertEqual((d["timecode_inicio"], d["pistas_audio"], d["niveles_audio"], d["enrutado_audio"], d["md5"], d["ficha_tecnica"],
                          d["medidor_audio"]), ("00:00:00:00", "max", "sin_tocar", [], True, True, True))
        self.assertEqual(eb.validar_opciones(self.cfg), [])

    def test_validar_opciones(self):
        malos = [("timecode_inicio", "25:00:00:00"), ("timecode_inicio", "00:00:00:25"), ("timecode_inicio", "0:0:0"),
                 ("ganancia_db", [0, 21]), ("enrutado_audio", [9]), ("niveles_audio", "fuerte"), ("pistas_audio", 9),
                 ("prefijo_fichero", "A/B")]
        for clave, valor in malos:
            cfg = dict(self.cfg, **{clave: valor})
            self.assertTrue(eb.validar_opciones(cfg), (clave, valor))
        self.assertEqual(eb.validar_opciones(dict(self.cfg, timecode_inicio="23:59:59:24", ganancia_db=[-20, 20], enrutado_audio=[2, 1, 0],
                                                  niveles_audio="r128", pistas_audio=8)), [])

    def test_validar_nombres(self):
        self.assertEqual(eb.validar_nombres(["A SEPTIEMBRE DE 2021", "B"]), [])
        self.assertTrue(eb.validar_nombres(["A", "A"]))
        self.assertTrue(eb.validar_nombres(["A:B"]))
        self.assertTrue(eb.validar_nombres([""]))

    def test_pistas_en_xdcam_solo_4_u_8(self):
        self.assertEqual([eb.pistas_salida(n, self.cfg) for n in (0, 1, 2, 4, 5, 8)], [0, 4, 4, 4, 8, 8])
        self.assertEqual(eb.pistas_salida(2, dict(self.cfg, pistas_audio=8)), 8)
        self.assertEqual(eb.pistas_salida(2, dict(self.cfg, formato_salida="auto")), 2)

    def test_ganancia_r128(self):
        self.assertEqual(eb.ganancia_r128(-40), 12)
        self.assertEqual(eb.ganancia_r128(-20), -3)
        self.assertEqual(eb.ganancia_r128(None), 0)

    def segmento(self, cfg, pistas=4, audios=2):
        rutas = ["V.mxf"] + ["A%d.mxf" % i for i in range(1, audios + 1)]
        mxf = {"V.mxf": {"pistas": [{"tipo": "video", "field_order": "bb"}]}}
        mxf.update({r: {"pistas": [{"tipo": "audio"}]} for r in rutas[1:]})
        clip = {"clave": "K", "nombre": "C", "ficheros": rutas, "fichero_video": "V.mxf", "duracion": 2.0}
        plan = {"modo": "convertir", "pistas_audio": pistas, "destino": {"xdcam": True}, "fps": 25.0}
        info = {}
        seg, err, cmd = eb.hacer_segmento(clip, 1, plan, self.tmp, mxf, self.tmp, "ffmpeg", cfg, info=info)
        self.cadenas = audio_del_comando(cmd)
        return [nombre for nombre, _ in self.cadenas], cmd, info

    def test_enrutado_y_silencio(self):
        audio, _, info = self.segmento(dict(self.cfg, enrutado_audio=[2, 1]))
        self.assertEqual(audio, ["A2.mxf", "A1.mxf", "anullsrc=r=48000:cl=mono", "anullsrc=r=48000:cl=mono"])
        audio, _, info = self.segmento(dict(self.cfg, enrutado_audio=[0, 3]))  # silencio a propósito y una pista que no tiene
        self.assertEqual(audio[:2], ["anullsrc=r=48000:cl=mono", "anullsrc=r=48000:cl=mono"])
        self.assertIn("pista 3", info["aviso_audio"])
        self.assertIn("no entran las pistas 1, 2", info["aviso_audio"])
        _, _, info = self.segmento(self.cfg)  # por defecto, rellenar con silencio no es un aviso
        self.assertIsNone(info.get("aviso_audio"))
        _, _, info = self.segmento(self.cfg, pistas=4, audios=6)
        self.assertEqual(info["aviso_audio"], "no entran las pistas 5, 6 del clip")

    def test_ganancia_por_pista(self):
        _, cmd, info = self.segmento(dict(self.cfg, niveles_audio="ganancia", ganancia_db=[3, 0, -2]))
        ganancias = [cadena for _, cadena in self.cadenas]
        self.assertIn("volume=3.0dB", ganancias[0])
        self.assertNotIn("volume", ganancias[1])
        self.assertNotIn("volume", ganancias[2])  # la pista 3 es silencio: no se toca
        self.assertEqual(info["ganancia"], "1: +3.0")

    def test_r128_con_tope_y_medidor(self):
        self.medidas = {"A1.mxf": {"lufs": -45.0, "pico": -30.0}, "A2.mxf": {"lufs": -23.0, "pico": 0.0}}
        _, cmd, info = self.segmento(dict(self.cfg, niveles_audio="r128"))
        self.assertIn("volume=12.0dB", self.cadenas[0][1])
        self.assertNotIn("volume", self.cadenas[1][1])  # ya está a -23
        self.assertEqual((info["lufs"], info["pico"]), (-23.0, 0.0))
        self.assertIn("saturado", info["aviso_audio"])
        self.assertIn("tope", info["aviso_audio"])

    def test_casi_mudo(self):
        self.medidas = {"A1.mxf": {"lufs": -70.0, "pico": -90.0}, "A2.mxf": {"lufs": -65.0, "pico": -50.0}}
        _, _, info = self.segmento(self.cfg)
        self.assertIn("casi mudo", info["aviso_audio"])
        self.assertIsNone(info.get("ganancia"))

    def test_sin_medidor_no_mide(self):
        llamadas = []
        eb.medir_audio = lambda ffmpeg, ruta, *canal: llamadas.append(ruta) or {"lufs": None, "pico": None}
        self.segmento(dict(self.cfg, medidor_audio=False))
        self.assertEqual(llamadas, [])

    def test_firma_cambia_con_el_orden_y_las_opciones(self):
        a, b = [{"clave": "K1"}, {"clave": "K2"}], [{"clave": "K2"}, {"clave": "K1"}]
        self.assertNotEqual(eb.firma_bruto(a, self.cfg), eb.firma_bruto(b, self.cfg))
        self.assertNotEqual(eb.firma_bruto(a, self.cfg), eb.firma_bruto(a, dict(self.cfg, timecode_inicio="10:00:00:00")))
        self.assertEqual(eb.firma_bruto(a, self.cfg), eb.firma_bruto(a, dict(self.cfg, md5=False)))  # no cambia el bruto


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
                          "duracion": DUR, "bytes": 1000, "timecode": None, "proyectos": ["P%d" % (i % 2)], "origen": "Cámara (tarjeta P2)"})
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
        cfg["ficha_tecnica"] = False  # ffmpeg simulado: no hay MXF de verdad que comprobar (eso se prueba en test_mxf.py)
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
            self.assertEqual([r["proyecto"] for r in res], ["P1 SEPTIEMBRE DE 2019"])
            res = eb.exportar_todo(clips, {}, list(mxf.values()), self.tmp, destino, "ffmpeg", "ffprobe", lambda m: None, cfg, proyectos=["P0"])
            self.assertEqual([r["proyecto"] for r in res], ["P0 SEPTIEMBRE DE 2019"])
        finally:
            eb.unir_segmentos = orig
        guardado = json.loads((destino / "brutos_resultado.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(r["proyecto"] for r in guardado), ["P0 SEPTIEMBRE DE 2019", "P1 SEPTIEMBRE DE 2019"])

    def exportar_con_piezas(self, cfg, **kw):
        mxf, clips = self.preparar(4)
        cruce = {"por_proyecto": [{"proyecto": "P1", "secuencias_nombres": ["CLIP 03"]}]}  # CLIP 03 es una pieza de P1
        eb.duracion_ffprobe = lambda ffprobe, ruta: DUR
        eb.unir_segmentos, orig = (lambda segs, salida, *a: (salida.write_bytes(b"x"), (True, None, []))[1]), eb.unir_segmentos
        try:
            cfg["ficha_tecnica"] = False
            return eb.exportar_todo(clips, cruce, list(mxf.values()), self.tmp, self.tmp / "brutos", "ffmpeg", "ffprobe", lambda m: None,
                                    cfg, **kw)
        finally:
            eb.unir_segmentos = orig

    def test_piezas_no_salen_por_defecto(self):
        cfg = json.loads(json.dumps(eb.CONFIG_DEFECTO))
        self.assertFalse(cfg["exportar_piezas"])
        nombres = [r["proyecto"] for r in self.exportar_con_piezas(cfg)]
        self.assertEqual(nombres, ["P0 SEPTIEMBRE DE 2019", "P1 SEPTIEMBRE DE 2019"])

    def test_piezas_si_se_piden(self):
        cfg = dict(json.loads(json.dumps(eb.CONFIG_DEFECTO)), exportar_piezas=True)
        self.assertIn("P1 PIEZAS SEPTIEMBRE DE 2019", [r["proyecto"] for r in self.exportar_con_piezas(cfg)])

    def test_solo_los_brutos_marcados_en_la_cola(self):
        cfg = json.loads(json.dumps(eb.CONFIG_DEFECTO))
        res = self.exportar_con_piezas(cfg, solo_brutos=["P1 PIEZAS SEPTIEMBRE DE 2019", "P0 SEPTIEMBRE DE 2019"])
        self.assertEqual(sorted(r["proyecto"] for r in res), ["P0 SEPTIEMBRE DE 2019", "P1 PIEZAS SEPTIEMBRE DE 2019"])

    def test_eventos_de_avance(self):
        cfg = json.loads(json.dumps(eb.CONFIG_DEFECTO))
        eventos = []
        self.exportar_con_piezas(cfg, solo_brutos=["P0 SEPTIEMBRE DE 2019"], evento=eventos.append)
        estados = [e["estado"] for e in eventos]
        self.assertEqual(estados[0], "empezando")
        self.assertEqual(estados[-1], "terminado")
        self.assertIn("uniendo", estados)
        clips = [(e["clip"], e["total"]) for e in eventos if e["estado"] == "convirtiendo"]
        self.assertEqual(clips, [(1, 2), (2, 2)])
        self.assertTrue(all(e["bruto"] == "P0 SEPTIEMBRE DE 2019" for e in eventos))
        self.assertIn("resultado", eventos[-1])

    def disco_que_se_va(self, despues_de):
        """ffmpeg simulado en el que el disco de origen desaparece tras despues_de segmentos: los siguientes fallan como
        lo haría ffmpeg con el fichero ya inaccesible, y origen_disponible dice que no está."""
        disco = {"ok": True, "hechos": 0}
        simulado = eb.ejecutar_ffmpeg

        def ejecutar(cmd, timeout):
            if not disco["ok"]:
                return False, "No such file or directory"
            disco["hechos"] += 1
            disco["ok"] = disco["hechos"] < despues_de
            return simulado(cmd, timeout)
        eb.ejecutar_ffmpeg = ejecutar
        orig = eb.origen_disponible
        eb.origen_disponible = lambda origen: disco["ok"]
        self.addCleanup(setattr, eb, "origen_disponible", orig)

    def test_disco_desconectado_entre_brutos(self):
        """Se para como con Detener: el bruto terminado se queda en brutos_resultado.json y el siguiente no se empieza."""
        cfg = dict(json.loads(json.dumps(eb.CONFIG_DEFECTO)), clips_a_la_vez=1)
        mxf, clips = self.preparar(4)
        self.disco_que_se_va(despues_de=2)  # los dos clips de P0 (en el reparto, P0 va antes que P1)
        eb.unir_segmentos, orig = (lambda segs, salida, *a: (salida.write_bytes(b"x"), (True, None, []))[1]), eb.unir_segmentos
        self.addCleanup(setattr, eb, "unir_segmentos", orig)
        cfg["ficha_tecnica"] = False
        eb.duracion_ffprobe = lambda ffprobe, ruta: DUR
        with self.assertRaises(eb.OrigenPerdido) as e:
            eb.exportar_todo(clips, {"por_proyecto": []}, list(mxf.values()), self.tmp, self.tmp / "brutos", "ffmpeg", "ffprobe",
                             lambda m: None, cfg)
        self.assertIn("misma letra", str(e.exception))
        hechos = json.loads((self.tmp / "brutos" / "brutos_resultado.json").read_text(encoding="utf-8"))
        self.assertEqual([r["proyecto"] for r in hechos], ["P0 SEPTIEMBRE DE 2019"])
        self.assertFalse(any(x.name.startswith("_tmp_") for x in (self.tmp / "brutos").iterdir()))

    def test_disco_desconectado_a_mitad_de_un_bruto(self):
        """El bruto a medias no se termina con huecos: se descarta, como con Detener, y no queda marcado como hecho."""
        cfg = dict(json.loads(json.dumps(eb.CONFIG_DEFECTO)), clips_a_la_vez=1, ficha_tecnica=False)
        mxf, clips = self.preparar(4)
        self.disco_que_se_va(despues_de=1)
        with self.assertRaises(eb.OrigenPerdido):
            eb.exportar_todo(clips, {"por_proyecto": []}, list(mxf.values()), self.tmp, self.tmp / "brutos", "ffmpeg", "ffprobe",
                             lambda m: None, cfg)
        self.assertEqual(json.loads((self.tmp / "brutos" / "brutos_resultado.json").read_text(encoding="utf-8")), [])
        self.assertEqual([x.name for x in (self.tmp / "brutos").iterdir()], ["brutos_resultado.json"])

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
        self.assertEqual(g, {"P SEPTIEMBRE DE 2019 1": ["A"], "P SEPTIEMBRE DE 2019 2": ["B", "C"]})
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
    def test_columnas_distintas_en_cada_clip(self):
        # Disco real (26/10/2026): un clip con "Camroll" y otro con "Shoot Date" rompían el análisis en el paso 4/6
        # ("can only concatenate str (not 'int') to str"): se sumaba lo que ponía la columna en vez de contarla.
        clips = [{"columnas": {"Camroll": "A001"}}, {"columnas": {"Shoot Date": "15/09/2021", "Camroll": "A002"}},
                 {"columnas": {"Tape": "7"}, "comentarios": {"Record Date": "2021-09-15"}}]
        r = da.fijar_fechas_grabacion(clips)
        self.assertEqual(r["columnas_vistas"], {"Camroll": 2, "Shoot Date": 1, "Tape": 1, "MXF: Record Date": 1})

    def test_fechas_de_grabacion_imposibles(self):
        D, f = eb.dt.datetime, eb.fecha_imposible
        ahora, volcado = D(2026, 10, 23, 12), D(2021, 9, 15, 10)
        self.assertEqual(f(D(1999, 12, 31, 23), volcado, ahora), "anterior a 2000")
        self.assertEqual(f(D(2027, 3, 1), volcado, ahora), "en el futuro")
        self.assertIn("reloj de la cámara sin poner", f(D(2015, 1, 1, 0, 3), volcado, ahora))
        self.assertIsNone(f(D(2022, 1, 1, 0, 30), D(2022, 1, 1, 9), ahora))  # Nochevieja de verdad: el volcado cuadra
        self.assertIsNone(f(D(2021, 9, 14, 9), volcado, ahora))  # un día antes del volcado: normal
        self.assertIsNone(f(D(2021, 9, 15, 9)))  # sin volcado ni "ahora": solo cuenta lo absoluto

    @unittest.skipIf(da is None, "sin pyavb")
    def test_fecha_imposible_y_reloj_desfasado_en_el_analisis(self):
        mxf = [{"ruta": r, "modificado": "2021-09-15 10:00:00"} for r in ("a.mxf", "b.mxf", "c.mxf", "d.mxf")]
        clips = [{"fichero_video": "a.mxf", "columnas": {"Shoot Date": "01/01/2000", "Shoot Time": "00:03:00"}},
                 {"fichero_video": "b.mxf", "columnas": {"Shoot Date": "01/01/1970", "Shoot Time": "00:00:10"}},
                 {"fichero_video": "c.mxf", "columnas": {"Shoot Date": "16/09/2021", "Shoot Time": "12:00:00"}},
                 {"fichero_video": "d.mxf", "columnas": {"Shoot Date": "15/09/2021", "Shoot Time": "08:00:00"}}]
        r = da.fijar_fechas_grabacion(clips, mxf)
        self.assertEqual((r["clips"], sum(r["imposibles"].values()), r["desfasados"]), (2, 2, 1))
        self.assertNotIn("fecha_grabacion", clips[0])  # no se usa: se ordena por el volcado
        self.assertIn("01/01/2000", clips[0]["aviso_fecha"])
        self.assertIn("anterior a 2000", clips[1]["aviso_fecha"])
        self.assertEqual(clips[2]["fecha_grabacion"], "2021-09-16 12:00:00")  # desfasada: se usa, con aviso
        self.assertIn("26 h", clips[2]["aviso_fecha"])
        self.assertNotIn("aviso_fecha", clips[3])

    @unittest.skipIf(da is None, "sin pyavb")
    def test_resolucion_del_movil_vertical(self):
        r = da.resolucion_vista
        self.assertEqual(r({"ancho": 1920, "alto": 1080, "rotacion": 90}), "1080x1920 (vertical)")
        self.assertEqual(r({"ancho": 1920, "alto": 1080, "rotacion": -90}), "1080x1920 (vertical)")
        self.assertEqual(r({"ancho": 1920, "alto": 1080, "rotacion": 180}), "1920x1080")  # boca abajo: mismo tamaño
        self.assertEqual(r({"ancho": 1920, "alto": 1080, "rotacion": None}), "1920x1080")
        self.assertIsNone(r(None))

    @unittest.skipIf(da is None, "sin pyavb")
    def test_clips_con_media_omf(self):
        cruce = {"por_proyecto": [{"offline_detalle": [{"nombre": "Viejo  OMF"}, {"nombre": "OTRO"}, {"nombre": "VIEJO"}]}]}
        ficheros = [{"ext": ".omf", "nombre": "VIEJO OMFV01.5A1B2C.omf"}, {"ext": ".omf", "nombre": "VIEJO OMFA01.5A1B2D.omf"},
                    {"ext": ".omf", "nombre": "suelto.omf"}, {"ext": ".mxf", "nombre": "OTROV01.1.mxf"}]
        self.assertEqual(da.marcar_offline_en_omf(cruce, ficheros), 1)
        self.assertEqual([o.get("omf") for o in cruce["por_proyecto"][0]["offline_detalle"]], [True, None, None])
        self.assertEqual(cruce["offline_en_omf"], 1)

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
        self.assertEqual(da.reparar_ruta("D//Documents and Settings/usuario1/Mis documentos/Mis vÌdeos/a.mp4"),
                         "D//Documents and Settings/usuario1/Mis documentos/Mis vídeos/a.mp4")
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
                              "duracion": DUR, "bytes": 1000, "timecode": None, "proyectos": ["PROY " + p], "origen": "Cámara (tarjeta P2)"})

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


class DiscosDeChina(unittest.TestCase):
    """Disco de la Corresponsalía de Pekín (oct. 2026): material propio importado desde una carpeta del PC, bins de
    agencias, DNxHD 1080i50 y nombres en chino."""

    def setUp(self):
        self.cfg = json.loads(json.dumps(eb.CONFIG_DEFECTO))

    def test_ficheros_de_camara(self):
        for f in [r"D:\China\北京 0916\DJI_0012.MP4", "C0380.MP4", "MVI_1234.MOV", "GX010045.MP4", "P1000123.MOV", "A001C002_220916_R1AB.mov",
                  "20260916_C0001.mp4"]:
            self.assertTrue(eb.es_fichero_de_camara(f, self.cfg), f)
        for f in ["IMG_1234.MOV", "CCTV 新闻 0916.mp4", "Reuters_xi.mp4", "descarga.mp4", "", None]:
            self.assertFalse(eb.es_fichero_de_camara(f, self.cfg), f)

    def test_camaras_con_tarjeta_sd_y_osmo(self):
        # Osmo y cámaras con tarjeta SD. Con un config_brutos.json viejo (escrito al
        # primer uso, sin los patrones nuevos) también se reconocen: van en el código
        viejo = dict(self.cfg, camara_propia_regex=r"^(DJI_\d{3,5}|C\d{4})(\D|$)")
        for f in [r"C:\Users\a\Desktop\osmo\DJI_20231015123456_0001_D.MP4", "DJI_0012.MP4", "DSCF1234.MOV", "ZOOM0001.WAV",
                  "MAQ00012.MP4", "MVI_1234.MOV", r"D:\copia SD\00001.MTS", "P1000123.MOV", "GX010045.MP4"]:
            self.assertTrue(eb.es_fichero_de_camara(f, viejo), f)
        for f in ["IMG_1234.MOV", "VID_20210915_101500.mp4", "00001.mp4", "DJI_X.mp4", "descarga.mp4", "Reuters_xi.mp4"]:
            self.assertFalse(eb.es_fichero_de_camara(f, viejo), f)
        # Osmo copiada al PC: material; y si el bin no guarda la ruta, por el nombre del clip
        c = {"origen": "Fichero importado", "fichero_origen": r"C:\Users\a\Desktop\osmo\DJI_20231015123456_0001_D.MP4", "bins": ["b.avb"]}
        self.assertEqual(eb.clasificar(c, viejo)[0], "propio")
        c = {"nombre": "DJI_20231015123456_0001_D", "origen": "Sin determinar", "bins": ["b.avb"]}
        self.assertEqual(eb.clasificar(c, viejo)[:2], ("propio", "nombre de clip de cámara"))
        # Tarjeta SD montada (DCIM, PRIVATE/AVCHD, MP_ROOT)
        for r in [r"F:\DCIM\100MEDIA\DJI_0001.MP4", r"F:\PRIVATE\AVCHD\BDMV\STREAM\00001.MTS", r"F:\MP_ROOT\100ANV01\MAH00001.MP4"]:
            self.assertEqual(eb.clasificar({"origen": "Fichero importado", "fichero_origen": r, "bins": ["b.avb"]}, viejo)[0], "propio", r)

    def test_camara_propia_importada_de_una_carpeta_es_material(self):
        dji = {"origen": "Fichero importado", "fichero_origen": r"C:\Users\Usuario\Desktop\China\DJI_0012.MP4"}
        movil = {"origen": "Fichero importado", "fichero_origen": r"C:\Users\Usuario\Desktop\China\IMG_1234.MOV"}
        tarjeta = {"origen": "Fichero importado", "fichero_origen": r"J:\XDROOT\Clip\C0001.MXF"}
        self.assertFalse(eb.es_importado(dji, self.cfg))
        self.assertTrue(eb.es_importado(movil, self.cfg))
        self.assertFalse(eb.es_importado(tarjeta, self.cfg))
        self.assertIsNone(eb.motivo_descarte(dji, self.cfg))
        self.assertEqual(eb.motivo_descarte(movil, self.cfg), "importado")

    def test_bins_de_agencias(self):
        for b in ["20260916 agencias.avb", "AGENCIA.avb", "Reuters 16-09.avb", "CCTV.avb", "xinhua 0917"]:
            self.assertTrue(eb.es_bin_de_agencia(b, self.cfg), b)
        for b in ["20260916 brutos.avb", "Capturas.avb", "Rodaje Pekín.avb", "mapas.avb"]:
            self.assertFalse(eb.es_bin_de_agencia(b, self.cfg), b)

    @unittest.skipIf(da is None, "sin pyavb")
    def test_origen_segun_el_bin(self):
        importado = {"origen": "Fichero importado", "fichero_origen": r"C:\descargas\CCTV 新闻.mp4"}
        da.origen_segun_bin(importado, "20260916 agencias.avb", self.cfg)
        self.assertEqual(importado["origen"], "Agencia (bin de agencias)")
        self.assertEqual(eb.motivo_descarte(importado, self.cfg), "agencia")
        propio = {"origen": "Fichero importado", "fichero_origen": r"C:\China\DJI_0012.MP4"}
        da.origen_segun_bin(dict(propio), "20260916 agencias.avb", self.cfg)  # en un bin de agencias, lo de cámara sigue siendo propio
        da.origen_segun_bin(propio, "20260916 agencias.avb", self.cfg)
        self.assertEqual(propio["origen"], "Cámara (fichero importado)")
        reuters = {"origen": "Agencia Reuters", "fichero_origen": r"C:\\descargas\\2026-09-16T120000Z_1_X_RTR_A_1-CHINA.MP4"}
        da.origen_segun_bin(reuters, "20260916 agencias.avb", self.cfg)
        self.assertEqual(reuters["origen"], "Agencia Reuters")
        normal = {"origen": "Sin determinar", "fichero_origen": None, "nombre": "entrevista"}
        da.origen_segun_bin(normal, "Rodaje.avb", self.cfg)
        self.assertEqual(normal["origen"], "Sin determinar")

    def test_1080i50_no_se_desentrelaza(self):
        dnxhd = {"tipo": "video", "ancho": 1920, "alto": 1080, "fps": "25/1", "field_order": "tt"}
        self.assertTrue(eb.es_1080i25(dnxhd))
        for otro in [dict(dnxhd, fps="30000/1001"), dict(dnxhd, field_order="bb"), dict(dnxhd, field_order="progressive"), dict(dnxhd, ancho=1440)]:
            self.assertFalse(eb.es_1080i25(otro), otro)
        args = eb.args_video({"modo": "convertir", "destino": {"xdcam": True}}, {"pistas": [dnxhd]}, self.cfg)
        filtros = args[args.index("-vf") + 1]
        self.assertNotIn("bwdif", filtros)
        self.assertNotIn("interlace", filtros)
        self.assertIn("setfield=tff", filtros)
        self.assertIn("color_primaries=bt709", filtros)
        dv = {"tipo": "video", "ancho": 720, "alto": 480, "fps": "30000/1001", "field_order": "bb"}
        filtros = eb.args_video({"modo": "convertir", "destino": {"xdcam": True}}, {"pistas": [dv]}, self.cfg)
        self.assertIn("bwdif", filtros[filtros.index("-vf") + 1])

    @unittest.skipIf(da is None, "sin pyavb")
    def test_casos_de_video_en_el_informe(self):
        v = lambda **kw: {"pistas": [dict({"tipo": "video", "fps": "30/1", "fps_medio": "30/1"}, **kw)]}
        mxf = [v(rotacion=-90), v(rotacion=180), v(color_transfer="arib-std-b67"), v(color_transfer="smpte2084"),
               v(color_primarias="bt2020"), v(fps_medio="2910/100"), v(), {"pistas": [{"tipo": "audio"}]}]
        self.assertEqual(da.casos_de_video(mxf), {"vertical (girado)": 1, "HDR HLG": 1, "HDR PQ": 1, "gama amplia BT.2020": 1,
                                                  "velocidad variable": 1})

    def test_rutas_largas_solo_en_windows(self):
        larga = "C:\\" + "\\".join(["北京 0916 entrevista larga con nombre"] * 10) + "\\DJI_0012.MP4"
        if os.name == "nt":
            self.assertTrue(eb.ruta_larga(larga).startswith("\\\\?\\"))
            self.assertEqual(eb.ruta_larga("\\\\servidor\\" + "x" * 250), "\\\\?\\UNC\\servidor\\" + "x" * 250)
        else:
            self.assertEqual(eb.ruta_larga(larga), larga)
        self.assertEqual(eb.ruta_larga("corta"), "corta")


@unittest.skipIf(da is None, "sin pyavb")
class OrigenPorSenales(unittest.TestCase):
    """Parte 4 del plan: el origen se decide por lo que dice el fichero y de dónde se importó; los nombres son pistas."""

    def setUp(self):
        self.cfg = dict(eb.CONFIG_DEFECTO)

    def clip(self, fichero=None, origen="Sin determinar", bins=("material.avb",), **kw):
        c = {"nombre": "X", "origen": origen, "bins": list(bins)}
        if fichero:
            c["fichero_origen"] = fichero
        c.update(kw)
        return c

    def test_marca_de_metadatos(self):
        m = eb.marca_de_metadatos
        self.assertEqual(m({"com.apple.quicktime.make": "Apple", "com.apple.quicktime.model": "iPhone 13"}), ("Apple iPhone 13", "movil"))
        self.assertEqual(m({"make": "Panasonic", "model": "DC-GH5"}), ("Panasonic DC-GH5", "camara"))
        self.assertEqual(m({"make": "Canon", "model": "Canon EOS R5"}), ("Canon EOS R5", "camara"))
        self.assertEqual(m({"com.android.version": "13"}), ("Android", "movil"))
        self.assertEqual(m({}, [{"handler_name": "\tGoPro AVC  "}]), ("GoPro", "camara"))
        self.assertEqual(m({"encoder": "DJIMavic3"}), ("DJI", "camara"))
        self.assertEqual(m({"major_brand": "XAVC"}), ("Sony (XAVC)", "camara"))
        self.assertEqual(m({"company_name": "Sony", "product_name": "PMW-500"}), ("Sony PMW-500", "camara"))
        self.assertEqual(m({}, [{"handler_name": "ISO Media file produced by Google Inc."}]), ("YouTube/Google", "internet"))
        # Los MXF de Avid y lo recodificado con ffmpeg no dicen nada de la cámara
        self.assertEqual(m({"company_name": "Avid Technology, Inc.", "product_name": "Media Composer"}), (None, None))
        self.assertEqual(m({"encoder": "Lavf60.16.100", "major_brand": "isom"}), (None, None))
        # Grabadores externos: la cabecera de un Atomos (make, o solo el encoder en otros firmwares) y la de Blackmagic
        self.assertEqual(m({"make": "Atomos", "encoder": "Blade - 5.2   "}), ("Atomos", "camara"))
        self.assertEqual(m({"encoder": "Blade - 5.2   "}), ("Atomos (Blade - 5.2)", "camara"))
        self.assertEqual(m({"encoder": "Ninja V"}), ("Atomos (Ninja V)", "camara"))
        self.assertEqual(m({"encoder": "Atomos Shogun 7"}), ("Atomos Shogun 7", "camara"))
        self.assertEqual(m({"com.blackmagic-design.camera.model": "Pocket Cinema Camera 6K"}),
                         ("Blackmagic Pocket Cinema Camera 6K", "camara"))
        self.assertEqual(m({"encoder": "Bladerunner export"}), (None, None))  # la palabra entera, no un trozo

    def test_metadatos_de_camara_con_nombre_desconocido_es_propio(self):
        c = self.clip(r"E:\material\entrevista alcalde.mov", marca="Panasonic DC-GH5", marca_tipo="camara")
        self.assertEqual(eb.clasificar(c, self.cfg), ("propio", "metadatos: Panasonic DC-GH5", True))
        self.assertFalse(eb.es_importado(c, self.cfg))

    def test_sin_pistas_es_dudoso_y_queda_fuera(self):
        # Desde el 27/10/2026 solo entra lo de cámara. Hasta entonces, ante la duda entraba (solo_material_de_camara = false)
        c = self.clip(r"D:\varios\plano recurso.mp4")
        clase, motivo, seguro = eb.clasificar(c, self.cfg)
        self.assertEqual((clase, seguro), ("dudoso", False))
        self.assertIn("varios", motivo)
        self.assertFalse(eb.es_importado(c, self.cfg))
        self.assertEqual(eb.motivo_descarte(c, self.cfg), "dudoso")
        self.assertIsNone(eb.motivo_descarte(c, dict(self.cfg, solo_material_de_camara=False)))
        self.assertEqual(eb.texto_origen(clase, motivo, seguro), "Dudoso · para revisar")
        # Sin ruta de importación y sin nombre de cámara (capturado, o creado en Avid: pantallas partidas): dudoso, fuera
        for c in [self.clip(), self.clip(nombre="PANTALLA PARTIDA")]:
            self.assertEqual(eb.clasificar(c, self.cfg)[:2], ("dudoso", "sin pistas de cámara ni ruta de importación (¿capturado o creado en Avid?)"))
            self.assertEqual(eb.motivo_descarte(c, self.cfg), "dudoso")
            self.assertEqual(eb.clasificar(c, dict(self.cfg, solo_material_de_camara=False))[0], "propio")
        # Un clip P2 (aunque no esté en ningún bin) o con el origen de cámara de config_origen.json, entra
        for c in [self.clip(nombre="0001AB"), self.clip(nombre="X", nombres_bin=["0016Q3.new.01"]),
                  self.clip(origen="Cámara (tarjeta P2)")]:
            self.assertEqual(eb.clasificar(c, self.cfg)[0], "propio", c)
            self.assertIsNone(eb.motivo_descarte(c, self.cfg))
        for n in ["0001AB EXTRA", "2017 07 21", "12345AB", "AB0001"]:
            self.assertEqual(eb.clasificar(self.clip(nombre=n), self.cfg)[0], "dudoso", n)
        # Lo marcado "Entra" en Origen del material entra
        cfg = dict(self.cfg, decisiones_origen=[{"carpeta": "", "bin": "material.avb", "clase": "propio"}])
        self.assertIsNone(eb.motivo_descarte(self.clip(nombre="PANTALLA PARTIDA"), cfg))

    def test_bin_de_compras_es_dudoso_no_agencia(self):
        c = self.clip(r"C:\material\compras\vista aerea.mp4", bins=["compras.avb"])
        self.assertEqual(eb.clasificar(c, self.cfg)[0], "dudoso")

    def test_lo_elegido_manda_y_queda_como_revisado(self):
        c = self.clip(r"C:\material\compras\vista aerea.mp4", bins=["compras.avb"])
        cfg = dict(self.cfg, decisiones_origen=[{"carpeta": r"\material\compras", "bin": "compras.avb", "clase": "agencia"}])
        clase, motivo, seguro = eb.clasificar(c, cfg)
        self.assertEqual((clase, seguro), ("agencia", True))
        self.assertEqual(eb.texto_origen(clase, motivo, seguro), "Agencia · revisado")
        # Otra letra de unidad (otro PC): es la misma procedencia
        self.assertEqual(eb.clasificar(self.clip(r"F:\material\compras\otra.mp4", bins=["compras.avb"]), cfg)[0], "agencia")

    def test_regla_recordada_para_otros_discos(self):
        cfg = dict(self.cfg, reglas_origen=[{"carpeta": "Compras", "clase": "agencia"}, {"bin": "señal", "clase": "importado"}])
        self.assertEqual(eb.clasificar(self.clip(r"C:\Users\a\COMPRAS\x.mp4"), cfg)[0], "agencia")
        self.assertEqual(eb.clasificar(self.clip(r"C:\x\y.mp4", bins=["Señal 24h.avb"]), cfg)[0], "importado")
        self.assertEqual(eb.clasificar(self.clip(r"C:\x\y.mp4"), cfg)[0], "dudoso")

    def test_pistas_contrarias_es_dudoso(self):
        # Una tarjeta de cámara... con un vídeo de móvil según sus metadatos
        c = self.clip(r"E:\CARD01\CONTENTS\CLIP\x.mp4", marca="Apple iPhone 13", marca_tipo="movil")
        clase, motivo, seguro = eb.clasificar(c, self.cfg)
        self.assertEqual((clase, seguro), ("dudoso", False))
        self.assertIn("contrarias", motivo)

    def test_casos_de_siempre(self):
        cl = lambda c: eb.clasificar(c, self.cfg)[0]
        # Reuters (por el nombre) y el bin de agencias, aunque estén en Descargas: agencia
        self.assertEqual(cl(self.clip(r"C:\Users\c\Downloads\x_RTRMADV_y.MP4", origen="Agencia Reuters")), "agencia")
        self.assertEqual(cl(self.clip(r"C:\Users\c\Downloads\CCTV.mp4", origen="Agencia (bin de agencias)")), "agencia")
        # Dron y A7 copiados al PC: propio por el nombre del fichero, aunque estén en el Escritorio
        self.assertEqual(cl(self.clip(r"C:\Users\c\Desktop\China\dron\DJI_0012.MP4", origen="Cámara (fichero importado)")), "propio")
        self.assertEqual(cl(self.clip(r"D:\北京\A7III\C0380.MP4")), "propio")
        # Móvil y descargas: fuera
        self.assertEqual(cl(self.clip(r"C:\Users\a\Desktop\videos movil\VID_20210915_101500.mp4")), "importado")
        self.assertEqual(cl(self.clip(r"C:\Users\a\Downloads\video.mp4")), "importado")
        self.assertEqual(cl(self.clip(r"C:\Users\a\WhatsApp Video\x.mp4")), "importado")
        # Tarjeta, capturado (sin ruta) y render
        self.assertEqual(cl(self.clip(r"E:\TARJETAS\CARD01\CONTENTS\VIDEO\0001AB.MXF", origen="Fichero importado")), "propio")
        self.assertEqual(cl(self.clip(nombre="0001AB")), "propio")
        self.assertEqual(cl(self.clip()), "dudoso")  # capturado sin nombre de cámara: solo_material_de_camara
        self.assertEqual(cl(self.clip(render=True)), "efecto")

    def test_agencia_por_el_nombre(self):
        # Disco real (26/10/2026): un Reuters con espacios en vez de guiones no cumplía la regla de config_origen.json,
        # quedaba "dudoso" y entraba en el bruto. El indicativo en el nombre del fichero o del clip basta.
        cl = lambda c: eb.clasificar(c, self.cfg)
        for c in [self.clip(r"D:\varios\2019 09 29T150951Z_1_LWD0016J7GF9J_RTRWNEV_D_7121 YEMEN.mp4", origen="Fichero importado"),
                  self.clip(r"C:\x\Reuters_xi.mp4"), self.clip(nombre="REUTERS SISMO"), self.clip(nombres_bin=["sismo afp"]),
                  self.clip(r"C:\x\EFE-incendio.mp4"), self.clip(nombre="APTN 0916"), self.clip(nombre="CCTV 新闻 0916"),
                  self.clip(nombre="EUROVISION EVN 12"), self.clip(nombre="xinhua_beijing")]:
            clase, motivo, seguro = cl(c)
            self.assertEqual((clase, seguro), ("agencia", True), c)
            self.assertIn("nombre de agencia", motivo)
            self.assertEqual(eb.motivo_descarte(c, self.cfg), "agencia")
        # Manda sobre los metadatos de cámara (Reuters graba con Sony: el MP4 trae su XAVC) y la carpeta de tarjeta
        c = self.clip(r"C:\x\RTRMADV_SPAIN.mp4", marca="Sony (XAVC)", marca_tipo="camara")
        self.assertEqual(cl(c)[0], "agencia")
        # Lo elegido a mano sigue mandando
        cfg = dict(self.cfg, decisiones_origen=[{"carpeta": r"\x", "bin": "material.avb", "clase": "propio"}])
        self.assertEqual(eb.clasificar(c, cfg)[0], "propio")
        # Palabras que contienen las siglas no cuentan
        for n in ["defensa", "Efectos", "Capturas", "Afganistán", "TRTR", "plano AP", "aptitud", "cctvx"]:
            self.assertNotEqual(cl(self.clip(nombre=n))[0], "agencia", n)
        self.assertEqual(cl(self.clip(nombre="REUTERS", nombres_bin=None)), cl(self.clip(nombre="REUTERS")))
        # Se puede apagar
        self.assertEqual(eb.clasificar(self.clip(nombre="REUTERS"), dict(self.cfg, nombres_agencia_regex=""))[0], "dudoso")

    def test_anotar_y_leer_decisiones(self):
        cs = [self.clip(r"D:\varios\a.mp4"), self.clip(nombre="0001AB")]
        eb.anotar_origen(cs, self.cfg)
        self.assertEqual([c["origen_texto"] for c in cs], ["Dudoso · para revisar", "Material propio"])
        d = Path(tempfile.mkdtemp())
        try:
            self.assertEqual(eb.leer_decisiones_origen(d), [])
            (d / "origen.json").write_text(json.dumps({"decisiones": [{"carpeta": "\\varios", "bin": "material.avb", "clase": "propio"},
                                                                      {"carpeta": "x", "clase": "inventada"}]}), encoding="utf-8")
            self.assertEqual(len(eb.leer_decisiones_origen(d)), 1)
            (d / "origen.json").write_text("{roto", encoding="utf-8")
            self.assertEqual(eb.leer_decisiones_origen(d), [])
        finally:
            shutil.rmtree(d)


class PantallaDeOrigen(unittest.TestCase):
    """Parte 4b: la pantalla "Origen del material" agrupa por procedencia, guarda origen.json y recuerda reglas."""

    def setUp(self):
        self.cfg = dict(eb.CONFIG_DEFECTO)
        self.d = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.d)

    def clips(self):
        c = lambda n, f=None, b="material.avb", d=10.0, **kw: dict({"nombre": n, "fichero_origen": f, "bins": [b], "duracion": d,
                                                                    "origen": "Sin determinar"}, **kw)
        return [c("A1", r"D:\varios\a1.mp4"), c("A2", r"E:\varios\a2.mp4", d=20.0),  # misma carpeta en otra unidad
                c("CAM", r"E:\XDROOT\Clip\C0001.MXF", d=100.0),
                c("COMPRA", r"C:\material\compras\x.mp4", b="compras.avb", d=5.0),
                c("CAPTURADO", None, d=50.0),
                c("FUNDIDO", None, render=True)]

    def test_agrupa_por_procedencia_con_lo_dudoso_arriba(self):
        gs = eb.grupos_de_origen(self.clips(), self.cfg)
        # Lo capturado sin nombre de cámara también es dudoso (solo_material_de_camara): arriba, por duración
        self.assertEqual([(g["carpeta"], len(g["clips"])) for g in gs],
                         [("", 1), (r"\varios", 2), (r"\material\compras", 1), (r"\XDROOT\Clip", 1)])  # sin el render
        varios = gs[1]
        self.assertEqual((varios["dudosos"], varios["segundos"], varios["propuesta"], varios["ejemplos"]), (2, 30.0, "Dudoso", ["A1", "A2"]))
        self.assertIn("sin pistas", varios["motivo"])
        self.assertEqual(gs[3]["propuesta"], "Material propio")
        self.assertIsNone(varios["elegido"])

    def test_guardar_y_aplicar_lo_elegido(self):
        eb.guardar_decisiones_origen(self.d, [{"carpeta": r"\material\compras", "bin": "compras.avb", "clase": "agencia"},
                                              {"carpeta": r"\varios", "bin": "material.avb", "clase": None}])
        decisiones = eb.leer_decisiones_origen(self.d)
        self.assertEqual(decisiones, [{"carpeta": r"\material\compras", "bin": "compras.avb", "clase": "agencia"}])
        cfg = dict(self.cfg, decisiones_origen=decisiones)
        gs = {g["carpeta"]: g for g in eb.grupos_de_origen(self.clips(), cfg)}
        self.assertEqual(gs[r"\material\compras"]["elegido"], "agencia")
        self.assertEqual(gs[r"\material\compras"]["propuesta"], "Dudoso")  # la propuesta sigue siendo la del programa
        compra = next(c for c in self.clips() if c["nombre"] == "COMPRA")
        self.assertEqual(eb.motivo_descarte(compra, cfg), "agencia")  # y al exportar, fuera

    def test_regla_para_otros_discos(self):
        self.assertEqual(eb.regla_de_origen(r"\material\compras", "compras.avb", "agencia"),
                         {"carpeta": r"\material\compras", "clase": "agencia"})
        self.assertEqual(eb.regla_de_origen("", "Señal 24h.avb", "importado"), {"bin": "Señal 24h", "clase": "importado"})

    @unittest.skipUnless(da, "sin pyavb")
    def test_recordar_reglas_en_config_brutos(self):
        original = da.carpeta_base
        da.carpeta_base = lambda: self.d
        try:
            da.recordar_reglas_origen([{"carpeta": "Compras", "clase": "agencia"}, {"bin": "señal", "clase": "importado"}])
            da.recordar_reglas_origen([{"carpeta": "compras", "clase": "propio"}, {"bin": "señal", "clase": None}])
            cfg = eb.cargar_config(self.d / "config_brutos.json")
            self.assertEqual(cfg["reglas_origen"], [{"carpeta": "compras", "clase": "propio"}])  # sustituida y quitada
            self.assertEqual(cfg["agrupar"], "proyecto_mes")  # el resto de la configuración, intacto
        finally:
            da.carpeta_base = original


class QueProyectosRestaurar(unittest.TestCase):
    """Antes del montaje, por proyecto, los clips de cámara (P2, no agencias) con una captura de
    cada uno, y los que solo tienen uno o dos clips cortos marcados para descartar."""

    def setUp(self):
        self.cfg = json.loads(json.dumps(eb.CONFIG_DEFECTO))

    def clip(self, nombre, proyectos, segundos, **kw):
        return dict({"clave": "K" + nombre, "nombre": nombre, "proyectos": list(proyectos), "duracion": segundos,
                     "fichero_video": "MXF/%s.mxf" % nombre, "origen": "Sin determinar", "bins": ["b.avb"],
                     "num_video": 1, "num_audio": 2, "timecode": "10:00:00:00"}, **kw)

    def clips(self):
        return [self.clip("0001AB", ["GRANDE"], 600), self.clip("0002AB", ["GRANDE"], 300), self.clip("0003AB", ["GRANDE"], 30),
                self.clip("0004AB", ["CORTO"], 20), self.clip("0005AB", ["CORTO"], 40),
                self.clip("REUTERS SISMO", ["CORTO"], 900), self.clip("RTR_X", ["AGENCIAS"], 900),
                self.clip("PANTALLA PARTIDA", ["AGENCIAS"], 50),
                self.clip("0006AB", ["LARGO"], 200),  # uno solo, pero largo
                self.clip("0007AB", ["DOS"], 50), self.clip("0007AC", ["DOS", "GRANDE"], 80),  # en dos proyectos: cuenta en los dos
                self.clip("X,Dissolve,1", ["GRANDE"], 1, render=True),
                self.clip("0009AB", [], 30), self.clip("0010AB", ["GRANDE"], 0, fichero_video=None)]

    def test_veredicto_por_proyecto(self):
        t = {x["proyecto"]: x for x in eb.triaje_proyectos(self.clips(), self.cfg, ["GRANDE", "CORTO", "AGENCIAS", "LARGO", "DOS", "VACIO"])}
        self.assertEqual({k: v["veredicto"] for k, v in t.items()},
                         {"GRANDE": "restaurar", "CORTO": "poco", "AGENCIAS": "revisar", "LARGO": "restaurar", "DOS": "restaurar",
                          "VACIO": "sin", "SIN_PROYECTO": "poco"})
        self.assertEqual([c["nombre"] for c in t["AGENCIAS"]["dudosos"]], ["PANTALLA PARTIDA"])  # para revisar, no de cámara
        self.assertEqual([c["nombre"] for c in t["GRANDE"]["camara"]], ["0001AB", "0002AB", "0003AB", "0007AC"])
        self.assertEqual((t["GRANDE"]["segundos"], t["GRANDE"]["mas_largo"]), (1010, 600))
        self.assertEqual(t["GRANDE"]["otros"], {"sin vídeo": 1})  # el render no cuenta
        self.assertEqual([c["nombre"] for c in t["CORTO"]["camara"]], ["0004AB", "0005AB"])  # Reuters no es de cámara
        self.assertEqual(t["CORTO"]["otros"], {"agencia": 1})
        self.assertEqual(t["AGENCIAS"]["otros"], {"agencia": 1, "dudoso": 1})
        self.assertEqual(t["DOS"]["segundos"], 130)  # dos clips, pero 2 min 10 s: no es poco
        # Orden: restaurar, poco, sin; dentro, por nombre
        self.assertEqual([x["proyecto"] for x in eb.triaje_proyectos(self.clips(), self.cfg, ["GRANDE", "CORTO", "AGENCIAS", "LARGO", "DOS", "VACIO"])],
                         ["DOS", "GRANDE", "LARGO", "CORTO", "SIN_PROYECTO", "AGENCIAS", "VACIO"])

    def test_proyectos_solo_con_mp4_y_mov(self):
        # Proyectos solo con MP4 y MOV. Sin nombre ni
        # datos de cámara son dudosos y no entran en los brutos, pero el proyecto no es "sin material": se revisa
        cs = [self.clip("entrevista alcalde", ["MOVS"], 900, fichero_origen=r"D:\rodaje\entrevista alcalde.mov"),
              self.clip("recurso plaza", ["MOVS"], 300, fichero_origen=r"D:\rodaje\recurso plaza.mp4"),
              self.clip("0001AB", ["MEZCLA"], 30), self.clip("toma larga", ["MEZCLA"], 600, fichero_origen=r"D:\x\toma larga.mov"),
              self.clip("IMG_1234", ["MOVIL"], 60, fichero_origen=r"D:\x\IMG_1234.MOV")]
        t = {x["proyecto"]: x for x in eb.triaje_proyectos(cs, self.cfg, ["MOVS", "MEZCLA", "MOVIL"])}
        self.assertEqual((t["MOVS"]["veredicto"], len(t["MOVS"]["dudosos"]), t["MOVS"]["camara"]), ("revisar", 2, []))
        self.assertEqual(t["MEZCLA"]["veredicto"], "revisar")  # un P2 corto, pero 10 min de MOV sin pistas: se revisa
        self.assertEqual(t["MOVIL"]["veredicto"], "sin")  # el móvil es importado de fuera, no dudoso
        # Elegido "Entra" en Origen del material: pasan a ser de cámara y el proyecto se restaura
        cfg = dict(self.cfg, decisiones_origen=[{"carpeta": r"\rodaje", "bin": "b.avb", "clase": "propio"}])
        self.assertEqual({x["proyecto"]: x["veredicto"] for x in eb.triaje_proyectos(cs, cfg)}["MOVS"], "restaurar")
        # Los umbrales se cambian en config_brutos.json
        t = {x["proyecto"]: x["veredicto"] for x in eb.triaje_proyectos(self.clips(), dict(self.cfg, triaje_max_clips=3, triaje_max_minutos=5))}
        self.assertEqual((t["DOS"], t["LARGO"], t["GRANDE"]), ("poco", "poco", "restaurar"))
        # Lo elegido a mano en Origen del material cuenta: lo dudoso marcado "Entra" es de cámara
        cfg = dict(self.cfg, decisiones_origen=[{"carpeta": "", "bin": "b.avb", "clase": "propio"}])
        t = {x["proyecto"]: x for x in eb.triaje_proyectos(self.clips(), cfg)}
        self.assertIn("PANTALLA PARTIDA", [c["nombre"] for c in t["AGENCIAS"]["camara"]])

    @unittest.skipIf(da is None, "sin pyavb")
    def test_listado_solo_de_lo_analizado_ahora(self):
        """Con proyectos de análisis anteriores sumados, proyectos.html y proyectos.csv son de los analizados ahora;
        triaje.json lleva todos, para que el selector siga marcándolos."""
        d = Path(tempfile.mkdtemp())
        try:
            triaje = eb.triaje_proyectos(self.clips(), self.cfg, ["GRANDE", "CORTO", "AGENCIAS"])
            cuenta = da.escribir_triaje(triaje, {"origen": "E:\\", "fecha": "29/10/2026"}, d, self.cfg, {"CORTO"})
            self.assertEqual(cuenta, {"poco": 1})
            filas = (d / "proyectos.csv").read_text(encoding="utf-8-sig").splitlines()[1:]
            self.assertEqual([f.split(";")[0] for f in filas], ["CORTO"])
            self.assertNotIn("GRANDE", (d / "proyectos.html").read_text(encoding="utf-8"))
            todos = {t["proyecto"] for t in json.loads((d / "triaje.json").read_text(encoding="utf-8"))}
            self.assertTrue({"GRANDE", "CORTO", "AGENCIAS"} <= todos)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    @unittest.skipIf(da is None, "sin pyavb")
    def test_listado_html_csv_y_json(self):
        d = Path(tempfile.mkdtemp())
        try:
            cs = self.clips()
            cs[0]["miniatura"] = "miniaturas/00000_0001AB.jpg"
            triaje = eb.triaje_proyectos(cs, self.cfg, ["GRANDE", "CORTO", "AGENCIAS"])
            cuenta = da.escribir_triaje(triaje, {"origen": "E:\\", "fecha": "28/10/2026"}, d, self.cfg)
            self.assertEqual(cuenta, {"restaurar": 3, "poco": 2, "revisar": 1})
            html = (d / "proyectos.html").read_text(encoding="utf-8")
            self.assertIn("Qué proyectos restaurar", html)
            self.assertIn("miniaturas/00000_0001AB.jpg", html)
            self.assertIn("Poco material: ¿descartar?", html)
            self.assertNotIn("REUTERS SISMO", html.split("<table>")[1].split("</table>")[1])  # sin captura de la agencia
            self.assertIn("1 de agencia", html)
            filas = (d / "proyectos.csv").read_text(encoding="utf-8-sig").splitlines()
            self.assertEqual(filas[0], "Proyecto;Propuesta;Clips de cámara;Duración;El más largo;Sin audio;Otros clips")
            self.assertTrue(filas[1].startswith("DOS;Restaurar;2;"))
            datos = {t["proyecto"]: t for t in json.loads((d / "triaje.json").read_text(encoding="utf-8"))}
            self.assertEqual((datos["CORTO"]["veredicto"], datos["CORTO"]["clips"], datos["CORTO"]["segundos"]), ("poco", 2, 60))
            # Lo que dice el selector de proyectos
            self.assertEqual(da.texto_triaje(datos["CORTO"]), "   · Poco material: ¿descartar?: 2 clips de cámara, %s" % da.fmt_duracion(60))
            self.assertEqual(da.texto_triaje(datos["AGENCIAS"]), "   · Revisar: vídeos sin pistas de cámara: 1")
            self.assertEqual(da.texto_triaje({"clips": 0, "dudosos": 0}), "   · Sin material de cámara")
            self.assertIn("vídeo sin pistas de cámara", html)
            self.assertEqual(da.texto_triaje(None), "")
            self.assertEqual(da.texto_triaje(dict(datos["CORTO"], sin_audio=1)),
                             "   · Poco material: ¿descartar?: 2 clips de cámara, %s, 1 SIN AUDIO" % da.fmt_duracion(60))
        finally:
            shutil.rmtree(d)

    def test_aviso_de_clips_sin_audio(self):
        # Los clips de cámara sin ninguna pista de sonido se avisan
        cs = self.clips()
        cs[1]["num_audio"] = 0
        t = {x["proyecto"]: x for x in eb.triaje_proyectos(cs, self.cfg)}
        self.assertEqual([c["nombre"] for c in t["GRANDE"]["sin_audio"]], ["0002AB"])
        self.assertEqual(t["CORTO"]["sin_audio"], [])
        fila = lambda c: eb._fila_clip(1, "GRANDE", dict(c, ficheros=[c["fichero_video"]]), {})
        self.assertEqual(fila(cs[1])["aviso_audio"], "SIN AUDIO")
        self.assertIsNone(fila(cs[0])["aviso_audio"])
        if da:
            self.assertIn("SIN AUDIO", da.tarjeta_clip(dict(cs[1], duracion=300)))
            self.assertNotIn("SIN AUDIO", da.tarjeta_clip(dict(cs[0], duracion=600)))


@unittest.skipIf(da is None, "sin pyavb")
class AnalisisAcumulado(unittest.TestCase):
    """Un análisis guardado no se repite al pasar a otro proyecto. Un análisis de algunos proyectos suma los de análisis anteriores del mismo disco que no se repiten."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.origen = self.d / "disco"
        self.origen.mkdir()

    def tearDown(self):
        shutil.rmtree(self.d)

    def analisis(self, nombre, fecha, proyectos, clips, mxf, origen=None):
        a = self.d / "informes" / nombre
        (a / "miniaturas").mkdir(parents=True)
        for c in clips:
            if c.get("miniatura"):
                (a / c["miniatura"]).write_bytes(b"jpg")
        datos = {"resumen.json": {"origen": str(origen or self.origen), "fecha": fecha, "parcial": {"proyectos": proyectos}},
                 "proyectos.json": [{"nombre": p, "carpeta": p} for p in proyectos],
                 "bins.json": [{"proyecto": p, "nombre": p + ".avb", "clips": []} for p in proyectos],
                 "clips.json": clips, "mxf.json": mxf,
                 "cruce.json": {"por_proyecto": [{"proyecto": p, "clips_fisicos": 1} for p in proyectos]}}
        for n, v in datos.items():
            (a / n).write_text(json.dumps(v), encoding="utf-8")
        return a

    def clip(self, nombre, proyectos, **kw):
        return dict({"clave": "K" + nombre, "nombre": nombre, "proyectos": list(proyectos), "ficheros": ["MXF/%s.mxf" % nombre],
                     "fichero_video": "MXF/%s.mxf" % nombre}, **kw)

    def mxf(self, nombre, bytes_=100, fecha="2021-09-15 10:00:00"):
        return {"ruta": "MXF/%s.mxf" % nombre, "bytes": bytes_, "modificado": fecha}

    def test_suma_lo_de_antes_y_salta_lo_que_ha_cambiado(self):
        self.analisis("Diagnostico_disco_1", "2026-10-27 10:00:00", ["A", "B"],
                      [self.clip("a1", ["A"], miniatura="miniaturas/00000_a1.jpg"), self.clip("b1", ["B"]),
                       self.clip("comun", ["A", "C"])],
                      [self.mxf("a1"), self.mxf("b1"), self.mxf("comun")])
        self.analisis("Diagnostico_disco_2", "2026-10-28 09:00:00", ["B"], [self.clip("b1", ["B"])], [self.mxf("b1", fecha="2021-09-16 10:00:00")])
        self.analisis("Diagnostico_otro", "2026-10-28 11:00:00", ["Z"], [self.clip("z1", ["Z"])], [self.mxf("z1")], origen=self.d / "otro")
        destino = self.d / "informes" / "Diagnostico_disco_3"
        destino.mkdir()
        # Ahora se analiza C. En el disco: a1 y comun igual que antes; b1 ha cambiado después del análisis 2
        ficheros = [{"ruta": "MXF/a1.mxf", "bytes": 100, "modificado": "2021-09-15 10:00:00"},
                    {"ruta": "MXF/comun.mxf", "bytes": 100, "modificado": "2021-09-15 10:00:00"},
                    {"ruta": "MXF/b1.mxf", "bytes": 999, "modificado": "2021-09-16 10:00:00"},
                    {"ruta": "MXF/c1.mxf", "bytes": 100, "modificado": "2021-09-17 10:00:00"}]
        proyectos, bins = [{"nombre": "C"}], [{"proyecto": "C", "nombre": "C.avb"}]
        mxf, clips = [self.mxf("c1"), self.mxf("comun")], [self.clip("c1", ["C"]), self.clip("comun", ["C"])]
        cruce = {"por_proyecto": [{"proyecto": "C"}]}
        sumados, cambiados = da.sumar_analisis_anteriores(self.d / "informes", self.origen, destino, ficheros, proyectos, bins, mxf, clips, cruce)
        self.assertEqual(sumados, {"A": "2026-10-27 10:00:00"})  # B, del análisis 2 (el más reciente con B), ha cambiado
        self.assertEqual(cambiados, ["B"])
        self.assertEqual([p["nombre"] for p in proyectos], ["A", "C"])
        self.assertEqual(sorted(b["nombre"] for b in bins), ["A.avb", "C.avb"])
        self.assertEqual([x["proyecto"] for x in cruce["por_proyecto"]], ["A", "C"])
        por = {c["nombre"]: c for c in clips}
        self.assertEqual(sorted(por), ["a1", "c1", "comun"])
        self.assertEqual(sorted(por["comun"]["proyectos"]), ["A", "C"])  # compartido: los dos proyectos
        self.assertEqual(sorted(m["ruta"] for m in mxf), ["MXF/a1.mxf", "MXF/c1.mxf", "MXF/comun.mxf"])
        self.assertEqual(por["a1"]["miniatura"], "miniaturas/ant_00000_a1.jpg")
        self.assertTrue((destino / "miniaturas" / "ant_00000_a1.jpg").exists())
        self.assertNotIn("Z", [p["nombre"] for p in proyectos])  # otro disco: nada

    def test_mas_reciente_primero(self):
        self.analisis("Diagnostico_disco_1", "2026-10-27 10:00:00", ["A"], [], [])
        self.analisis("Diagnostico_disco_2", "2026-10-28 10:00:00", ["B"], [], [])
        self.assertEqual([d.name for d, _ in da.analisis_del_disco(self.d / "informes", self.origen)],
                         ["Diagnostico_disco_2", "Diagnostico_disco_1"])
        self.assertEqual(da.buscar_analisis(self.d / "informes", self.origen).name, "Diagnostico_disco_2")


class AnalisisParcial(unittest.TestCase):
    def test_proyectos_fuera_del_analisis(self):
        import diagnostico_avid as da
        self.assertEqual(da.fuera_del_analisis({}, None), [])  # análisis del disco entero: está todo
        r = {"parcial": {"proyectos": ["ABORTO"], "proyectos_disco": 7}}
        self.assertEqual(da.fuera_del_analisis(r, ["ABORTO"]), [])
        self.assertEqual(da.fuera_del_analisis(r, ["ABORTO", "NTSC"]), ["NTSC"])
        self.assertEqual(da.fuera_del_analisis(r, None), ["el resto de proyectos del disco"])


class DatosRarosDeUnDiscoReal(unittest.TestCase):
    """Un dato inesperado de un fichero o un clip no debe parar el análisis de todo el disco (26/10/2026)."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_un_mxf_con_datos_raros_sale_como_ilegible(self):
        import diagnostico_avid as da
        original = da.ffprobe_json
        da.ffprobe_json = lambda ffprobe, ruta: ({"format": {"duration": "N/A"}, "streams": []}, None)
        try:
            r = da.analizar_mxf([{"ruta": "raro.mxf", "nombre": "raro.mxf", "ext": ".mxf", "bytes": 1, "modificado": ""}],
                                self.tmp, "ffprobe", lambda m: None)
        finally:
            da.ffprobe_json = original
        self.assertEqual(len(r), 1)
        self.assertIn("datos inesperados", r[0]["error"])

    def test_el_error_dice_en_que_paso_y_que_fichero_mandar(self):
        import diagnostico_avid as da
        (self.tmp / "disco").mkdir()
        original = da.detectar_proyectos
        da.detectar_proyectos = lambda *a: 1 / 0
        try:
            with self.assertRaises(ZeroDivisionError) as cm:
                da.ejecutar(self.tmp / "disco", self.tmp / "informe", progreso=lambda m: None, abrir=False)
        finally:
            da.detectar_proyectos = original
        self.assertTrue(cm.exception.fase.startswith("2/6 Detectando proyectos"), cm.exception.fase)
        self.assertTrue(cm.exception.log.exists())
        self.assertIn("ZeroDivisionError", cm.exception.log.read_text(encoding="utf-8"))


class CacheDelAnalisis(unittest.TestCase):
    """Lo ya leído con ffprobe no se repite si el análisis se para o se vuelve a hacer (26/10/2026)."""
    def setUp(self):
        import diagnostico_avid as da
        self.da = da
        self.tmp = Path(tempfile.mkdtemp())
        self.llamadas = []
        self.original, self.hilos = da.ffprobe_json, da.NUM_HILOS
        da.NUM_HILOS = 1  # en orden: el segundo fichero se lee después del primero
        da.ffprobe_json = self.ffprobe_falso
        self.ficheros = [{"ruta": n, "nombre": n, "ext": ".mxf", "bytes": 10, "modificado": "2021-09-15 10:00:00"}
                         for n in ("a.mxf", "b.mxf")]
        self.ruta = self.tmp / "cache_analisis" / "DISCO.json"

    def tearDown(self):
        self.da.ffprobe_json, self.da.NUM_HILOS = self.original, self.hilos
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ffprobe_falso(self, ffprobe, ruta):
        nombre = Path(str(ruta)).name
        self.llamadas.append(nombre)
        if nombre == "roto.mxf":
            return None, "ffprobe devolvió error"
        if nombre == "falla.mxf":
            raise RuntimeError("se desconecta el disco")
        return {"format": {"duration": "2.0", "tags": {"material_package_umid": "0x" + "AB" * 32}},
                "streams": [{"codec_type": "video", "codec_name": "dnxhd", "width": 1920, "height": 1080}]}, None

    def analizar(self, ficheros):
        cache = self.da.CacheMXF(self.ruta)
        return self.da.analizar_mxf(ficheros, self.tmp, "ffprobe", lambda m: None, cache=cache)

    def test_no_repite_lo_ya_leido_y_si_lo_cambiado(self):
        primero = self.analizar(self.ficheros)
        self.assertEqual(self.llamadas, ["a.mxf", "b.mxf"])
        segundo = self.analizar(self.ficheros)  # otra vez, leyendo la caché del disco
        self.assertEqual(self.llamadas, ["a.mxf", "b.mxf"])  # ni una llamada más
        self.assertEqual(primero, segundo)
        cambiado = [dict(self.ficheros[0], bytes=11), self.ficheros[1]]  # a.mxf ha cambiado de tamaño
        self.analizar(cambiado)
        self.assertEqual(self.llamadas, ["a.mxf", "b.mxf", "a.mxf"])

    def test_lo_que_falla_se_vuelve_a_intentar(self):
        roto = [{"ruta": "roto.mxf", "nombre": "roto.mxf", "ext": ".mxf", "bytes": 5, "modificado": ""}]
        self.analizar(roto)
        self.analizar(roto)
        self.assertEqual(self.llamadas, ["roto.mxf", "roto.mxf"])

    def test_si_algo_falla_a_mitad_lo_leido_no_se_pierde(self):
        con_fallo = self.ficheros[:1] + [{"ruta": "falla.mxf", "nombre": "falla.mxf", "ext": ".mxf", "bytes": 5, "modificado": ""}]
        with self.assertRaises(RuntimeError):
            self.analizar(con_fallo)
        self.analizar(self.ficheros[:1])
        self.assertEqual(self.llamadas, ["a.mxf", "falla.mxf"])  # a.mxf no se ha vuelto a leer

    def test_olvida_lo_que_ya_no_esta_y_las_versiones_viejas(self):
        self.analizar(self.ficheros)
        c = self.da.CacheMXF(self.ruta)
        c.quedarse(self.ficheros[:1])
        c.guardar()
        self.assertEqual(len(self.da.CacheMXF(self.ruta).datos), 1)
        self.ruta.write_text(json.dumps({"version": -1, "ficheros": c.datos}), encoding="utf-8")
        self.assertEqual(self.da.CacheMXF(self.ruta).datos, {})  # de otra versión: no se usa
        self.ruta.write_text("{roto", encoding="utf-8")
        self.assertEqual(self.da.CacheMXF(self.ruta).datos, {})

    def test_si_cambia_lo_que_se_guarda_hay_que_subir_la_version(self):
        r = self.da.resumir_mxf({"format": {"tags": {}}, "streams": [{"codec_type": "video"}]})
        self.assertEqual((self.da.VERSION_CACHE, sorted(r), sorted(r["pistas"][0])), (1, [
            "comentarios", "duracion", "formato", "marca", "marca_tipo", "nombre_clip", "op", "pistas", "producto", "timecode", "umid"], [
            "alto", "ancho", "bits", "canales", "codec", "color_matriz", "color_primarias", "color_rango", "color_transfer", "duracion",
            "field_order", "file_package_name", "file_package_umid", "fps", "fps_medio", "muestreo", "perfil", "pix_fmt", "rotacion",
            "tipo"]), "Ha cambiado lo que guarda resumir_mxf: sube VERSION_CACHE y actualiza esta lista")


@unittest.skipIf(da is None, "sin pyavb")
class CarpetasSinAvid(unittest.TestCase):
    """Todo el disco: si tiene 118 carpetas y 108 con proyecto de Avid, salen las 118. Las carpetas de primer nivel sin proyecto de Avid también son proyectos, y sus vídeos cuentan como material solo si
    algo los reconoce como de cámara (la cabecera, la carpeta de tarjeta o el nombre)."""

    def inventario(self, rutas):
        return [{"ruta": r, "carpeta": str(Path(r).parent), "nombre": Path(r).name, "ext": Path(r).suffix.lower(), "bytes": 1,
                 "modificado": "2026-10-28 10:00:00"} for r in rutas]

    def setUp(self):
        self.ficheros = self.inventario([
            "MEXICO/MEXICO.avp", "MEXICO/MEXICO Bin.avb", "MEXICO/brutos/C0001.MP4",
            "BINS VIEJOS/viejo.avb", "BINS VIEJOS/sub/otro.avb", "suelto.avb",
            "VIAJE/BLADE_S001_S001_T001.MOV", "VIAJE/sub/recurso.mp4", "VIAJE/notas.txt",
            "SOLO PAPELES/guion.docx", "Avid MediaFiles/MXF/1/a.mxf", "$RECYCLE.BIN/x.mp4", "raiz.mp4"])

    def test_bins_sin_avp_son_el_proyecto_de_su_carpeta(self):
        p = {x["nombre"]: x for x in da.detectar_proyectos(self.ficheros)}
        self.assertEqual(sorted(p), ["(bins sin proyecto)", "BINS VIEJOS", "MEXICO"])
        self.assertEqual(p["BINS VIEJOS"]["num_bins"], 2)
        self.assertTrue(p["BINS VIEJOS"]["sin_avp"])
        self.assertEqual([b["ruta"] for b in p["(bins sin proyecto)"]["bins"]], ["suelto.avb"])

    def test_carpetas_sin_nada_de_avid(self):
        proyectos = da.detectar_proyectos(self.ficheros)
        nuevos, videos = da.carpetas_sin_avid(self.ficheros, proyectos)
        # MEXICO y BINS VIEJOS ya son proyectos; la media de Avid, la papelera y lo de la raíz no son carpetas de proyecto
        self.assertEqual([(n["nombre"], n["videos"]) for n in nuevos], [("SOLO PAPELES", 0), ("VIAJE", 2)])
        self.assertTrue(all(n["sin_avid"] for n in nuevos))
        self.assertEqual(videos, {"VIAJE/BLADE_S001_S001_T001.MOV": "VIAJE", "VIAJE/sub/recurso.mp4": "VIAJE"})

    def test_carpetas_de_otro_origen(self):
        otro = str(Path("/otro/disco").resolve())
        fich = self.inventario([str(Path(otro) / "RODAJE" / "C0002.MP4"), str(Path(otro) / "PROY" / "PROY.avp")])
        nuevos, videos = da.carpetas_sin_avid(fich, da.detectar_proyectos(fich, [otro]), [otro])
        self.assertEqual([n["nombre"] for n in nuevos], ["RODAJE"])
        self.assertEqual(nuevos[0]["carpeta"], str(Path(otro) / "RODAJE"))
        self.assertEqual(list(videos.values()), ["RODAJE"])

    def test_el_nombre_no_choca_con_un_proyecto(self):
        fich = self.inventario(["A/VIAJE/VIAJE.avp", "VIAJE/plano.mov"])
        nuevos, _ = da.carpetas_sin_avid(fich, da.detectar_proyectos(fich))
        self.assertEqual([n["nombre"] for n in nuevos], ["VIAJE (carpeta)"])

    def test_asignar_sueltos(self):
        clips = [{"nombre": "BLADE_S001_S001_T001", "ficheros": ["VIAJE/BLADE_S001_S001_T001.MOV"]},
                 {"nombre": "enlazado", "ficheros": ["VIAJE/sub/recurso.mp4"]},
                 {"nombre": "de un bin", "ficheros": ["VIAJE/x.mov"], "proyectos": ["MEXICO"]}]
        videos = {"VIAJE/BLADE_S001_S001_T001.MOV": "VIAJE", "VIAJE/sub/recurso.mp4": "VIAJE", "VIAJE/x.mov": "VIAJE"}
        origen = str(Path("/disco").resolve())
        da.asignar_sueltos(clips, videos, {r"E:\VIAJE\sub\recurso.mp4": "VIAJE/sub/recurso.mp4"}, origen)
        self.assertEqual(clips[0]["proyectos"], ["VIAJE"])
        self.assertEqual(clips[0]["fichero_origen"], str(Path(origen) / "VIAJE/BLADE_S001_S001_T001.MOV"))
        self.assertTrue(clips[0]["sin_avid"])
        self.assertNotIn("proyectos", clips[1])  # lo enlaza un bin: es de ese proyecto, no de la carpeta
        self.assertEqual(clips[2]["proyectos"], ["MEXICO"])

    def test_solo_cuenta_lo_que_la_cabecera_reconoce(self):
        """Un vídeo suelto solo es material si algo de la cabecera lo reconoce. Con la de un Atomos es
        material; un MP4 sin nada (recodificado, descargado) es dudoso: fuera del bruto y el proyecto, para revisar."""
        cfg = dict(eb.CONFIG_DEFECTO)
        base = {"origen": "Fichero importado", "sin_avid": True, "proyectos": ["VIAJE"], "bins": [], "fichero_video": "x"}
        atomos = dict(base, nombre="toma 1", fichero_origen=r"D:\VIAJE\toma 1.mov", marca="Atomos", marca_tipo="camara")
        anonimo = dict(base, nombre="recurso", fichero_origen=r"D:\VIAJE\recurso.mp4")
        self.assertEqual(eb.clasificar(atomos, cfg)[:2], ("propio", "metadatos: Atomos"))
        self.assertEqual(eb.clasificar(anonimo, cfg)[0], "dudoso")
        self.assertTrue(eb.excluido(anonimo, cfg))
        self.assertTrue(eb.es_fichero_de_camara(r"D:\VIAJE\BLADE_S001_S001_T001.MOV", cfg))
        t = eb.triaje_proyectos([anonimo], cfg)
        self.assertEqual(t[0]["veredicto"], "revisar")

    def test_proxies_de_camara_no_son_material(self):
        """Una tarjeta copiada trae copias de baja calidad (P2: CONTENTS/PROXY; Sony: XDROOT/Sub, M4ROOT/SUB): fuera, o
        cada toma saldría dos veces en el bruto. El material de verdad sí cuenta."""
        for ruta in [r"T\CONTENTS\PROXY\0001AB.MP4", "T/PRIVATE/M4ROOT/SUB/C0001S03.MP4", "T/XDROOT/Sub/C0001S01.MXF", "Proxies/x.mov"]:
            self.assertTrue(da.es_proxy(ruta), ruta)
        for ruta in [r"T\CONTENTS\VIDEO\0001AB.MXF", "T/XDROOT/Clip/C0001.MXF", "T/PRIVATE/M4ROOT/CLIP/C0001.MP4", "T/sub/plano.mp4"]:
            self.assertFalse(da.es_proxy(ruta), ruta)
        fich = self.inventario(["TARJETA/CONTENTS/VIDEO/0001AB.MXF", "TARJETA/CONTENTS/AUDIO/0001AB00.MXF",
                                "TARJETA/CONTENTS/PROXY/0001AB.MP4", "TARJETA/CONTENTS/CLIP/0001AB.XML"])
        _, videos = da.carpetas_sin_avid(fich, [])
        self.assertEqual(sorted(videos), ["TARJETA/CONTENTS/AUDIO/0001AB00.MXF", "TARJETA/CONTENTS/VIDEO/0001AB.MXF"])

    def test_clip_p2_enlazado_es_del_proyecto_de_avid(self):
        """En P2 el bin enlaza el .MXF de vídeo; el sonido va en otros .MXF del mismo clip (CONTENTS/AUDIO). El clip es del
        proyecto que lo enlaza, no también de la carpeta de la tarjeta."""
        v, a0 = "TARJETA/CONTENTS/VIDEO/0001AB.MXF", "TARJETA/CONTENTS/AUDIO/0001AB00.MXF"
        clips = [{"nombre": "0001AB", "ficheros": [a0, v]}]
        da.asignar_sueltos(clips, {v: "TARJETA", a0: "TARJETA"}, {r"E:\TARJETA\CONTENTS\VIDEO\0001AB.MXF": v}, "/disco")
        self.assertNotIn("proyectos", clips[0])

    def test_lista_rapida_con_carpetas(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "VACIA").mkdir()
            for f in ["MEXICO/MEXICO.avp", "VIAJE/a.mov", "Avid MediaFiles/MXF/1/a.mxf", "System Volume Information/x"]:
                (tmp / f).parent.mkdir(parents=True, exist_ok=True)
                (tmp / f).write_bytes(b"")
            lista = da.listar_proyectos(tmp)
            self.assertEqual([(p["nombre"], bool(p.get("sin_avid"))) for p in lista], [("MEXICO", False), ("VACIA", True), ("VIAJE", True)])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class MediaEnlazada(unittest.TestCase):
    """Media enlazada (AMA) o importada sin MXF de Avid: el fichero original se busca en el disco esté donde esté."""

    def inventario(self, rutas):
        return [{"ruta": r, "nombre": r.replace("\\", "/").rsplit("/", 1)[-1], "ext": "." + r.rsplit(".", 1)[-1].lower()} for r in rutas]

    def indice(self, rutas):
        from collections import defaultdict
        i = defaultdict(list)
        for f in self.inventario(rutas):
            i[f["nombre"].lower()].append(f)
        return i

    def test_localizar(self):
        i = self.indice([r"RODAJE\TARJETA A\XDROOT\Clip\C0001.MP4", r"RODAJE\TARJETA B\XDROOT\Clip\C0001.MP4", r"varios\dron.mov"])
        # otra letra de unidad, y dos C0001.MP4: manda la carpeta de la tarjeta
        self.assertEqual(da.localizar(r"E:\RODAJE\TARJETA B\XDROOT\Clip\C0001.MP4", i)["ruta"], r"RODAJE\TARJETA B\XDROOT\Clip\C0001.MP4")
        self.assertEqual(da.localizar("/Volumes/RODAJE/varios/DRON.MOV", i)["ruta"], r"varios\dron.mov")  # de un Mac y en mayúsculas
        self.assertIsNone(da.localizar(r"E:\otro\C0001.MP4", i))  # dos candidatos y ninguno encaja mejor: no se adivina
        self.assertIsNone(da.localizar(r"E:\no_esta.mov", i))

    def test_solo_los_que_no_tienen_media_de_avid(self):
        bins = [{"ok": True, "clips": [
            {"nombre": "con media", "fichero_origen": r"E:\x\a.mov", "mob_id": {"le": "AA" * 16}},
            {"nombre": "enlazado", "fichero_origen": r"E:\x\b.mov", "mob_id": {"le": "BB" * 16}},
            {"nombre": "falta", "fichero_origen": "/Volumes/MAC/c.mov", "mob_id": {"le": "CC" * 16}},
            {"nombre": "render", "fichero_origen": r"E:\x\d.mov", "render": True}]}]
        ficheros = self.inventario([r"x\a.mov", r"x\b.mov", r"x\d.mov"])
        analizar, enlaces, faltan = da.buscar_enlazados(bins, ficheros, [{"ruta": "m.mxf", "umid": "AA" * 16}])
        self.assertEqual([f["ruta"] for f in analizar], [r"x\b.mov"])
        self.assertEqual(enlaces, {r"E:\x\b.mov": r"x\b.mov"})
        self.assertEqual(faltan, ["/Volumes/MAC/c.mov"])
        self.assertTrue(da.es_ruta_de_mac("/Volumes/MAC/c.mov"))
        self.assertTrue(da.es_ruta_de_mac("Macintosh HD:Users:a:b.mov"))
        self.assertFalse(da.es_ruta_de_mac(r"E:\x\b.mov"))


try:
    import tkinter  # noqa: F401
    TK_OK = True
except ImportError:  # algunos Python de Linux vienen sin tkinter
    TK_OK = False


@unittest.skipUnless(TK_OK, "sin tkinter")
class TextosDeLaCola(unittest.TestCase):
    """Lo que dice cada fila de la cola de la página Exportar según el avance que llega de la exportación."""

    def test_estados(self):
        import interfaz
        self.assertEqual(interfaz.texto_estado({"estado": "convirtiendo", "clip": 18, "total": 40}), "Convirtiendo · clip 18 de 40 (45 %)")
        self.assertEqual(interfaz.texto_estado({"estado": "uniendo"}), "Uniendo los clips")
        self.assertEqual(interfaz.texto_estado({"estado": "terminado", "resultado": {"estado": "OK"}}), "Terminado")
        self.assertTrue(interfaz.texto_estado({"estado": "terminado", "resultado": {"estado": "SIN ESPACIO: hacen falta"}}).startswith("SIN ESPACIO"))
        self.assertEqual(interfaz.texto_estado({"estado": "reanudado", "resultado": {"estado": "OK"}}), "Ya exportado (se salta)")

    def test_comprobacion(self):
        import interfaz
        self.assertEqual(interfaz.texto_calidad({"estado": "OK", "conforme": True, "md5": "abc"}), "Conforme · MD5")
        self.assertEqual(interfaz.texto_calidad({"estado": "AVISO", "conforme": False, "md5": "abc"}), "NO CONFORME")
        self.assertEqual(interfaz.texto_calidad({"estado": "OK"}), "Hecho")

    def test_resumen_del_disco_al_elegirlo(self):
        import interfaz
        lista = [{"nombre": "ABORTO", "bins": 2, "modificado": "2021-09-15 10:00:00"},
                 {"nombre": "NTSC", "bins": 1, "modificado": "2021-09-16 18:30:00"},
                 {"nombre": "(bins sin proyecto)", "bins": 3, "modificado": "2021-09-01 08:00:00"}]
        self.assertEqual(interfaz.resumen_disco(lista), "2 proyectos · 3 bins · 3 bins sin proyecto · último cambio 16/09/2021")
        self.assertEqual(interfaz.resumen_disco(lista[:1]), "1 proyecto · 2 bins · último cambio 15/09/2021")
        self.assertIn("No hay proyectos de Avid", interfaz.resumen_disco([]))
        carpeta = {"nombre": "VIAJE", "bins": 0, "modificado": "2026-10-28 10:00:00", "sin_avid": True}
        self.assertEqual(interfaz.resumen_disco(lista[:1] + [carpeta]),
                         "1 proyecto · 1 carpeta sin Avid · 2 bins · último cambio 15/09/2021")
        self.assertEqual(interfaz.texto_calidad(None), "—")


if __name__ == "__main__":
    unittest.main()
