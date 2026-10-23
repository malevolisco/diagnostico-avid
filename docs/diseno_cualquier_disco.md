# Diseño: que valga para cualquier disco

Pedido por Adrián el 23/10/2026: "no quiero que me los hagas exactamente hechos para esos discos… necesito que me los hagas para cualquier disco que venga, con cualquier casuística".

## Cómo quedó (23/10/2026)

Hecho entero en seis partes (estado y PR de cada una en `docs/plan_cualquier_disco.md`). Diferencias con lo que dice este diseño, y por qué:

- **HDR**: se pasa a BT.709 con `zscale` de forma directa, sin `tonemap`. Medido con una imagen de prueba: ~30 dB de PSNR frente a la original, cuando con el mapeo de tonos clásico salía ~16 dB y sin convertir ~14 dB.
- **Velocidad variable**: no hizo falta `aresample=async`. Con `fps=50` y el audio ajustado a la duración del vídeo (`apad` + `-t`), las pruebas con ffmpeg real ya salían en sincronía.
- **Disco de Mac**: se avisa cuando la media enlazada tiene rutas de Mac (`/Volumes/…`, `Macintosh HD:…`) y no está en el disco. No se intenta leer HFS+/APFS.
- **OMF**: se cuenta y, clip a clip, los clips de bin con un `.omf` de su nombre salen offline marcados "media en OMF".
- **Origen**: un fichero importado sin ninguna pista se marca dudoso y entra en el bruto. En el disco de México, eso son unos 1.000 clips de "Mis vídeos" y los del Escritorio y discos externos, que antes se quedaban fuera. La pestaña "Origen del material" sirve para decidirlos por carpeta.

## Objetivo

Que el programa trate bien un disco de Avid que nadie ha visto antes, sin tener que añadirle reglas para cada corresponsalía. Hoy acierta con lo que conoce (México, el simulado, China), pero:

- Reconoce el material propio, las agencias y las tarjetas por **listas de nombres**. Lo que no está en la lista se clasifica mal y sin avisar.
- Hay formatos frecuentes que **no convierte bien**:
  - vídeo vertical de móvil (saldría tumbado);
  - HDR (colores lavados);
  - velocidad variable de los móviles (el audio se desincroniza);
  - estéreo en un solo fichero (se pierde un canal).
- **No ve** la media enlazada (AMA), que está fuera de `Avid MediaFiles`.

## Criterio de éxito

- Un disco con cualquier mezcla de cámaras, formatos y orígenes da brutos conformes, sin perder material propio en silencio.
- Lo que el programa no puede saber lo pregunta una vez, de forma agrupada.
- Lo que no puede hacer lo dice, con el motivo.

## Decisiones

- **Principio**: se decide por lo que el material es (lo que dice el propio fichero), no por cómo se llama. Los nombres solo sirven como pista.
- **Ante la duda, entra** (Adrián, 23/10/2026, opción A): un clip del que no se sabe si es propio o de fuera entra en el bruto marcado "para revisar". No se pierde material propio aunque nadie lo revise. Lo que sí se sabe que es de fuera (agencia, descarga, móvil, efectos de Avid) se queda fuera, como ahora.
- Nada falla en silencio: lo que no se puede leer o convertir se salta, se lista con el motivo y lo demás sigue (ya es así; se amplía a los casos nuevos).
- Las listas actuales (cámaras, agencias, carpetas de tarjeta) se mantienen como pistas y se pueden ampliar en `config_brutos.json`. Ya no son lo único que decide.

## 1. Medir el material: un perfil técnico por clip

En el análisis, ffprobe ya lee cada fichero. Se añade lo que hoy no se mira:

| Qué | De dónde | Para qué |
|---|---|---|
| Rotación | `side_data` *Display Matrix* / etiqueta `rotate` | Enderezar el vídeo vertical u horizontal girado |
| Color y HDR | `color_primaries`, `color_transfer`, `color_space` (bt2020, arib-std-b67 = HLG, smpte2084 = PQ) | Pasar HDR a BT.709 sin que salga lavado |
| Velocidad | `r_frame_rate` frente a `avg_frame_rate`, y duración frente a número de imágenes | Detectar velocidad variable (móviles) |
| Entrelazado | `field_order`; si dice `unknown`, un muestreo corto con `idet` | Saber si hay que separar campos |
| Tamaño y aspecto | ancho, alto, SAR y DAR | Encajar sin deformar, también 4K y anamórfico |
| Audio | canales y su distribución (mono, estéreo, 5.1), muestreo, formato y duración de cada fichero | No perder canales ni sincronía |

