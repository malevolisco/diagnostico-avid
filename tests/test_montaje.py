#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pruebas de la lógica de la pantalla de montaje (montaje.py), sin ventana ni ffmpeg.

Uso:  python -m unittest discover -s tests -v
"""

import datetime as dt
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import exportar_brutos as eb  # noqa: E402
import montaje  # noqa: E402


def clip(nombre, fecha, minutos):
    return {"clave": "K" + nombre, "nombre": nombre, "duracion": minutos * 60, "fichero_video": nombre + ".mxf",
            "ficheros": [nombre + ".mxf"], "fecha": fecha}


def fecha(c):
    return dt.datetime.strptime(c["fecha"], "%Y-%m-%d %H:%M") if c.get("fecha") else None


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        # P: A+B = 110 min (bruto 1), C+D = 50 min (bruto 2), Z sin fecha (bruto 3); pieza X aparte
        self.material = [clip("A", "2021-09-01 10:00", 60), clip("B", "2021-09-02 10:00", 50),
                         clip("C", "2021-09-03 10:00", 20), clip("D", "2021-10-04 10:00", 30), clip("Z", None, 5)]
        self.piezas = [clip("X", "2021-09-05 10:00", 2)]
        self.listas = {"P": {"material": self.material, "piezas": self.piezas},
                       "Q": {"material": [clip("Q1", "2021-09-01 11:00", 10)], "piezas": []}}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def m(self, guardado=None):
        return montaje.Montaje(self.listas, fecha, 7200, guardado)

    @staticmethod
    def vista(m, p="P"):
        return [(b["nombre"], [c["nombre"] for c in b["clips"]]) for b in m.brutos(p)]


class Automatico(Base):
    def test_reparto_y_nombres(self):
        self.assertEqual(self.vista(self.m()), [("P SEPTIEMBRE DE 2021", ["A", "B"]), ("P SEPTIEMBRE A OCTUBRE DE 2021", ["C", "D"]),
                                                ("P", ["Z"]), ("P PIEZAS SEPTIEMBRE DE 2021", ["X"])])
        self.assertEqual(self.m().proyectos(), ["P", "Q"])
        self.assertFalse(self.m().tocado("P"))

    def test_segundos_y_aviso_de_largo(self):
        b = self.m().brutos("P")
        self.assertEqual(b[0]["segundos"], 110 * 60)
        self.assertFalse(b[0]["largo"])


class Operaciones(Base):
    def test_quitar_y_recuperar(self):
        m = self.m()
        m.quitar("P", ["KB"])
        self.assertEqual(self.vista(m)[0][1], ["A"])
        self.assertEqual([c["nombre"] for c in m.papelera("P")], ["B"])
        self.assertTrue(m.tocado("P"))
        m.recuperar("P", ["KB"])
        self.assertEqual(self.vista(m)[0][1], ["A", "B"])  # vuelve a su sitio por orden
        self.assertEqual(m.papelera("P"), [])

    def test_bruto_vacio_desaparece(self):
        m = self.m()
        m.quitar("P", ["KA", "KB"])
        self.assertEqual([n for n, _ in self.vista(m)], ["P SEPTIEMBRE A OCTUBRE DE 2021", "P", "P PIEZAS SEPTIEMBRE DE 2021"])

    def test_mover_al_siguiente_y_al_anterior(self):
        m = self.m()
        m.mover("P", ["KB"], +1)
        self.assertEqual(self.vista(m)[:2], [("P SEPTIEMBRE DE 2021", ["A"]), ("P SEPTIEMBRE A OCTUBRE DE 2021", ["B", "C", "D"])])
        self.assertEqual(m.movidos("P"), {"KB"})
        m.mover("P", ["KC", "KD"], -1)
        self.assertEqual(self.vista(m)[0][1], ["A", "C", "D"])
        self.assertTrue(m.brutos("P")[0]["largo"] is False)
        m.mover("P", ["KA"], -1)  # no hay anterior: bruto nuevo delante
        self.assertEqual(self.vista(m)[0][1], ["A"])

    def test_mover_puede_pasar_de_dos_horas_y_se_marca(self):
        m = self.m()
        m.mover("P", ["KC"], -1)  # A+B+C = 130 min
        self.assertTrue(m.brutos("P")[0]["largo"])

    def test_mover_solo_dentro_de_un_bruto(self):
        with self.assertRaises(ValueError):
            self.m().mover("P", ["KA", "KC"], +1)

    def test_subir_y_bajar(self):
        m = self.m()
        m.bajar("P", ["KA"])
        self.assertEqual(self.vista(m)[0][1], ["B", "A"])
        m.subir("P", ["KA"])
        self.assertEqual(self.vista(m)[0][1], ["A", "B"])
        self.assertEqual(m.movidos("P"), {"KA"})
        m.subir("P", ["KA"])  # ya es el primero: no cambia
        self.assertEqual(self.vista(m)[0][1], ["A", "B"])

    def test_cortar_y_rehacer_los_siguientes(self):
        m = self.m()
        m.cortar("P", "KB")  # el bruto 1 acaba en A; B, C, D se reparten de nuevo hasta 2 h
        self.assertEqual(self.vista(m)[:2], [("P SEPTIEMBRE DE 2021", ["A"]), ("P SEPTIEMBRE A OCTUBRE DE 2021", ["B", "C", "D"])])
        self.assertEqual(self.vista(m)[2], ("P", ["Z"]))  # lo que no tiene fecha y las piezas no se tocan

    def test_renombrar(self):
        m = self.m()
        m.renombrar("P", 1, "ENTREVISTA PRESIDENTE")
        self.assertEqual(self.vista(m)[1][0], "ENTREVISTA PRESIDENTE")
        self.assertTrue(m.brutos("P")[1]["manual"])
        m.cortar("P", "KB")  # el bruto con nombre a mano no se reparte de nuevo: B va solo
        self.assertEqual(self.vista(m)[:3], [("P SEPTIEMBRE DE 2021 1", ["A"]), ("P SEPTIEMBRE DE 2021 2", ["B"]),
                                             ("ENTREVISTA PRESIDENTE", ["C", "D"])])
        m.deshacer()
        m.renombrar("P", 1, "")
        self.assertEqual(self.vista(m)[1][0], "P SEPTIEMBRE A OCTUBRE DE 2021")

    def test_nombres_repetidos_se_numeran(self):
        m = self.m()
        m.cortar("P", "KB")
        m.mover("P", ["KD"], +1)  # D (octubre) pasa al bruto de Z
        nombres = [n for n, _ in self.vista(m)]
        self.assertEqual(nombres[:3], ["P SEPTIEMBRE DE 2021 1", "P SEPTIEMBRE DE 2021 2", "P OCTUBRE DE 2021"])

    def test_deshacer_y_volver_al_automatico(self):
        m = self.m()
        antes = self.vista(m)
        m.quitar("P", ["KA"])
        m.bajar("P", ["KC"])
        m.deshacer()
        m.deshacer()
        self.assertEqual(self.vista(m), antes)
        m.quitar("P", ["KA"])
        m.volver_automatico("P")
        self.assertEqual(self.vista(m), antes)
        self.assertFalse(m.tocado("P"))
        self.assertEqual(m.papelera("P"), [])


class Guardar(Base):
    def test_guardar_y_cargar(self):
        m = self.m()
        m.cortar("P", "KB")
        m.quitar("P", ["KC"])
        m.renombrar("P", 0, "PRIMERO")
        m.guardar(self.tmp)
        datos = json.loads((self.tmp / montaje.FICHERO).read_text(encoding="utf-8"))
        self.assertEqual(list(datos["proyectos"]), ["P"])  # Q no se tocó: reparto automático
        m2 = montaje.Montaje.cargar(self.tmp, self.listas, fecha, 7200)
        self.assertEqual(self.vista(m2), self.vista(m))
        self.assertEqual([c["nombre"] for c in m2.papelera("P")], ["C"])
        self.assertEqual(m2.avisos, [])

    def test_convierte_cortes_json(self):
        eb.guardar_cortes(self.tmp, {"P": ["KB"]})
        m = montaje.Montaje.cargar(self.tmp, self.listas, fecha, 7200)
        self.assertEqual(self.vista(m)[:2], [("P SEPTIEMBRE DE 2021", ["A"]), ("P SEPTIEMBRE A OCTUBRE DE 2021", ["B", "C", "D"])])
        self.assertTrue(m.tocado("P"))
        self.assertTrue(any("cortes.json" in a for a in m.avisos))

    def test_reanalisis_clips_que_desaparecen_y_nuevos(self):
        m = self.m()
        m.bajar("P", ["KA"])
        m.guardar(self.tmp)
        self.material.remove(self.material[2])  # C ya no está en el disco
        self.material.insert(1, clip("N", "2021-09-01 12:00", 5))  # N es nuevo
        m2 = montaje.Montaje.cargar(self.tmp, self.listas, fecha, 7200)
        todos = [n for _, cs in self.vista(m2) for n in cs]
        self.assertNotIn("C", todos)
        self.assertNotIn("N", todos)  # no se cuela sin avisar
        self.assertEqual([c["nombre"] for c in m2.nuevos("P")], ["N"])
        self.assertEqual(len(m2.avisos), 2)
        m2.colocar("P", ["KN"])  # por fecha: tras A (orden automático), en su bruto
        self.assertEqual(self.vista(m2)[0][1], ["B", "A", "N"])
        self.assertEqual(m2.nuevos("P"), [])

    def test_montaje_de_un_proyecto_que_ya_no_existe(self):
        (self.tmp / montaje.FICHERO).write_text(json.dumps({"proyectos": {"VIEJO": {"brutos": [{"nombre": None, "tipo": "material", "clips": ["K1"]}]}}}),
                                                encoding="utf-8")
        m = montaje.Montaje.cargar(self.tmp, self.listas, fecha, 7200)
        self.assertEqual(m.proyectos(), ["P", "Q"])
        self.assertEqual(len(m.avisos), 1)

    def test_fichero_ilegible(self):
        (self.tmp / montaje.FICHERO).write_text("{roto", encoding="utf-8")
        m = montaje.Montaje.cargar(self.tmp, self.listas, fecha, 7200)
        self.assertFalse(m.tocado("P"))
        self.assertEqual(len(m.avisos), 1)


class FueraDelBruto(Base):
    def test_lista_de_descartados(self):
        m = montaje.Montaje(self.listas, fecha, 7200, descartados={"P": [(clip("R", "2021-09-01 09:00", 1), "agencia")]})
        self.assertEqual([(c["nombre"], mo) for c, mo in m.fuera_del_bruto("P")], [("R", "agencia")])
        self.assertEqual(m.fuera_del_bruto("Q"), [])


class Grupos(Base):
    def test_grupos_para_exportar(self):
        m = self.m()
        m.bajar("P", ["KA"])
        m.quitar("P", ["KC"])
        g = m.grupos()
        self.assertEqual([x["proyecto"] for x in g], ["P"] * 4)  # solo los proyectos tocados
        self.assertEqual([c["nombre"] for c in g[0]["clips"]], ["B", "A"])
        self.assertEqual(g[0]["movidos"], {"KA"})
        self.assertEqual([c["nombre"] for c in g[0]["quitados"]], ["C"])
        self.assertTrue(g[0]["ordenado"])
        self.assertEqual(g[0]["base"], "P SEPTIEMBRE DE 2021")
        self.assertEqual(g[3]["tipo"], "piezas")


@unittest.skipUnless(shutil.which("ffmpeg"), "sin ffmpeg")
class Visor(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_un_fotograma_por_segundo_y_no_se_repite(self):
        import subprocess
        video = self.tmp / "clip.mxf"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=720x576:r=25:d=3", "-c:v", "mpeg2video",
                        "-f", "mxf", str(video)], check=True)
        ruta = montaje.sacar_fotogramas("ffmpeg", video, self.tmp / "visor", "K:1/a")
        self.assertEqual(ruta.parent, self.tmp / "visor")
        fs = montaje.fotogramas(ruta)
        self.assertEqual(len(fs), 3)
        self.assertTrue(all(f.startswith(b"\xff\xd8") and f.endswith(b"\xff\xd9") for f in fs))
        antes = ruta.stat().st_mtime_ns
        self.assertEqual(montaje.sacar_fotogramas("no-existe-ffmpeg", video, self.tmp / "visor", "K:1/a"), ruta)  # ya hecho
        self.assertEqual(ruta.stat().st_mtime_ns, antes)

    def test_fallo_de_ffmpeg(self):
        self.assertIsNone(montaje.sacar_fotogramas("ffmpeg", self.tmp / "no_existe.mxf", self.tmp / "visor", "K"))
        self.assertEqual(list((self.tmp / "visor").glob("*")), [])


if __name__ == "__main__":
    unittest.main()
