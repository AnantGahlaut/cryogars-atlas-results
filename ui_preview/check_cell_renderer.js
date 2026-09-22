/* Discrete-cell numeric WebGL2 regression; loads only a tiny synthetic local document, never the viewer.
   Usage: node ui_preview/check_cell_renderer.js [explorer_template.html]
   Requires installed Chrome; CHROME_PATH may select its executable. No dependencies. */
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {spawnSync} = require('node:child_process');

const source = fs.readFileSync(process.argv[2] || path.join(__dirname, '..', 'explorer_template.html'), 'utf8');
const shaders = ['VS', 'FS'].map(name => {
  const match = source.match(new RegExp('const ' + name + '=\\x60([\\s\\S]*?)\\x60;'));
  if (!match) throw new Error('Could not extract ' + name + ' from the template');
  return match[1];
});

const averageStart=source.indexOf('function averageCellValues('), averageEnd=source.indexOf('\n}',averageStart);
if(averageStart<0||averageEnd<=averageStart)throw new Error('Could not extract averageCellValues');
const averageSource=source.slice(averageStart,averageEnd+2);

function checkShaders(VS, FS, averageCellValues) {
  const report = {passed: 0, failures: []};
  try {
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 8;
    const gl = canvas.getContext('webgl2', {antialias: false, preserveDrawingBuffer: true});
    if (!gl) throw new Error('WebGL2 unavailable');
    const program = gl.createProgram();
    for (const [type, source] of [[gl.VERTEX_SHADER, VS], [gl.FRAGMENT_SHADER, FS]]) {
      const shader = gl.createShader(type);
      gl.shaderSource(shader, source); gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
      gl.attachShader(program, shader);
    }
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
    gl.useProgram(program); gl.bindVertexArray(gl.createVertexArray());
    const locations = {}, buffers = {}, textures = [];
    const uniform = name => name in locations ? locations[name] : (locations[name] = gl.getUniformLocation(program, name));
    for (const name of ['uCellColors', 'uCells', 'uCells2', 'uCellMap', 'uCellMap2']) {
      if (uniform(name) === null) throw new Error('Missing cell-render uniform: ' + name);
    }
    function attribute(name, size, values) {
      const location = gl.getAttribLocation(program, name);
      if (location < 0) throw new Error('Missing shader attribute: ' + name);
      gl.bindBuffer(gl.ARRAY_BUFFER, buffers[name] || (buffers[name] = gl.createBuffer()));
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(values), gl.STATIC_DRAW);
      gl.enableVertexAttribArray(location); gl.vertexAttribPointer(location, size, gl.FLOAT, false, 0, 0);
    }
    // One coarse triangle covers the entire screen; X/Z are the terrain plane.
    attribute('aPos', 3, [-1,0,-1, 3,0,-1, -1,0,3]);
    gl.uniformMatrix4fv(uniform('uMVP'), false, [1,0,0,0, 0,0,1,0, 0,1,0,0, 0,0,0,1]);
    gl.uniform3f(uniform('uSun'), 0, 1, 0);
    const colors = new Float32Array(24); colors.set([1,1,1], 3);
    for (const suffix of ['', '2']) {
      gl.uniform3fv(uniform('uCustomColors' + suffix + '[0]'), colors);
      gl.uniform1fv(uniform('uCustomStops' + suffix + '[0]'), [0,1,0,0,0,0,0,0]);
    }
    gl.viewport(0, 0, 8, 8); gl.disable(gl.DITHER); gl.clearColor(0,0,0,0);
    function texture(slot, grid) {
      gl.activeTexture(gl.TEXTURE0 + slot);
      gl.bindTexture(gl.TEXTURE_2D, textures[slot] || (textures[slot] = gl.createTexture()));
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.R32F, grid.w, grid.h, 0, gl.RED, gl.FLOAT, new Float32Array(grid.values));
      const suffix = slot ? '2' : '';
      gl.uniform1i(uniform('uCells' + suffix), slot);
      gl.uniform4fv(uniform('uCellMap' + suffix), grid.map || [0,0,.5,.5]);
    }
    const constant = v => [v,v,v];
    const grid = (values, w = values.length, h = 1, map) => ({values,w,h,map});
    const base = grid([.1,.3,.6,.9, .2,.4,.7,1],4,2,[2,1,2,1]);
    function render(options = {}, x = 1, y = 1) {
      attribute('aVal', 1, options.values || constant(.37));
      attribute('aVal2', 1, options.values2 || constant(-1e30));
      texture(0, options.grid || base); texture(1, options.grid2 || grid([-1e30]));
      for (const [name, value] of Object.entries({
        uCellColors: 1, uAngleKind: 0, uAngleKind2: 0, uCmap: 10, uCmap2: 10,
        uMaskBinary: 0, uMaskBinary2: 0, uReverse: 0, uReverse2: 0,
        uDiverging: 0, uDiverging2: 0, uCustomN: 2, uCustomN2: 2, uWire: 0,
      })) gl.uniform1i(uniform(name), options[name] ?? value);
      for (const [name, value] of Object.entries({
        uExag: 1, uLo: 0, uHi: 1, uLo2: 0, uHi2: 1, uOpacity: 0, uRelief: 0,
      })) gl.uniform1f(uniform(name), options[name] ?? value);
      gl.clear(gl.COLOR_BUFFER_BIT); gl.drawArrays(gl.TRIANGLES, 0, 3);
      const pixel = new Uint8Array(4); gl.readPixels(x, y, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
      const error = gl.getError(); if (error !== gl.NO_ERROR) throw new Error('WebGL error: ' + error);
      return Array.from(pixel);
    }
    const rgba = value => [...Array(3).fill(Math.round(value * 255)),255], clear = [0,0,0,0];
    function check(name, actual, expected, tolerance = 2) {
      if (actual.every((v,i) => Math.abs(v - expected[i]) <= tolerance)) report.passed++;
      else report.failures.push({name,actual,expected});
    }
    // All eight exported cells survive one triangle, without borrowing its vertex values.
    for (let row = 0; row < 2; row++) for (let col = 0; col < 4; col++) {
      const expected = rgba(base.values[row * 4 + col]);
      check('coarse triangle cell ' + col + ',' + row, render({},col*2,row*4+1), expected);
      check('same cell has one color ' + col + ',' + row, render({},col*2+1,row*4+2), expected);
    }
    // Exercise the real CPU reduction and GPU lookup together: 16×16 at 24 m
    // becomes 2×2 at 192 m. Every block has alternating input values, so sampling
    // a single 24 m cell gives the wrong color even when the boundaries are right.
    const quadrantMeans=[.1,.3,.6,.9],fine=new Float32Array(16*16);
    for(let row=0;row<16;row++)for(let col=0;col<16;col++){
      const q=Math.floor(row/8)*2+Math.floor(col/8);
      fine[row*16+col]=quadrantMeans[q]+((row+col)%2?.04:-.04);
    }
    const sourceGrid={w:16,h:16,origin:[400000,4500000],pixel:[24,-24]};
    const targetGrid={w:2,h:2,origin:sourceGrid.origin,pixel:[192,-192],extent:[384,-384]};
    const reduced=averageCellValues(fine,sourceGrid,targetGrid);
    const coarseGrid=grid(reduced,2,2,[1,1,1,1]);
    for(let row=0;row<2;row++)for(let col=0;col<2;col++)for(let sample=0;sample<4;sample++){
      check('24-to-192 m averaged cell '+col+','+row+' sample '+sample,
        render({grid:coarseGrid},col*4+sample,row*4+sample),rgba(quadrantMeans[row*2+col]));
    }
    check('missing vertex values do not erase finite texture cells', render({values:constant(-1e30)}),rgba(.1));
    check('zero is a valid black cell',render({grid:grid([0])}),rgba(0));
    for (const value of [-1e30,NaN,Infinity,-Infinity]) {
      check('missing original cell ' + value,render({grid:grid([value])}),clear);
    }
    const hole = grid([.2,NaN,.6,.8],4,1,[2,0,2,.5]);
    check('finite neighbor does not fill cell hole',render({grid:hole},2,1),clear);
    check('cell immediately beside hole stays exact',render({grid:hole},4,1),rgba(.6));
    const cropped = grid([.2,.8],2,1,[2,0,1,.5]);
    check('left outside cropped extent stays blank',render({grid:cropped},0,1),clear);
    check('right outside cropped extent stays blank',render({grid:cropped},7,1),clear);
    check('shifted first source cell',render({grid:cropped},2,1),rgba(.2));
    check('shifted second source cell',render({grid:cropped},4,1),rgba(.8));
    check('cell edge belongs to following cell',render({grid:grid([.2,.8],2,1,[1,0,1.625,.5])},1,1),rgba(.8));
    const overlay = grid([.8,.2],2,1,[1,0,1,.5]);
    check('overlay uses its independent coarser grid',render({grid2:overlay,uOpacity:.5},2,1),rgba(.55));
    check('overlay independent second cell',render({grid2:overlay,uOpacity:.5},4,1),rgba(.4));
    check('missing overlay preserves primary',render({grid2:grid([NaN]),uOpacity:1}),rgba(.1));
    check('overlay outside extent preserves primary',render({grid2:cropped,uOpacity:1},7,1),rgba(.9));
    check('finite overlay does not fill missing primary',render({grid:grid([NaN]),grid2:overlay,uOpacity:1}),clear);
    check('valid zero overlay is drawn',render({grid2:grid([0]),uOpacity:1}),rgba(0));
    for (const [kind,value,expected] of [[1,360,0],[1,-1,359/360],[1,180,.5],[2,Math.PI,0],[2,0,.5],[2,-Math.PI/2,.25]]) {
      const lo = kind===1?0:-Math.PI, hi=kind===1?360:Math.PI;
      check('individual angle canonicalization ' + kind + '/' + value,
        render({grid:grid([value]),uAngleKind:kind,uLo:lo,uHi:hi}),rgba(expected));
      check('individual overlay angle canonicalization ' + kind + '/' + value,
        render({grid2:grid([value]),uAngleKind2:kind,uLo2:lo,uHi2:hi,uOpacity:1}),rgba(expected));
    }
    for (const value of [0,1]) {
      check('binary cell class ' + value,render({grid:grid([value]),uMaskBinary:1}),rgba(value));
      check('binary overlay cell class ' + value,render({grid2:grid([value]),uMaskBinary2:1,uOpacity:1}),rgba(value));
    }
    for (const value of [-1,0,1]) {
      check('transition cell class ' + value,render({grid:grid([value]),uMaskBinary:2,uLo:-1}),rgba((value+1)/2));
    }
    check('cells keep exact color under relief',render({uRelief:1}),rgba(.1));
    check('cells keep exact color under wire brightening',render({uWire:1}),rgba(.1));
    check('large scalar texture preserves fractional value',render({grid:grid([98231.75]),uLo:98000,uHi:99000}),rgba(.23175));
    check('cell palette reversal',render({uReverse:1}),rgba(.9));
    check('cell diverging normalization',render({grid:grid([-.5]),uLo:-1,uHi:1,uDiverging:1}),rgba(.25));
    const values=[0,1,1];
    check('switch to smooth restores vertex interpolation',render({uCellColors:0,values},1,1),rgba(.1875));
    check('switch back to cells restores original cell color',render({values},1,1),rgba(.1));
    report.webgl=gl.getParameter(gl.VERSION);
  } catch (error) { report.error=String(error.stack||error); }
  const bytes=new TextEncoder().encode(JSON.stringify(report));
  document.getElementById('result').textContent=btoa(Array.from(bytes,b=>String.fromCharCode(b)).join(''));
}

