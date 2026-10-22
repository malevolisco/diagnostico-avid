# Plan de implementación: pantalla de montaje y panel de exportación

**Objetivo:** que lo que se revisa y corrige en el montaje sea exactamente lo que se exporta, con las opciones elegidas, y que cada bruto lleve su prueba de formato y su MD5.

**Diseño:** `docs/diseno_montaje_exportacion.md`. Este plan lo sigue; ante cualquier duda manda el diseño.

**Arquitectura:**
- `montaje.py`: lógica pura, sin tkinter. Montaje por proyecto, persistencia, reanálisis y fotogramas del visor.
- `ventana_montaje.py`: pantalla de montaje y panel de opciones (tkinter y Pillow).
- `mxf_util.py`: timecode GOP, ficha técnica y MD5.
- `exportar_brutos.py`: exporta a partir de una lista de grupos, como ahora; el montaje solo cambia qué grupos le llegan.

**Tecnología:** Python 3.12/3.13, tkinter, ffmpeg/ffprobe 7.1.1, pyavb, openpyxl. Nuevo: **Pillow**, para mostrar JPEG en tkinter. Va dentro del `.exe`, así que el usuario no instala nada.

## Restricciones globales

- **Nombres:** proyecto en mayúsculas + mes(es) y año en letra.
  - Ejemplos: `VENEZUELA SEPTIEMBRE DE 2021`, `VENEZUELA SEPTIEMBRE A OCTUBRE DE 2021`, `VENEZUELA DICIEMBRE DE 2021 A ENERO DE 2022`.
  - Repetidos: ` 1`, ` 2`. Sin fecha: solo el proyecto. Piezas: `PROYECTO PIEZAS <MES> DE <AÑO>`.
  - `SIN_PROYECTO` se escribe `SIN PROYECTO`. Se quitan `/ \ : * ? " < > |`.
- **TC de inicio:** `00:00:00:00` por defecto.
- **Pistas:** `max` por defecto; en XDCAM se redondea a 4 u 8 (0 sigue siendo 0).
- **Niveles:** `sin_tocar` por defecto. Ganancia de −20 a +20 dB. R128 a −23 LUFS con tope de ±12 dB.
- **Visor:** 1 fotograma/s, 480×270, nunca en el zip de diagnóstico.
- **Por defecto:** MD5, ficha técnica y medidor activados; actualización automática desactivada.
- **El montaje solo se aplica con `agrupar = proyecto_mes`.** En los otros modos se avisa y se ignora.
- **Commits:** a nombre de Adrián, sin firmas. Escaneo Opsera antes de cada commit.

## Puntos de atención (casos no cubiertos explícitamente por el diseño)

1. **Montaje guardado de otro análisis** con claves que ya no existen: se quitan con aviso y no fallan. Prueba en la tarea 2.
2. **Un bruto vacío** tras quitar o mover todos sus clips: desaparece del montaje y no se exporta un MXF vacío. Prueba en la tarea 2.
3. **Dos brutos renombrados a mano con el mismo nombre**, o con caracteres prohibidos: el panel no deja exportar. Prueba en la tarea 3.
4. **Clip con más pistas que las de salida**, o enrutado a una pista que el clip no tiene: esa pista va en silencio y el shotlist lo dice. Prueba en la tarea 3.
5. **Bytes `00 00 01 B8` dentro del audio PCM:** el parche del TC GOP solo toca elementos de imagen MPEG-2. Prueba en la tarea 4.

---

### Tarea 1: nombres nuevos de los brutos

**Ficheros:** `exportar_brutos.py` (`grupos_por_proyecto_mes`, nueva `nombre_bruto`); `tests/test_unitarios.py`; `tests/comprobar_resultado.py`; `.github/workflows/construir.yml` (paso del selector); `tests/probar_cortes.py` (se sustituye en la tarea 2).

**Produce:** `nombre_bruto(proyecto: str, fechas: list[datetime], piezas: bool) -> str`. Devuelve el nombre sin numerar; la numeración la añade quien agrupa.

- [ ] Prueba `test_nombres_de_bruto`:
  - un mes → `"VENEZUELA SEPTIEMBRE DE 2021"`;
  - septiembre–octubre 2021 → `"VENEZUELA SEPTIEMBRE A OCTUBRE DE 2021"`;
  - diciembre 2021–enero 2022 → `"VENEZUELA DICIEMBRE DE 2021 A ENERO DE 2022"`;
  - sin fechas → `"VENEZUELA"`;
  - piezas → `"VENEZUELA PIEZAS SEPTIEMBRE DE 2021"`;
  - `"Méx/ico"` → `"MÉXICO"` (sin la barra);
  - `"SIN_PROYECTO"` → `"SIN PROYECTO ..."`.
