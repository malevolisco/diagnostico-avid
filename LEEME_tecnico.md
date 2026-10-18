# Diagnóstico y exportación de discos Avid — notas técnicas

## Qué hay en el paquete

| Fichero | Para qué |
|---|---|
| `construir_exe.bat` | **Lo que ejecutas tú, una vez.** Descarga ffmpeg, construye el `.exe` y deja `DiagnosticoAvid_portable.zip` listo para enviar |
| `diagnostico_avid.py` | Programa principal: ventana, línea de comandos, análisis (fase 1) e informe |
| `exportar_brutos.py` | Fase 2: un MXF por proyecto + shotlist Excel + hoja de contactos. Lo importa el principal |
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

Si el antivirus corporativo bloquea el `.exe` (pasa con PyInstaller), la alternativa es `ejecutar.bat` + Python instalado en el PC de destino.

## Modo línea de comandos (para ti)

```
python diagnostico_avid.py --origen F:\ --destino C:\Users\TU_USUARIO\Desktop --sin-gui
python diagnostico_avid.py --origen F:\ --destino C:\Users\TU_USUARIO\Desktop --brutos E:\BRUTOS --sin-gui
```
Opciones: `--sin-miniaturas` (análisis mucho más rápido), `--max-miniaturas 200`, `--no-abrir`.

## Fase 1: qué hace el análisis

1. **Inventario**: `os.walk` del disco entero → `inventario.csv`.
2. **Proyectos**: cada carpeta con un `.avp` es un proyecto; sus `.avb` (y los de subcarpetas, marcando `Trash`) son sus bins.
3. **Bins**: `pyavb` lee cada bin y separa master clips (`MasterMob`) de secuencias (`CompositionMob`), con nombre, duración, fechas y MobID.
4. **MXF**: `ffprobe -show_format -show_streams` de cada `.mxf` (4 en paralelo): códec, resolución, fps, entrelazado, duración, timecode, `material_package_umid` (identificador del clip), `material_package_name` y comentarios de usuario (`comment_*`).
5. **Clips**: los MXF que comparten `material_package_umid` son pistas (V/A) del mismo clip.
6. **Cruce**: el MobID de cada clip de bin contra el UMID de los clips físicos, probando dos codificaciones del UUID (`le` y `be`); `resumen.json → modo_coincidencia` dice cuál ha funcionado.
7. **Miniaturas**: un fotograma al 10 % de cada clip con vídeo, 320 px.
8. **Informe**: `informe.html` + JSON por capa + ZIP.

## Fase 2: cómo se construye cada bruto

1. Clips del proyecto (los cruzados en la fase 1), ordenados por nombre y timecode. Los huérfanos van a `BRUTO_SIN_PROYECTO`.
2. **Plan**: si todos los clips comparten códec, resolución, fps y formato de píxel y el códec es copiable (DNxHD, MPEG-2, DV, ProRes, H.264) → **copia** (sin recomprimir, sin pérdida). Si no → **convertir** al formato de `config_brutos.json` (por defecto MPEG-2 4:2:2 50 Mb/s 1920×1080 25p, tipo XDCAM HD422; desentrelaza con `bwdif` si la fuente es entrelazada).
3. **Audio**: siempre PCM 24 bits 48 kHz, una pista mono por fichero de audio de Avid; todos los clips del bruto llevan el mismo número de pistas (el máximo del proyecto, tope 8); las que faltan se rellenan con silencio.
4. Cada clip se convierte en un MXF OP1a intermedio homogéneo (`_tmp_<proyecto>/seg_NNNNN.mxf`); luego se unen con el demuxer `concat` de ffmpeg con `-c copy` y timecode inicial `00:00:00:00`.
5. **Verificación**: ffprobe del bruto; la duración debe coincidir con la suma de los segmentos (tolerancia 1 s). Con `"verificar_decodificando": true` en el config además se decodifica entero (lento).
6. **Shotlist** (`openpyxl`): nº, clip, TC inicio/fin dentro del bruto, duración, TC original, fecha, códec, resolución, pistas, estado, ficheros origen. **Contactos**: HTML con las miniaturas de la fase 1.
7. Un clip que falle se salta, queda marcado en el shotlist y el bruto se hace con el resto. Antes de empezar se comprueba el espacio libre.

Lo que no hace: reconstruir las piezas montadas (secuencias con efectos o rótulos). Quedan listadas en el informe, no exportadas.

## Config de exportación (`config_brutos.json`)

Se crea con valores por defecto al primer uso. Claves: `modo` (`auto` | `copia` | `convertir`), `pistas_audio` (`max` | número | 0), `timecode_inicio`, `incluir_huerfanos`, `prefijo_fichero`, y `convertir` (`codec`, `ancho`, `alto`, `fps`, `bitrate`, `desentrelazar`). Si el archivo pide otro formato de ingesta, se cambia aquí sin tocar código.

## Qué NO está verificado con datos reales

Probado con discos simulados (bins creados con pyavb y MXF OP-Atom DNxHD creados con ffmpeg, con V y A compartiendo UMID como hace Avid) y en Linux; el `.exe` se construye en tu Windows. Quedan por confirmar con el primer disco real:

- que pyavb lea bins guardados por Media Composer 2019+ (si falla, "bins ilegibles" en el informe y el resto sigue);
- qué codificación del UMID coincide (`modo_coincidencia`);
- si Avid escribe el nombre del proyecto dentro del MXF;
- que el códec real del disco sea copiable (si es DNxHD o XDCAM, lo es);
- tiempos reales: la copia va a la velocidad del disco; la conversión, a unos 2-4× tiempo real por clip según el PC.
