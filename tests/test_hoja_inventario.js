// Pruebas del buzón de la hoja (hoja_inventario.gs) con node: node tests/test_hoja_inventario.js
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const ctx = {module: {exports: {}}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', 'hoja_inventario.gs'), 'utf8'), ctx);
const g = ctx.module.exports;
const CAB = g.CABECERA;
const fila = (o) => CAB.map((c) => o[c] === undefined ? '' : o[c]);
let n = 0;
const prueba = (nombre, f) => { f(); n++; console.log('ok -', nombre); };

prueba('estado: lo puesto a mano se respeta', () => {
  assert.strictEqual(g.nuevoEstado('HECHO', 'Exportado', false), 'HECHO');
  assert.strictEqual(g.nuevoEstado('Descartado', 'Analizado', false), 'Descartado');
  assert.strictEqual(g.nuevoEstado('HECHO', 'Exportado', true), 'Exportado');
  assert.strictEqual(g.nuevoEstado('Exportado', 'Analizado', false), 'Exportado');
  assert.strictEqual(g.nuevoEstado('Analizado', 'Sin analizar', false), 'Analizado');
  assert.strictEqual(g.nuevoEstado('', 'Sin analizar', false), 'Sin analizar');
  assert.strictEqual(g.nuevoEstado('Exportado', 'Exportar de nuevo', false), 'Exportar de nuevo');
  assert.strictEqual(g.nuevoEstado('Analizado', '', false), 'Analizado');
});

prueba('fusionar: nunca toca vuestras columnas y añade los nuevos al final', () => {
  const valores = [CAB.slice(),
    fila({'Disco': 'DISCO 1', 'Proyecto': 'ABORTO', 'Estado': 'Exportar de nuevo', '¿Merece la pena?': 'Merece la pena', 'Motivo / notas': 'ojo', 'Quién': 'A'}),
    fila({'Disco': 'DISCO 1', 'Proyecto': 'CHAVELA VARGAS', 'Estado': 'HECHO'})];
  const r = g.fusionar(valores, [
    {disco: 'disco 1 ', proyecto: 'aborto', estado: 'Exportado', brutos: 'ABORTO · 39 clips', clips: 63, notas: 'NO', quien: 'NO'},
    {disco: 'DISCO 1', proyecto: 'CHAVELA VARGAS', estado: 'Exportado'},
    {disco: 'DISCO 2', proyecto: 'NUEVO', estado: 'Sin analizar', bins: 2}]);
  assert.strictEqual(r.nuevas, 1);
  assert.strictEqual(r.actualizadas, 2);
  const c = (f, n) => f[r.cabecera.indexOf(n)];
  assert.strictEqual(c(r.filas[0], 'Estado'), 'Exportado');
  assert.strictEqual(c(r.filas[0], 'Brutos exportados'), 'ABORTO · 39 clips');
  assert.strictEqual(c(r.filas[0], 'Proyecto'), 'aborto');  // el nombre que manda el programa
  assert.strictEqual(c(r.filas[0], 'Motivo / notas'), 'ojo');
  assert.strictEqual(c(r.filas[0], 'Quién'), 'A');
  assert.strictEqual(c(r.filas[0], '¿Merece la pena?'), 'Merece la pena');
  assert.strictEqual(c(r.filas[1], 'Estado'), 'HECHO');
  assert.strictEqual(c(r.filas[2], 'Proyecto'), 'NUEVO');
  assert.strictEqual(c(r.filas[2], 'Bins'), 2);
  assert.strictEqual(c(r.filas[2], 'Motivo / notas'), '');
});

prueba('fusionar: columnas cambiadas de sitio o añadidas a mano', () => {
  const cab = ['Proyecto', 'Mi columna', 'Disco', 'Estado', '¿Merece la pena?'];
  const r = g.fusionar([cab, ['X', 'mío', 'D', 'Analizado', 'Dudas']], [{disco: 'D', proyecto: 'X', estado: 'Exportado', duracion: '0:10:00'}]);
  assert.deepStrictEqual(r.cabecera.slice(0, 5), cab);
  const c = (n) => r.filas[0][r.cabecera.indexOf(n)];
  assert.strictEqual(c('Mi columna'), 'mío');
  assert.strictEqual(c('Estado'), 'Exportado');
  assert.strictEqual(c('Duración'), '0:10:00');  // la columna del programa que faltaba, al final
  assert.ok(r.cabecera.indexOf('Motivo / notas') < 0);  // las vuestras no se inventan
});

prueba('fusionar: pestaña vacía', () => {
  const r = g.fusionar([['']], [{disco: 'D', proyecto: 'X', estado: 'Analizado'}]);
  assert.deepStrictEqual(r.cabecera, CAB);
  assert.strictEqual(r.nuevas, 1);
});

prueba('escribir: solo las columnas del programa', () => {
  const escritas = [];
  const valores = [CAB.slice(), fila({'Disco': 'D', 'Proyecto': 'X', 'Motivo / notas': 'nota'})];
  const tam = {filas: 2, cols: 13};  // una hoja con el sitio justo: hay que ampliarla para la fila nueva
  const hoja = {getDataRange: () => ({getValues: () => valores}),
    getMaxRows: () => tam.filas, getMaxColumns: () => tam.cols,
    insertRowsAfter: (tras, n) => { assert.strictEqual(tras, tam.filas); tam.filas += n; },
    insertColumnsAfter: (tras, n) => { tam.cols += n; },
    getRange: (r, c, nr, nc) => {
      assert.ok(r + nr - 1 <= tam.filas && c + nc - 1 <= tam.cols, 'fuera de la hoja: fila ' + (r + nr - 1));
      return {setValues: (v) => escritas.push({r, c, nr, nc, v})};
    }};
  g.escribir(hoja, [{disco: 'D', proyecto: 'X', estado: 'Analizado'}, {disco: 'D', proyecto: 'Y', estado: 'Analizado'}]);
  const cols = escritas.map((e) => CAB[e.c - 1]);
  for (const nuestra of ['¿Merece la pena?', 'Motivo / notas', 'Quién']) assert.ok(cols.indexOf(nuestra) < 0, nuestra);
  assert.ok(escritas.every((e) => e.r === 2 && e.nr === 2 && e.nc === 1));
  assert.strictEqual(tam.filas, 3);
});

prueba('leer: vuestras decisiones', () => {
  const valores = [CAB.slice(), fila({'Disco': 'D', 'Proyecto': 'X', 'Estado': 'HECHO', '¿Merece la pena?': 'Descartar: entradilla', 'Quién': 'B'}),
    fila({})];
  const d = g.leerDecisiones(valores);
  assert.strictEqual(d.length, 1);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(d[0])), {disco: 'D', proyecto: 'X', estado: 'HECHO', decision: 'Descartar: entradilla', notas: '', quien: 'B'});
});

