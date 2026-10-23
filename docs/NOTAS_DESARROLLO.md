# Notas de desarrollo — DiagnosticoAvid

Herramienta de Adrián Merinero Sánchez para el Archivo de RTVE: analiza un disco de edición Avid Media Composer completo y exporta los brutos MXF de cada proyecto. La usa una persona sin conocimientos técnicos (la jefa de Adrián, del Archivo de Informativos) en Windows, sin Adrián delante. Adrián (documentalista) es el autor: decide qué hace la herramienta y cómo. Cada cambio se sube a `main` y GitHub construye y prueba el `.exe`.

Estas notas recogen lo que no está en el código: cómo probar, lo aprendido de los discos reales y las decisiones tomadas. Leerlas antes de cambiar nada.

## Normas del proyecto

- Nada sin comprobar: ejecutar `tests/` antes de dar algo por hecho. Si algo no se ha podido verificar, decirlo explícitamente (hay una lista en `LEEME_tecnico.md`).
- Si se comete un error, se reconoce y se corrige.
- No deducir el origen del material (agencia, cámara) por el nombre de un MXF. Solo por lo que diga el bin (`config_origen.json`) y marcando lo demás "Sin determinar".
- Material de RTVE: nunca subir vídeo ni miniaturas a ningún servicio. Para diagnosticar bastan JSON y log.
- Autoría: los commits van a nombre de Adrián (`git config user.name "Adrián Merinero Sánchez"`, `user.email malevolisco@gmail.com`), sin firmas automáticas ni líneas de coautoría en commits, PR ni comentarios. Si aparece una firma automática en la descripción de una PR, quitarla.

## Estructura

| Fichero | Qué es |
|---|---|
| `diagnostico_avid.py` | Programa principal: inventario, lectura de bins (pyavb), ffprobe de MXF, cruce bin↔MXF, miniaturas, informe HTML y CLI. Fase 1. |
| `interfaz.py` | La ventana: tres páginas (Disco, Montaje, Exportar) con barra abajo, cola de exportación con casillas y avance por eventos. Recibe el módulo principal (`lanzar(da)`). |
| `tema.py` | Tema oscuro (colores, letras, estilos ttk) y componentes: `Tarjeta`, `Seleccion` (4 \| 8), `BarraPaginas`. |
| `exportar_brutos.py` | Fase 2: brutos MXF OP1a. Por defecto un bruto por proyecto y mes (`PROYECTO_2019-09`), hasta 2 h, en orden cronológico, sin importados, piezas aparte, todo en XDCAM HD 50 1080i25 16:9; opcionalmente por días o por proyecto y tarjeta (`agrupar`) y en el formato mayoritario (`formato_salida = auto`); shotlist xlsx, hoja de contactos. Lo importa el principal. |
| `montaje.py` | Lógica de la pantalla de montaje (quitar, mover, cortar, renombrar, deshacer; `montaje.json` en la carpeta de brutos) y fotogramas del visor (`.mjpeg` por clip en `<análisis>/visor/`). Sin tkinter. |
| `ventana_montaje.py` | Página de montaje como marco dentro de la ventana (visor con Pillow, línea de tiempo, lista de clips, "Fuera del bruto") y diálogo de opciones avanzadas de exportación. |
| `mxf_util.py` | Acabado del bruto: TC de las cabeceras GOP igual al del fichero, fecha de creación, ficha técnica XDCAM HD422 y MD5. |
| `actualizar.py` | Actualización del `.exe` solo al pulsar "Buscar actualización": consulta la última release en GitHub, descarga, comprueba y un `.bat` cambia la carpeta conservando `config_*.json`. |
| `config_brutos.json`, `config_origen.json` | Se crean junto al programa con valores por defecto la primera vez. No están en git. |
| `tests/crear_disco_prueba.py` | Genera un disco Avid simulado con casos límite (bins con pyavb, MXF OP-Atom con ffmpeg, V y A compartiendo UMID como hace Avid). |
| `tests/probar_montaje.py` | Prueba del montaje sin ventana: escribe un `montaje.json` (cortar, reordenar, renombrar, quitar) y comprueba los brutos exportados con `--usar-analisis`. |
| `tests/test_montaje.py` | Lógica del montaje y fotogramas del visor. |
| `tests/probar_interfaz.py` | La ventana de principio a fin sobre el disco simulado (analizar, montar, exportar), con capturas. En Linux con xvfb; en GitHub, en Windows. |
| `tests/test_mxf.py` | TC GOP (comparado con el que escribe ffmpeg), audio intacto, ficha técnica, fecha de creación y MD5. Necesita ffmpeg en el PATH. |
| `tests/comprobar_resultado.py` | Comprueba un resultado contra valores fijos (también ficha conforme, MD5, TC interno y fecha de creación); sale con 1 si algo no cuadra. `--reanudado` para la segunda ejecución. |
| `tests/test_unitarios.py` | Timecode drop-frame, reintento copia→convertir con ffmpeg simulado, reanudar, botón Detener, reparto por tarjeta/agencias/piezas, autoactualización con release falsa local. Solo stdlib. |
| `.github/workflows/construir.yml` | En cada push a `main` (o a mano en otra rama): pruebas unitarias, ffmpeg 7.1.1 fijado, PyInstaller con `build.txt`, disco de prueba, ejecuta el `.exe` dos veces (la segunda debe reanudar) y una tercera con `agrupar = proyecto` y `formato_salida = auto`, comprueba, prueba la actualización con una release falsa local y, solo en `main`, publica release `build-N` con `DiagnosticoAvid_portable.zip`. El repositorio es público (la actualización lo necesita sin contraseña). |
| `construir_exe.bat`, `ejecutar.bat` | Plan B local sin GitHub. |
| `INSTRUCCIONES.txt` | Para quien ejecuta el programa. Se copia dentro del portable. |
| `LEEME_tecnico.md` | Cómo funciona por dentro, qué está verificado con datos reales y qué no. |
| `docs/NOTAS_DESARROLLO.md` | Estas notas. |
| `docs/plan_fase3.md` | Plan aprobado de la fase 3. Implementados los pasos 1-4 y 6; el paso 5 (regla APTN) espera un nombre de fichero real. |
| `docs/diseno_montaje_exportacion.md`, `docs/plan_montaje.md` | Diseño aprobado (oct. 2026) y plan de la pantalla de montaje, el panel de opciones y el acabado profesional. |

