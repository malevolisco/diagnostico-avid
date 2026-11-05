#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pruebas de hoja_inventario.py: las filas que el programa manda a la hoja de inventario y la conexión con su buzón."""

import http.server
import json
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import hoja_inventario as hi  # noqa: E402


class Filas(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        for c in ("DISCO 1/ABORTO", "DISCO 1/CHAVELA VARGAS", "DISCO 2/NIÑOS", "SUELTA"):
            (self.d / c).mkdir(parents=True)
        self.lista = [{"nombre": "ABORTO", "carpeta": "DISCO 1/ABORTO", "bins": 3, "modificado": "2026-10-14 10:00"},
                      {"nombre": "CHAVELA VARGAS", "carpeta": "DISCO 1/CHAVELA VARGAS", "bins": 3, "modificado": "2026-10-05"},
                      {"nombre": "NIÑOS", "carpeta": "DISCO 2/NIÑOS", "bins": 1, "modificado": ""},
                      {"nombre": "SUELTA", "carpeta": "SUELTA", "bins": 0, "sin_avid": True}]
        self.triaje = [{"proyecto": "ABORTO", "veredicto": "restaurar", "clips": 63, "segundos": 2253, "dudosos": 22, "otros": {"agencia": 10}},
                       {"proyecto": "CHAVELA VARGAS", "veredicto": "restaurar", "clips": 120, "segundos": 3396, "otros": {}}]
        self.res = [{"proyecto_avid": "ABORTO", "proyecto": "ABORTO SEPTIEMBRE DE 2020", "clips": 39, "incluidos": 4, "fallidos": 35,
                     "segundos": 90, "estado": "OK"},
                    {"proyecto_avid": "CHAVELA VARGAS", "proyecto": "NOVIEMBRE DE 2002", "clips": 78, "incluidos": 78, "fallidos": 0,
                     "segundos": 3000, "estado": "OK"}]

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_disco_desde_la_raiz(self):
        self.assertEqual(hi.disco_de("DISCO 1/ABORTO", [str(self.d)])[-len("DISCO 1"):], "DISCO 1")
        # elegir la carpeta DISCO 1 en vez del disco entero da lo mismo
        self.assertEqual(hi.disco_de("ABORTO", [str(self.d / "DISCO 1")]), hi.disco_de("DISCO 1/ABORTO", [str(self.d)]))
        # de otro origen (＋ Añadir origen): el primero en el que existe
        self.assertEqual(hi.disco_de("NIÑOS", ["/no/existe", str(self.d / "DISCO 2")])[-len("DISCO 2"):], "DISCO 2")

    def test_filas(self):
        filas = {f["proyecto"]: f for f in hi.filas_inventario(self.lista, [str(self.d)], self.triaje, self.res, {}, ahora="2026-11-05 10:00")}
        self.assertNotIn("SUELTA", filas)  # carpeta sin proyecto de Avid y sin nada: fuera
        a, c, n = filas["ABORTO"], filas["CHAVELA VARGAS"], filas["NIÑOS"]
        self.assertEqual(a["estado"], "Exportar de nuevo")
        self.assertIn("INCOMPLETO (4 de 39 clips)", a["brutos"])
        self.assertEqual(a["propuesta"], "Restaurar (10 de agencia, 22 dudosos)")
        self.assertEqual((a["clips"], a["duracion"], a["ultimo_cambio"], a["bins"]), (63, "0:37:33", "2026-10-14", 3))
        self.assertEqual(c["estado"], "Exportado")
        self.assertEqual(c["brutos"], "NOVIEMBRE DE 2002 · 78 clips · 0:50:00")
        self.assertEqual(n["estado"], "Sin analizar")
        self.assertNotIn("brutos", n)
        self.assertNotIn("clips", n)
        self.assertNotIn("forzar_estado", a)
        for f in filas.values():  # nunca imágenes ni rutas de ficheros: solo texto y cifras
            self.assertEqual(set(f) - {"disco", "proyecto", "bins", "ultimo_cambio", "estado", "actualizado", "clips", "duracion",
                                       "propuesta", "brutos", "forzar_estado"}, set())

    def test_sin_carpeta_de_brutos_no_se_manda_nada_de_brutos(self):
        filas = {f["proyecto"]: f for f in hi.filas_inventario(self.lista, [str(self.d)], self.triaje, None)}
        self.assertEqual(filas["CHAVELA VARGAS"]["estado"], "Analizado")
        self.assertNotIn("brutos", filas["CHAVELA VARGAS"])

    def test_hecho_en_el_programa(self):
        filas = {f["proyecto"]: f for f in hi.filas_inventario(self.lista, [str(self.d)], self.triaje, self.res, {"ABORTO": "x"}, ["NIÑOS"])}
        self.assertEqual((filas["ABORTO"]["estado"], filas["ABORTO"]["forzar_estado"]), ("HECHO", True))
        self.assertEqual((filas["NIÑOS"]["estado"], filas["NIÑOS"]["forzar_estado"]), ("Sin analizar", True))  # HECHO quitado

    def test_decisiones(self):
        self.assertTrue(hi.descartado({"decision": "Descartar: entradilla"}))
        self.assertTrue(hi.descartado({"estado": "Descartado"}))
        self.assertFalse(hi.descartado({"decision": "Merece la pena"}))
        self.assertFalse(hi.descartado(None))
        self.assertEqual(hi.texto_decision({"decision": "Descartar: solo agencias", "quien": "A"}), "descartado en la hoja: solo agencias (A)")
        self.assertEqual(hi.texto_decision({"decision": "Merece la pena"}), "★ merece la pena")
        self.assertEqual(hi.texto_decision({"estado": "HECHO"}), "HECHO en la hoja")
        self.assertEqual(hi.texto_decision({"estado": "Analizado"}), "")

    def test_gran_proyecto_por_disco(self):
        cfg = hi.poner_gran_proyecto({}, "DISCO_A", "México")
        cfg = hi.poner_gran_proyecto(cfg, "DISCO_B", "China")
        self.assertEqual(hi.gran_proyecto(cfg, "DISCO_A"), "México")
        self.assertEqual(hi.gran_proyecto(cfg, "DISCO_NUEVO"), "China")  # uno nuevo: el último que se usó

    def test_direccion(self):
        with self.assertRaises(hi.ErrorHoja):
            hi.comprobar_url("https://docs.google.com/spreadsheets/d/xxx/edit")
        self.assertTrue(hi.comprobar_url(" https://script.google.com/macros/s/abc/exec "))


class Buzon(unittest.TestCase):
    """Un buzón falso en local que contesta como el de Google (con su redirección a la respuesta)."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.recibido, self.respuesta, self.caido = [], {"ok": True, "nuevas": 1, "pestanas": ["México"]}, False
        prueba = self

        class Manejador(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                if prueba.caido:
                    self.send_response(500)
                    self.end_headers()
                    return
                prueba.recibido.append(json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8")))
                self.send_response(302)
                self.send_header("Location", "/respuesta")
                self.end_headers()

            def do_GET(self):
                cuerpo = json.dumps(prueba.respuesta).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(cuerpo)
        self.servidor = http.server.HTTPServer(("127.0.0.1", 0), Manejador)
        threading.Thread(target=self.servidor.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d/exec" % self.servidor.server_port
        self.comprobar, hi.comprobar_url = hi.comprobar_url, lambda u: u

    def tearDown(self):
        hi.comprobar_url = self.comprobar
        self.servidor.shutdown()
        self.servidor.server_close()
        shutil.rmtree(self.base, ignore_errors=True)

    def test_actualizar_y_leer(self):
        r = hi.actualizar(self.url, self.base, "México", [{"disco": "DISCO 1", "proyecto": "ABORTO", "estado": "Analizado"}])
        self.assertEqual(r["nuevas"], 1)
        self.assertEqual(self.recibido[-1]["accion"], "actualizar")
        self.assertEqual(self.recibido[-1]["gran_proyecto"], "México")
        self.assertEqual(self.recibido[-1]["filas"][0]["proyecto"], "ABORTO")
        self.respuesta = {"ok": True, "filas": [{"disco": "DISCO 1", "proyecto": "ABORTO", "estado": "HECHO", "decision": ""}]}
        self.assertEqual(hi.leer(self.url, "México")["ABORTO"]["estado"], "HECHO")
        self.assertEqual(self.recibido[-1], {"accion": "leer", "gran_proyecto": "México"})

    def test_sin_conexion_se_guarda_y_se_manda_la_proxima_vez(self):
        self.caido = True
        with self.assertRaises(hi.ErrorHoja):
            hi.actualizar(self.url, self.base, "México", [{"disco": "D", "proyecto": "A", "estado": "Analizado"}])
        with self.assertRaises(hi.ErrorHoja):
            hi.actualizar(self.url, self.base, "China", [{"disco": "D", "proyecto": "B", "estado": "Analizado"}])
        self.assertTrue((self.base / hi.FICHERO_PENDIENTE).exists())
        self.caido = False
        hi.actualizar(self.url, self.base, "México", [{"disco": "D", "proyecto": "A", "estado": "Exportado"}])
        por_gran = {x["gran_proyecto"]: x["filas"] for x in self.recibido}
        self.assertEqual(por_gran["México"], [{"disco": "D", "proyecto": "A", "estado": "Exportado"}])  # la última vale
        self.assertEqual(por_gran["China"][0]["proyecto"], "B")
        self.assertFalse((self.base / hi.FICHERO_PENDIENTE).exists())

    def test_error_del_buzon(self):
        self.respuesta = {"ok": False, "error": "Falta el gran proyecto"}
        with self.assertRaisesRegex(hi.ErrorHoja, "Falta el gran proyecto"):
            hi.probar(self.url)

    def test_latido_y_ordenes(self):
        self.respuesta = {"ok": True, "ordenes": [{"n": 1, "orden": "Exportar", "proyectos": "ABORTO"}]}
        self.assertEqual(hi.latido(self.url, {"pc": "PC1", "actividad": "libre"})[0]["n"], 1)
        self.assertEqual(self.recibido[-1]["accion"], "latido")
        hi.marcar_orden(self.url, 1, "En marcha", "x" * 900, "PC1", desde="Pendiente")
        self.assertEqual((self.recibido[-1]["n"], self.recibido[-1]["desde"], len(self.recibido[-1]["detalle"])), (1, "Pendiente", 500))

    def test_subir_datos(self):
        import base64
        hi.subir_datos(self.url, "México", "DISCO_F", "a.zip", b"PK\x03\x04")
        self.assertEqual(base64.b64decode(self.recibido[-1]["zip_base64"]), b"PK\x03\x04")
        with self.assertRaises(hi.ErrorHoja):
            hi.subir_datos(self.url, "México", "D", "a.zip", b"0" * (hi.MAX_ZIP + 1))

    def test_datos_sin_red_se_suben_al_volver(self):
        self.caido = True
        with self.assertRaisesRegex(hi.ErrorHoja, "cuando vuelva la conexión"):
            hi.subir_o_guardar(self.url, self.base, "México", "DISCO_F", "a.zip", b"PK1")
        with self.assertRaises(hi.ErrorHoja):  # el mismo zip otra vez: sustituye al pendiente
            hi.subir_o_guardar(self.url, self.base, "México", "DISCO_F", "a.zip", b"PK2")
        self.assertEqual(len(list((self.base / hi.CARPETA_PENDIENTES).glob("*.zip"))), 1)
        self.caido = False
        self.assertEqual(hi.subir_pendientes(self.url, self.base), 1)
        import base64
        self.assertEqual(base64.b64decode(self.recibido[-1]["zip_base64"]), b"PK2")
        self.assertEqual((self.recibido[-1]["gran_proyecto"], self.recibido[-1]["disco"], self.recibido[-1]["nombre"]), ("México", "DISCO_F", "a.zip"))
        self.assertEqual(list((self.base / hi.CARPETA_PENDIENTES).iterdir()), [])
        self.assertEqual(hi.subir_pendientes(self.url, self.base), 0)

    def test_direccion_que_no_es_el_buzon(self):
        self.respuesta = "<html>"  # contesta otra cosa (una página de Google, por ejemplo)
        with self.assertRaisesRegex(hi.ErrorHoja, "no es la del buzón"):
            hi.probar(self.url)

class Nube(unittest.TestCase):
    """El programa en la nube: órdenes y el zip de datos (solo texto)."""

    def test_proyectos_de_una_orden(self):
        self.assertEqual(hi.proyectos_de_orden("ABORTO; chavela vargas,PANAMA 2019\nX"), ["ABORTO", "chavela vargas", "PANAMA 2019", "X"])
        self.assertEqual(hi.proyectos_de_orden(""), [])
        self.assertEqual(hi.casar_proyectos(["chavela vargas", "NO"], ["CHAVELA VARGAS", "ABORTO"]), (["CHAVELA VARGAS"], ["NO"]))

    def test_zip_solo_de_texto(self):
        import io
        import zipfile
        d = Path(tempfile.mkdtemp())
        try:
            a, b = d / "Diagnostico_F", d / "brutos"
            for c in (a / "miniaturas", a / "visor", b):
                c.mkdir(parents=True)
            for f in ("resumen.json", "inventario.csv", "diagnostico.log", "informe.html", "proyectos.html"):
                (a / f).write_text("x", encoding="utf-8")
            (a / "miniaturas" / "c.jpg").write_bytes(b"\xff\xd8")
            (a / "visor" / "v.json").write_text("{}", encoding="utf-8")
            for f in ("brutos_resultado.json", "exportacion.log", "A_shotlist.xlsx", "A_ficha.txt", "A_contactos.html"):
                (b / f).write_text("x", encoding="utf-8")
            (b / "A.mxf").write_bytes(b"\x06\x0e")
            datos, nombres = hi.zip_texto([("analisis", a), ("brutos", b), ("nada", d / "no_existe")])
            self.assertEqual(sorted(nombres), sorted(zipfile.ZipFile(io.BytesIO(datos)).namelist()))
            self.assertEqual(sorted(nombres), ["analisis/diagnostico.log", "analisis/inventario.csv", "analisis/resumen.json",
                                               "brutos/A_ficha.txt", "brutos/A_shotlist.xlsx", "brutos/brutos_resultado.json",
                                               "brutos/exportacion.log"])
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
