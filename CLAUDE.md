# CLAUDE.md — DiagnosticoAvid

Herramienta para el Archivo de RTVE: analiza un disco de edición Avid Media Composer completo y exporta un bruto MXF por proyecto. La usa una persona sin conocimientos técnicos (la jefa de Adrián) en Windows, sin Adrián delante. Adrián (documentalista, no programador) la mantiene con Claude; cada cambio se sube a `main` y GitHub construye y prueba el `.exe`.

## Reglas de trabajo con Adrián

- Responder en español. Tono pragmático, sin paternalismo ni jerga sin explicar.
- Al cambiar código entregar **ficheros completos**, nunca fragmentos. Varios ficheros → zip; uno solo → suelto.
- Nada sin comprobar: ejecutar `tests/` antes de dar algo por hecho. Si algo no se ha podido verificar, decirlo explícitamente (hay una lista en `LEEME_tecnico.md`).
- Si se cometió un error, reconocerlo y corregirlo; no suavizarlo.
- No deducir el origen del material (agencia, cámara) por el nombre de un MXF. Solo por lo que diga el bin (`config_origen.json`) y marcando lo demás "Sin determinar".
- Material de RTVE: nunca subir vídeo ni miniaturas a ningún servicio. Para diagnosticar bastan JSON y log.

## Estructura

| Fichero | Qué es |
|---|---|
| `diagnostico_avid.py` | Programa principal: inventario, lectura de bins (pyavb), ffprobe de MXF, cruce bin↔MXF, miniaturas, informe HTML, ventana tkinter y CLI. Fase 1. |
| `exportar_brutos.py` | Fase 2: un MXF OP1a por proyecto (copia directa si el formato es uniforme, conversión si no), shotlist xlsx, hoja de contactos. Lo importa el principal. |
| `config_brutos.json`, `config_origen.json` | Se crean junto al programa con valores por defecto la primera vez. No están en git. |
| `tests/crear_disco_prueba.py` | Genera un disco Avid simulado con casos límite (bins con pyavb, MXF OP-Atom con ffmpeg, V y A compartiendo UMID como hace Avid). |
| `tests/comprobar_resultado.py` | Comprueba un resultado contra valores fijos; sale con 1 si algo no cuadra. `--reanudado` para la segunda ejecución. |
| `tests/test_unitarios.py` | Timecode drop-frame con valores conocidos, reintento copia→convertir con ffmpeg simulado, reanudar. Solo stdlib. |
| `.github/workflows/construir.yml` | En cada push a `main` (o a mano en otra rama): pruebas unitarias, ffmpeg 7.1.1 fijado, PyInstaller con `build.txt`, disco de prueba, ejecuta el `.exe` dos veces (la segunda debe reanudar), comprueba y, solo en `main`, publica release `build-N` con `DiagnosticoAvid_portable.zip`. |
| `construir_exe.bat`, `ejecutar.bat` | Plan B local sin GitHub. |
| `INSTRUCCIONES.txt` | Para quien ejecuta el programa. Se copia dentro del portable. |
| `LEEME_tecnico.md` | Cómo funciona por dentro, qué está verificado con datos reales y qué no. |
| `docs/plan_fase3.md` | Plan aprobado de la fase 3. Implementados los pasos 1-4 y 6; el paso 5 (regla APTN) espera un nombre de fichero real. |

Descarga del portable: `https://github.com/malevolisco/diagnostico-avid/releases/latest/download/DiagnosticoAvid_portable.zip`

## Cómo probar en local (Linux o Windows con ffmpeg en el PATH)

```
pip install pyavb openpyxl
python tests/crear_disco_prueba.py disco_prueba
python diagnostico_avid.py --origen disco_prueba --destino salida --brutos brutos --sin-gui --no-abrir
python tests/comprobar_resultado.py salida brutos
python -m unittest discover -s tests
```
Debe terminar con "Análisis OK", "Brutos OK" y las pruebas unitarias en OK. Para probar reanudar: repetir la ejecución con otro `--destino` y la misma carpeta de brutos, y `comprobar_resultado.py ... --reanudado`. Si se añaden casos al disco de prueba, actualizar los valores fijos del comprobador en el mismo commit.

## Conocimiento del dominio que no está en el código

- Un disco Avid separa proyectos (carpetas con `.avp` y bins `.avb`) de la media (`Avid MediaFiles/MXF/<n>/`, OP-Atom: una pista por fichero; V01, A01, A02… de un clip comparten `material_package_umid`). La relación proyecto→media solo existe en los bins.
- El cruce bin↔MXF se hace por el **número de material** del UMID (16 últimos bytes), probando dos codificaciones del UUID (`le`, `be`); vía secundaria por los source mobs contra `file_package_umid`. `resumen.json → modo_coincidencia` dice cuál ha funcionado. **Aún no verificado con MXF reales de Avid** (solo simulados); sí verificado con bins reales de Media Composer 23.12.6.
- Los bins guardan para cada clip el disco donde Avid vio la media por última vez (`last_known_volume`) y la ruta del fichero importado. Es la base de la columna "Último disco conocido" y de la clasificación de origen.
- Master clips con `usage == "precompute"` son renders de efectos (títulos, fundidos): no son material, se excluyen de los brutos. Composiciones con `usage` distinto de None son contenedores de efectos, no secuencias.
- Los bins se copian entre proyectos: un clip puede estar en varios. `config_brutos.json → clips_compartidos` decide (por defecto solo al primero, orden alfabético).
- El disco real de la Corresponsalía de México es **NTSC 29,97** con material SD 720×486 y HD, cámaras Panasonic P2 (clips `0016Q3.new.01`), MP4 importados y algo de Reuters. Por eso la conversión por defecto es "formato mayoritario del proyecto", no 1080p25.
- Timecode: los proyectos NTSC usan drop-frame. ffmpeg solo acepta `-timecode hh:mm:ss;ff` (el `;` solo antes de los frames) y aborta si se pide DF con fps de salida 25. Implementado en `exportar_brutos.fmt_tc` / `tc_a_frames` (decisión por los fps de **salida**, `es_df`). Ojo: el minuto 0 de cada decena es completo, así que a 29,97 el primer frame del minuto 1 es el 1800, no el 1798.
- Nombres de fichero: Windows no admite `:` (el disco simulado lo tenía en un render "Title: 1" y en Windows ese clip no se creaba). El nombre del clip va en `material_package_name`, no en el nombre de fichero.
- ffmpeg: `mxf_opatom` sirve para generar fixtures; el muxer `mxf` (OP1a) admite `-c copy` de DNxHD, MPEG-2, DV, ProRes, H.264. Con el demuxer `concat` hay que usar `-map 0` o pierde pistas de audio, y la lista debe llevar rutas absolutas.
- pyavb necesita Python ≤ 3.13 con wheel universal: instalar siempre con `--only-binary :all:` (el sdist exige Cython 0.29, que no compila en 3.13).

## Decisiones ya tomadas (no reabrir sin motivo)

- Las piezas montadas (secuencias) no se reconstruyen: requiere Avid. Se listan en el informe.
- Audio siempre PCM 24 bits 48 kHz, una pista mono por fichero de Avid; mismo número de pistas en todo el bruto, rellenando con silencio.
- El programa nunca escribe en el disco de origen y se niega si el destino está dentro de él.
- Clips en `Quarantined Files` se incluyen en los brutos pero marcados "incluido (cuarentena)" en el shotlist.
