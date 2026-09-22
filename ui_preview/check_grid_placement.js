/* Run actual template decode/lift and layer/probe consumers without a viewer build.
   Usage: node ui_preview/check_grid_placement.js [explorer_template.html] */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = path.join(__dirname, '..');
const source = fs.readFileSync(process.argv[2] || path.join(root, 'explorer_template.html'), 'utf8');
const between = (start, end) => {
  const a = source.indexOf(start), b = source.indexOf(end, a);
  assert(a >= 0 && b > a, 'Template section missing: ' + start);
  return source.slice(a, b);
};
const decoding = between('const NODATA=', '/* ================= gl');
const layers = between('function setPrimary(', '/* ================= info card');
const probe = between('function showProbe(', '/* ================= interaction');
const kinds = between('function productKind(', 'const PRODUCT_CMAP=');
const report = {passed: 0, failures: [], checkedCells: 0};
function check(name, run) {
  try { run(); report.passed++; }
  catch (error) { report.failures.push(name + ': ' + error.message); }
}
function runtime(grid, arrays) {
  const nodes = new Map(), uploaded = {};
  const $ = id => {
    if (!nodes.has(id)) nodes.set(id, {style: {setProperty() {}}, value: '100', offsetWidth: 0, offsetHeight: 0});
    return nodes.get(id);
  };
  const noop = () => {};
  const context = vm.createContext({P: {grid, arrays}, atob, Float32Array, Int32Array,
    $, nodes, uploaded, window: {innerWidth: 1000, innerHeight: 1000},
    gl: {bindBuffer(_, buffer) { this.buffer = buffer; }, bufferData(_, data) { uploaded[this.buffer] = data; },
      uniform1i: noop, uniform1f: noop},
    U: {}, bVal: 'primary', bVal2: 'overlay', CM: {viridis: 4}, RAMP: {viridis: ''},
    DOM: {meta: {c: '', n: ''}}, layerCmap: () => 'viridis', layerRange: k => [arrays[k].lo, arrays[k].hi],
    layerReverse: () => 0, isDiverging: () => false, nodeAt: () => null,
    applyCustomUniforms: noop, syncRangeStatus: noop, renderInfo: noop, syncPaletteSettings: noop,
    syncLegendEditor: noop, draw: noop, fmt: String, esc: String,
    opts: {colors: 'smooth'}, syncCellColors: noop,
    drag: null, probeAt: () => context.hit, hit: {i: 0, j: 0}});
  vm.runInContext('const G=P.grid,W=G.w,H=G.h;let primKey=null,ovKey=null;' +
    'const elev=new Float32Array(W*H).fill(2000);' + decoding + kinds + layers + probe, context);
  return context;
}
function pack(values, metadata) {
  const bytes = Buffer.alloc(values.length * 2);
  values.forEach((v, i) => bytes.writeUInt16LE(Number.isFinite(v) ? v + 1 : 0, i * 2));
  return {...metadata, bits: 16, lo: 0, hi: 65534, b64: bytes.toString('base64'), label: 'fixture', leaf: 'cor'};
}
function equalCells(actual, expected, label) {
  assert.equal(actual.length, expected.length, label + ' length');
  let differences = 0, first = '';
  for (let k = 0; k < expected.length; k++) {
    if (!Object.is(actual[k], expected[k])) {
      if (!differences) first = `cell ${k}: ${actual[k]} instead of ${expected[k]}`;
      differences++;
    }
  }
  report.checkedCells += expected.length;
  assert.equal(differences, 0, `${label}: ${differences} differences; ${first}`);
}
// Frozen export metadata; all eight sites use four terrain cells per coarse cell.
const evidence = JSON.parse(fs.readFileSync(path.join(root, 'docs/product_trace/evidence/viewer_metadata.json'), 'utf8'));
assert.equal(evidence.length, 8);
for (const {payload_without_pixel_buffers: payload} of evidence) {
  const G = payload.grid, L = Object.values(payload.arrays).find(l => l.cell_m > G.cell_m);
  assert.equal(L.cell_m / G.cell_m, 4);
  const values = Float32Array.from({length: L.w * L.h}, (_, k) => k % 997 === 0 ? NaN : k);
  const expected = new Float32Array(G.w * G.h).fill(NaN);
  // Independent fixture construction: each retained coarse block covers exactly 4 x 4 centers.
  for (let row = 0; row < L.h; row++) for (let col = 0; col < L.w; col++)
    for (let y = row * 4; y < row * 4 + 4 && y < G.h; y++)
      for (let x = col * 4; x < col * 4 + 4 && x < G.w; x++) expected[y * G.w + x] = values[row * L.w + col];
  check(payload.site + ' all terrain centers, missing cells and cropped boundaries', () => {
    const ctx = runtime(G, {fixture: pack(values, L)});
    equalCells(vm.runInContext('floats("fixture")', ctx), expected, payload.site);
  });
}
const nan = NaN;
const fixtures = [
  {name: 'offset coarse footprint and internal missing cell',
    grid: {w: 5, h: 5, cell_m: 2, origin: [100,200], pixel: [2,-2]},
    layer: {w: 2, h: 2, cell_m: 4, origin: [102,198]}, values: [0,1,2,nan],
    expected: [nan,nan,nan,nan,nan, nan,0,0,1,1, nan,0,0,1,1, nan,2,2,nan,nan, nan,2,2,nan,nan]},
  {name: 'equal shape with different spacing',
    grid: {w: 3, h: 1, cell_m: 2, origin: [100,200], pixel: [2,-2]},
    layer: {w: 3, h: 1, cell_m: 4}, values: [0,1,2], expected: [0,0,1]},
  {name: 'equal shape with different origin',
    grid: {w: 3, h: 1, cell_m: 2, origin: [100,200], pixel: [2,-2]},
    layer: {w: 3, h: 1, cell_m: 2, origin: [102,200]}, values: [0,1,2], expected: [nan,0,1]},
  {name: 'explicit pixel orientation',
    grid: {w: 3, h: 1, cell_m: 2, origin: [100,200], pixel: [2,-2]},
    layer: {w: 3, h: 1, cell_m: 2, origin: [106,198], pixel: [-2,2]}, values: [0,1,2], expected: [2,1,0]},
  {name: 'centers exactly on source edges use following cell; final edge is outside',
    grid: {w: 5, h: 1, cell_m: 2, origin: [101,199], pixel: [2,-2]},
    layer: {w: 2, h: 1, cell_m: 4, origin: [102,198]}, values: [0,1], expected: [0,0,1,1,nan]},
];
for (const f of fixtures) check(f.name, () => {
  const ctx = runtime(f.grid, {fixture: pack(f.values, f.layer)});
  equalCells(vm.runInContext('floats("fixture")', ctx), f.expected, f.name);
});
// Actual primary, overlay and mouse-probe functions consume the same corrected arrays.
// Rendering/DOM side effects unrelated to sampling are inert; assert numeric uploads and probe output.
for (const [leaf, unit] of [['cor',''], ['aspect','deg'], ['int ∠phase','rad'], ['coherence_mask','']]) {
  for (const slot of ['primary', 'overlay']) check(slot + ' and probe / ' + leaf, () => {
    const f = fixtures[0], layer = {...pack(f.values, f.layer), leaf, unit};
    const base = pack(new Float32Array(25).fill(7), {...f.grid, cell_m: 2});
    const ctx = runtime(f.grid, {fixture: layer, base});
    vm.runInContext('setPrimary("base");' + (slot === 'primary' ? 'setPrimary' : 'setOverlay') + '("fixture");', ctx);
    const gpuExpected = f.expected.map(v => Number.isNaN(v) ? Math.fround(-1e30) : v);
    equalCells(ctx.uploaded[slot], gpuExpected, leaf + ' ' + slot + ' upload');
    for (const [i, j, text] of [[3,1,'fixture</b> 1'], [0,0,'no data'], [3,3,'no data']]) {
      ctx.hit = {i,j};
      vm.runInContext('showProbe({clientX:20,clientY:20})', ctx);
      const probeHtml = ctx.nodes.get('probe').innerHTML;
      assert(probeHtml.includes(slot === 'overlay' && text.includes('</b>') ? 'fixture 1' : text), probeHtml);
    }
  });
}
for (const failure of report.failures) console.error('FAIL ' + failure);
console.log(`${report.failures.length ? 'FAIL' : 'PASS'}: ${report.passed} passed, ${report.failures.length} failed; ` +
  `${report.checkedCells.toLocaleString()} cells checked, actual template decode/lift/layer/probe functions, eight captured site grids.`);
if (report.failures.length) process.exitCode = 1;
