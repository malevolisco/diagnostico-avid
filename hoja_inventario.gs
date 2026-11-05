/**
 * Buzón de la hoja de inventario de DiagnosticoAvid (Google Apps Script).
 *
 * Se pega una vez en la hoja (Extensiones → Apps Script) y se publica como aplicación web: ver INSTRUCCIONES.txt,
 * "Hoja de inventario". El programa le manda lo que analiza y exporta y le pregunta lo que habéis decidido vosotros.
 *
 * Reglas:
 *  - Una pestaña por gran proyecto (México, China…). Si no existe, se crea con el mismo formato y se añade al Resumen.
 *  - Una fila por proyecto de Avid: se reconoce por Disco + Proyecto.
 *  - El programa solo escribe sus columnas (cabecera azul). Las vuestras (cabecera ocre: ¿Merece la pena?, Motivo /
 *    notas, Quién) no se tocan nunca, ni las columnas que añadáis.
 *  - Un Estado puesto a mano como HECHO o Descartado no lo cambia el programa (salvo que se marque o desmarque HECHO
 *    desde el propio programa). Un análisis nuevo no baja un proyecto ya exportado a "Analizado".
 */

var VERSION = 2;
var CABECERA = ['Disco', 'Proyecto', 'Bins', 'Último cambio', 'Estado', 'Clips de cámara', 'Duración',
  'Lo que propone el programa', '¿Merece la pena?', 'Motivo / notas', 'Brutos exportados', 'Quién', 'Actualizado'];
var NUESTRAS = ['¿Merece la pena?', 'Motivo / notas', 'Quién'];
// campo que manda el programa → columna de la hoja
var CAMPOS = {disco: 'Disco', proyecto: 'Proyecto', bins: 'Bins', ultimo_cambio: 'Último cambio', estado: 'Estado',
  clips: 'Clips de cámara', duracion: 'Duración', propuesta: 'Lo que propone el programa', brutos: 'Brutos exportados',
  actualizado: 'Actualizado'};
var ESTADOS = ['Sin analizar', 'Analizado', 'Exportar de nuevo', 'Exportado', 'HECHO', 'Descartado'];
var DECISIONES = ['Merece la pena', 'Descartar: entradilla', 'Descartar: falso positivo', 'Descartar: solo agencias',
  'Descartar: duplicado de otro', 'Dudas'];
var RESUMEN = 'Resumen';
// El programa en la nube: qué hace cada PC, las órdenes y los datos (zip solo de texto) para revisar
var PROGRAMA = 'Programa';
var CAB_PROGRAMA = ['PC', 'Disco', 'Gran proyecto', 'Acepta órdenes', 'Actividad', 'Progreso', 'Último aviso', 'Versión', 'Visto'];
var ORDENES_PESTANA = 'Órdenes';
var CAB_ORDENES = ['Nº', 'Pedida', 'Orden', 'Proyectos', 'PC', 'Pedida por', 'Estado', 'Detalle', 'Actualizado'];
var ORDENES = ['Analizar', 'Exportar', 'Detener', 'Marcar HECHO', 'Sincronizar'];
var ESTADOS_ORDEN = ['Pendiente', 'En marcha', 'Hecha', 'Error', 'Rechazada', 'Cancelada'];
var CARPETA_DATOS = 'DiagnosticoAvid datos';

function doGet(e) {
  return responder({ok: true, version: VERSION, pestanas: pestanas(SpreadsheetApp.getActiveSpreadsheet())});
}

function doPost(e) {
  var cerrojo = LockService.getScriptLock();
  cerrojo.waitLock(30000);
  try {
    var p = JSON.parse(e.postData.contents);
    var libro = SpreadsheetApp.getActiveSpreadsheet();
    var gran = String(p.gran_proyecto || '').trim();
    var sinGran = ['probar', 'latido', 'orden', 'nueva_orden'];
    if (sinGran.indexOf(p.accion) < 0 && !gran) return responder({ok: false, error: 'Falta el gran proyecto (la pestaña).'});
    var r = {ok: true, version: VERSION};
    if (p.accion === 'actualizar') {
      var hoja = libro.getSheetByName(gran);
      if (!hoja || hoja.getLastRow() === 0) hoja = crearPestana(libro, gran, hoja);
      var cuenta = escribir(hoja, p.filas || []);
      r.nuevas = cuenta.nuevas;
      r.actualizadas = cuenta.actualizadas;
    } else if (p.accion === 'leer') {
      var h = libro.getSheetByName(gran);
      r.filas = h ? leerDecisiones(h.getDataRange().getValues()) : [];
    } else if (p.accion === 'latido') {
      latido(libro, p);
      r.ordenes = ordenesPendientes(libro);
    } else if (p.accion === 'orden') {
      var res = cambiarOrden(libro, p);
      if (!res.ok) return responder(res);
    } else if (p.accion === 'nueva_orden') {
      var nueva = nuevaOrden(libro, p);
      if (!nueva.ok) return responder(nueva);
      r.n = nueva.n;
    } else if (p.accion === 'datos') {
      r.fichero = guardarDatos(libro, gran, p);
    } else if (p.accion !== 'probar') {
      return responder({ok: false, error: 'Acción desconocida: ' + p.accion});
    }
    r.pestanas = pestanas(libro);
    return responder(r);
  } catch (err) {
    return responder({ok: false, error: String(err)});
  } finally {
    cerrojo.releaseLock();
  }
}