const chrome = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
if (!fs.existsSync(chrome)) throw new Error('Chrome not found; set CHROME_PATH to an installed executable');
const tempRoot = path.resolve(os.tmpdir());
const temp = fs.mkdtempSync(path.join(tempRoot, 'snex-cell-colors-'));
try {
  const documentPath = path.join(temp, 'synthetic.html');
  fs.writeFileSync(documentPath, '<!doctype html><meta charset="utf-8"><pre id="result">PENDING</pre><script>(' +
    checkShaders.toString() + ')(...' + JSON.stringify(shaders).replaceAll('<', '\\u003c') + ',' + averageSource + ');</script>');
  const result = spawnSync(chrome, [
    '--headless', '--dump-dom', '--no-first-run', '--no-default-browser-check',
    '--disable-background-networking', '--disable-sync', '--disable-extensions',
    '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
    '--user-data-dir=' + path.join(temp, 'profile'), '--virtual-time-budget=5000',
    pathToFileURL(documentPath).href,
  ], {encoding: 'utf8', timeout: 45000, windowsHide: true, maxBuffer: 2 * 1024 * 1024});
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error('Headless Chrome failed (' + result.status + '): ' + result.stderr.slice(-2000));
  const encoded = result.stdout.match(/<pre id="result">([A-Za-z0-9+/=]+)<\/pre>/)?.[1];
  if (!encoded || encoded === 'PENDING') {
    const at=result.stdout.indexOf('<pre'),context=at<0?result.stdout.slice(0,600):result.stdout.slice(Math.max(0,at-100),at+800);
    throw new Error('Headless Chrome did not return a numeric WebGL report. '+JSON.stringify({
      stdoutBytes:Buffer.byteLength(result.stdout),resultContext:context,htmlTail:result.stdout.slice(-1400),stderr:result.stderr.slice(-1000)
    }));
  }
  const report = JSON.parse(Buffer.from(encoded, 'base64').toString('utf8'));
  if (report.error) throw new Error(report.error);
  for (const failure of report.failures) console.error('FAIL ' + failure.name + ': got ' + failure.actual + ', expected ' + failure.expected);
  console.log((report.failures.length ? 'FAIL' : 'PASS') + ': ' + report.passed + ' passed, ' + report.failures.length +
    ' failed; extracted production shaders, ' + report.webgl + ', 8x8 synthetic cell textures, numeric readPixels.');
  if (report.failures.length) process.exitCode = 1;
} finally {
  if (path.dirname(temp) !== tempRoot || !path.basename(temp).startsWith('snex-cell-colors-')) throw new Error('Unsafe temporary cleanup path');
  fs.rmSync(temp, {recursive: true, force: true, maxRetries: 3, retryDelay: 100});
}