El informe muestra un resumen por formato ("12 clips verticales", "3 clips HDR HLG", "40 clips con velocidad variable") para que se vea antes de exportar.

## 2. Convertir cualquier cosa a XDCAM HD422 1080i25

Mismo destino que ahora, con las conversiones según el perfil:

- **Rotación**: se endereza (`transpose`). El vertical queda centrado con bandas negras a los lados, sin deformar.
- **HDR (HLG o PQ, BT.2020)**: se pasa a BT.709 con `zscale` y `tonemap`. Si no se puede, se convierte sin ese paso y el clip lleva el aviso "HDR sin ajustar".
- **Velocidad variable**: se fija a imágenes constantes con el reloj real del fichero y el audio se ajusta a ese reloj, para que no se desincronice.
- **Cadencias**:
  - 23,976, 24, 29,97, 30, 50 y 59,94, entrelazado o progresivo: a 25i con la duración real, sin cambiar la velocidad (se repiten o se quitan imágenes, como ahora con el NTSC). Así el bruto dura lo mismo que el original y el TC sigue al material.
  - 1080i25 con el campo superior primero: sin separar campos (ya está hecho).
- **4K, 2K, 720p y SD**: se escala a 1920×1080 con el aspecto de pantalla correcto.
- **Audio**: **cada canal de cada fichero es una pista mono del bruto**.
  - Un estéreo en un solo fichero da 2 pistas; un 5.1 da 6.
  - Todo a 48 kHz y 24 bits.
  - Si un clip trae más de 8 canales, entran los 8 primeros y se avisa en el shotlist.
  - Si un fichero de audio es más corto que el vídeo, se rellena con silencio; si es más largo, se corta al vídeo.
  - El 4 | 8 pistas de la página Exportar se calcula con estos canales.
- Lo que ffmpeg no sabe leer (códec raro, fichero dañado) queda como ERROR con el motivo, como ahora.

## 3. Encontrar la media esté donde esté

- **Media enlazada (AMA)**: los bins guardan la ruta del fichero original (MOV, MP4, MXF de tarjeta). Si ese fichero está en el disco, se analiza y el clip entra como uno más (origen "enlazado"). Se busca:
  1. en la ruta exacta, cambiando la letra de unidad por la del disco analizado;
  2. si no está, por nombre y tamaño en el inventario del disco.
- **Varios discos**: en Disco, "Añadir otra carpeta de media…" (opcional) para cuando la media está repartida. Se busca en todas. Sigue siendo solo lectura.
- **Disco de Mac (HFS+/APFS)**: Windows no lo lee sin un programa aparte. Si el disco no se puede abrir, o solo se ven carpetas vacías donde el bin dice que hay media, se dice claramente: "Parece un disco de Mac: hace falta leerlo en un Mac o con un programa como HFSExplorer/Paragon". No se intenta escribir nada.
- **OMF antiguo**: ffmpeg no lee la media OMF de Avid. Se cuenta y se avisa ("N ficheros OMF: no se pueden exportar con este programa"), como ahora, pero también por clip.

## 4. Origen del material: decidir con señales y preguntar lo dudoso

### Señales (de más a menos fiable)

1. **Lo que dice el fichero**:
   - marca o modelo de cámara en los metadatos (Sony, Canon, Panasonic, DJI, GoPro, iPhone…);
   - TC de cámara;
   - etiquetas de agencia (Reuters, AP, EBU…).
2. **De dónde se importó** (la ruta que guarda el bin):
   - estructura de tarjeta (CONTENTS, XDROOT, DCIM… y cualquier carpeta que contenga `.XML`/`.BIM` de cámara);
   - Descargas, Escritorio, WhatsApp…
