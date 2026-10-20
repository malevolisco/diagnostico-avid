# Diagnóstico y exportación de discos Avid — notas técnicas

## Qué hay en el paquete

| Fichero | Para qué |
|---|---|
| `construir_exe.bat` | **Lo que ejecutas tú, una vez.** Descarga ffmpeg, construye el `.exe` y deja `DiagnosticoAvid_portable.zip` listo para enviar |
| `diagnostico_avid.py` | Programa principal: ventana, línea de comandos, análisis (fase 1) e informe |
| `exportar_brutos.py` | Fase 2: un MXF por tarjeta de cada proyecto (y aparte agencias y piezas) + shotlist Excel + hoja de contactos. Lo importa el principal |
| `actualizar.py` | Autoactualización del `.exe` al abrir la ventana. Lo importa el principal |
| `config_brutos.json` | Parámetros de exportación. Se crea solo la primera vez que se exporta, junto al programa |
| `INSTRUCCIONES.txt` | Hoja de uso para quien reciba el portable (se copia dentro automáticamente) |
| `ejecutar.bat` | Alternativa sin `.exe`: lanza el programa con Python instalado |
| `requirements.txt` | Dependencias Python (`pyavb`, `openpyxl`) |
| `bin/` | Aquí deja `construir_exe.bat` los `ffmpeg.exe` y `ffprobe.exe` |

## Construir el portable (en tu PC con Windows)

1. Descomprime `diagnostico_avid.zip`.
2. Doble clic en `construir_exe.bat`. Pasos que anuncia: comprueba Python, instala `pyinstaller`, `pyavb` y `openpyxl` (versiones precompiladas), descarga ffmpeg de GitHub (~100 MB) a la carpeta temporal y copia solo `ffmpeg.exe` y `ffprobe.exe` a `bin\`, construye el ejecutable y comprime `dist\DiagnosticoAvid` en `DiagnosticoAvid_portable.zip`.
3. Pruébalo antes de enviarlo: entra en `dist\DiagnosticoAvid`, abre `DiagnosticoAvid.exe`, elige cualquier carpeta con un `.avp` (vale una carpeta con un fichero vacío llamado `prueba.avp`) y analiza. Debe abrirse el informe.
4. Envía `DiagnosticoAvid_portable.zip` (~150 MB). Quien lo recibe descomprime y hace doble clic; no instala nada.

**Versión de ffmpeg fijada**: 7.1.1 "essentials" de gyan.dev (`https://github.com/GyanD/codexffmpeg/releases/tag/7.1.1`), la misma en GitHub Actions y en `construir_exe.bat`. El workflow comprueba además la huella SHA-256 del zip, así que cada release se construye con exactamente el mismo ffmpeg. Para cambiar de versión hay que actualizar la URL en los dos sitios y la huella en el workflow.

**Número de build**: GitHub Actions escribe `build.txt` (`build-<n> <commit> <fecha>`) y lo mete en el `.exe`. Se ve en el título de la ventana, en la cabecera y el pie del informe, en `resumen.json → build` y en la primera línea de `diagnostico.log`. Así se sabe qué versión ha usado quien envía un informe. Ejecutado con Python, sin `.exe`, pone "local".

Si el antivirus corporativo bloquea el `.exe` (pasa con PyInstaller), la alternativa es `ejecutar.bat` + Python instalado en el PC de destino.

## Modo línea de comandos (para ti)

```
python diagnostico_avid.py --origen F:\ --destino C:\Users\TU_USUARIO\Desktop --sin-gui
python diagnostico_avid.py --origen F:\ --destino C:\Users\TU_USUARIO\Desktop --brutos E:\BRUTOS --sin-gui
```
Opciones: `--sin-miniaturas` (análisis mucho más rápido), `--max-miniaturas 200`, `--no-abrir`, `--zip-sin-miniaturas` (el zip para enviar solo lleva texto; el informe local sí tiene las miniaturas).

## Fase 1: qué hace el análisis

