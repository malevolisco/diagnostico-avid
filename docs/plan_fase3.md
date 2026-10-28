# Plan final (v4, APROBADO en la 4ª revisión): DiagnosticoAvid, de prototipo probado en simulado a herramienta fiable sobre el disco real

> Sugerencias no bloqueantes de la 4ª revisión, aceptadas para la implementación: constante `TIEMPO_AGOTADO`; reanudar acepta también estado `AVISO` si existen .mxf y shotlist con tamaño coincidente; `shutil.rmtree(tmp, ignore_errors=True)` antes del fallback; la prueba del fallback comprueba que todas las llamadas a `ejecutar_ffmpeg` llevan timeout numérico.
>
> Cambios v3→v4 (tras tercera revisión): el clip con ñ es un renombrado (no altera los números fijados); la unión con tiempo agotado no dispara el fallback; `hacer_segmento` devuelve `(ruta, error)`; los clips que fallaron por timeout no se reintentan en el fallback; `duracion or 0`; fórmula DF generalizada a 59,94; sustituto de `duracion_ffprobe` coherente para el fichero unido; el comprobador lee xlsx o csv.
>
> Cambios v2→v3 (tras segunda revisión): sintaxis DF que acepta ffmpeg (`hh:mm:ss;ff`), decisión DF por fps de salida, valores de prueba corregidos y expresados en frames, comprobación del TC del bruto con ffprobe; fallback con discriminador clip dañado vs códec y nunca por timeout; prueba del fallback con sustitutos completos; números esperados del comprobador fijados; generador parametrizado en fps/códec; reanudar exige también el shotlist; ambigüedad sobre `segundos`.
>
> Cambios v1→v2 (tras primera revisión): reanudación basada en un JSON escrito tras cada proyecto y con comprobación de nº de clips; fallback copia→convertir a nivel de proyecto (cubre fallo en segmentos, no solo en la unión); drop-frame también en la pista de timecode del bruto y prueba unitaria con valores conocidos; `try/finally` para log y flag de energía; timeout en ffmpeg; reglas de ambigüedad y contadores por proyecto en el cruce aproximado; `build.txt` vía `_MEIPASS`; ffmpeg fijado en el workflow; campo `reintento`; nota en instrucciones; prueba de reanudación sobre la carpeta correcta.

## Contexto (estado actual)

Herramienta Python (Windows, usuario no técnico) que analiza un disco de edición Avid Media Composer completo y exporta un bruto MXF por proyecto. Código: `diagnostico_avid.py` (inventario, bins con pyavb, ffprobe de MXF, cruce bin↔MXF, miniaturas, informe HTML, ventana tkinter, CLI), `exportar_brutos.py` (plan por proyecto copia/convertir, segmentos MXF OP1a homogéneos, concat, shotlist xlsx, hoja de contactos), `tests/crear_disco_prueba.py` + `tests/comprobar_resultado.py`, `.github/workflows/construir.yml` (build Windows + prueba del .exe + release), `construir_exe.bat` (plan B local).

Verificado: pipeline completo en Linux sobre disco simulado; pyavb sobre bins reales (MC 23.12.6, 1.100 clips); cadena clip→source mob→volumen/fichero importado; clasificación de origen.
No verificado: cruce con MXF reales de Avid (codificación del UMID), códec real del disco, tiempos con miles de ficheros, ejecución en Windows (el `.bat` falla en el PC del usuario; el repo de GitHub no existe todavía).

## Objetivo

Que la primera ejecución sobre el disco real (persona sin conocimientos técnicos, sin nadie técnico delante) produzca un informe útil aunque algo falle, y que la exportación de brutos no pierda horas de trabajo por un fallo puntual. Que el autor pueda corregir y redistribuir sin tocar el PC de destino.

## Enfoque

Cuatro frentes, por prioridad: (1) distribución y prueba en Windows vía GitHub Actions; (2) resiliencia de la exportación; (3) robustez del cruce; (4) protocolo de primera ejecución real.

Fuera de alcance: reconstruir secuencias/piezas montadas (requiere Avid); firma de código del .exe; interfaz más allá de la ventana actual; media OMF.

## Pasos de implementación