function responder(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}

function pestanas(libro) {
  return libro.getSheets().map(function (h) { return h.getName(); })
    .filter(function (n) { return [RESUMEN, PROGRAMA, ORDENES_PESTANA].indexOf(n) < 0; });
}

function clave(disco, proyecto) {
  return String(disco || '').trim().toLowerCase() + '|' + String(proyecto || '').trim().toLowerCase();
}

/** El Estado que queda: el del programa, salvo lo decidido a mano. */
function nuevoEstado(actual, nuevo, forzar) {
  actual = String(actual || '');
  if (!nuevo) return actual;
  if (forzar) return nuevo;
  if (actual === 'HECHO' || actual === 'Descartado') return actual;
  if (nuevo === 'Sin analizar' && actual) return actual;
  if (nuevo === 'Analizado' && (actual === 'Exportado' || actual === 'Exportar de nuevo')) return actual;
  return nuevo;
}

/**
 * Funde lo que manda el programa con lo que hay en la hoja. valores: getValues() de la pestaña (con la cabecera).
 * Devuelve {cabecera, filas, nuevas, actualizadas}: filas son todas las de datos (las de antes y las nuevas al final),
 * con todas las columnas; solo cambian las del programa.
 */
function fusionar(valores, filasPrograma) {
  var cabecera = (valores[0] || []).map(function (c) { return String(c).trim(); });
  if (!cabecera.join('')) {  // pestaña vacía (creada a mano): la cabecera entera
    cabecera = CABECERA.slice();
    valores = [cabecera];
  }
  for (var i = 0; i < CABECERA.length; i++) {  // una columna del programa borrada a mano se vuelve a poner al final
    if (cabecera.indexOf(CABECERA[i]) < 0 && NUESTRAS.indexOf(CABECERA[i]) < 0) cabecera.push(CABECERA[i]);
  }
  var col = {};
  cabecera.forEach(function (c, j) { if (!(c in col)) col[c] = j; });
  var filas = valores.slice(1).map(function (f) {
    var g = f.slice();
    while (g.length < cabecera.length) g.push('');
    return g;
  });
  var donde = {};
  filas.forEach(function (f, j) {
    var k = clave(f[col['Disco']], f[col['Proyecto']]);
    if (f[col['Proyecto']] !== '' && !(k in donde)) donde[k] = j;
  });
  var nuevas = 0, actualizadas = 0;
  filasPrograma.forEach(function (p) {
    if (!p || !p.proyecto) return;
    var k = clave(p.disco, p.proyecto);
    var f;
    if (k in donde) {
      f = filas[donde[k]];
      actualizadas++;
    } else {
      f = cabecera.map(function () { return ''; });
      filas.push(f);
      donde[k] = filas.length - 1;
      nuevas++;
    }
    Object.keys(CAMPOS).forEach(function (campo) {
      if (!(campo in p) || p[campo] === null || p[campo] === undefined) return;  // lo que no manda, no se toca
      var j = col[CAMPOS[campo]];
      if (campo === 'estado') f[j] = nuevoEstado(f[j], p.estado, p.forzar_estado);
      else f[j] = p[campo];
    });
  });
  return {cabecera: cabecera, filas: filas, nuevas: nuevas, actualizadas: actualizadas};
}