prueba('letra de columna', () => {
  assert.strictEqual(g.letra(0), 'A');
  assert.strictEqual(g.letra(4), 'E');
  assert.strictEqual(g.letra(26), 'AA');
});

// Una hoja de Google simulada, con su tamaño fijo y lo justo de la API, para recorrer el buzón entero (doPost)
function libroFalso() {
  const cadena = () => new Proxy({}, {get: (o, k) => (k === 'build' ? () => ({}) : () => cadena())});
  class Hoja {
    constructor(nombre) { this.nombre = nombre; this.celdas = {}; this.filas = 1000; this.cols = 26; this.filtro = null; }
    getName() { return this.nombre; }
    getMaxRows() { return this.filas; }
    getMaxColumns() { return this.cols; }
    insertRowsAfter(t, n) { this.filas += n; }
    insertColumnsAfter(t, n) { this.cols += n; }
    getLastRow() { return Math.max(0, ...Object.keys(this.celdas).filter((k) => this.celdas[k] !== '').map((k) => +k.split(',')[0])); }
    getLastColumn() { return Math.max(0, ...Object.keys(this.celdas).filter((k) => this.celdas[k] !== '').map((k) => +k.split(',')[1])); }
    getDataRange() { return this.getRange(1, 1, Math.max(this.getLastRow(), 1), Math.max(this.getLastColumn(), 1)); }
    getFilter() { return this.filtro; }
    getRange(r, c, nr = 1, nc = 1) {
      if (r < 1 || c < 1 || r + nr - 1 > this.filas || c + nc - 1 > this.cols) throw new Error('Rango fuera de la hoja: ' + [r, c, nr, nc]);
      const h = this;
      const rango = new Proxy({}, {get: (o, k) => {
        if (k === 'setValues') return (v) => { v.forEach((f, i) => f.forEach((x, j) => { h.celdas[(r + i) + ',' + (c + j)] = x; })); return rango; };
        if (k === 'setValue') return (x) => { h.celdas[r + ',' + c] = x; return rango; };
        if (k === 'getValues') return () => Array.from({length: nr}, (_, i) => Array.from({length: nc}, (_, j) => h.celdas[(r + i) + ',' + (c + j)] ?? ''));
        if (k === 'createFilter') return () => { h.filtro = {}; return h.filtro; };
        return () => rango;  // formato: no importa aquí
      }});
      return rango;
    }
  }
  const hojas = [];
  const libro = {getId: () => 'hoja', getSheets: () => hojas.slice(), getSheetByName: (n) => hojas.find((h) => h.nombre === n) || null,
    insertSheet: (n, pos) => { const h = new Hoja(n); pos === 0 ? hojas.unshift(h) : hojas.push(h); return h; }};
  const drive = {ficheros: [], carpetas: {}};
  const carpetaFalsa = (ruta) => ({
    getFoldersByName: (n) => { const r = ruta + '/' + n; return {hasNext: () => r in drive.carpetas, next: () => drive.carpetas[r]}; },
    createFolder: (n) => { const r = ruta + '/' + n; drive.carpetas[r] = carpetaFalsa(r); return drive.carpetas[r]; },
    getFilesByName: (n) => { const fs = drive.ficheros.filter((f) => f.ruta === ruta && f.nombre === n && !f.papelera); let i = 0;
      return {hasNext: () => i < fs.length, next: () => { const f = fs[i++]; return {setTrashed: () => { f.papelera = true; }}; }}; },
    createFile: (blob) => { const f = {ruta, nombre: blob.nombre, datos: blob.datos}; drive.ficheros.push(f); return {getId: () => 'id' + drive.ficheros.length}; }});
  const ctx2 = {module: {exports: {}}, SpreadsheetApp: {getActiveSpreadsheet: () => libro, newDataValidation: cadena, newConditionalFormatRule: cadena},
    Utilities: {formatDate: () => '2026-11-05 10:00', base64Decode: (b) => Buffer.from(b, 'base64'), newBlob: (datos, tipo, nombre) => ({datos, nombre})},
    Session: {getScriptTimeZone: () => 'Europe/Madrid'},
    DriveApp: {getFileById: () => ({getParents: () => ({hasNext: () => true, next: () => carpetaFalsa('Mi unidad')})}), getRootFolder: () => carpetaFalsa('Mi unidad')},
    drive,
    LockService: {getScriptLock: () => ({waitLock() {}, releaseLock() {}})},
    ContentService: {MimeType: {JSON: 'json'}, createTextOutput: (t) => ({setMimeType: () => JSON.parse(t)})}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', 'hoja_inventario.gs'), 'utf8'), ctx2);
  Hoja.prototype.setColumnWidth = function () {};
  Hoja.prototype.setRowHeight = function () {};
  Hoja.prototype.setFrozenRows = function () {};
  Hoja.prototype.setFrozenColumns = function () {};
  Hoja.prototype.setConditionalFormatRules = function () {};
  return {libro, ctx: ctx2, Hoja};
}

prueba('buzón entero: pestaña nueva, Resumen, escribir y leer', () => {
  const {libro, ctx: c} = libroFalso();
  const post = (o) => c.doPost({postData: {contents: JSON.stringify(o)}});
  assert.deepStrictEqual(JSON.parse(JSON.stringify(post({accion: 'probar'}))).pestanas, []);
  let r = post({accion: 'actualizar', gran_proyecto: 'China', filas: [{disco: 'D1', proyecto: 'PEKIN', estado: 'Analizado', clips: 3}]});
  assert.ok(r.ok, r.error);
  assert.strictEqual(r.nuevas, 1);
  assert.deepStrictEqual(Array.from(r.pestanas), ['China']);
  const china = libro.getSheetByName('China');
  assert.ok(china.getMaxRows() >= 1001 && china.filtro);
  const rs = libro.getSheetByName('Resumen');
  assert.strictEqual(rs.getRange(4, 1).getValues()[0][0], 'China');
  assert.ok(String(rs.getRange(4, 3).getValues()[0][0]).indexOf("'China'!E2:E5000") > 0);
  // el equipo decide; el programa vuelve a escribir y no lo pisa
  china.getRange(2, 9).setValue('Descartar: entradilla');
  china.getRange(2, 5).setValue('Descartado');
  r = post({accion: 'actualizar', gran_proyecto: 'China', filas: [{disco: 'D1', proyecto: 'PEKIN', estado: 'Exportado'}]});
  assert.strictEqual(r.actualizadas, 1);
  r = post({accion: 'leer', gran_proyecto: 'China'});
  assert.strictEqual(r.filas[0].estado, 'Descartado');
  assert.strictEqual(r.filas[0].decision, 'Descartar: entradilla');
  assert.strictEqual(post({accion: 'leer', gran_proyecto: 'Nada'}).filas.length, 0);
  // otra pestaña: otra fila en el Resumen
  post({accion: 'actualizar', gran_proyecto: "Méx'ico", filas: [{disco: 'D', proyecto: 'X', estado: 'Sin analizar'}]});
  assert.strictEqual(rs.getRange(5, 1).getValues()[0][0], "Méx'ico");
  assert.ok(!post({accion: 'actualizar', filas: []}).ok);  // sin gran proyecto
  assert.ok(!post({accion: 'otra', gran_proyecto: 'China'}).ok);
});

prueba('buzón entero: una hoja que viene justa de tamaño (la convertida de Excel)', () => {
  const {libro, ctx: c, Hoja} = libroFalso();
  const mx = libro.insertSheet('México');
  mx.filas = 3; mx.cols = 13;
  mx.getRange(1, 1, 1, 13).setValues([CAB.slice()]);
  mx.getRange(2, 1, 2, 13).setValues([fila({'Disco': 'DISCO 1', 'Proyecto': 'A', 'Quién': 'yo'}), fila({'Disco': 'DISCO 1', 'Proyecto': 'B'})]);
  const r = c.doPost({postData: {contents: JSON.stringify({accion: 'actualizar', gran_proyecto: 'México',
    filas: [{disco: 'DISCO 1', proyecto: 'A', estado: 'Exportado'}, {disco: 'DISCO 2', proyecto: 'C', estado: 'Analizado'}]})}});
  assert.ok(r.ok, r.error);
  assert.strictEqual(mx.getRange(4, 2).getValues()[0][0], 'C');
  assert.strictEqual(mx.getRange(2, 12).getValues()[0][0], 'yo');
  assert.strictEqual(mx.getRange(2, 5).getValues()[0][0], 'Exportado');
});

prueba('el programa en la nube: latido, órdenes y datos', () => {
  const {libro, ctx: c} = libroFalso();
  const post = (o) => JSON.parse(JSON.stringify(c.doPost({postData: {contents: JSON.stringify(o)}})));
  let r = post({accion: 'latido', pc: 'PC-MONTAJE', disco: 'F: MEXICO', acepta: true, actividad: 'libre'});
  assert.ok(r.ok, r.error);
  assert.deepStrictEqual(r.ordenes, []);
  post({accion: 'latido', pc: 'pc-montaje', actividad: 'exportando 2 de 5'});  // el mismo PC: la misma fila
  const prog = libro.getSheetByName('Programa');
  assert.strictEqual(prog.getLastRow(), 2);
  assert.strictEqual(prog.getRange(2, 5).getValues()[0][0], 'exportando 2 de 5');
  assert.ok(!post({accion: 'nueva_orden', orden: 'Borrar todo'}).ok);  // solo la lista cerrada
  assert.strictEqual(post({accion: 'nueva_orden', orden: 'Exportar', proyectos: 'ABORTO; CHAVELA VARGAS', quien: 'Claude'}).n, 1);
  // una orden escrita a mano en la hoja, sin número ni estado
  const ord = libro.getSheetByName('Órdenes');
  ord.getRange(3, 3, 1, 2).setValues([['Analizar', 'PANAMA 2019']]);
  r = post({accion: 'latido', pc: 'PC-MONTAJE'});
  assert.deepStrictEqual(r.ordenes.map((o) => [o.n, o.orden, o.proyectos]), [[1, 'Exportar', 'ABORTO; CHAVELA VARGAS'], [2, 'Analizar', 'PANAMA 2019']]);
  assert.strictEqual(ord.getRange(3, 7).getValues()[0][0], 'Pendiente');
  // la coge un PC; otro ya no puede
  assert.ok(post({accion: 'orden', n: 1, estado: 'En marcha', pc: 'PC-MONTAJE', desde: 'Pendiente'}).ok);
  assert.ok(!post({accion: 'orden', n: 1, estado: 'En marcha', pc: 'OTRO', desde: 'Pendiente'}).ok);
  assert.ok(post({accion: 'orden', n: 1, estado: 'Hecha', detalle: '2 brutos, 2 OK'}).ok);
  assert.strictEqual(ord.getRange(2, 8).getValues()[0][0], '2 brutos, 2 OK');
  assert.deepStrictEqual(post({accion: 'latido', pc: 'PC-MONTAJE'}).ordenes.map((o) => o.n), [2]);
  assert.deepStrictEqual(post({accion: 'probar'}).pestanas, []);  // Programa y Órdenes no son grandes proyectos
  // datos: el zip a su carpeta; uno nuevo con el mismo nombre sustituye al anterior
  for (const t of ['uno', 'dos']) {
    r = post({accion: 'datos', gran_proyecto: 'México', disco: 'DISCO_F', nombre: 'analisis.zip', zip_base64: Buffer.from(t).toString('base64')});
    assert.ok(r.ok, r.error);
  }
  const vivos = c.drive.ficheros.filter((f) => !f.papelera);
  assert.strictEqual(vivos.length, 1);
  assert.strictEqual(vivos[0].ruta, 'Mi unidad/DiagnosticoAvid datos/México/DISCO_F');
  assert.strictEqual(String(vivos[0].datos), 'dos');
});

console.log(n + ' pruebas OK');