### Paso 1. Repo y CI (bloqueado por el usuario)
- Se crea el repositorio `malevolisco/diagnostico-avid` y se sube el código.
- En `construir.yml`, fijar ffmpeg a una release concreta de BtbN (URL con tag de fecha, no `latest`), anotando la versión en el LEEME. Reproducibilidad de cada release.
- El workflow escribe `build.txt` (`build-<run_number> <sha corto> <fecha>`) y lo incluye con `--add-data "build.txt;."`. El programa lo lee desde `sys._MEIPASS` si está congelado (igual que `localizar_ffmpeg`), si no desde `carpeta_base()`, y si no existe usa `"local"`. Se muestra en el título de la ventana y en el pie del informe.
- Verificar en Actions: build, prueba del .exe en Windows, release con `DiagnosticoAvid_portable.zip`.
- Se descarga la release y se prueba en un PC con una carpeta que tenga su `.avp` + bins reales. Criterio: la ventana abre, el informe muestra 3 bins y 1.130 clips offline, el pie muestra el número de build.

### Paso 2. Resiliencia de la exportación (`exportar_brutos.py`, `diagnostico_avid.py`)
- **Reestructurar**: el cuerpo del bucle de `exportar_todo` pasa a `exportar_proyecto(proyecto, clips, ..., modo_forzado=None) -> res`. `exportar_todo` solo agrupa, itera, reanuda y persiste.
- **Persistencia incremental**: `brutos_resultado.json` se escribe **después de cada proyecto** (lista acumulada), no al final.
- **Reanudar**: al arrancar, si existe `brutos_resultado.json` en el destino, se indexa por proyecto. Un proyecto se salta si: `estado` empieza por `OK`, el `.mxf` existe y su tamaño coincide con `bytes`, el shotlist registrado existe, y `clips` (nº de clips del grupo actual) coincide con el registrado. Si no coinciden (p. ej. cambió `config_brutos.json` o el análisis), se rehace. Los saltados se añaden a `resultados` con `reanudado: true` y el log dice "reanudado". Nota en INSTRUCCIONES: relanzar repite el análisis (ffprobe + miniaturas); lo que se ahorra es la exportación.
- **Fallback copia→convertir** con discriminador, en `exportar_proyecto`, solo cuando `plan["modo"] == "copia"`:
  - Si falla la **unión** por motivo distinto de "tiempo agotado": borrar `tmp`, registrar motivo y repetir una sola vez con `modo_forzado="convertir"`. Unión con tiempo agotado → `ERROR al unir (tiempo agotado)`, sin reintento.
  - Si falla un **segmento** por motivo distinto de "tiempo agotado": antes de decidir, repetir **solo ese clip** en modo convertir (segmento de prueba, se descarta). Si también falla → es el fichero: el clip queda `ERROR` y el proyecto sigue en copia. Si va bien → es el códec: borrar `tmp` y repetir todo el proyecto con `modo_forzado="convertir"`.
  - "Tiempo agotado" nunca dispara el fallback, ni en segmentos ni en la unión: el clip queda `ERROR (tiempo agotado)` y se sigue. En la pasada de fallback, los clips que ya fallaron por timeout en copia no se reintentan (quedan `ERROR (tiempo agotado)` directamente).
  - Para esto `hacer_segmento` pasa a devolver `(ruta | None, error)` en vez de `None` (hoy solo registra el error en el log). El segmento de prueba en convertir se construye con `dict(plan, modo="convertir")`: `plan_proyecto` ya calcula `destino` también en modo copia, así que `args_video` no necesita recalcular nada.
  - El resultado lleva `modo: "convertir"` y `reintento: true` (campo aparte; `comprobar_resultado.py` sigue comparando `modo` por igualdad). En modo convertir no hay reintento.
- **Timeout en ffmpeg**: `ejecutar_ffmpeg(cmd, timeout)` recibe siempre un valor: segmentos `max(600, 30 × (duración_clip or 0))`; unión `max(1800, 10 × duración_total)`; miniaturas ya tienen 120 s. Un MXF dañado deja de colgar la exportación; el clip queda "ERROR (tiempo agotado)" y se sigue.
- **Evitar suspensión del PC**: en `ejecutar` (`diagnostico_avid.py`), en Windows, `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)` al inicio; en un `try/finally` que también hace `LOG.removeHandler(fh)` y `fh.close()`, para que una excepción no deje el flag puesto ni el log duplicado en la siguiente ejecución desde la ventana.
- **Drop-frame** (NTSC), con una sola función de decisión `es_df(fps)` = fps de **salida** ≈ 29,97 o 59,94 (`plan["fps"]`; en convertir puede ser 25 aunque el origen sea 29,97, y ffmpeg aborta si se pide DF con 25):
  - `fmt_tc(frames, fps)` trabaja en **frames** (no en segundos) y aplica el ajuste estándar de ffmpeg (`av_timecode_adjust_ntsc_framenum2`): con `D = frames // 17982`, `M = frames % 17982`: `frames_ajustados = frames + 18·D + 2·max(0, (M − 2) // 1798)`; generalizado con `k = fps_nominal / 30` (k = 1 para 29,97, k = 2 para 59,94): bloque de 10 minutos `17982·k`, salto `2·k` por minuto, minuto `1798·k`. Después se descompone a `30·k` fps nominales. Formato `hh:mm:ss;ff` (el `;` solo delante de los frames: es la única sintaxis que acepta ffmpeg; verificado con ffmpeg 6.1). NDF `hh:mm:ss:ff` para el resto.
  - Los TC de entrada/salida del shotlist se calculan acumulando **frames** por segmento (`round(dur_seg × fps)`), no segundos, para no arrastrar redondeos.
  - `unir_segmentos` pasa `-timecode hh:mm:ss;ff` cuando `es_df(plan["fps"])` (convirtiendo `timecode_inicio` del config), y `hh:mm:ss:ff` si no. Documentado en `config_brutos.json`.