function escribir(hoja, filasPrograma) {
  var valores = hoja.getDataRange().getValues();
  var r = fusionar(valores, filasPrograma);
  var ancho = r.cabecera.length;
  // sitio para las filas y columnas nuevas (una hoja tiene un tamaño fijo hasta que se amplía)
  if (hoja.getMaxRows() < r.filas.length + 1) hoja.insertRowsAfter(hoja.getMaxRows(), r.filas.length + 1 - hoja.getMaxRows());
  if (hoja.getMaxColumns() < ancho) hoja.insertColumnsAfter(hoja.getMaxColumns(), ancho - hoja.getMaxColumns());
  if (r.cabecera.join('|') !== (valores[0] || []).map(String).join('|')) hoja.getRange(1, 1, 1, ancho).setValues([r.cabecera]);
  if (r.filas.length) {
    // columna a columna y solo las del programa: lo que escribís vosotros no se reescribe nunca
    r.cabecera.forEach(function (c, j) {
      if (NUESTRAS.indexOf(c) >= 0 || CABECERA.indexOf(c) < 0) return;
      hoja.getRange(2, j + 1, r.filas.length, 1).setValues(r.filas.map(function (f) { return [f[j]]; }));
    });
  }
  return r;
}

/** Lo que habéis decidido: [{disco, proyecto, estado, decision, notas, quien}]. */
function leerDecisiones(valores) {
  var cabecera = (valores[0] || []).map(function (c) { return String(c).trim(); });
  var j = function (n) { return cabecera.indexOf(n); };
  var texto = function (f, n) { return j(n) >= 0 && f[j(n)] !== null ? String(f[j(n)]).trim() : ''; };
  return valores.slice(1).filter(function (f) { return texto(f, 'Proyecto'); }).map(function (f) {
    return {disco: texto(f, 'Disco'), proyecto: texto(f, 'Proyecto'), estado: texto(f, 'Estado'),
      decision: texto(f, '¿Merece la pena?'), notas: texto(f, 'Motivo / notas'), quien: texto(f, 'Quién')};
  });
}

function letra(n) {
  var s = '';
  for (n = n + 1; n > 0; n = Math.floor((n - 1) / 26)) s = String.fromCharCode(65 + (n - 1) % 26) + s;
  return s;
}

/** Pestaña nueva de un gran proyecto, con el mismo formato que la de México, y su fila en el Resumen. */
function crearPestana(libro, gran, vacia) {
  var hoja = vacia || libro.insertSheet(gran);
  var n = CABECERA.length;
  hoja.getRange(1, 1, 1, n).setValues([CABECERA]).setFontColor('#FFFFFF').setFontWeight('bold').setWrap(true)
    .setVerticalAlignment('middle');
  CABECERA.forEach(function (c, j) {
    hoja.getRange(1, j + 1).setBackground(NUESTRAS.indexOf(c) >= 0 ? '#8A5A00' : '#1F3A5F');
  });
  [9, 38, 6, 13, 18, 10, 10, 38, 26, 50, 34, 12, 12].forEach(function (a, j) { hoja.setColumnWidth(j + 1, a * 7); });
  hoja.setRowHeight(1, 36);
  hoja.setFrozenRows(1);
  hoja.setFrozenColumns(2);
  if (hoja.getMaxRows() < 1001) hoja.insertRowsAfter(hoja.getMaxRows(), 1001 - hoja.getMaxRows());
  if (hoja.getMaxColumns() < n) hoja.insertColumnsAfter(hoja.getMaxColumns(), n - hoja.getMaxColumns());
  var filas = hoja.getMaxRows() - 1;
  var lista = function (valores) {
    return SpreadsheetApp.newDataValidation().requireValueInList(valores, true).setAllowInvalid(true).build();
  };
  var cE = CABECERA.indexOf('Estado') + 1, cI = CABECERA.indexOf('¿Merece la pena?') + 1;
  hoja.getRange(2, cE, filas, 1).setDataValidation(lista(ESTADOS));
  hoja.getRange(2, cI, filas, 1).setDataValidation(lista(DECISIONES));
  var E = letra(cE - 1), I = letra(cI - 1);
  var todo = hoja.getRange(2, 1, filas, n);
  var regla = function (formula, color) {
    return SpreadsheetApp.newConditionalFormatRule().whenFormulaSatisfied(formula).setBackground(color).setRanges([todo]).build();
  };
  hoja.setConditionalFormatRules([
    regla('=OR(LEFT($' + I + '2,9)="Descartar",$' + E + '2="Descartado")', '#D9D9D9'),
    regla('=$' + E + '2="HECHO"', '#A9D08E'),
    regla('=$' + E + '2="Exportado"', '#C6EFCE'),
    regla('=$' + E + '2="Exportar de nuevo"', '#F8CBAD'),
    regla('=$' + I + '2="Merece la pena"', '#FFF2CC')]);
  if (!hoja.getFilter()) hoja.getRange(1, 1, filas + 1, n).createFilter();
  anadirAlResumen(libro, gran, E, I);
  return hoja;
}