3. **El bin**: nombre (agencias, compras, señal, internet…) y columnas del bin.
4. **El nombre del fichero**: listas actuales, como pista.

Cada clip acaba en una de estas clases:
- **material propio**;
- **agencia**;
- **importado de fuera** (internet, móvil ajeno);
- **efecto de Avid**;
- **dudoso**.

### Pantalla "Origen del material"

Va en la página Montaje, como pestaña junto a los brutos.

- Agrupa por procedencia: la carpeta de la que se importó (o "capturado", o "sin ruta"), el bin y la clase.
- Cada grupo muestra:
  - un ejemplo de nombres;
  - nº de clips y duración;
  - la propuesta del programa y por qué ("metadatos: Sony FX6", "carpeta Descargas", "bin 'agencias'", "no se sabe");
  - un selector: **Entra** / **Fuera: agencia** / **Fuera: importado**.
- Los dudosos salen arriba, marcados **Entra · para revisar**.
- Lo elegido se guarda:
  - en `origen.json` (carpeta de brutos), para ese disco;
  - si se marca "Recordar para otros discos", como regla en `config_brutos.json` (por ejemplo "carpeta …\Compras\ = agencia"). Así el programa aprende sin tocar el código.
- El shotlist lleva una columna "Origen" con la clase y "revisado" o "para revisar".

## 5. Fechas que no cuadran

- **Fecha de grabación imposible** (antes de 2000, después de hoy, o el 1/1 de fábrica de las cámaras con el reloj sin poner): se ignora para ordenar, se usa la del volcado y se avisa en el informe y en el shotlist.
- **Reloj de cámara desfasado** (como en China, un día de diferencia): se ordena por la de grabación, como ahora. El informe dice cuántos clips difieren más de 12 horas de su volcado, para que se vea.
- **Clips sin ninguna fecha**: van al bruto del proyecto sin fecha, como ahora.

## 6. Disco de pruebas "de todo"

El disco simulado añade, generado con ffmpeg:

- **Vídeo**:
  - vertical de móvil (rotación 90);
  - HLG BT.2020 10 bits en HEVC;
  - velocidad variable;
  - 4K 50p en H.264;
  - 23,976p en ProRes;
  - SD PAL 4:3 anamórfico.
- **Audio**: estéreo en un solo fichero, 5.1, 44,1 kHz y audio más corto que el vídeo.
- **Media y origen**:
  - un MOV enlazado (AMA) fuera de `Avid MediaFiles`;
  - un clip con marca de cámara en los metadatos y un nombre que no está en ninguna lista (debe entrar como propio);
  - uno sin ninguna pista de origen (debe entrar como "para revisar");
  - un bin "compras" (debe salir como dudoso, no como agencia, hasta que se marque).
- **Fechas**: una fecha de grabación imposible.

Se comprueba en Linux y en el Windows de GitHub en cada build:
- todos los brutos conformes;
- vertical derecho (relación de aspecto en la ficha);
- número de pistas de audio esperado;
- duración igual a la del original;
- el dudoso dentro y marcado;
- el AMA dentro.

## Fuera de alcance

- Leer discos de Mac desde Windows: solo se detecta y se explica.
- Reconstruir secuencias.
- Media en almacenamiento compartido (Avid NEXIS).
- Exportar otros formatos que no sean XDCAM HD422 (sigue la opción "formato mayoritario" que ya existe).

## Orden de trabajo

Por lo que más material salva o estropea:
1. audio por canales;
2. rotación, HDR y velocidad variable;
3. media enlazada (AMA) y varias carpetas;
4. origen por señales y pantalla "Origen del material";
5. fechas imposibles;
6. disco de Mac.

El disco de pruebas crece con cada paso. Cada paso se puede fusionar por separado, con su build de Windows en verde.

## No verificable aquí

- Cómo se ven en el monitor del Archivo el HDR pasado a BT.709 y el vertical con bandas.
- El comportamiento con media AMA real de Avid (rutas guardadas por cada versión).
- Si el ffmpeg 7.1.1 de Windows trae `zscale` (la build de GitHub lo comprobará y, si no, el HDR va con aviso).
