# CLAUDE.md — DiagnosticoAvid

Herramienta de Adrián Merinero Sánchez para el Archivo de RTVE: analiza un disco de edición Avid Media Composer completo y exporta un bruto MXF por proyecto. La usa una persona sin conocimientos técnicos (la jefa de Adrián) en Windows, sin Adrián delante. Adrián (documentalista, no programador) es el autor: decide qué hace la herramienta y cómo, y Claude escribe el código siguiendo sus indicaciones; cada cambio se sube a `main` y GitHub construye y prueba el `.exe`.

## Reglas de trabajo con Adrián

- Responder en español. Tono pragmático, sin paternalismo ni jerga sin explicar.
- Al cambiar código entregar **ficheros completos**, nunca fragmentos. Varios ficheros → zip; uno solo → suelto.
- Nada sin comprobar: ejecutar `tests/` antes de dar algo por hecho. Si algo no se ha podido verificar, decirlo explícitamente (hay una lista en `LEEME_tecnico.md`).
- Si se cometió un error, reconocerlo y corregirlo; no suavizarlo.
- No deducir el origen del material (agencia, cámara) por el nombre de un MXF. Solo por lo que diga el bin (`config_origen.json`) y marcando lo demás "Sin determinar".
- Material de RTVE: nunca subir vídeo ni miniaturas a ningún servicio. Para diagnosticar bastan JSON y log.
- Autoría: los commits van a nombre de Adrián (`git config user.name "Adrián Merinero Sánchez"`, `user.email malevolisco@gmail.com`). Sin líneas de atribución a Claude: nada de `Co-Authored-By`, `Claude-Session` ni "Generated with Claude Code" en commits, PR ni comentarios.

## Estructura

