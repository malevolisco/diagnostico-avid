# Diseño: interfaz nueva al estilo DaVinci Resolve

Pedido por Adrián el 22/10/2026: "más vistosa y más user friendly, más parecido a DaVinci". Maquetas aprobadas en el canvas "DiagnosticoAvid — nueva interfaz" (privado de Adrián), con acento naranja.

## Objetivo

Quien usa el programa es la jefa de Adrián: sin conocimientos técnicos, en Windows, sin Adrián delante.

- **Vistoso**: tema oscuro profesional, como un programa de edición.
- **Fácil**: se ve qué toca hacer y en qué orden; el programa da los valores ya elegidos y solo se cambian si hace falta.
- Sin perder nada de lo que hace hoy: análisis, montaje, exportación, Detener, reanudar y actualización a mano.

Criterio de éxito: tu jefa abre el programa y, sin instrucciones, sabe que tiene que ir de **Disco** a **Montaje** a **Exportar**.

## Decisiones

- **Tecnología**: se sigue con tkinter, con un tema oscuro hecho a medida. No entran componentes nuevos (el `.exe` pesa lo mismo; para Ingeniería no cambia nada). Límite asumido: esquinas rectas, sin sombras ni degradados. Las maquetas ya están dibujadas así.
- **Una sola ventana con tres páginas** y una barra abajo, como la de páginas de Resolve: **1 · Disco**, **2 · Montaje**, **3 · Exportar**. La página activa va iluminada en el color de acento. Desaparecen las ventanas que se abrían encima (montaje, panel de opciones); solo quedan los diálogos de elegir carpeta y de confirmar.
- **Colores**:
  - fondo `#18191b`; paneles `#1e1f22` y `#232427`; campos `#141517`; bordes `#333539` y `#44474c`;
  - texto `#e6e7e9`; texto secundario `#a3a7ad`;
  - acento **naranja `#e8833a`** (botón principal de cada página, cabezal de la línea de tiempo, página activa, clips movidos a mano);
  - aviso rojo `#ff8a80`; correcto verde `#6fdc9b`.

  Todo con contraste suficiente para leerse bien.
- **Letra**: Segoe UI (la de Windows); Consolas para timecodes y registro.
- **Principio de los valores elegidos**: en vez de explicar una regla, se muestra el valor ya escogido y se puede cambiar con un clic. Ejemplo: pistas de audio con dos botones, **4 pistas | 8 pistas**, y marcado el que corresponde al material. Lo poco habitual va en **"Opciones avanzadas…"**.

## Página 1 · Disco

- Cabecera: nombre del programa, build, "solo lectura del disco de origen" y "Buscar actualización" (solo en el `.exe`).
- Bloque **Carpetas**: Disco de Avid, Informe y Carpeta de brutos, cada uno con su "Elegir…".
- **Proyectos**: un botón con el texto "Proyectos: 12 de 108…" que abre la lista para marcar (diálogo con el mismo tema).
- **Botón principal "Analizar disco"** (naranja, grande) y "Detener". Debajo, barra de progreso y registro.
- Panel derecho **"Último análisis de este disco"**: fecha, proyectos, clips, horas de material y brutos previstos, con "Abrir el informe". Y **"Siguiente paso"**, que lleva a Montaje.
- Opciones avanzadas (plegadas):
  - "Sin miniaturas";
  - "Zip sin miniaturas";
  - "Analizar y exportar seguido", que es lo que hoy hace la casilla 3 de la ventana: analiza y exporta del tirón sin pasar por el montaje.

## Página 2 · Montaje

Es la pantalla de montaje actual, metida en la ventana y con el tema nuevo. La lógica (`montaje.py`) no cambia.

- Cabecera: selector de proyecto y "Guardar" (naranja; avisa de "Cambios sin guardar").
- Izquierda:
  - **Brutos**: nombre, duración y clips; en rojo los que pasan de 2 h; ✎ si el nombre se cambió a mano;
  - **Papelera** con "Recuperar";
  - **Clips nuevos sin colocar**, si los hay;
  - **"Fuera del bruto"**: lo que se descarta del proyecto y por qué (efecto, agencia, importado). Es solo para consultar.
- Centro: **visor** grande con controles (retroceder, avance, adelantar), **timecode en grande en naranja** y velocidad.
- Derecha: **ficha del clip**: nombre, fecha de grabación, TC original, duración, formato, audio (pistas y sonoridad) y montaje (automático o movido a mano).
- Fila de acciones: Quitar, Subir, Bajar, ← Bruto anterior, Bruto siguiente →, Cortar aquí, Deshacer, y zoom − / +.
- Abajo: **línea de tiempo** con regla de timecodes, clips en azul (orden cronológico) o naranja (movidos a mano), cabezal rojo, y una leyenda.
- Sin análisis de ese disco, la página dice qué hacer ("Primero analiza el disco en 1 · Disco") en vez de estar vacía.