- [ ] Prueba: dos brutos del mismo mes → sufijos `" 1"` y `" 2"`.
- [ ] Implementar y cambiar `grupos_por_proyecto_mes` para usarla. `base` es el nombre sin más.
- [ ] Actualizar los nombres esperados en `ProyectoMes`, `CortesYGrabacion`, `comprobar_resultado.py` y el workflow.
- [ ] `python -m unittest discover -s tests` en verde; disco simulado de principio a fin con "Brutos OK".

### Tarea 2: modelo de montaje y exportación desde el montaje

**Ficheros:** nuevo `montaje.py`; `exportar_brutos.py` (`exportar_todo` con `montaje=`); `diagnostico_avid.py` (`exportar_desde_analisis`); nuevo `tests/test_montaje.py`; `tests/probar_montaje.py` sustituye a `tests/probar_cortes.py`; workflow.

**Produce:**
- `Montaje.automatico(listas: dict, fecha, tope) -> Montaje`, desde `listas_por_proyecto`.
- Estado de cada proyecto: `brutos: list[{"nombre": str|None, "tipo": "material"|"piezas", "clips": [clave]}]`, más `papelera: [clave]`, `movidos: set[clave]` y `modificado: str`.
- Operaciones sobre un proyecto `p`, todas con deshacer (pila de 50):
  - `quitar(p, claves)`, `recuperar(p, claves)`;
  - `mover(p, claves, delta_bruto: int)` (−1 al anterior, al final; +1 al siguiente, al principio);
  - `subir(p, claves)`, `bajar(p, claves)`;
  - `cortar(p, clave)`;
  - `renombrar(p, i_bruto, nombre|None)`;
  - `volver_automatico(p)`, `deshacer()`.
- Conciliación: `reconciliar(listas) -> list[str]` (avisos). `nuevos(p) -> [clave]` da los clips sin colocar.
- `cargar(carpeta_brutos, listas, fecha, tope) -> Montaje` (convierte `cortes.json` si existe) y `guardar(carpeta_brutos)`.
- `grupos(clips_por_clave, fecha, cfg) -> list[grupo]`: mismo formato que `grupos_por_proyecto_mes`, más `movidos` y `nombre_manual`.

**Pasos:**
- [ ] `tests/test_montaje.py`, que debe fallar primero:
  - quitar y recuperar;
  - mover al bruto siguiente y anterior, marcando `movidos`;
  - un bruto vacío desaparece;
  - subir y bajar;
  - cortar;
  - deshacer;
  - volver al automático;
  - `cortes.json` antiguo convertido;
  - reanálisis con una clave desaparecida (aviso) y una nueva (en `nuevos`, no se exporta);
  - nombres repetidos numerados;
  - nombre manual respetado;
  - guardar y cargar idéntico.
- [ ] Implementar `montaje.py`.
- [ ] `exportar_todo(..., montaje=None)`: para los proyectos del montaje, sus grupos sustituyen a los automáticos. Si no se pasa montaje, se carga `montaje.json` de la carpeta de brutos. Con `agrupar` distinto de `proyecto_mes`, se avisa y se ignora.
- [ ] `tests/probar_montaje.py preparar|comprobar`:
  - en 15 SEPT: cortar tras el primer clip, invertir el orden del segundo bruto y renombrarlo `PRUEBA MONTAJE`;
  - en ABORTO: quitar un clip;
  - comprobar el orden exacto en el shotlist, los nombres y que el clip quitado no está.
- [ ] Workflow: sustituir el paso de cortes por este. Borrar `probar_cortes.py`.
- [ ] Pruebas unitarias y de principio a fin en verde.

### Tarea 3: opciones de exportación (audio, TC, validación, firma, shotlist)

**Ficheros:** `exportar_brutos.py` (`CONFIG_DEFECTO`, `plan_proyecto`, `hacer_segmento`, `firma_clips`, `_fila_clip`, `escribir_shotlist`, `exportar_proyecto`); `tests/test_unitarios.py`.

**Produce:**
- Claves nuevas de configuración:
  - `enrutado_audio: []` (lista de enteros 1–8 o 0 = silencio; vacía = cada pista en la suya);
  - `niveles_audio: "sin_tocar"|"ganancia"|"r128"`;
  - `ganancia_db: []`;
  - `md5: true`, `ficha_tecnica: true`, `medidor_audio: true`.
