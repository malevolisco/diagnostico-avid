# Diseño: pantalla de montaje y panel de exportación

Aprobado por Adrián el 22/10/2026, parte por parte. Va antes del visto bueno de Ingeniería de RTVE, así que el resultado tiene que ser comprobable y presentable.

## Objetivo

Antes de lanzar la exportación, que es lo caro (unos 40 s de proceso por minuto de material en un PC de 4 núcleos), Adrián quiere:

1. ver cómo ha quedado montado cada bruto y moverse por él en una línea de tiempo, para detectar saltos raros, clips fuera de sitio o algo al revés;
2. corregirlo: quitar clips, moverlos (también a otro bruto) y decidir dónde se corta;
3. elegir las opciones de exportación (timecode, pistas y niveles de audio, nombres) con valores recomendados y sin poder meter un valor imposible;
4. entregar brutos con lo que pediría Ingeniería: formato conforme y comprobado, MD5 y sin actualizaciones automáticas.

Criterio de éxito: lo que se ve en el montaje es exactamente lo que sale exportado, con las opciones elegidas, y cada bruto lleva su prueba de formato y su MD5.

## Flujo

1. **Analizar disco** (como ahora). Inventario, bins, cruce, orden por fecha y hora, reparto automático en brutos de hasta `horas_por_bruto`. No procesa vídeo pesado.
2. **Montaje**. Pantalla nueva que sustituye a "Revisar cortes...".
3. **Exportar** desde el montaje con el panel de opciones. Por la línea de comandos, `--usar-analisis` exporta lo que diga `montaje.json` con las opciones de `config_brutos.json`.

## Pantalla de montaje (módulo nuevo `montaje.py`)

`diagnostico_avid.py` tiene ya más de 2.000 líneas. La pantalla y su lógica van en un módulo propio, y la lógica (sin tkinter) se puede probar por separado.

De arriba abajo:

- **Selector de proyecto y bruto**, con la duración de cada bruto. Va en rojo si pasa de `horas_por_bruto`.
- **Visor**: fotograma del punto actual (480×270) con nombre del clip, fecha y hora (de grabación o volcado) y TC dentro del bruto.
- **Línea de tiempo** del bruto. Cada clip es un bloque proporcional a su duración; los movidos a mano van en otro color.
  - Arrastrar mueve el visor al instante.
  - Botón de avance rápido: reproduce los fotogramas a ritmo de presentación.
  - Zoom.
- **Lista de clips**: nombre, hora, duración, origen. Admite selección múltiple.
- **Acciones**:
  - **Quitar del bruto**: el clip va a la papelera del proyecto y se puede recuperar. Un clip quitado no se exporta.
  - **Mover**: arriba o abajo dentro del bruto, o al bruto anterior o siguiente del mismo proyecto. Un bruto que pasa del máximo sale en rojo; se avisa pero no se impide.
  - **Cortar aquí**: el bruto termina antes del clip y el resto pasa al siguiente.
  - **Renombrar bruto**: doble clic en el nombre. Se avisa si el nombre se repite o no es válido en Windows.
  - **Volver al orden automático** del proyecto: deshace todos los cambios a mano.
  - **Deshacer** (Ctrl+Z) de los últimos cambios.
- **Bandeja "clips nuevos sin colocar"** (en rojo, solo si hay alguno): ver "Reanálisis".
- Botón **Exportar...**: abre el panel de opciones.

No se mueve material entre proyectos: el nombre del bruto depende del proyecto.

Los brutos de un proyecto incluyen material, `SIN FECHA` y `PIEZAS`, igual que ahora. Las agencias y los importados siguen fuera.

### Fotogramas del visor

- Se generan la primera vez que se abre un bruto: 1 por segundo, 480×270, JPEG de calidad baja.
- Se hace con varios clips a la vez (`clips_a_la_vez`), y se puede mirar mientras se completan.
- Se guardan en `<carpeta del análisis>/visor/<clave del clip>/`. Así son por clip, sirven aunque el clip cambie de bruto y no se repiten.
- Nunca entran en el zip de diagnóstico ni salen del PC (norma del proyecto).
- Estimación sin medir: 1–2 min y unos 100 MB por bruto de 2 h. Se medirá en las pruebas y se anotará en `LEEME_tecnico.md`.
- Sin sonido: decidido por Adrián.