## Página 3 · Exportar

Sustituye al panel de opciones, que hoy es una ventana aparte.

- **Izquierda, ajustes de exportación** con los valores recomendados ya puestos:
  - Formato: XDCAM HD422 50 Mb/s 1080i25 16:9 (recomendado).
  - TC de inicio: 00:00:00:00.
  - Audio: **4 pistas | 8 pistas**, marcado el que corresponde al material.
  - Niveles: Sin tocar (recomendado) / Ganancia fija por pista / Normalizar a −23 LUFS (altera el original).
  - Comprobaciones: ficha técnica, MD5 y medidor de audio, marcadas.
  - **"Opciones avanzadas…"**: enrutado de pistas, ganancia por pista, prefijo, formato mayoritario, "Guardar como predeterminados" y "Restablecer recomendados".
- **Centro, cola de exportación**: tabla con cada bruto (casilla para incluirlo, nombre, duración, estado y formato).
  - Durante la exportación cada fila muestra su avance ("Convirtiendo · clip 18 de 40" y barra) y al acabar "Conforme · MD5", o "NO CONFORME" en rojo.
  - **Los brutos de piezas vienen desmarcados**: no se exportan salvo que se marquen (Adrián: "déjalas como bruto aparte, pero siempre se va a descartar").
- Tarjetas **Espacio** (necesario frente a libre, con barra; en rojo si no cabe) y **Tiempo** (estimado tras el primer bruto).
- **Botón principal "Exportar N brutos"** (naranja) y "Detener". Con errores (TC no válido, nombres repetidos, sin espacio) el botón se desactiva y dice por qué, como ahora.

## Comportamiento común

- La barra de páginas siempre se ve. Mientras analiza o exporta se puede mirar las otras páginas, pero no lanzar nada más ni cambiar el montaje.
- Cerrar la ventana mientras trabaja pregunta antes, como ahora. Detener sigue igual.
- Ventana de 1280×800 al abrir (mínimo 1100×720), con todo dentro.
- Atajos: Ctrl+Z deshacer y flechas ±1 s en Montaje; Ctrl+S guardar el montaje.

## Cambios en la lógica (pocos)

- **`exportar_piezas`** en `config_brutos.json`: `false` por defecto. En la línea de comandos y en la exportación de "Analizar y exportar seguido" tampoco salen los brutos de piezas salvo que se ponga `true`. En la ventana, la casilla de cada bruto manda.
- `exportar_todo` acepta la lista de brutos elegidos en la cola (`solo_brutos`) y avisa del avance de cada bruto con datos, no solo con texto: estado, clip actual de un total, y conforme/MD5 al terminar. Así la tabla se actualiza sin leer el registro.
- "Fuera del bruto": `montaje.py` da, por proyecto, los clips descartados y su motivo (render de efecto, agencia, importado), con las mismas reglas que ya se usan (`listas_por_proyecto`, `es_importado`).

## Estructura del código

- `tema.py` (nuevo): colores, letras y estilos ttk del tema oscuro, y pequeños componentes reutilizables (botón principal, botones de elección tipo 4 | 8, tarjeta, barra de páginas).
- `ventana_montaje.py`: `PantallaMontaje` pasa a ser un marco dentro de la ventana, no una ventana aparte. `PanelOpciones` pasa a la página Exportar, con la cola.
- `diagnostico_avid.py`: `lanzar_gui` construye la ventana con las tres páginas; la página Disco recoge lo que hoy es la ventana principal.
- Lo que no es ventana (análisis, exportación, montaje, ficha, MD5) no cambia.

## Pruebas

- **Unitarias**:
  - `exportar_piezas` (por defecto no sale el bruto de piezas; con `true`, sí);
  - `solo_brutos`;
  - eventos de avance de `exportar_todo`;
  - "Fuera del bruto" con efectos, agencias e importados.
- **Ventana real con pantalla virtual**, sobre el disco simulado:
  - recorrer las tres páginas;
  - analizar, montar (bajar, quitar, deshacer, guardar);
  - en Exportar: TC no válido → botón desactivado; piezas desmarcadas; exportar; la tabla acaba en "Conforme · MD5";
  - el botón de actualizar no sale fuera del `.exe`.

  Capturas de las tres páginas para compararlas con las maquetas.
- **Lo de siempre**: disco simulado de principio a fin, reanudar, `probar_montaje.py` y Windows en GitHub. El comprobador del disco simulado se ajusta: el bruto `15 SEPT CENTROAMERICA PIEZAS…` ya no se exporta por defecto.
- **No verificable aquí**: el aspecto exacto en Windows (el tema se prueba en Linux; en Windows la letra Segoe UI cambia algo las medidas). Se revisa con capturas de la ventana real en Windows tras la build.

## Fuera de alcance

- Pasar a Qt u otra tecnología.
- Sonido en el visor.
- Tema claro o elegir colores desde el programa.