- `brutos_resultado.json` guarda, para cada error, el comando ffmpeg truncado (300 caracteres) y las últimas líneas de stderr.

### Paso 3. Robustez del cruce (`diagnostico_avid.py`)
- **Cruce aproximado** solo por proyecto y solo cuando en ese proyecto el cruce exacto (material del UMID o source mobs) ha dado 0 coincidencias habiendo bins legibles y MXF en el disco. Candidatos: clip de bin ↔ clip físico con `nombre normalizado` igual (`material_package_name` del MXF; minúsculas, espacios colapsados) y `|segundos_bin − duracion_fisica| ≤ 0,5 s`, donde `segundos_bin` = `length / edit_rate` del mob; un clip de bin sin `segundos` no participa. Se empareja solo si el clip de bin tiene exactamente un candidato físico **y** ese físico tiene exactamente un candidato de bin (singleton mutuo). Cualquier otro caso cuenta como `ambiguo` y queda offline/huérfano.
- Contadores por proyecto: `clips_aproximados`, `clips_ambiguos`. Global: `modo_coincidencia["nombre_duracion"]`. Los clips así emparejados llevan `coincidencia: "aproximada"` y el informe los marca; aviso ámbar "cruce por nombre y duración en N proyectos; revisar".
- El aviso rojo "ningún clip ha coincidido" sigue mirando solo los modos exactos (`le`, `be`, `fuente_*`), para que el aproximado no lo oculte.

### Paso 4. Zip "para enviar" sin contenido
- Casilla en la ventana (marcada por defecto): "Incluir miniaturas en el zip para enviar". Desmarcada, el zip lleva JSON, CSV, log e informe sin `miniaturas/` (el informe local sí las tiene). CLI: `--zip-sin-miniaturas`.

### Paso 5. Origen de material: APTN y otros
- Añadir regla APTN en `PATRONES_ORIGEN_DEFECTO` cuando haya un nombre de fichero real. Hasta entonces, nada.

### Paso 6. Protocolo de primera ejecución real (documentación)
- `INSTRUCCIONES.txt`: primero solo análisis; enviar el zip; segunda ejecución con brutos solo cuando el responsable lo confirme; relanzar repite el análisis entero; cerrar Avid antes.
- `LEEME_tecnico.md`: lista de comprobación del primer zip: `modo_coincidencia` con modos exactos no vacíos; `formatos` y `productos`; `bins_ilegibles`; proporción offline y sus volúmenes; `clips_aproximados`/`ambiguos`; tiempos en `diagnostico.log`.

## Casos límite que sí cubrimos
- Corte o cierre durante la exportación → reanudar con JSON incremental.
- Códec no copiable por el muxer (falla al hacer el segmento o al unir) → fallback a convertir, una vez.
- ffmpeg colgado por un MXF dañado → timeout, clip marcado, se sigue.
- Identificadores que no coinciden → aproximado por proyecto, con regla de ambigüedad; nombres repetidos (fechas) quedan como ambiguos, no emparejados a ciegas.
- Rutas con acentos/ñ → ya cubierto; se añade un clip con ñ al disco simulado.
- Excepción a mitad de ejecución desde la ventana → `finally` limpia log y flag de energía.

## Casos límite que no cubrimos (y por qué)
- Rutas de más de 260 caracteres en Windows: raro en discos Avid; el error queda en el log y se sigue.
- Discos dañados a nivel de sectores: fuera del alcance; el log muestra los ficheros ilegibles.
- Bins bloqueados por un Avid abierto: instrucción de cerrar Avid.