## Almacenamiento: `montaje.json`

Va en la carpeta de los brutos y sustituye a `cortes.json`. Un `cortes.json` antiguo se convierte en montaje al abrirlo y se avisa en el log. Por proyecto guarda:

- `brutos`: lista ordenada; cada bruto lleva `nombre` (si se editó a mano) y `clips` (claves en el orden exacto de exportación);
- `papelera`: claves de los clips quitados;
- `movidos`: claves de los clips movidos a mano;
- `modificado`: fecha del último cambio.

Un proyecto que no aparece en `montaje.json` usa el reparto automático, como ahora. La exportación lee el montaje y no recalcula nada: lo que se ve es lo que sale.

### Reanálisis

Si se repite el análisis del disco:

- las claves del montaje que ya no existen se quitan, con aviso;
- los clips nuevos del proyecto no se colocan solos: van a la bandeja "clips nuevos sin colocar" y no se exportan hasta que se coloquen o se pulse "volver al orden automático";
- el log y el informe dicen qué ha cambiado.

### Reanudar

Sigue como ahora: los brutos terminados se saltan. La `firma` de cada bruto pasa a incluir el orden de los clips y las opciones de exportación que le afectan. Si cambia algo, el bruto se marca para volver a exportarse.

## Nombres de los brutos

Por defecto: proyecto (en mayúsculas, como esté en Avid) y mes y año de los clips en letra, con espacios, sin guiones ni guiones bajos.

| Caso | Nombre |
|---|---|
| Un mes | `VENEZUELA SEPTIEMBRE DE 2021.mxf` |
| Varios meses del mismo año | `VENEZUELA SEPTIEMBRE A OCTUBRE DE 2021.mxf` |
| Cambia de año | `VENEZUELA DICIEMBRE DE 2021 A ENERO DE 2022.mxf` |
| Repetido (más de un bruto ese mes) | `VENEZUELA SEPTIEMBRE DE 2021 1.mxf`, `... 2.mxf` |
| Sin fecha | `VENEZUELA.mxf` |
| Piezas | `VENEZUELA PIEZAS SEPTIEMBRE DE 2021.mxf` |

- La fecha es la de grabación si el bin o el MXF la tienen; si no, la de volcado. Es la misma que se usa para ordenar.
- Del nombre del proyecto se quitan solo los caracteres que Windows no admite (`/ \ : * ? " < > |`).
- Junto al bruto, el shotlist, la hoja de contactos, el `.md5` y la ficha técnica llevan el mismo nombre base.
- Se puede editar en el panel (prefijo) y por bruto en el montaje.

Esto sustituye al `PROYECTO_AAAA-MM` actual. Sigue cumpliendo el punto 2 del correo del Archivo (proyecto + año y mes), pero con otra forma. Adrián se lo comunica a su jefa.

## Panel de opciones de exportación

Cada opción lleva el valor recomendado marcado y una línea de explicación. Son desplegables o campos validados al escribir: el botón Exportar queda desactivado y dice por qué mientras haya un valor no válido.

- **Formato**: XDCAM HD422 50 Mb/s 1080i25 16:9 (recomendado). En "Avanzado", el modo "formato mayoritario de cada bruto" (`formato_salida = auto`).
- **TC de inicio** de cada bruto: `00:00:00:00` por defecto. Formato `hh:mm:ss:ff`, fotogramas 00–24 a 25 fps.
- **Pistas de audio**:
  - número: "las que más encuentre" (máximo 8) por defecto, o fijo 4 u 8. En XDCAM se redondea a 4 u 8 (XDCAM HD422 no admite otra cantidad) y las que falten van en silencio;
  - enrutado: una fila por pista de salida con desplegable "pista 1–8 del clip o silencio". Por defecto cada una en la suya.