1. **Inventario**: `os.walk` del disco entero → `inventario.csv`.
2. **Proyectos**: cada carpeta con un `.avp` es un proyecto; sus `.avb` (y los de subcarpetas, marcando `Trash`) son sus bins.
3. **Bins**: `pyavb` lee cada bin y separa master clips (`MasterMob`) de secuencias (`CompositionMob`), con nombre, duración, fechas y MobID.
4. **MXF**: `ffprobe -show_format -show_streams` de cada `.mxf` (4 en paralelo): códec, resolución, fps, entrelazado, duración, timecode, `material_package_umid` (identificador del clip), `material_package_name` y comentarios de usuario (`comment_*`).
5. **Clips**: los MXF que comparten `material_package_umid` son pistas (V/A) del mismo clip.
6. **Cruce**: el MobID de cada clip de bin contra el UMID de los clips físicos, probando dos codificaciones del UUID (`le` y `be`), y como vía secundaria los source mobs contra el `file_package_umid`; `resumen.json → modo_coincidencia` dice cuál ha funcionado.
   **Cruce aproximado** (`nombre_duracion`): solo en un proyecto donde el exacto ha dado 0 coincidencias. Un clip de bin se empareja con un clip físico que no esté ya en ningún proyecto si tienen el mismo nombre (minúsculas, espacios colapsados) y la duración difiere ≤ 0,5 s, y solo si el emparejamiento es único en los dos sentidos. Lo demás cuenta como ambiguo y queda offline/huérfano (no se empareja a ciegas: dos clips con la misma fecha como nombre son habituales). `cruce.json → por_proyecto → clips_aproximados / clips_ambiguos`; aviso ámbar en el informe y etiqueta "cruce aproximado" en el clip. El aviso rojo "ningún clip ha coincidido" solo mira los modos exactos.
7. **Miniaturas**: un fotograma al 10 % de cada clip con vídeo, 320 px.
8. **Informe**: `informe.html` + JSON por capa + ZIP.

## Fase 2: cómo se construye cada bruto

1. **Reparto** (`grupos_de_exportacion`): los clips de cada proyecto (los cruzados en la fase 1; los huérfanos, como proyecto `SIN_PROYECTO`) se reparten en:
   - **`_PIEZAS`**: los renders de efectos del proyecto (`usage = precompute`: títulos, fundidos) y las **piezas renderizadas**: clips que se llaman como una secuencia del proyecto, por su nombre o por el del fichero importado, quitando extensión, `.new.01` y "mixdown" (pieza exportada y reimportada, o mixdown). `piezas_regex` en el config añade otra regla por nombre.
   - **`_AGENCIAS`**: clips cuyo origen según el bin (`config_origen.json`) empieza por "Agencia" (`origenes_agencia`).
   - **`_T01_<fecha>`, `_T02_…`**: el resto, **un bruto por tarjeta** (`partir_por_tarjeta`). Se agrupan por (a) la carpeta de la tarjeta según la ruta del fichero importado que guarda el bin (la carpeta que contiene `CONTENTS`, `PRIVATE`, `DCIM`, `XDROOT`, `BPAV`, `AVCHD`, `M4ROOT`, `CLIPR`; si se importó con la tarjeta montada queda la letra, p. ej. `E:`), (b) la cámara P2 (los dos últimos caracteres de `0016Q3`) y (c) dentro de eso, un salto de más de `tarjeta_hueco_horas` (2 h) en la fecha de la media (la del MXF: cuándo se capturó o importó en Avid). Las tarjetas se numeran por fecha y el bruto lleva la fecha de la primera. El informe y el log dicen en qué se basó cada tarjeta ("carpeta CARD02", "cámara P2 Q3", fechas).
   Dentro de cada bruto, los clips van ordenados por nombre y timecode. `brutos_resultado.json` guarda de cada bruto `proyecto` (nombre del bruto), `proyecto_avid`, `tipo` (tarjeta, agencias, piezas) y `contenido`.
2. **Plan**: si todos los clips comparten códec, resolución, fps y formato de píxel y el códec es copiable (DNxHD, MPEG-2, DV, ProRes, H.264) → **copia** (sin recomprimir, sin pérdida). Si no → **convertir** al formato de `config_brutos.json` (por defecto MPEG-2 4:2:2 50 Mb/s 1920×1080 25p, tipo XDCAM HD422; desentrelaza con `bwdif` si la fuente es entrelazada).
3. **Audio**: siempre PCM 24 bits 48 kHz, una pista mono por fichero de audio de Avid; todos los clips del bruto llevan el mismo número de pistas (el máximo del proyecto, tope 8); las que faltan se rellenan con silencio de la duración del clip (no con `-shortest`, que recortaba el vídeo a la pista más corta).
4. Cada clip se convierte en un MXF OP1a intermedio homogéneo (`_tmp_<proyecto>/seg_NNNNN.mxf`); luego se unen con el demuxer `concat` de ffmpeg con `-c copy` y el `timecode_inicio` del config.
   - **Si copiar falla** (un segmento o la unión) por algo que no sea tiempo agotado, el proyecto se repite una vez entero en modo convertir (`reintento: true`, `motivo_reintento`). Antes, ante un segmento fallido se prueba ese clip en convertir: si también falla es el fichero (clip `ERROR`, el proyecto sigue en copia); si va bien es el códec.
   - **Tiempo máximo** de ffmpeg: segmentos `max(600 s, 30 × duración)`, unión y verificación `max(1800 s, 10 × duración total)`. Un MXF dañado deja el clip en `ERROR (tiempo agotado)` y se sigue; el tiempo agotado nunca dispara el reintento.
   - **Timecode drop-frame**: si los fps de salida son 29,97 o 59,94, el bruto y el shotlist usan `hh:mm:ss;ff` (la única sintaxis DF que acepta ffmpeg). Con otros fps, `hh:mm:ss:ff`. Los TC del shotlist se calculan acumulando frames, no segundos.