## Estrategia de pruebas
- `tests/crear_disco_prueba.py`: `mkv()`/`clip()` parametrizados en `fps` y `codec` (hoy fijos a 25 y dnxhd). Nuevos proyectos: (a) `NTSC` con 2 clips MPEG-2 4:2:2 720×480 a 30000/1001 (2 s cada uno) para probar copia de MPEG-2 y DF; el `length` de los mobs = `round(dur × fps)` en vez de 75 fijo; (b) el clip `u3` de `15 SEPT` se **renombra** a `2017 07 21 ESPAÑA` (renombrado, no clip nuevo: los números del bruto no cambian) para fijar la prueba de rutas con ñ; (c) `APROX`: bin con 3 clips cuyos MobIDs no coinciden con ningún MXF, con 3 clips físicos de nombres y duraciones iguales a los del bin, dos de ellos con el mismo nombre y duración → 1 aproximado, 2 ambiguos (los 2 físicos ambiguos quedan sin proyecto).
- Nota: en el fixture los mobs no tienen pistas, así que `edit_rate_de` devuelve `None` y `leer_bin` asume 25 fps; el `segundos` del bin para `NTSC` (2,4 s) no es real y no se usa en ninguna comprobación (NTSC cruza por UMID). Para `APROX`, que sí cruza por `segundos`, el `length` se calcula a 25 fps para que coincida con la duración física.
- Valores esperados nuevos en `comprobar_resultado.py` (se fijan aquí para no ajustarlos sobre la marcha): `proyectos` 4, `bins` 6, `bins_ilegibles` 1, `offline` 3 (1 anterior + 2 ambiguos), `renders_en_bins` 1, `clips_compartidos` 1; `por_proyecto["APROX"]`: `clips_aproximados` 1, `clips_ambiguos` 2; `modo_coincidencia` conserva `le` y añade `nombre_duracion: 1`. Brutos: `15 SEPT` copia 3 clips 7,0 s; `ABORTO` convertir 2 clips 4,0 s; `NTSC` copia 2 clips 4,0 s (±0,1 s por 29,97); `APROX` copia 1 clip 2,0 s; `SIN_PROYECTO` copia 4 clips 7,0 s (huérfano 2 s + cuarentena 1 s + 2 ambiguos de 2 s).
- `tests/test_unitarios.py` (nuevo, stdlib `unittest`, se ejecuta en el workflow antes del build):
  - DF en frames: `fmt_tc(1798, 29.97) == "00:00:59;28"`, `fmt_tc(1799, 29.97) == "00:00:59;29"`, `fmt_tc(1800, 29.97) == "00:01:00;02"`, `fmt_tc(17982, 29.97) == "00:10:00;00"`, `fmt_tc(107892, 29.97) == "01:00:00;00"`; NDF: `fmt_tc(90000, 25) == "01:00:00:00"`; `es_df(25) is False`, `es_df(30000/1001) is True`.
  - Fallback por códec: `exportar_proyecto` con `exportar_brutos.ejecutar_ffmpeg` sustituido a nivel de módulo por una función que falla cuando el comando contiene `-c:v copy` y, si no, crea el fichero de salida (unos bytes); `exportar_brutos.duracion_ffprobe` sustituido por una función que devuelve la duración del clip para un segmento y la suma de los clips para el fichero del bruto (si no, con 2+ clips el estado sería "AVISO: duración" en vez de OK). Resultado esperado: `estado` empieza por `OK`, `modo == "convertir"`, `reintento == True`.
  - Fallback no disparado por fichero dañado: sustituto que falla para un clip concreto en ambos modos → ese clip `ERROR`, `modo == "copia"`, `reintento == False`.
  - Sin fallback por timeout: sustituto que devuelve "tiempo agotado" para un clip → `modo == "copia"`, clip `ERROR (tiempo agotado)`.
- `tests/comprobar_resultado.py`: además de los valores fijados arriba, en el bruto `NTSC`: `ffprobe -show_entries format_tags=timecode` devuelve un valor con `;` y el shotlist (xlsx si hay openpyxl; si no, el csv de respaldo: el comprobador lee cualquiera de los dos) tiene TC con `;`; en los brutos a 25 fps el TC lleva `:`; tras una segunda ejecución de la exportación sobre la misma carpeta de brutos, `brutos_resultado.json` marca todos los proyectos `OK` con `reanudado: true` (se lee el JSON de la carpeta de brutos, no un log elegido por patrón de nombre).
- Todo corre en el workflow de Windows antes de cada release.

## Dependencias
- Ninguna nueva (`ctypes` y `unittest` son estándar).

## Orden y tamaño
1. Paso 1 (espera externa; 30 min cuando exista el repo; el cambio de `build.txt` y ffmpeg fijado se hace ya).
2. Paso 2 (un día con pruebas; es el bloque más grande).
3. Paso 3 + 4 (3 horas).
4. Paso 6 (1 hora). Paso 5 cuando haya datos.
