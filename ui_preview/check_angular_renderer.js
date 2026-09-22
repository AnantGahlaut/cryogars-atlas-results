/* Numeric WebGL2 regression; loads only a tiny synthetic local document, never the viewer.
   Usage: node ui_preview/check_angular_renderer.js [explorer_template.html]
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

function checkShaders(VS, FS) {
  const report = {passed: 0, failures: []};
  try {
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 9;
    const gl = canvas.getContext('webgl2', {antialias: false, preserveDrawingBuffer: true});
    if (!gl) throw new Error('WebGL2 unavailable');
    const program = gl.createProgram();
    for (const [type, shaderSource] of [[gl.VERTEX_SHADER, VS], [gl.FRAGMENT_SHADER, FS]]) {
      const shader = gl.createShader(type);
      gl.shaderSource(shader, shaderSource);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
      gl.attachShader(program, shader);
    }
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
    gl.useProgram(program);
    gl.bindVertexArray(gl.createVertexArray());
    const locations = {};
    const uniform = name => name in locations ? locations[name] : (locations[name] = gl.getUniformLocation(program, name));
    const buffers = {};
    function attribute(name, size, data) {
      const location = gl.getAttribLocation(program, name);
      if (location < 0) throw new Error('Missing shader attribute: ' + name);
      gl.bindBuffer(gl.ARRAY_BUFFER, buffers[name] || (buffers[name] = gl.createBuffer()));
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(data), gl.STATIC_DRAW);
      gl.enableVertexAttribArray(location);
      gl.vertexAttribPointer(location, size, gl.FLOAT, false, 0, 0);
    }
    // Center sample weights: (1/2,1/4,1/4). Right neighbor: (4/9,11/36,1/4).
    attribute('aPos', 3, [-1,-1,0, 3,-1,0, -1,3,0]);
    gl.uniformMatrix4fv(uniform('uMVP'), false, [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]);
    gl.uniform3f(uniform('uSun'), 0, 0, 1);
    // The renderer's optional Cells branch adds complete float samplers; Smooth stays selected.
    for (const unit of [0,1]) {
      gl.activeTexture(gl.TEXTURE0+unit);gl.bindTexture(gl.TEXTURE_2D,gl.createTexture());
      gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.NEAREST);
      gl.texImage2D(gl.TEXTURE_2D,0,gl.R32F,1,1,0,gl.RED,gl.FLOAT,new Float32Array([0]));
      gl.uniform1i(uniform(unit?'uCells2':'uCells'),unit);
    }
    const colors = new Float32Array(24);
    colors.set([1,1,1], 3);
    const stops = new Float32Array([0,1,0,0,0,0,0,0]);
    for (const suffix of ['', '2']) {
      gl.uniform3fv(uniform('uCustomColors' + suffix + '[0]'), colors);
      gl.uniform1fv(uniform('uCustomStops' + suffix + '[0]'), stops);
    }
    gl.viewport(0, 0, 9, 9);
    gl.disable(gl.DITHER);
    gl.clearColor(0, 0, 0, 0);
    const constant = value => [value, value, value];
    function render(options = {}, x = 5, y = 4) {
      attribute('aVal', 1, options.values || constant(.37));
      attribute('aVal2', 1, options.values2 || constant(-1e30));
      for (const [name, value] of Object.entries({
        uAngleKind: 0, uAngleKind2: 0, uCmap: 10, uCmap2: 10,
        uMaskBinary: 0, uMaskBinary2: 0,
        uReverse: 0, uReverse2: 0, uDiverging: 0, uDiverging2: 0,
        uCustomN: 2, uCustomN2: 2, uWire: 0,
      })) gl.uniform1i(uniform(name), options[name] ?? value);
      for (const [name, value] of Object.entries({
        uExag: 1, uLo: 0, uHi: 1, uLo2: 0, uHi2: 1, uOpacity: 0, uRelief: 0,
      })) gl.uniform1f(uniform(name), options[name] ?? value);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      const pixel = new Uint8Array(4);
      gl.readPixels(x, y, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
      const error = gl.getError();
      if (error !== gl.NO_ERROR) throw new Error('WebGL error: ' + error);
      return Array.from(pixel);
    }
    function check(name, actual, expected, tolerance = 2) {
      if (actual.every((value, i) => Math.abs(value - expected[i]) <= tolerance)) report.passed++;
      else report.failures.push({name, actual, expected});
    }
    function field(slot, kind, values, cmap = 10) {
      const suffix = slot === 'primary' ? '' : '2';
      return {
        ['values' + suffix]: values,
        ['uAngleKind' + suffix]: kind,
        ['uCmap' + suffix]: cmap,
        ['uLo' + suffix]: kind === 2 ? -Math.PI : 0,
        ['uHi' + suffix]: kind === 2 ? Math.PI : 360,
        uOpacity: slot === 'overlay' ? 1 : 0,
      };
    }
    const clear = [0,0,0,0], base = render(), nodata = -1e30;
    for (const slot of ['primary', 'overlay']) {
      const undefinedScalar = slot === 'primary' ? clear : base;
      const scalar = values => field(slot, 0, values);
      const suffix = slot === 'primary' ? '' : '2';
      const mask = (values, mode) => ({...scalar(values),
        ['uMaskBinary' + suffix]: mode, ['uLo' + suffix]: mode === 2 ? -1 : 0, ['uHi' + suffix]: 1});
      for (const [name, values, mode, expected, x] of [
        ['binary below threshold', [0,1,1], 1, 0, 3],
        ['binary inclusive threshold', [0,1,1], 1, 1, 4],
        ['binary above threshold', [0,1,1], 1, 1, 5],
        ['binary missing corner preserves finite class', [nodata,1,1], 1, 1, 5],
        ['binary missing corner zero is valid', [nodata,0,0], 1, 0, 5],
        ['binary smooth mode stays continuous', [0,1,1], 0, 5/9, 5],
        ['transition negative', [-1,-1,0], 2, -1, 5],
        ['transition zero', [-1,1,0], 2, 0, 5],
        ['transition positive', [1,1,0], 2, 1, 5],
        ['transition missing preserves negative class', [nodata,-1,-1], 2, -1, 5],
      ]) check(slot + ' mask ' + name, render(mask(values, mode), x),
        render({...mask(constant(expected), mode), ['uMaskBinary' + suffix]: 0}));
      check(slot + ' binary all missing', render(mask(constant(nodata), 1)), undefinedScalar);
      check(slot + ' transition all missing', render(mask(constant(nodata), 2)), undefinedScalar);
      check(slot + ' scalar missing corner renormalizes finite weights',
        render(scalar([nodata,72,288])), render(scalar(constant(169.2))));
      check(slot + ' scalar two missing corners with sufficient coverage',
        render(scalar([144,nodata,nodata]), 3), render(scalar(constant(144))));
      check(slot + ' scalar coverage below half',
        render(scalar([nodata,144,144]), 3), undefinedScalar);
      check(slot + ' scalar coverage exactly half',
        render(scalar([nodata,144,144]), 4),
        slot === 'primary' ? render(scalar(constant(144))) : base);
      check(slot + ' scalar all missing', render(scalar(constant(nodata))), undefinedScalar);
      for (const invalid of [NaN, Infinity, -Infinity]) {
        check(slot + ' scalar nonfinite corner ' + invalid,
          render(scalar([invalid,72,288])), render(scalar(constant(169.2))));
        check(slot + ' scalar all nonfinite ' + invalid,
          render(scalar(constant(invalid))), undefinedScalar);
      }
      for (const kind of [1, 2]) {
        const label = slot + ' ' + (kind === 1 ? 'aspect' : 'phase');
        const seam = kind === 1 ? [359,1,1] : [179,-179,-179].map(value => value * Math.PI / 180);
        const seamReference = kind === 1 ? 0 : -Math.PI;
        for (const cmap of [6, 10]) {
          check(label + ' seam / ' + (cmap === 6 ? 'cyclic' : 'custom'),
            render(field(slot, kind, seam, cmap)),
            render(field(slot, kind, constant(seamReference), cmap)));
        }
        check(label + ' zero angle is valid', render(field(slot, kind, constant(0))),
          render(field(slot, 0, constant(kind === 1 ? 0 : 180))));
        const opposite = kind === 1 ? 180 : Math.PI;
        const undefinedPixel = slot === 'primary' ? clear : base;
        check(label + ' opposing mean is undefined',
          render(field(slot, kind, [0,opposite,opposite]), 4), undefinedPixel);
        check(label + ' near-zero resultant is undefined',
          render(field(slot, kind, [0,opposite - (kind === 1 ? 1e-5 : 1e-7),opposite]), 4), undefinedPixel);
        const direction = kind === 1 ? 90 : -Math.PI / 2;
        const reference = render(field(slot, kind, constant(direction)));
        check(label + ' one nodata corner',
          render(field(slot, kind, [nodata,direction,direction])), reference);
        check(label + ' two nodata corners with sufficient coverage',
          render(field(slot, kind, [direction,nodata,nodata]), 3), reference);
        check(label + ' coverage below half',
          render(field(slot, kind, [nodata,direction,direction]), 3), undefinedPixel);
        check(label + ' coverage exactly half',
          render(field(slot, kind, [nodata,direction,direction]), 4), slot === 'primary' ? reference : base);
        check(label + ' all nodata',
          render(field(slot, kind, constant(nodata))), undefinedPixel);
        for (const invalid of [NaN, Infinity, -Infinity]) {
          check(label + ' nonfinite corner ' + invalid,
            render(field(slot, kind, [invalid,direction,direction])), reference);
          check(label + ' all nonfinite ' + invalid,
            render(field(slot, kind, constant(invalid))), undefinedPixel);
        }
        // Equal valid weights sum to 11/18 here: test the normalized mean tolerance.
        const almostOpposite = opposite - (kind === 1 ? .00017 : 3e-6);
        check(label + ' small defined mean with nodata',
          render(field(slot, kind, [nodata,0,almostOpposite]), 5, 5),
          render(field(slot, kind, constant(kind === 1 ? 90 : Math.PI / 2))));
      }
      for (const cmap of [6, 10]) {
        const actual = field(slot, 0, [0,360,360], cmap);
        const expected = field(slot, 0, constant(200), cmap);
        check(slot + ' scalar linear interpolation / ' + (cmap === 6 ? 'cyclic' : 'custom'), render(actual), render(expected));
        const suffix = slot === 'primary' ? '' : '2';
        actual['uReverse' + suffix] = expected['uReverse' + suffix] = 1;
        check(slot + ' scalar reversed palette / ' + cmap, render(actual), render(expected));
        actual['uDiverging' + suffix] = expected['uDiverging' + suffix] = 1;
        check(slot + ' scalar diverging normalization / ' + cmap, render(actual), render(expected));
      }
    }
    for (const [primaryKind, overlayKind] of [[1,2], [2,1]]) {
      const seam = kind => kind === 1 ? [359,1,1] : [179,-179,-179].map(value => value * Math.PI / 180);
      const reference = kind => constant(kind === 1 ? 0 : -Math.PI);
      check('simultaneous independent angular fields ' + primaryKind + '/' + overlayKind,
        render({...field('primary', primaryKind, seam(primaryKind), 6),
          ...field('overlay', overlayKind, seam(overlayKind)), uOpacity: .35}),
        render({...field('primary', primaryKind, reference(primaryKind), 6),
          ...field('overlay', overlayKind, reference(overlayKind)), uOpacity: .35}));
    }
    report.webgl = gl.getParameter(gl.VERSION);
  } catch (error) {
    report.error = String(error.stack || error);
  }
  document.getElementById('result').textContent = btoa(JSON.stringify(report));
}

const chrome = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
if (!fs.existsSync(chrome)) throw new Error('Chrome not found; set CHROME_PATH to an installed executable');
const tempRoot = path.resolve(os.tmpdir());
const temp = fs.mkdtempSync(path.join(tempRoot, 'snex003-angular-'));
try {
  const documentPath = path.join(temp, 'synthetic.html');
  fs.writeFileSync(documentPath, '<!doctype html><meta charset="utf-8"><pre id="result">PENDING</pre><script>(' +
    checkShaders.toString() + ')(...' + JSON.stringify(shaders).replaceAll('<', '\\u003c') + ');</script>');
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
  if (!encoded || encoded === 'PENDING') throw new Error('Headless Chrome did not return a numeric WebGL report: ' + result.stderr.slice(-2000));
  const report = JSON.parse(Buffer.from(encoded, 'base64').toString('utf8'));
  if (report.error) throw new Error(report.error);
  for (const failure of report.failures) console.error('FAIL ' + failure.name + ': got ' + failure.actual + ', expected ' + failure.expected);
  console.log((report.failures.length ? 'FAIL' : 'PASS') + ': ' + report.passed + ' passed, ' + report.failures.length +
    ' failed; extracted production shaders, ' + report.webgl + ', 9x9 synthetic triangles, numeric readPixels.');
  if (report.failures.length) process.exitCode = 1;
} finally {
  if (path.dirname(temp) !== tempRoot || !path.basename(temp).startsWith('snex003-angular-')) throw new Error('Unsafe temporary cleanup path');
  fs.rmSync(temp, {recursive: true, force: true, maxRetries: 3, retryDelay: 100});
}