5. **Verificación**: ffprobe del bruto; la duración debe coincidir con la suma de los segmentos (tolerancia 1 s). Con `"verificar_decodificando": true` en el config además se decodifica entero (lento).
6. **Shotlist** (`openpyxl`): nº, clip, TC inicio/fin dentro del bruto, duración, TC original, fecha, códec, resolución, pistas, estado, ficheros origen. **Contactos**: HTML con las miniaturas de la fase 1.
7. Un clip que falle se salta, queda marcado en el shotlist y el bruto se hace con el resto; `brutos_resultado.json → errores` guarda de cada fallo el comando ffmpeg (300 caracteres) y las últimas líneas del error. Antes de empezar cada proyecto se comprueba el espacio libre.
8. **Reanudar**: `brutos_resultado.json` se reescribe tras cada bruto. Al relanzar sobre la misma carpeta de brutos, un bruto se salta (`reanudado: true`) si terminó en `OK` o `AVISO`, su `.mxf` existe con el mismo tamaño, existe su shotlist y tiene exactamente los mismos clips (`firma`: huella de los identificadores). Si cambió el análisis o `config_brutos.json`, se rehace. El análisis (ffprobe, miniaturas) sí se repite entero.
9. En Windows el programa impide la suspensión del PC mientras trabaja (`SetThreadExecutionState`) y la restaura al terminar, también si hay un error.
10. **Botón Detener** (y cerrar la ventana con la X mientras trabaja, que pregunta antes): activa `exportar_brutos.PARAR`. `ejecutar_ffmpeg` lanza ffmpeg con `Popen` y lo mata en ≤ 1 s; el análisis lo comprueba por carpeta del inventario, por bin y en los bucles de ffprobe y miniaturas. Durante los brutos: el proyecto a medias se descarta (temporales y, si estaba uniendo, el `.mxf` parcial), `brutos_resultado.json` conserva los terminados y los de ejecuciones anteriores a los que no se llegó, y se escribe igualmente el informe y el zip con un aviso rojo y `resumen.json → detenido: true`. Durante el análisis: no hay informe; el log termina con "Análisis detenido por el usuario".

Lo que no hace: reconstruir las piezas montadas (secuencias con efectos o rótulos). Quedan listadas en el informe, no exportadas.

## Autoactualización (`actualizar.py`)

Solo en el `.exe` de Windows y solo al abrir la ventana (nunca a mitad de un análisis; por línea de comandos no se actualiza). Consulta `api.github.com/repos/malevolisco/diagnostico-avid/releases/latest` (el repositorio es público: no hace falta contraseña); si el número de build es mayor que el propio (`build.txt`), descarga `DiagnosticoAvid_portable.zip` a una carpeta `<carpeta del programa>_actualizacion` junto al programa, comprueba tamaño, integridad del zip y que contiene `DiagnosticoAvid/DiagnosticoAvid.exe`, y lanza un `.bat` (en `%LOCALAPPDATA%\DiagnosticoAvid`) que espera a que el programa se cierre, renombra la carpeta vieja a `_anterior`, pone la nueva en su sitio, copia `config_*.json` de la vieja, borra lo sobrante y reabre el programa. Si Windows no deja renombrar (una ventana del Explorador abierta dentro), copia encima con `robocopy` sin tocar la configuración. Si falla, vuelve a la versión anterior, la reabre y no reintenta esa build (marca `fallo_build-N.txt` en `%LOCALAPPDATA%\DiagnosticoAvid`; borrarla para reintentar). Todo queda en `%LOCALAPPDATA%\DiagnosticoAvid\actualizacion.log`.

`--sin-actualizar` abre la ventana sin buscar versión nueva. `--actualizar-ahora` (lo usa la prueba del workflow) actualiza sin ventana y sin reabrir. `DIAGNOSTICO_URL_ACTUALIZACION` apunta a otro JSON con el formato de la API (sirve `file:///`).

Nota: `config_origen.json` viejo se conserva al actualizar, así que si una versión nueva añade reglas de origen (p. ej. APTN), hay que borrar ese fichero para que se regenere, o añadir la regla a mano.

## Config de exportación (`config_brutos.json`)