- `validar_opciones(cfg) -> list[str]`: errores legibles; vacía si todo está bien.
- `pistas_salida(n_max: int, cfg) -> int`: redondeo a 4 u 8 en XDCAM.
- `medir_audio(ffmpeg, ruta) -> {"lufs": float|None, "pico": float|None}`, con `ebur128=peak=sample`.
- `ganancia_r128(lufs) -> float`: `clamp(-23 - lufs, -12, 12)`.
- `firma_bruto(cs, cfg) -> str`: claves **en orden** + opciones que afectan a la salida.

**Pasos:**
- [ ] Pruebas:
  - `validar_opciones` rechaza un TC `25:00:00:00` o `00:00:00:25`, ganancias fuera de ±20, enrutado a pista 9 y `niveles_audio` desconocido;
  - `pistas_salida`: 2→4, 5→8, 0→0, fijo 4→4;
  - con el enrutado `[2,1]`, la orden de ffmpeg hace `-map` de la pista 2 del clip a la salida 1;
  - un enrutado a una pista que el clip no tiene da silencio;
  - con ganancia `[3]`, aparece `-filter:a:0 volume=3dB`;
  - `ganancia_r128(-40) == 12` y `ganancia_r128(-20) == -3`;
  - la firma cambia al cambiar el orden o las opciones.
- [ ] Implementar. El medidor se ejecuta por fichero de audio del clip dentro del mismo trabajo del segmento, así que va en paralelo.
- [ ] Shotlist:
  - columnas `Montaje` (automático / movido a mano), `Sonoridad (LUFS)`, `Pico (dBFS)`, `Ganancia (dB)` y `Aviso audio` (saturado si el pico es ≥ −0,1; casi mudo si la sonoridad es < −60; tope R128 si se llegó a ±12);
  - hoja `Quitados` con los clips de la papelera del proyecto.
- [ ] `brutos_resultado.json` registra `opciones` (las de la firma).
- [ ] Pruebas en verde.

### Tarea 4: acabado profesional (TC GOP, fecha, MD5, ficha técnica)

**Ficheros:** nuevo `mxf_util.py`; `exportar_brutos.py` (`unir_segmentos` y final de `exportar_proyecto`); `tests/test_unitarios.py`; `tests/comprobar_resultado.py`.

**Produce:**
- `parchear_tc_gop(ruta, frames_inicio: int, fps: float) -> int` (GOP parcheados). Recorre los KLV y en cada elemento de imagen MPEG-2 (clave `060e2b34.0102.0101.0d010301.15`) busca `00 00 01 B8` y reescribe los 25 bits de timecode. El TC es el inicio más los cuadros de los GOP anteriores, contados por los inicios de imagen `00 00 01 00`. Solo se aplica en modo convertir.
- `leer_tc_gop(ruta, maximo=3) -> list[str]`.
- `md5_fichero(ruta) -> str`, por bloques de 8 MB, y escribe `NOMBRE.md5` (`hash *NOMBRE.mxf`).
- `ficha_tecnica(ffprobe, ruta, esperado: dict) -> (bool, str)`. Comprueba OP1a, mpeg2video 4:2:2, 1920×1080, `field_order tt`, 16:9, 25/1, bt709, número de pistas `pcm_s24le` 48000 mono, TC de inicio, duración (±1 s) y TC GOP igual al TC del fichero.
- `unir_segmentos` añade `-metadata creation_time=<ahora UTC ISO>`.

**Pasos:**
- [ ] Pruebas:
  - generar con ffmpeg 2 s de XDCAM con audio, parchear desde `10:00:00:00` y que `leer_tc_gop` dé `10:00:00:00`, `10:00:00:12`, …;
  - un fichero cuyo audio contiene `00 00 01 B8` no se altera fuera de la imagen (mismo MD5 de los bytes de audio);
  - la ficha de un bruto bueno es conforme;
  - la ficha con 2 pistas cuando se esperaban 4 no es conforme;
  - el MD5 coincide con `hashlib`.
- [ ] Implementar e integrar al final de `exportar_proyecto`. `res` añade `md5`, `ficha` (nombre del fichero) y `conforme`; un bruto no conforme deja el estado en `AVISO: no conforme (...)`.
- [ ] `comprobar_resultado.py`: en modo XDCAM, para cada bruto, comprobar ficha conforme, `.md5` correcto, TC GOP igual al TC del MXF y `creation_time` presente.
- [ ] Pruebas en verde.

