# DiagnosticoAvid

[![Construir y probar](https://github.com/malevolisco/diagnostico-avid/actions/workflows/construir.yml/badge.svg)](https://github.com/malevolisco/diagnostico-avid/actions/workflows/construir.yml)

Herramienta de archivo para **recuperar el material de cámara de los discos de edición Avid Media Composer** que llegan de las corresponsalías y entregarlo al Archivo como **brutos XDCAM HD422 50 Mb/s 1080i50 16:9**, documentados (shotlist, ficha técnica, MD5).

Es un programa de Windows portable: se descomprime y se ejecuta, sin instalar nada y sin conexión.

**Descarga de la última versión:** [DiagnosticoAvid_portable.zip](https://github.com/malevolisco/diagnostico-avid/releases/latest/download/DiagnosticoAvid_portable.zip)

---

## Índice

1. [Qué problema resuelve](#qué-problema-resuelve)
2. [Garantías](#garantías)
3. [Cómo se usa](#cómo-se-usa)
4. [Decisiones de diseño y por qué](#decisiones-de-diseño-y-por-qué)
5. [Arquitectura](#arquitectura)
6. [Qué genera](#qué-genera)
7. [Construir y probar](#construir-y-probar)
8. [Seguridad y privacidad](#seguridad-y-privacidad)
9. [Dependencias y licencias](#dependencias-y-licencias)
10. [Limitaciones conocidas](#limitaciones-conocidas)
11. [Documentación](#documentación)

---

## Qué problema resuelve

Un disco de edición de una corresponsalía mezcla, sin orden, cosas muy distintas:

- el material grabado por el equipo (tarjetas P2, XDCAM, cámaras con SD, dron);
- vídeos de agencia (Reuters, AFP, EFE…), descargas y vídeos de móvil;
- efectos y rótulos calculados por Avid;
- piezas ya montadas.

Abrir cada proyecto en Avid para separar lo que vale es lento y depende de quien lo haga. DiagnosticoAvid lee el disco sin modificarlo y hace el trabajo en cuatro pasos:

1. Reconstruye qué clip es cada fichero MXF y a qué proyecto pertenece.
2. Clasifica el origen de cada clip.
3. Propone qué proyectos merece la pena restaurar.
4. Exporta solo el material de cámara, en el formato que pide el Archivo.

## Garantías

| Garantía | Por qué |
|---|---|
| **Solo lectura del disco de origen.** El destino no puede estar dentro del disco. Si el disco se desconecta, el programa se detiene sin dejar nada a medias. | El disco suele ser la única copia del material. |
| **Nada sale del ordenador.** No se sube vídeo ni imágenes a ningún servicio. El zip del informe puede ir solo con texto. | Es material sensible: para diagnosticar bastan los JSON y el log. |
| **Formato de salida fijo, con comprobaciones opcionales.** Si se marcan en Exportar, cada bruto lleva una ficha técnica que lo verifica con ffprobe ("CONFORME") y un `.md5`. Vienen sin marcar desde el 30/10/2026. | El Archivo ingesta un formato concreto, y la ficha y el MD5 permiten comprobarlo sin abrir el fichero. Pero el MD5 vuelve a leer el bruto entero y el medidor de audio escucha cada clip: en el día a día alargaban demasiado la exportación. |
| **Trazabilidad.** El shotlist dice, de cada clip, de dónde viene, por qué entra y su timecode original. Lo que se deja fuera consta en Montaje ("Fuera del bruto") y en el informe, con su motivo. | Las decisiones automáticas deben poder revisarse y corregirse. |
| **Reproducible.** Cada versión se construye y se prueba en GitHub Actions. ffmpeg va fijado por versión y hash. | Lo que se prueba es exactamente lo que se distribuye. |

## Cómo se usa

La ventana tiene tres páginas. La guía para quien lo usa está en [`INSTRUCCIONES.txt`](INSTRUCCIONES.txt).

| Paso | Qué se hace | Resultado |
|---|---|---|
| **1 · Disco** | "Analizar todo el disco" al recibirlo (análisis inicial). Se pueden añadir otros orígenes con "＋ Añadir origen". | Informe del disco y **"Qué proyectos restaurar"**: cada proyecto con una captura de cada clip de cámara y una propuesta (Restaurar / Poco material / Revisar / Sin material de cámara). |
| **2 · Montaje** (opcional) | Revisar los brutos de cada proyecto: visor, línea de tiempo, quitar o mover clips, y decidir el origen de lo dudoso. | `montaje.json` y `origen.json` en la carpeta de brutos. |
| **3 · Exportar** | Cola de brutos con su avance. Se puede detener y reanudar. | Brutos MXF con su shotlist y hoja de contactos (y ficha técnica y MD5 si se marcan). |

Los análisis se guardan. Si otro día se analizan otros proyectos del mismo disco, el análisis nuevo **suma los anteriores sin repetirlos**, salvo que su media haya cambiado en el disco.

## Decisiones de diseño y por qué

### Qué entra en un bruto

| Decisión | Por qué |
|---|---|
| En el bruto **solo entra el material de cámara**, reconocido por alguna de estas pistas: nombre de clip P2, carpeta de tarjeta (`CONTENTS`, `DCIM`, `PRIVATE`, `XDROOT`…), metadatos de cámara o nombre de fichero de cámara (DJI/Osmo, Sony, Canon, GoPro, AVCHD…). Lo dudoso se queda fuera y se puede incluir a mano. | Requisito del Archivo: en los brutos, solo el material grabado por los equipos propios. En discos reales se colaban pantallas partidas y ficheros sin origen. |
| **Todo el disco, también lo que no es de Avid.** Cada carpeta de primer nivel sin proyecto de Avid es un proyecto con su nombre, y sus vídeos sueltos se analizan. Un vídeo que enlaza un bin sigue siendo de ese proyecto. Esos vídeos cuentan como material solo si los reconoce la cabecera (marca de cámara o de grabador externo como Atomos, Blackmagic o AJA), la carpeta de tarjeta o el nombre. | Un disco puede tener carpetas de rodaje que nunca se importaron en Avid; si el análisis solo mirara los proyectos, ese material no aparecería. Exigir una pista de cámara evita que entren descargas o recodificados. |
| **Agencias fuera**, por el indicativo en el nombre (Reuters/RTR, AFP, EFE, APTN, Eurovisión, Xinhua, CCTV…) o por el nombre del bin. El indicativo manda sobre los metadatos. | Es material de terceros, no del Archivo. Reuters graba con cámaras Sony, así que sus metadatos dirían "cámara". |
| **Efectos de Avid fuera** (fundidos, rótulos, imagen dentro de imagen). Las piezas montadas van a un bruto aparte, que no se exporta por defecto. | No son material original. |
| Lo elegido a mano en "Origen del material" **manda sobre todo** y se puede recordar para otros discos. | Ninguna regla automática acierta siempre; la persona del Archivo tiene la última palabra. |

### Cómo se reparte y en qué formato sale

| Decisión | Por qué |
|---|---|
| **Un bruto por proyecto y mes, de 2 h como máximo**, con nombre `PROYECTO MES DE AÑO`. El clip que no cabe abre el bruto siguiente. | Petición del Archivo: brutos manejables y con un nombre que se entiende sin abrirlos. |
| **Material de otra época en el mismo proyecto, en otro bruto**: si entre dos clips seguidos pasan más de 6 meses (`separar_si_pasan_dias`), el siguiente abre un bruto nuevo. En DV, la fecha de grabación es la que escribe la cámara en cada fotograma. | Un mismo proyecto de Avid puede juntar material de dos corresponsales grabado con años de diferencia (en un disco real, 2002 y 2019, todo volcado en 2019); sin esto salía un solo bruto "NOVIEMBRE DE 2002 A ABRIL DE 2019". |
| **Orden cronológico**: por la fecha de grabación si consta, si no por la del fichero; con la misma fecha, por timecode. | Así el bruto respeta el orden en que se grabó. |
| **XDCAM HD422 50 Mb/s 1080i50** (25 imágenes entrelazadas = 50 campos, campo superior primero) **16:9**. Si todo el bruto ya está en ese formato, se copia sin recomprimir. | Es el formato de ingesta del Archivo. Copiar evita pérdida de calidad y tiempo. |
| El **vídeo vertical** se encaja sin deformar (pillarbox) y el **NTSC** se pasa a 25. Se conservan los **canales de audio** por separado. | Cualquier cámara tiene que acabar en el mismo formato sin estropear la imagen. |
| Si un clip trae **más canales de audio que pistas** tiene el bruto (p. ej. 12 y el bruto 8), entran primero los que tienen sonido. | Si no, un clip con el sonido en los canales 9 a 12 quedaría mudo en el bruto. |
| **Envoltura SMPTE RDD 9** (el MXF de XDCAM HD422 de Sony): los clips del bruto los une **bmx** de la BBC en lugar de ffmpeg, en la misma pasada. Si bmx falla, une ffmpeg y la ficha lo marca. | Los sistemas de emisión y archivo esperan XDCAM HD422 "conforme a RDD 9". Unir con bmx no añade tiempo (reempaquetar después duplicaba la escritura, por eso se retiró la primera versión). Se comprueba en cada versión que el vídeo y el audio son idénticos bit a bit a los de la unión con ffmpeg. |

### Antes de exportar

| Decisión | Por qué |
|---|---|
| **"Qué proyectos restaurar"** antes del montaje. Propone "poco material" con hasta 2 clips y menos de 2 min (configurable), y "revisar" cuando un proyecto solo tiene vídeos (MP4, MOV…) sin pistas de cámara. | Hay que decidir qué proyectos restaurar sin abrirlos uno a uno, y sin descartar en silencio material que podría servir. |
| **Selector de proyectos en tabla.** Casilla, bins, último cambio, propuesta del último análisis y estado (con brutos, analizado, HECHO); buscador y orden por columnas. | Con decenas de proyectos en un disco, una lista de texto no se podía leer ni ordenar. |
| **Informe preliminar.** Antes de analizar, una lista de cada carpeta de los orígenes con lo que tiene: proyecto de Avid, MOV, MP4, tarjeta de cámara, nada o mezclado. Solo nombres y tamaños; no se abre ningún fichero. | Saber en segundos qué trae un disco y dónde está cada cosa antes de dedicarle una hora de análisis. |
| **Proyectos HECHOS**, marcados a mano en el selector de proyectos: en verde con la fecha y desmarcados por defecto; se pueden repetir. | Llevar la cuenta de lo terminado en un disco que se hace en varias veces, sin perder la posibilidad de rehacer uno. |
| **Ver y oír en el montaje.** El clip de verdad, con todo su sonido, desde el cursor, en una ventana de ffplay (botón ▶ o Espacio); fotograma exacto al parar; línea de tiempo desde el bruto entero hasta cada fotograma, con regla, tira de imágenes y forma de onda del sonido. | Para decidir qué entra en un bruto hay que oír qué se dice en una entrevista, y localizar un corte al fotograma. Se reproduce el fichero original con ffplay, que va con ffmpeg, en vez de construir un reproductor dentro del programa. |
| **Aviso "SIN AUDIO"** en los clips de cámara que no tienen ninguna pista de sonido. | Puede indicar que el sonido está en otro fichero o en otro disco. |
| **Trozos repetidos fuera del bruto.** Un clip que es un trozo de otro del mismo proyecto no entra en el bruto; queda en la papelera del montaje, de donde se puede recuperar. En DV se reconoce por la fecha, la hora y el timecode de cinta que graba la cámara en cada fotograma, y los mismos fotogramas bit a bit; en cualquier otro formato, por el timecode del fichero y el mismo sonido, muestra a muestra, al principio y al final del trozo. | Al hacer un subclip o consolidar en Avid se crea otro fichero, con otra identidad, con los mismos fotogramas y el mismo sonido: sin esto, ese tramo salía dos veces en el bruto. Basta leer unos fotogramas o unos segundos de sonido de cada candidato, así que cuesta poco. Exigir fotogramas o muestras idénticos, y no parecidos, evita confundirlo con dos cámaras grabando a la vez. |

### Rendimiento

| Decisión | Por qué |
|---|---|
| **Caché del análisis** de cada MXF (ruta, tamaño y fecha) y **análisis acumulado** por proyecto. | Un disco puede tener miles de ficheros: no se vuelve a leer lo que no ha cambiado. |

## Arquitectura

Python 3.12 con tkinter. Las lecturas pesadas se hacen con [pyavb](https://github.com/markreidvfx/pyavb) para los bins de Avid y con ffmpeg/ffprobe para la media.

| Módulo | Responsabilidad |
|---|---|
| `diagnostico_avid.py` | Programa principal y línea de comandos. Análisis en 6 pasos: inventario, proyectos y bins, ffprobe de los MXF, cruce clip ↔ bin, capturas e informe. También el análisis acumulado y "Qué proyectos restaurar". |
| `exportar_brutos.py` | Clasificación del origen, reparto en brutos, conversión y copia con ffmpeg, shotlist y contactos. |
| `mxf_util.py` | Lectura de cabeceras MXF sin ffprobe, timecode, ficha técnica XDCAM HD422 (con la envoltura RDD 9) y MD5. |
| `montaje.py` | Modelo del montaje (quitar, mover, cortar, renombrar, deshacer) y fotogramas del visor. No tiene ventana, así que se puede probar solo. |
| `lecturas.py` | Lo que el análisis lee del vídeo y del sonido para decidir (posiciones de timecode, huellas MD5 de fotogramas, dónde está un trozo de sonido), guardado en `lecturas.json`. Permite repetir las decisiones sin el disco. |
| `interfaz.py`, `ventana_montaje.py`, `ventana_origen.py`, `tema.py` | La ventana: páginas, montaje, origen del material y tema. Se adapta a pantallas pequeñas. |
| `aprendizaje.py` | Sugerencias de origen aprendidas de decisiones anteriores (Bayes ingenuo sobre palabras y formas de nombre; nunca imágenes). |
| `actualizar.py` | Actualización manual desde las releases de GitHub. Solo se usa al pulsar "Buscar actualización". |

El funcionamiento interno con detalle está en [`LEEME_tecnico.md`](LEEME_tecnico.md).

## Qué genera

**Carpeta del informe** (`Diagnostico_<disco>_<fecha>/`):

- `informe.html`: el informe del disco.
- `proyectos.html` y `proyectos.csv`: qué proyectos restaurar.
- `diagnostico.log`.
- Datos por capa: `inventario.csv`, `proyectos.json`, `bins.json`, `mxf.json`, `clips.json`, `cruce.json`, `resumen.json`, `triaje.json` y `lecturas.json` (posiciones y huellas leídas del vídeo y del sonido; ni imagen ni sonido).
- `miniaturas/`.
- Un `.zip` para enviar. Las imágenes son opcionales en el zip.

**Carpeta de brutos:**

- Por cada bruto: `<BRUTO>.mxf`, `_shotlist.xlsx`, `_contactos.html`, `_ficha.txt` y `.md5`.
- `brutos_resultado.json`: estado de cada bruto; permite reanudar.
- `montaje.json`, `origen.json` y `exportacion.log`.

## Construir y probar

**Ejecutar desde el código:**

```
pip install -r requirements.txt      # pyavb, openpyxl, pillow
python diagnostico_avid.py           # ventana
python diagnostico_avid.py --help    # línea de comandos (--origen, --destino, --brutos, --proyectos, --sin-gui…)
```

**Pruebas:**

```
python -m unittest discover -s tests                      # unas 200 pruebas unitarias y los casos reales
python tests/crear_disco_prueba.py disco_prueba --ffmpeg ffmpeg --ffprobe ffprobe
python diagnostico_avid.py --origen disco_prueba --destino salida --brutos brutos --sin-gui --no-abrir
python tests/comprobar_resultado.py salida brutos --ffprobe ffprobe
```

**Casos reales** ([`tests/casos_reales.py`](tests/casos_reales.py)). Cada disco real que da un problema se guarda como prueba, para que un arreglo no rompa lo que ya funcionaba en otro disco:

1. `python tests/anonimizar_caso.py Diagnostico_X_....zip nombre_del_caso` convierte el zip de diagnóstico en `tests/casos_reales/nombre_del_caso/caso.json.gz`. Solo lleva texto: clips, cruce, MXF y lecturas. Cada palabra de nombres, rutas y comentarios se cambia por un código; quedan los identificadores de cámara, las fechas, los timecodes y los formatos. Antes de guardarlo, comprueba que el caso anonimizado decide igual, clip a clip, que el original.
2. `python tests/casos_reales.py --ver nombre_del_caso` enseña qué entra en cada bruto, qué va a la papelera y qué se queda fuera. Cuando está bien, `--actualizar` lo guarda como `esperado.json`.
3. `test_casos_reales.py` repite cada caso en cada build (fechas de grabación, trozos repetidos con las lecturas guardadas, origen y reparto en brutos) y falla si alguna decisión cambia. También falla si queda en los casos algo que nombre a la empresa.

**Integración continua** ([`.github/workflows/construir.yml`](.github/workflows/construir.yml)). En un Windows de GitHub:

1. Instala ffmpeg 7.1.1, comprobando su SHA-256.
2. Construye el `.exe` con PyInstaller.
3. Genera un **disco Avid simulado** con proyectos, bins, P2, NTSC, agencias, efectos, móvil, media enlazada y rutas con chino.
4. Ejecuta el `.exe` de principio a fin: análisis, brutos, reanudación, análisis parcial y acumulado, caché, montaje a mano, la ventana entera con capturas y la actualización.
5. Comprueba el resultado. También que el análisis del disco simulado se repite entero sin el disco (no falta ninguna lectura en `lecturas.json`), que anonimizado decide igual y que los brutos exportados tienen los clips del plan repetido.

Cada cambio en `main` publica una release.

## Seguridad y privacidad

- **Red:** el programa no se conecta a nada, salvo al pulsar "Buscar actualización". Entonces consulta la API pública de releases de este repositorio y descarga el zip por HTTPS. Si los equipos se gestionan centralmente, basta con no usar ese botón y distribuir el zip.
- **Escritura:** solo en la carpeta del informe y en la de brutos, más tres ficheros junto al programa: `config_brutos.json`, `config_origen.json` y `config_ventana.json`.
- **Análisis estático** antes de cada commit (gitleaks, semgrep, grype, checkov). No hay hallazgos altos ni críticos. Quedan 7 medios conocidos:
  - 5 por usar etiquetas de versión de GitHub Actions en vez de un hash fijo;
  - 2 por `urllib` con una URL en variable en `actualizar.py`. La URL es fija (la de este repositorio).
- **El `.exe` no está firmado**, así que Windows SmartScreen puede avisar la primera vez. Algún antivirus puede marcar ejecutables de PyInstaller. Si se distribuye en una red corporativa, conviene firmarlo con el certificado de la organización.

## Dependencias y licencias

| Componente | Uso | Licencia |
|---|---|---|
| Python 3.12 + tkinter | Programa y ventana | PSF |
| [pyavb](https://github.com/markreidvfx/pyavb) | Leer bins `.avb` de Avid | MIT |
| [openpyxl](https://openpyxl.readthedocs.io) | Shotlist en Excel | MIT |
| [Pillow](https://python-pillow.org) | Visor del montaje | MIT-CMU |
| [ffmpeg](https://ffmpeg.org) 7.1.1 ("essentials" de gyan.dev) | ffprobe, conversión y copia | GPL v3 en esa compilación |
| [PyInstaller](https://pyinstaller.org) | Construir el `.exe` | GPL con excepción para lo que empaqueta |
| [bmx](https://github.com/bbc/bmx) 1.6 (BBC, binario oficial de Windows) | Unir los clips del bruto en RDD 9 (`bmxtranswrap`) | BSD 3 cláusulas (`licencias/bmx_COPYING.txt`, va en el portable) |

El repositorio todavía no declara una licencia propia.

## Limitaciones conocidas

Todo lo descrito se ha probado con el disco simulado y con datos reales parciales: los bins y fechas de un disco de la Corresponsalía de México. Queda por verificar con discos completos de verdad:

- la ingesta de los brutos en el sistema del Archivo: los brutos salen en RDD 9 por bmx, pero no se han pasado por un validador independiente ni por el sistema del Archivo;
- los tiempos con miles de ficheros en el PC donde se use;
- cómo nombra Avid los mixdowns;
- la pantalla con escalado del 125 % o 150 %.

La lista completa está en [`LEEME_tecnico.md` → Qué sigue sin verificar](LEEME_tecnico.md#qué-sigue-sin-verificar). Recomendación: antes de un disco entero, exportar un bruto corto y probarlo en el Archivo.

## Documentación

| Documento | Para quién |
|---|---|
| [`INSTRUCCIONES.txt`](INSTRUCCIONES.txt) | Quien usa el programa, paso a paso. Va dentro del portable. |
| [`LEEME_tecnico.md`](LEEME_tecnico.md) | Funcionamiento interno, configuración (`config_brutos.json`) y qué falta verificar. |
| [`docs/NOTAS_DESARROLLO.md`](docs/NOTAS_DESARROLLO.md) | Registro de decisiones: qué se pidió, cuándo y por qué. |
| [`docs/`](docs/README.md) | Diseños y planes de cada fase. |

---

Autor: Adrián Merinero Sánchez.