- **Niveles de audio**:
  - **sin tocar** (recomendado, práctica de archivo);
  - **ganancia fija por pista**, de −20 a +20 dB;
  - **normalizar a EBU R128 (−23 LUFS) clip a clip**, con aviso de que altera el original. Se mide la sonoridad integrada de cada clip y se aplica la ganancia con un tope de ±12 dB; los clips que llegan al tope se señalan en el informe. Medir obliga a leer el audio una vez más: la exportación tarda algo más.
- **Nombres**: prefijo opcional y vista previa de los nombres resultantes.
- **Resumen antes de lanzar**:
  - número de brutos y horas;
  - espacio necesario (unos 25 GB por hora en XDCAM HD50) frente al espacio libre del destino, en rojo si no cabe;
  - tiempo estimado tras el primer bruto.
- **Guardar como predeterminados** en `config_brutos.json` (así Ingeniería puede fijar una configuración) y **Restablecer recomendados**.
- Cada exportación registra sus opciones en `brutos_resultado.json`.

## Acabado profesional

Todo activado por defecto y desactivable en el panel.

- **TC dentro del vídeo** igual al del fichero: el TC de inicio de cada segmento se calcula por su posición en el bruto y se graba en las cabeceras GOP del MPEG-2. El material que se copia sin recomprimir (ya en XDCAM) conserva su TC interno; la ficha técnica lo indica.
- **Fecha de creación** real en el MXF. Ahora sale vacía tras la unión.
- **MD5** de cada bruto en `NOMBRE.md5`, en formato estándar (`hash *fichero`).
- **Ficha técnica** de cada bruto (texto) con veredicto conforme o no conforme frente al perfil XDCAM HD422, hecha con ffprobe sobre el fichero exportado. Comprueba:
  - contenedor MXF OP1a;
  - MPEG-2 4:2:2@High a 50 Mb/s CBR, 1920×1080, entrelazado con campo superior primero, 16:9, BT.709;
  - pistas de audio PCM 24 bits 48 kHz en el número elegido;
  - TC de inicio, y duración igual a la suma de los clips.

  Un bruto no conforme sale en rojo en el informe.
- **Medidor de audio**: en el shotlist y el informe se marcan los clips saturados (pico ≥ −0,1 dBFS) o casi mudos (sonoridad < −60 LUFS). No se tocan.
- **Actualización automática desactivada por defecto.** La ventana tiene un botón "Buscar actualización" que solo actúa al pulsarlo; `--actualizar-ahora` sigue igual. Se elimina la comprobación al abrir.

## Pruebas

- **Unitarias** (sin ventana), sobre `montaje.py`:
  - quitar, recuperar, mover dentro y entre brutos;
  - cortar aquí, deshacer, volver al orden automático;
  - conversión desde `cortes.json`;
  - reanálisis con clips que desaparecen y clips nuevos;
  - nombres: un mes, varios meses, cambio de año, repetidos, sin fecha, piezas, caracteres prohibidos;
  - validación de cada opción del panel;
  - enrutado y ganancias, con la orden de ffmpeg resultante;
  - firma que cambia con el orden y las opciones.
- **De principio a fin con el disco simulado**:
  - un `montaje.json` con clips quitados, movidos entre brutos y un nombre editado;
  - exportación con `--usar-analisis`;
  - `comprobar_resultado.py` comprueba orden exacto, TC de fichero y de GOP, fecha de creación, pistas, nombres, ficha conforme y MD5.
- **Ventana real** en pantalla virtual (xvfb): montaje, visor y panel.
- **Windows**: el workflow `construir.yml` ejecuta lo mismo con el `.exe`.
- **No verificable aquí**: material real de RTVE, velocidad en el PC de la jefa de Adrián, importación en Avid y en el sistema del Archivo. Se anota en `LEEME_tecnico.md`.

## Fuera de alcance

- Sonido en el visor.
- Mover clips entre proyectos.
- Integrar DaVinci Resolve o compresores comerciales. FFmpeg ya produce XDCAM HD422 reconocido por MediaInfo.
- Ajustarse a una norma técnica de ingesta del Archivo que todavía no tenemos. Cuando llegue, se cambian los valores por defecto.