### Tarea 5: fotogramas del visor

**Ficheros:** `montaje.py`; `tests/test_montaje.py`.

**Produce:**
- `sacar_fotogramas(ffmpeg, ruta_video, carpeta_visor, clave) -> Path`: `<sha256(clave)[:16]>.mjpeg` a 1 fps, 480×270 con bandas si hace falta, `-q:v 10`. Si ya existe y no está vacío, no se repite.
- `indice_jpeg(ruta) -> list[(inicio, fin)]`.
- `fotograma(ruta, i) -> bytes`.

**Pasos:**
- [ ] Prueba: con un vídeo de 3 s salen 3 fotogramas; `fotograma(…, 2)` empieza por `FF D8`; la segunda llamada no relanza ffmpeg.
- [ ] Implementar. Comprobar que el zip de diagnóstico excluye `visor/`, igual que las miniaturas.
- [ ] Medir en local el tiempo y el tamaño por minuto de DV y anotarlo en `LEEME_tecnico.md`.

### Tarea 6: ventana de montaje y panel de opciones

**Ficheros:** nuevo `ventana_montaje.py`; `diagnostico_avid.py` (el botón "Revisar cortes..." pasa a "Montaje...", se borran `dialogo_cortes`/`miniatura_png` y "Exportar brutos" abre el panel); `construir_exe.bat`, workflow y `LEEME_tecnico.md` (Pillow).

**Produce:**
- `dialogo_montaje(raiz, carpeta_analisis, carpeta_brutos, elegidos) -> bool` (guardado).
- `dialogo_opciones(raiz, cfg, resumen: dict) -> dict|None`: la configuración elegida, o `None` si se cancela. `resumen` lleva `brutos`, `segundos`, `libre` y `pistas`.

**Pasos:**
- [ ] Implementar según el diseño:
  - selector de proyecto y bruto;
  - visor con Pillow y caché de 64 fotogramas;
  - línea de tiempo en un Canvas (bloques proporcionales, movidos en naranja, cursor arrastrable, zoom, avance rápido);
  - lista con selección múltiple;
  - botones de acciones y Ctrl+Z;
  - bandeja de clips nuevos;
  - doble clic para renombrar.
- [ ] Generar los fotogramas en un hilo con `clips_a_la_vez`, actualizando con `after()`.
- [ ] Panel:
  - campos validados con `validar_opciones`; el botón Exportar se desactiva con el motivo;
  - vista previa de nombres;
  - espacio necesario (`segundos × (50e6 + pistas × 1,152e6) / 8 × 1,02`) frente al libre;
  - "Guardar como predeterminados" (`config_brutos.json`) y "Restablecer recomendados".
- [ ] Prueba con pantalla virtual (xvfb, script de comprobación aparte):
  - abrir el montaje sobre el disco simulado;
  - quitar, mover, deshacer y guardar;
  - comprobar `montaje.json`;
  - abrir el panel, poner un TC no válido y comprobar el botón desactivado;
  - exportar.

### Tarea 7: actualización automática desactivada

**Ficheros:** `diagnostico_avid.py` (`lanzar_gui`); `INSTRUCCIONES.txt`.

- [ ] Quitar la búsqueda al abrir.
- [ ] Botón "Buscar actualización", visible solo si `puede_actualizarse()`: pide confirmación y lanza `actualizar.actualizar` en un hilo, con los botones desactivados.
- [ ] `--sin-actualizar` se mantiene sin efecto, por compatibilidad. `--actualizar-ahora`, igual que ahora.
- [ ] Prueba en xvfb: el botón no aparece fuera del `.exe`. El paso del workflow de autoactualización sigue en verde.

### Tarea 8: documentación, verificación completa y entrega

- [ ] `INSTRUCCIONES.txt` (CRLF), `LEEME_tecnico.md` y `docs/NOTAS_DESARROLLO.md`:
  - decisiones nuevas: montaje, nombres, opciones, actualización a mano;
  - tabla de ficheros;
  - lo no verificado.
- [ ] Pruebas unitarias, disco simulado de principio a fin (dos veces, la segunda debe reanudar), `probar_montaje`, prueba de la ventana y MediaInfo sobre un bruto.
- [ ] Escaneo Opsera, commit, push y workflow en Windows sobre la rama. Fusionar cuando esté en verde.