Se crea con valores por defecto al primer uso. Claves: `modo` (`auto` | `copia` | `convertir`), `pistas_audio` (`max` | número | 0), `timecode_inicio` (se escribe siempre `hh:mm:ss:ff`; en proyectos 29,97 el programa lo pasa a drop-frame), `incluir_huerfanos`, `prefijo_fichero`, el reparto (`partir_por_tarjeta`, `tarjeta_hueco_horas`, `agencias_aparte`, `origenes_agencia`, `piezas_aparte`, `piezas_regex`, `renders_en_piezas`; la antigua `incluir_renders` ya no se usa), y `convertir` (`codec`, `ancho`, `alto`, `fps`, `bitrate`, `desentrelazar`). Si el archivo pide otro formato de ingesta, se cambia aquí sin tocar código.

## Verificado con datos reales (bins del proyecto 15 SEPT CENTROAMERICA, Media Composer 23.12.6)

- pyavb lee los bins reales, incluido uno de 10 MB con 1.100 clips, en segundos.
- Los bins guardan para cada clip el **disco donde Avid vio la media por última vez** ("DISCO 1 (E:)", "PORTATIL JULIO 2021 (H:)") y la **ruta del fichero importado** (los MP4 de "Mis vídeos" o "Downloads"). El programa sigue la cadena clip → fichero de media → origen y lo saca en el informe (tabla de offline) y en el shotlist.
- Hay master clips que son **renders de efectos** (`usage = precompute`: títulos, fundidos de audio) y composiciones que son contenedores de efectos. Se cuentan aparte y no entran en los brutos.
- Los bins se copian entre proyectos: `Bin1.avb` (2018) vive dentro de un proyecto de 2021. Un clip puede estar en varios proyectos; el informe lo cuenta y `config_brutos.json → clips_compartidos` decide si va a un bruto o a todos.
- El proyecto es NTSC 29,97 con material SD 720×486 y HD. Por eso el formato de conversión por defecto es ahora "auto" (el mayoritario del proyecto), no 1080p25.

## Qué sigue sin verificar

- El cruce bin ↔ MXF con ficheros MXF reales de Avid (el disco de media). Se compara el número de material del UMID (16 últimos bytes) en dos codificaciones, y como vía secundaria los source mobs contra el `file_package_umid` de cada MXF. `resumen.json → modo_coincidencia` dirá cuál ha funcionado.
- Que el códec real de los MXF sea copiable (DNxHD, DV, IMX/MPEG-2 lo son).
- Tiempos reales con miles de ficheros.
- Timecode drop-frame: probado con valores conocidos y con MXF MPEG-2 29,97 simulados (ffmpeg 6.1 en Linux y 7.1.1 en Windows); falta compararlo con el TC que muestra Avid en un proyecto real.
- El reintento copia → convertir solo está probado con ffmpeg simulado (pruebas unitarias): en el disco de prueba todos los formatos copiables se copian bien.
- **Reparto por tarjetas**: probado con bins simulados que guardan la ruta de la tarjeta igual que Avid (source mob con localizador) y con fechas de MXF fijadas. Falta comprobar con el disco real qué rutas guardan los bins de material P2 importado o enlazado (AMA), y si 2 h de hueco separa bien las tarjetas de un día de rodaje. Mirar `clips.json → fichero_origen` y el reparto en el log ("Brutos previstos").
- **Piezas renderizadas**: se reconocen porque se llaman como una secuencia del proyecto. No está comprobado cómo nombra Avid un mixdown en el disco real; si no coinciden, añadir una regla en `piezas_regex`.
- **Autoactualización**: el cambio de carpeta con el `.bat` se prueba en cada build en el Windows de GitHub (carpeta con espacios, paréntesis y ñ; se conserva la configuración). Sin probar: un PC con proxy corporativo o antivirus que bloquee la descarga o el `.bat`. En ese caso el programa sigue con su versión y lo deja en `%LOCALAPPDATA%\DiagnosticoAvid\actualizacion.log`.

## Lista de comprobación del primer zip de un disco real

1. `resumen.json → modo_coincidencia`: debe tener `le`, `be` o `fuente_*` con números altos. Si solo hay `nombre_duracion`, el identificador no cruza con MXF reales: revisar antes de exportar brutos.
2. `resumen.json → formatos` y `productos`: qué códecs hay y qué programa escribió los MXF. Comprobar que los códecs están en la lista de copiables.
3. `bins_ilegibles` y `bins.json`: qué bins no se han podido leer y por qué.
4. `offline` frente a `clips_en_bins`, y los volúmenes de la tabla de offline: cuánta media de los bins no está en este disco y dónde la vio Avid por última vez.
5. `cruce.json → por_proyecto`: `clips_aproximados` y `clips_ambiguos` (deberían ser 0 si el cruce exacto funciona).
6. `diagnostico.log`: número de build en la primera línea y tiempos de cada fase (cuánto tardará en el disco entero).