function anadirAlResumen(libro, gran, E, I) {
  var rs = libro.getSheetByName(RESUMEN);
  if (!rs) {
    rs = libro.insertSheet(RESUMEN, 0);
    rs.getRange(1, 1).setValue('Inventario de proyectos').setFontWeight('bold').setFontSize(14);
    rs.getRange(3, 1, 1, 11).setValues([['Gran proyecto', 'Proyectos', 'Sin analizar', 'Analizados', 'Exportar de nuevo',
      'Exportados', 'HECHOS', 'Merecen la pena', 'Descartados', 'Dudas', 'Sin decidir']])
      .setBackground('#1F3A5F').setFontColor('#FFFFFF').setFontWeight('bold').setWrap(true);
  }
  var ya = rs.getRange(1, 1, Math.max(rs.getLastRow(), 1), 1).getValues().some(function (f) { return f[0] === gran; });
  if (ya) return;
  var t = "'" + gran.replace(/'/g, "''") + "'!";
  var fila = Math.max(rs.getLastRow() + 1, 4);
  var cuenta = function (col, valor) { return '=COUNTIF(' + t + col + '2:' + col + '5000,"' + valor + '")'; };
  rs.getRange(fila, 1, 1, 11).setValues([[gran, '=COUNTA(' + t + 'B2:B5000)',
    cuenta(E, 'Sin analizar'), cuenta(E, 'Analizado'), cuenta(E, 'Exportar de nuevo'), cuenta(E, 'Exportado'),
    cuenta(E, 'HECHO'), cuenta(I, 'Merece la pena'),
    '=COUNTIF(' + t + I + '2:' + I + '5000,"Descartar*")+COUNTIFS(' + t + E + '2:' + E + '5000,"Descartado",' + t + I + '2:' + I + '5000,"")',
    cuenta(I, 'Dudas'), '=B' + fila + '-COUNTA(' + t + I + '2:' + I + '5000)']]);
}

// --- el programa en la nube --------------------------------------------------------------------------------------

function pestanaFija(libro, nombre, cabecera, listas) {
  var h = libro.getSheetByName(nombre);
  if (h) return h;
  h = libro.insertSheet(nombre);
  h.getRange(1, 1, 1, cabecera.length).setValues([cabecera]).setFontColor('#FFFFFF').setFontWeight('bold')
    .setBackground('#1F3A5F').setWrap(true);
  h.setFrozenRows(1);
  (listas || []).forEach(function (l) {
    var regla = SpreadsheetApp.newDataValidation().requireValueInList(l[1], true).setAllowInvalid(false).build();
    h.getRange(2, cabecera.indexOf(l[0]) + 1, Math.max(h.getMaxRows() - 1, 1), 1).setDataValidation(regla);
  });
  return h;
}

function ahora() {
  return Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd HH:mm');
}

/** Una fila por PC con lo que está haciendo ahora. */
function latido(libro, p) {
  var h = pestanaFija(libro, PROGRAMA, CAB_PROGRAMA);
  var pc = String(p.pc || '').trim();
  if (!pc) return;
  var fila = [pc, p.disco || '', p.gran_proyecto || '', p.acepta ? 'sí' : 'no', p.actividad || '', p.progreso || '',
    p.aviso || '', p.version || '', ahora()];
  var valores = h.getDataRange().getValues();
  for (var i = 1; i < valores.length; i++) {
    if (String(valores[i][0]).trim().toLowerCase() === pc.toLowerCase()) {
      h.getRange(i + 1, 1, 1, fila.length).setValues([fila]);
      return;
    }
  }
  if (h.getMaxRows() < valores.length + 1) h.insertRowsAfter(h.getMaxRows(), 1);
  h.getRange(valores.length + 1, 1, 1, fila.length).setValues([fila]);
}

function pestanaOrdenes(libro) {
  return pestanaFija(libro, ORDENES_PESTANA, CAB_ORDENES, [['Orden', ORDENES], ['Estado', ESTADOS_ORDEN]]);
}

/** Las órdenes pendientes: [{n, orden, proyectos, pc, quien}]. Una fila escrita a mano sin Nº ni Estado también
 * cuenta (se le pone el número). */
function ordenesPendientes(libro) {
  var h = pestanaOrdenes(libro);
  var v = h.getDataRange().getValues();
  var c = function (n) { return CAB_ORDENES.indexOf(n); };
  var siguiente = 1;
  for (var i = 1; i < v.length; i++) siguiente = Math.max(siguiente, (parseInt(v[i][c('Nº')], 10) || 0) + 1);
  var r = [];
  for (var j = 1; j < v.length; j++) {
    var f = v[j];
    if (!String(f[c('Orden')]).trim()) continue;
    var estado = String(f[c('Estado')]).trim();
    if (estado && estado !== 'Pendiente') continue;
    var n = parseInt(f[c('Nº')], 10);
    if (!n) {  // escrita a mano en la hoja: se numera y queda pendiente
      n = siguiente++;
      h.getRange(j + 1, c('Nº') + 1).setValue(n);
      if (!f[c('Pedida')]) h.getRange(j + 1, c('Pedida') + 1).setValue(ahora());
    }
    if (!estado) h.getRange(j + 1, c('Estado') + 1).setValue('Pendiente');
    r.push({n: n, orden: String(f[c('Orden')]).trim(), proyectos: String(f[c('Proyectos')]).trim(),
      pc: String(f[c('PC')]).trim(), quien: String(f[c('Pedida por')]).trim()});
  }
  return r;
}

function cambiarOrden(libro, p) {
  var h = pestanaOrdenes(libro);
  var v = h.getDataRange().getValues();
  var c = function (n) { return CAB_ORDENES.indexOf(n); };
  for (var i = 1; i < v.length; i++) {
    if (parseInt(v[i][c('Nº')], 10) !== Number(p.n)) continue;
    var actual = String(v[i][c('Estado')]).trim() || 'Pendiente';
    if (p.desde && actual !== p.desde) return {ok: false, error: 'La orden ' + p.n + ' ya está ' + actual + '.'};
    h.getRange(i + 1, c('Estado') + 1).setValue(p.estado);
    if (p.detalle !== undefined) h.getRange(i + 1, c('Detalle') + 1).setValue(p.detalle);
    if (p.pc) h.getRange(i + 1, c('PC') + 1).setValue(p.pc);
    h.getRange(i + 1, c('Actualizado') + 1).setValue(ahora());
    return {ok: true};
  }
  return {ok: false, error: 'No existe la orden ' + p.n + '.'};
}

function nuevaOrden(libro, p) {
  if (ORDENES.indexOf(p.orden) < 0) return {ok: false, error: 'Orden no válida: ' + p.orden + '. Valen: ' + ORDENES.join(', ')};
  var h = pestanaOrdenes(libro);
  var v = h.getDataRange().getValues();
  var n = 1;
  for (var i = 1; i < v.length; i++) n = Math.max(n, (parseInt(v[i][0], 10) || 0) + 1);
  var fila = v.length + 1;
  if (h.getMaxRows() < fila) h.insertRowsAfter(h.getMaxRows(), fila - h.getMaxRows());
  h.getRange(fila, 1, 1, CAB_ORDENES.length).setValues([[n, ahora(), p.orden, p.proyectos || '', p.pc || '',
    p.quien || '', 'Pendiente', '', ahora()]]);
  return {ok: true, n: n};
}

/** El zip de texto del programa, en la carpeta de datos junto a la hoja (DiagnosticoAvid datos/<gran>/<disco>/). */
function guardarDatos(libro, gran, p) {
  var raiz = DriveApp.getFileById(libro.getId()).getParents();
  var carpeta = raiz.hasNext() ? raiz.next() : DriveApp.getRootFolder();
  [CARPETA_DATOS, gran, String(p.disco || 'disco')].forEach(function (n) {
    var it = carpeta.getFoldersByName(n);
    carpeta = it.hasNext() ? it.next() : carpeta.createFolder(n);
  });
  var nombre = String(p.nombre || 'datos.zip').replace(/[\\/]/g, '_');
  var viejos = carpeta.getFilesByName(nombre);
  while (viejos.hasNext()) viejos.next().setTrashed(true);
  var blob = Utilities.newBlob(Utilities.base64Decode(p.zip_base64), 'application/zip', nombre);
  var f = carpeta.createFile(blob);
  return {id: f.getId(), nombre: nombre};
}

if (typeof module !== 'undefined') {  // para las pruebas (node), no en Google
  module.exports = {nuevoEstado: nuevoEstado, fusionar: fusionar, leerDecisiones: leerDecisiones, letra: letra,
    escribir: escribir, CABECERA: CABECERA, CAB_ORDENES: CAB_ORDENES};
}
