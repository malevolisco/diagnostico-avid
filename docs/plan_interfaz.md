# Plan: interfaz nueva al estilo DaVinci Resolve

**Objetivo:** pasar a una ventana oscura con tres páginas, sin cambiar lo que hacen el análisis, el montaje y la exportación.

**Diseño:** `docs/diseno_interfaz.md`. Si este plan y el diseño no coinciden, manda el diseño.

**Arquitectura:**
- `tema.py`: colores, estilos ttk y componentes de interfaz.
- `interfaz.py`: la ventana y sus páginas Disco, Montaje y Exportar.
- `ventana_montaje.PantallaMontaje` pasa a ser un marco dentro de una página.
- `diagnostico_avid.lanzar_gui` solo llama a `interfaz.lanzar(modulo)`. Se le pasa el propio módulo para no importarlo dos veces cuando se ejecuta como `__main__`.

**Restricciones:**
- tkinter sin dependencias nuevas.
- Acento `#e8833a`.
- Segoe UI en Windows; si no está, la letra por defecto.
- Las ventanas existentes no pueden quedar rotas a medio camino: cada tarea termina con todo funcionando.
- Análisis de seguridad antes de cada commit.

## Puntos de atención

1. Si se cambia de página con el montaje sin guardar, se pregunta si guardar (sí / no / cancelar).
2. Si la página Montaje o Exportar se abre sin análisis del disco elegido, la página explica qué hacer y no falla.
3. Mientras se analiza o se exporta, Montaje no se puede editar: se ve el aviso "Trabajando…".
4. Los eventos de avance llegan desde el hilo de exportación. Pasan por la cola y nunca tocan tkinter fuera del hilo principal.
5. Un proyecto sin brutos, o un bruto de 0 clips, no rompe la cola.

---

### Tarea 1: lógica (piezas, cola, avance y "Fuera del bruto")

**Ficheros:** `exportar_brutos.py`, `montaje.py`, `diagnostico_avid.py`, `tests/test_unitarios.py`, `tests/test_montaje.py`, `tests/comprobar_resultado.py`, `tests/probar_montaje.py`.

**Cambios:**
- `CONFIG_DEFECTO["exportar_piezas"] = False`.
- `exportar_todo(..., solo_brutos=None, evento=None)`:
  - quita los grupos de tipo `piezas` salvo que `exportar_piezas` sea verdadero o el bruto esté en `solo_brutos`;
  - si se pasa `solo_brutos`, solo exporta esos.
- `exportar_proyecto(..., evento=None)`:
  - `evento(dict)` con `{"bruto", "estado": "empezando"|"convirtiendo"|"uniendo"|"comprobando"|"terminado"|"reanudado", "clip", "total"}`;
  - al terminar, `res` lleva `conforme` y `md5`.
- `descartados_por_proyecto(clips, cruce, mxf_por_ruta, cfg) -> {proyecto: [(clip, motivo)]}`:
  - motivo "efecto de Avid", "agencia" o "importado";
  - mismas reglas que `listas_por_proyecto`.
- `Montaje.fuera_del_bruto(p)`. `Montaje.desde_analisis` lo rellena.
- `exportar_desde_analisis(..., solo_brutos=None, evento=None)`.
- `resumen_exportacion` devuelve además `filas`: `[{nombre, tipo, segundos, pistas}]`.

**Pruebas:**
- Por defecto no sale el bruto de piezas; con `exportar_piezas` verdadero o en `solo_brutos`, sí.
- Los eventos llegan en orden para un bruto (con ffmpeg simulado).
- `descartados_por_proyecto` con un render, una agencia, un importado y un fichero de tarjeta (este último, fuera de la lista).
- Se actualizan las expectativas:
  - `comprobar_resultado.py`: sin el bruto `… PIEZAS …` en `proyecto_mes`, y sin `_PIEZAS` en `proyecto`;
  - `probar_montaje.py`: sin piezas.

### Tarea 2: `tema.py`

- Funciones:
  - `aplicar(raiz) -> dict` con letras y colores, sobre el tema `clam` de ttk;
  - `colores_tk()` para Listbox, Text y Canvas;
  - componentes `BotonPrincipal`, `Seleccion` (botones de elección del tipo 4 | 8), `Tarjeta` (marco con título) y `BarraPaginas` (las tres pestañas de abajo, con la activa marcada).
- Estilos:
  - `Fondo`, `Panel` y `Tarjeta` para los marcos;
  - `Titulo`, `Seccion` (mayúsculas pequeñas), `Suave`, `Aviso` y `Bien` para los textos;
  - `Principal` (naranja) y `Fantasma` para los botones;
  - `Treeview` oscuro y barra de progreso naranja.
- Prueba: aplicar el tema en una ventana de pantalla virtual y crear cada componente.

### Tarea 3: `interfaz.py` (ventana y página Disco)

- Cabecera:
  - nombre, build y "solo lectura del disco de origen";
  - "Buscar actualización", solo si `puede_actualizarse()`.
- Página Disco:
  - tarjeta de carpetas;
  - botón "Proyectos: N de M…" (`dialogo_proyectos` con el tema);
  - "Analizar disco" y "Detener", barra de progreso y registro;
  - tarjeta "Último análisis" desde `resumen.json` del último análisis (`buscar_analisis`);
  - "Siguiente paso";
  - opciones avanzadas plegadas: miniaturas, zip sin miniaturas y "Analizar y exportar seguido".
- La cola de mensajes, Detener, cerrar con la X y actualizar funcionan igual que ahora (se mueven de `lanzar_gui`).
- `lanzar_gui` queda en tres líneas.

### Tarea 4: página Montaje

- `PantallaMontaje(contenedor, ...)` pasa a ser un marco.
- Se reorganiza como en la maqueta:
  - izquierda: brutos, papelera, nuevos y "Fuera del bruto";
  - centro: visor, controles y timecode naranja;
  - derecha: ficha del clip;
  - debajo: fila de acciones y línea de tiempo.
- Guardar va en la cabecera de la página, con "Cambios sin guardar".
- `interfaz` crea el montaje al entrar a la página, con `montaje_de` del análisis actual.

### Tarea 5: página Exportar

- Izquierda:
  - formato;
  - TC;
  - `Seleccion` 4 | 8, marcada con `pistas_salida(máximo)`;
  - niveles;
  - comprobaciones;
  - "Opciones avanzadas…", que abre `PanelOpciones` (el panel de opciones actual, con el tema) y al cerrarse actualiza la página.
- Centro:
  - cola en un Treeview, con casilla, bruto, duración, estado y formato; las piezas, sin marcar;
  - tarjetas Espacio y Tiempo;
  - "Exportar N brutos" y "Detener", con el motivo si el botón está desactivado.
- Los eventos de avance actualizan las filas. Al terminar cada bruto sale "Conforme · MD5" o "NO CONFORME".

### Tarea 6: pruebas de la ventana, documentación y entrega

- Prueba en pantalla virtual (script en el scratchpad): recorrer las tres páginas, analizar, montar, exportar y comprobar estados; capturas de las tres páginas.
- Documentación: `INSTRUCCIONES.txt` (CRLF), `LEEME_tecnico.md` y `NOTAS_DESARROLLO.md`.
- Pruebas unitarias, disco simulado de principio a fin, reanudar, `probar_montaje.py`, análisis de seguridad, commit, push y Windows. Fusionar cuando esté en verde.
