# Plan: que valga para cualquier disco

**Estado (23/10/2026): hecho entero.** Cada parte se fusionó con su build de Windows en verde:

| Parte | Qué | PR |
|---|---|---|
| 1 | Audio por canales | malevolisco/diagnostico-avid#18 |
| 2 y 3 | Vídeo de cualquier origen (HDR, color, entrelazado sin marcar) y media enlazada (AMA) | malevolisco/diagnostico-avid#19 |
| 4a | Origen del material por señales | malevolisco/diagnostico-avid#20 |
| 4b | Pestaña "Origen del material" | malevolisco/diagnostico-avid#21 |
| 5 | Fechas imposibles y reloj desfasado | malevolisco/diagnostico-avid#22 |
| 6 | OMF clip a clip y repaso de la documentación | el de este cambio |

Lo que no se puede comprobar sin un disco real está en "No verificable aquí", al final del diseño.

**Diseño:** `docs/diseno_cualquier_disco.md` (aprobado por Adrián el 23/10/2026: "me encaja"). Si este plan y el diseño no coinciden, manda el diseño.

**Restricciones comunes:**
- Destino XDCAM HD422 50 Mb/s 1080i25 16:9; el disco de origen solo se lee.
- Nada falla en silencio: lo que no se puede, se salta y se lista con el motivo.
- Cada parte se fusiona por separado, con su build de Windows en verde, el escaneo Opsera antes de cada commit y los commits a nombre de Adrián.
- El disco simulado y `comprobar_resultado.py` crecen con cada parte.

---

### Parte 1: audio por canales

**Ficheros:** `exportar_brutos.py`, `diagnostico_avid.py` (`agrupar_clips`), `tests/test_unitarios.py`, `tests/test_mxf.py`.

- `canales_clip(clip, mxf_por_ruta) -> [{"ruta", "flujo", "canal", "de"}]`: cada canal de audio del clip, en orden.
  - Primero el audio que vaya dentro del fichero de vídeo (MOV o MXF OP1a de cámara), luego los ficheros de audio por su orden.
  - En cada fichero, sus flujos de audio; en cada flujo, sus canales.
- `num_audio` de cada clip pasa a ser el número de canales (en la media de Avid, que es mono, no cambia nada).
- `hacer_segmento`:
  - cada pista del bruto sale de un canal (`pan=mono|c0=cN`), a 48 kHz;
  - con `apad` y `-t` igual a la duración del vídeo: el audio más corto se rellena con silencio y el más largo se corta, así que el sonido nunca se desplaza al unir;
  - el silencio sale de `anullsrc` en el mismo grafo;
  - las ganancias van en la misma cadena de filtros.
- `medir_audio(ffmpeg, ruta, flujo=0, canal=None)`: mide un canal concreto.
- Las pistas que pasen de 8 se avisan en el shotlist ("no entran las pistas 9… del clip", como ahora).

**Pruebas:**
- Unitarias de `canales_clip`:
  - Avid OP-Atom (mono por fichero) igual que antes;
  - un MOV con estéreo dentro, que da 2 canales;
  - un MOV con estéreo más un WAV de 5.1 aparte, que da 8.
- Con ffmpeg real:
  - estéreo con el canal izquierdo con tono y el derecho en silencio: sale la pista 1 con sonido y la 2 muda;
  - 5.1: salen 6 pistas, más 2 de silencio en XDCAM;
  - audio de 1 s con vídeo de 2 s: todas las pistas duran 2 s;
  - ficha técnica conforme.
- Lo de siempre: el disco simulado de principio a fin, reanudar, montaje y ventana.

### Parte 2: vídeo girado, HDR y velocidad variable

- El perfil de vídeo añade rotación, color (primarias, transferencia, matriz) y velocidad (`r_frame_rate` frente a `avg_frame_rate`).
- `filtros_xdcam`:
  - `transpose` según la rotación;
  - HLG o PQ: `zscale` y `tonemap` a BT.709; si no hay `zscale`, sin ese paso y con aviso;
  - velocidad variable: `fps=50` con el reloj real del fichero y el audio con `aresample=async`.
- Si el campo es `unknown`, un muestreo corto con `idet`.
- La build de Windows comprueba que el ffmpeg trae `zscale`.
- Pruebas con ffmpeg real (vertical, HLG en HEVC de 10 bits, velocidad variable, 4K 50p, 23,976p, SD anamórfico) y los mismos casos en el disco simulado.

### Parte 3: media enlazada (AMA) y varias carpetas

- El análisis pasa ffprobe también por MOV, MP4, MXF, MTS, AVI y WAV fuera de `Avid MediaFiles` cuando un bin los enlaza.
- Para encontrar la media:
  1. la ruta del bin, cambiando la letra de unidad;
  2. si no está, por nombre y tamaño en el inventario.
- Página Disco: "Añadir otra carpeta de media…".
- Aviso de disco de Mac cuando no se puede leer o la media está y no se ve.
- Disco simulado: un MOV enlazado con estéreo dentro, que debe entrar en el bruto con sus 2 pistas.

### Parte 4: origen por señales y pantalla "Origen del material"

Se hace en dos entregas:

- **4a (lógica)**:
  - `clasificar(clip, cfg) -> (clase, motivo, seguro)` con las señales del diseño;
  - marca de cámara o móvil de los metadatos en el análisis;
  - `origen.json` (carpeta de brutos) y `config_brutos.json → reglas_origen` se aplican;
  - shotlist con las columnas Origen y Por qué;
  - ante la duda, entra marcado "para revisar";
  - disco simulado: proyecto ORIGEN.
- **4b (pantalla)**: pestaña "Origen del material" en Montaje (`ventana_origen.py`), que escribe `origen.json`, rehace el montaje y, si se marca "Recordar para otros discos", guarda `reglas_origen`. La prueba de la ventana marca el bin "compras" como agencia y comprueba que su clip sale del bruto.

### Parte 5: fechas imposibles

Fecha de grabación antes de 2000, en el futuro o el 1/1 de fábrica: se ignora y se avisa. Además, aviso de reloj desfasado de más de 12 h.

Hecho (`fecha_imposible`): el 1 de enero solo cuenta como de fábrica si está a más de 2 días del volcado (Año Nuevo puede ser de verdad). Aviso en el informe y en la columna "Aviso fecha" del shotlist. Disco simulado: una fecha 01/01/2000 00:03 y un reloj 26 h adelantado en el proyecto ORIGEN.

### Parte 6: documentación y entrega final

`INSTRUCCIONES.txt`, `LEEME_tecnico.md` y `NOTAS_DESARROLLO.md` en cada parte; aquí el repaso final.

Hecho. Se añadió lo que faltaba del diseño: la media OMF también clip a clip (`marcar_offline_en_omf`: sus clips de bin salen offline, marcados "media en OMF"; disco simulado: "VIEJO OMF").