Descarga del portable: `https://github.com/malevolisco/diagnostico-avid/releases/latest/download/DiagnosticoAvid_portable.zip`

## Cómo probar en local (Linux o Windows con ffmpeg en el PATH)

```
pip install pyavb openpyxl pillow
python tests/crear_disco_prueba.py disco_prueba
python diagnostico_avid.py --origen disco_prueba --destino salida --brutos brutos --sin-gui --no-abrir
python tests/comprobar_resultado.py salida brutos
python -m unittest discover -s tests
```
Debe terminar con "Análisis OK", "Brutos OK" y las pruebas unitarias en OK. Para probar reanudar: repetir la ejecución con otro `--destino` y la misma carpeta de brutos, y `comprobar_resultado.py ... --reanudado`. Si se añaden casos al disco de prueba, actualizar los valores fijos del comprobador en el mismo commit.

## Conocimiento del dominio que no está en el código

- Un disco Avid separa proyectos (carpetas con `.avp` y bins `.avb`) de la media (`Avid MediaFiles/MXF/<n>/`, OP-Atom: una pista por fichero; V01, A01, A02… de un clip comparten `material_package_umid`). La relación proyecto→media solo existe en los bins.
- El cruce bin↔MXF se hace por el **número de material** del UMID (16 últimos bytes), probando dos codificaciones del UUID (`le`, `be`); vía secundaria por los source mobs contra `file_package_umid`. `resumen.json → modo_coincidencia` dice cuál ha funcionado. **Verificado con el disco real de México**: funciona `be` (18.215) y `fuente_be` (2.508); `le` no aparece.
- Los bins guardan para cada clip el disco donde Avid vio la media por última vez (`last_known_volume`) y la ruta del fichero importado. Es la base de la columna "Último disco conocido" y de la clasificación de origen.
- Master clips con `usage == "precompute"` son renders de efectos (títulos, fundidos, picture-in-picture): no son material; no entran en los brutos (`renders_en_piezas`). Composiciones con `usage` distinto de None son contenedores de efectos, no secuencias.
- Tarjetas: en el disco real los clips P2 **no** guardan en el bin la ruta de la tarjeta, y su nombre (`0016Q3`, `00016R`, `0002CI`…) **no** indica ni tarjeta ni cámara (clips seguidos tienen nombres sin relación). Lo único fiable es la fecha del MXF (cuándo Avid escribió la media): cada volcado de tarjeta es una tanda. Solo los ficheros importados guardan ruta (localizador `WinFileLocator`/`FileLocator` del source mob); si pasa por `CONTENTS`, `PRIVATE`, `DCIM`… es una tarjeta, si no es un importado (`_IMPORTADOS`). pyavb puede escribir esa cadena (master → SourceClip → source mob con descriptor y localizador): así lo hace el disco simulado.
- Renders de efectos sin bin: Avid los llama `<clip>,<efecto>,<n>` ("PIEZA X,Dissolve,1"). En México: 806 sin bin, 1.100 de 1.200 con bin, ningún clip de material.
- Rutas antiguas sin UTF-8 en los bins: pyavb las decodifica como Mac Roman; son Windows-1252 (`diagnostico_avid.reparar_ruta`).
- Los bins se copian entre proyectos: un clip puede estar en varios. `config_brutos.json → clips_compartidos` decide (por defecto solo al primero, orden alfabético).
- El disco real de la Corresponsalía de México es **NTSC 29,97**: 4 TB, 108 proyectos, 18.847 clips, la mayoría DV 720×480 (13.756), además H.264, DNxHD, MPEG-2; cámaras Panasonic P2 (clips `0016Q3.new.01`), MP4 importados y algo de Reuters. El Archivo pide los brutos en XDCAM HD 50 1080i25 16:9 (`formato_salida`): el DV 4:3 va con bandas negras y el NTSC se pasa a 25. Cifras y hallazgos en `LEEME_tecnico.md`.
- Timecode: los proyectos NTSC usan drop-frame. ffmpeg solo acepta `-timecode hh:mm:ss;ff` (el `;` solo antes de los frames) y aborta si se pide DF con fps de salida 25. Implementado en `exportar_brutos.fmt_tc` / `tc_a_frames` (decisión por los fps de **salida**, `es_df`). Ojo: el minuto 0 de cada decena es completo, así que a 29,97 el primer frame del minuto 1 es el 1800, no el 1798.
- Nombres de fichero: Windows no admite `:` (el disco simulado lo tenía en un render "Title: 1" y en Windows ese clip no se creaba). El nombre del clip va en `material_package_name`, no en el nombre de fichero.
- ffmpeg: `mxf_opatom` sirve para generar fixtures; el muxer `mxf` (OP1a) admite `-c copy` de DNxHD, MPEG-2, DV, ProRes, H.264. Con el demuxer `concat` hay que usar `-map 0` o pierde pistas de audio, y la lista debe llevar rutas absolutas.
- pyavb necesita Python ≤ 3.13 con wheel universal: instalar siempre con `--only-binary :all:` (el sdist exige Cython 0.29, que no compila en 3.13).