| Fichero | Qué es |
|---|---|
| `diagnostico_avid.py` | Programa principal: inventario, lectura de bins (pyavb), ffprobe de MXF, cruce bin↔MXF, miniaturas, informe HTML, ventana tkinter y CLI. Fase 1. |
| `exportar_brutos.py` | Fase 2: brutos MXF OP1a. Por defecto todo el disco por días seguidos hasta 3 h (`BRUTO_2018-12-01_a_2018-12-04`), con agencias y piezas aparte; opcionalmente por proyecto y tarjeta (`agrupar`); copia directa si el formato es uniforme, conversión si no; shotlist xlsx, hoja de contactos. Lo importa el principal. |
| `actualizar.py` | Autoactualización del `.exe` al abrir la ventana: consulta la última release en GitHub, descarga, comprueba y un `.bat` cambia la carpeta conservando `config_*.json`. |
| `config_brutos.json`, `config_origen.json` | Se crean junto al programa con valores por defecto la primera vez. No están en git. |
| `tests/crear_disco_prueba.py` | Genera un disco Avid simulado con casos límite (bins con pyavb, MXF OP-Atom con ffmpeg, V y A compartiendo UMID como hace Avid). |
| `tests/comprobar_resultado.py` | Comprueba un resultado contra valores fijos; sale con 1 si algo no cuadra. `--reanudado` para la segunda ejecución. |
| `tests/test_unitarios.py` | Timecode drop-frame, reintento copia→convertir con ffmpeg simulado, reanudar, botón Detener, reparto por tarjeta/agencias/piezas, autoactualización con release falsa local. Solo stdlib. |
| `.github/workflows/construir.yml` | En cada push a `main` (o a mano en otra rama): pruebas unitarias, ffmpeg 7.1.1 fijado, PyInstaller con `build.txt`, disco de prueba, ejecuta el `.exe` dos veces (la segunda debe reanudar) y una tercera con `agrupar = proyecto`, comprueba, prueba la autoactualización con una release falsa local y, solo en `main`, publica release `build-N` con `DiagnosticoAvid_portable.zip`. El repositorio es público (la autoactualización lo necesita sin contraseña). |
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
- El cruce bin↔MXF se hace por el **número de material** del UMID (16 últimos bytes), probando dos codificaciones del UUID (`le`, `be`); vía secundaria por los source mobs contra `file_package_umid`. `resumen.json → modo_coincidencia` dice cuál ha funcionado. **Verificado con el disco real de México**: funciona `be` (18.215) y `fuente_be` (2.508); `le` no aparece.
- Los bins guardan para cada clip el disco donde Avid vio la media por última vez (`last_known_volume`) y la ruta del fichero importado. Es la base de la columna "Último disco conocido" y de la clasificación de origen.
- Master clips con `usage == "precompute"` son renders de efectos (títulos, fundidos, picture-in-picture): no son material; no entran en los brutos (`renders_en_piezas`). Composiciones con `usage` distinto de None son contenedores de efectos, no secuencias.
- Tarjetas: en el disco real los clips P2 **no** guardan en el bin la ruta de la tarjeta, y su nombre (`0016Q3`, `00016R`, `0002CI`…) **no** indica ni tarjeta ni cámara (clips seguidos tienen nombres sin relación). Lo único fiable es la fecha del MXF (cuándo Avid escribió la media): cada volcado de tarjeta es una tanda. Solo los ficheros importados guardan ruta (localizador `WinFileLocator`/`FileLocator` del source mob); si pasa por `CONTENTS`, `PRIVATE`, `DCIM`… es una tarjeta, si no es un importado (`_IMPORTADOS`). pyavb puede escribir esa cadena (master → SourceClip → source mob con descriptor y localizador): así lo hace el disco simulado.
- Renders de efectos sin bin: Avid los llama `<clip>,<efecto>,<n>` ("PIEZA X,Dissolve,1"). En México: 806 sin bin, 1.100 de 1.200 con bin, ningún clip de material.
- Rutas antiguas sin UTF-8 en los bins: pyavb las decodifica como Mac Roman; son Windows-1252 (`diagnostico_avid.reparar_ruta`).
- Los bins se copian entre proyectos: un clip puede estar en varios. `config_brutos.json → clips_compartidos` decide (por defecto solo al primero, orden alfabético).
- El disco real de la Corresponsalía de México es **NTSC 29,97**: 4 TB, 108 proyectos, 18.847 clips, la mayoría DV 720×480 (13.756), además H.264, DNxHD, MPEG-2; cámaras Panasonic P2 (clips `0016Q3.new.01`), MP4 importados y algo de Reuters. Por eso la conversión por defecto es "formato mayoritario del proyecto", no 1080p25. Cifras y hallazgos en `LEEME_tecnico.md`.
- Timecode: los proyectos NTSC usan drop-frame. ffmpeg solo acepta `-timecode hh:mm:ss;ff` (el `;` solo antes de los frames) y aborta si se pide DF con fps de salida 25. Implementado en `exportar_brutos.fmt_tc` / `tc_a_frames` (decisión por los fps de **salida**, `es_df`). Ojo: el minuto 0 de cada decena es completo, así que a 29,97 el primer frame del minuto 1 es el 1800, no el 1798.
- Nombres de fichero: Windows no admite `:` (el disco simulado lo tenía en un render "Title: 1" y en Windows ese clip no se creaba). El nombre del clip va en `material_package_name`, no en el nombre de fichero.
- ffmpeg: `mxf_opatom` sirve para generar fixtures; el muxer `mxf` (OP1a) admite `-c copy` de DNxHD, MPEG-2, DV, ProRes, H.264. Con el demuxer `concat` hay que usar `-map 0` o pierde pistas de audio, y la lista debe llevar rutas absolutas.
- pyavb necesita Python ≤ 3.13 con wheel universal: instalar siempre con `--only-binary :all:` (el sdist exige Cython 0.29, que no compila en 3.13).

## Decisiones ya tomadas (no reabrir sin motivo)

- Las piezas montadas (secuencias) no se reconstruyen: requiere Avid. Se listan en el informe.
- Audio siempre PCM 24 bits 48 kHz, una pista mono por fichero de Avid; mismo número de pistas en todo el bruto, rellenando con silencio.
- Brutos (Adrián, oct. 2026, tras ver los 481 del reparto por tarjetas sobre el disco real): **todo el disco por días seguidos hasta 3 h, sin separar proyectos** (`agrupar = dias`, 67 brutos en México), y **dentro de cada bruto los clips agrupados por proyecto**; agencias y piezas renderizadas aparte, también por días; el shotlist dice el proyecto de cada clip. Los renders de efectos (fundidos, picture-in-picture, rótulos) no entran. El reparto por proyecto y tarjeta sigue disponible con `agrupar = proyecto`.
- La autoactualización es automática, sin preguntar, al abrir la ventana.
- El programa nunca escribe en el disco de origen y se niega si el destino está dentro de él.
- Clips en `Quarantined Files` se incluyen en los brutos pero marcados "incluido (cuarentena)" en el shotlist.