## Decisiones ya tomadas (no reabrir sin motivo)

- Las piezas montadas (secuencias) no se reconstruyen: requiere Avid. Se listan en el informe.
- Audio siempre PCM 24 bits 48 kHz, una pista mono por canal (un estéreo da dos pistas); mismo número de pistas en todo el bruto, rellenando con silencio; cada clip con el audio ajustado a la duración de su vídeo.
- Que valga para cualquier disco (Adrián, 23/10/2026): se decide por lo que el material es, no por cómo se llama; ante la duda, el clip entra marcado "para revisar". Diseño en `docs/diseno_cualquier_disco.md`, plan por partes en `docs/plan_cualquier_disco.md`.
- Brutos: lo pidió por correo la jefa de Adrián (Archivo de Informativos, 21/10/2026), tras ver los brutos por días: (1) fuera los ficheros importados, "son clips de agencias, internet"; (2) nombre = proyecto + año y mes de los ficheros; (3) máximo 2 h, "si se pasa, que no añada el último" (pasa al bruto siguiente, no se descarta); (4) orden cronológico por la fecha del fichero (en el modo por días salía desordenado); (5) **XDCAM HD 50 1080i25 16:9**. Es `agrupar = proyecto_mes` y `formato_salida = xdcam_hd50` (113 brutos en México). Los renders de efectos no entran; las piezas renderizadas van aparte. Siguen disponibles `agrupar = dias` (todo el disco por días, la decisión anterior de Adrián), `agrupar = proyecto` (tarjetas) y `formato_salida = auto`.
- Un fichero con ruta de tarjeta (`CONTENTS`, `PRIVATE`…) es material de cámara aunque la columna Origen diga "Fichero importado" (el disco simulado tiene ese caso en el proyecto NTSC).
- Formato real del disco de México: 94 % DV 720×480 29,97 (151 de 160 h); nada a 25 ni en XDCAM HD. Con XDCAM HD 50 hay que convertirlo todo; unir antes de convertir no ahorra trabajo. Se convierten varios clips a la vez (`clips_a_la_vez`) y se unen en orden.
- Orden de los clips: estricto por fecha y hora del fichero (al segundo), luego timecode original, luego nombre (`orden_cronologico`).
- Hora que manda para ordenar (Adrián, oct. 2026): la de grabación si el bin o el MXF la guardan con fecha y hora (columnas de usuario tipo "Shoot Date"/"Shoot Time"); si no, la del volcado (fecha del MXF). No verificado con bins reales: `cruce.json → fecha_grabacion` dice qué hay.
- Montaje (Adrián, oct. 2026, antes de pasarlo a Ingeniería; diseño en `docs/diseno_montaje_exportacion.md`): tres pasos (analizar; "Montaje..." sobre ese análisis; exportar sin repetirlo con el panel de opciones). En el montaje se ven los brutos con un visor de fotogramas (1/s, 480×270, sin sonido) y una línea de tiempo, y se puede quitar clips (papelera), moverlos dentro del bruto o a otro bruto del mismo proyecto (puede pasar de 2 h: sale en rojo, no se impide), cortar, renombrar y deshacer. Lo que se ve es lo que se exporta (`montaje.json`). Sustituye a "Revisar cortes" (`cortes.json` se convierte).
- Nombres de los brutos (Adrián, oct. 2026): `PROYECTO MES DE AÑO` en mayúsculas, con el mes en letra y sin guiones; sin fecha, solo el proyecto. Editables por bruto.
- Opciones de exportación (Adrián, oct. 2026): TC de inicio 00:00:00:00; pistas: las que más haya, redondeadas a 4 u 8 en XDCAM; audio **sin tocar** por defecto, con ganancia por pista o EBU R128 como opciones; MD5, ficha técnica y medidor de audio activados.
- Ventana (Adrián, 22/10/2026: "más vistosa y más user friendly, más parecido a DaVinci"; diseño en `docs/diseno_interfaz.md`): una sola ventana oscura con tres páginas (Disco, Montaje, Exportar), acento naranja `#e8833a`, tkinter sin componentes nuevos. Se muestran los valores ya elegidos (4 \| 8 pistas marcado según el material) en vez de explicar reglas; lo raro va en "Opciones avanzadas…".
- Piezas (Adrián, 22/10/2026): bruto aparte, pero sin marcar en la cola y fuera por defecto en línea de comandos (`exportar_piezas = false`). Efectos de Avid, agencias e importados nunca entran en un bruto; el montaje los lista en "Fuera del bruto".
- Discos de China (Adrián, 22/10/2026: "el programa tiene que ser resiliente también, van a traer discos duros de China"; "son material propio claro"): el material de cámara propia importado desde una carpeta del PC (DJI_, C0380…) es material; un bin de agencias manda sobre lo que no es de cámara; 1080i50 nativo sin separar campos; disco desconectado = Detener con aviso; lista de ficheros que no se pueden leer; rutas largas y chino. Detalle en `LEEME_tecnico.md` ("Discos de China").
- Selector de proyectos (ventana y `--proyectos`): el análisis es siempre del disco entero; los brutos, solo de los elegidos; `brutos_resultado.json` conserva los de otras veces para hacer un disco en varias sesiones.
- La actualización ya no es automática (Adrián, oct. 2026, para que Ingeniería revise una versión fija): solo con el botón "Buscar actualización" del `.exe`.
- El programa nunca escribe en el disco de origen y se niega si el destino está dentro de él.
- Clips en `Quarantined Files` se incluyen en los brutos pero marcados "incluido (cuarentena)" en el shotlist.
