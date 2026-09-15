/* Current-template checks run real decoding, range selection, legend/info rendering
 * and tab handlers with small DOM/GL stubs. No browser or screenshot claim.
 * Optional template path supports checking the pre-fix backup.
 */
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
const html=fs.readFileSync(process.argv[2]||path.join(root,'explorer_template.html'),'utf8');
const extract=(start,end)=>{const a=html.indexOf(start),b=html.indexOf(end,a+start.length);assert(a>=0&&b>a,start);return html.slice(a,b);};
const noop=()=>{},ids=new Map(),uniforms={};
function element(){
  let content='';const classes=new Set();
  return {get innerHTML(){return content;},set innerHTML(v){content=v;this.children=[];},
    textContent:'',title:'',children:[],attrs:{},dataset:{},style:{setProperty:noop},
    classList:{add:c=>classes.add(c),remove:c=>classes.delete(c),contains:c=>classes.has(c),
      toggle(c,on){on=on===undefined?!classes.has(c):on;if(on)classes.add(c);else classes.delete(c);}},
    setAttribute(k,v){this.attrs[k]=v;},appendChild(x){this.children.push(x);},
    querySelector:s=>s==='.le-presets'?presets:null,querySelectorAll:()=>buttons};
}
const $=id=>{if(!ids.has(id))ids.set(id,element());return ids.get(id);};
const presets=element(),buttons=['full','robust','detail'].map(stretch=>({...element(),dataset:{stretch}}));
const PREF={ranges:{},rangeModes:{},palettes:{},reverse:{},customs:{},typeActive:{},savedPalettes:{}};
const scope=vm.createContext({$,PREF,document:{createElement:element},console,atob,
  P:{arrays:{},tree:[]},G:{full:[1,1],res_m:3,cell_m:3,origin:[0,0],pixel:[3,-3],dem_valid_fraction:.5},W:101,H:1,DEM:'dem',
  levels:[{w:101,h:1,cell:3}],curLevel:0,glOn:true,elev:new Float32Array(101).fill(100),
  nodeAt:p=>scope.P.tree.find(n=>n.path===p),
  DOM:{lidar:{c:'teal',n:'Lidar'},meta:{c:'gray',n:'Metadata'},amp:{c:'gold',n:'Amplitude'}},
  bVal:{},bVal2:{},U:new Proxy({},{get:(_,k)=>k}),
  gl:{bindBuffer:noop,bufferData:noop,uniform1i:noop,uniform1f:(k,v)=>{uniforms[k]=v;}},
  applyCustomUniforms:noop,customRampCss:()=>'',syncPaletteSettings:noop,draw:noop,
  fillPaletteSelect:noop,renderCustomEditor:noop,closeSettings:noop,closeColorPicker:noop,
  savePrefs:noop,openSettings:noop,renderDetails:noop,renderTimeline:noop,PALETTE_LABELS:{}});
const run=source=>vm.runInContext(source,scope);
run(extract('const NODATA=','/* ================= gl'));
run(extract('const CM=','const hexRgb='));
run(extract('function setPrimary(k,','/* ================= info card'));
run(extract('const kvRow=','/* ================= validity help')); // Help controller has its own interaction checks.
run(extract('function syncLegendEditor(){','window.addEventListener("keydown",e=>{'));
run(extract('const tabs=[','/* ================= tree'));
run(extract('function matches(n){','function renderTree(){'));
scope.q='';

// Codes 1..101 decode exactly to 0..100; code 0 is missing. Hand-derived
// expectations exercise actual export decoding/lifting, not native rasters.
function layer(codes,extra={}){
  return {w:codes.length,h:1,bits:8,lo:0,hi:254,b64:Buffer.from(codes).toString('base64'),
    valid:codes.filter(x=>x!==0).length,total:codes.length,cell_m:3,domain:'lidar',
    leaf:'snow_depth',label:'Snow <depth> & "range"',source:'snow',...extra};
}
scope.P.arrays={dem:layer([1,2],{leaf:'elevation'}),
  snow:layer(Array.from({length:101},(_,i)=>i+1)),
  tied:layer([51,51,51,51]),constant:layer([1,1,1],{lo:7,hi:7}),
  empty:layer([0,0]),coarse:layer([1,101]),
  signed:layer(Array.from({length:101},(_,i)=>i+1),{leaf:'unw',lo:-25,hi:229})};
function select(key){scope.key=key;run('setPrimary(key)');}
function preset(mode){presets.onclick({target:{closest:()=>buttons.find(b=>b.dataset.stretch===mode)}});}
function range(want){
  assert.deepEqual([+$('leMin').value,+$('leMax').value],want,'editor uses effective colour limits');
  assert.deepEqual([uniforms.uLo,uniforms.uHi],want,'shader receives effective colour limits');
  scope.want=want;assert.deepEqual([$('t0').textContent,$('t2').textContent],Array.from(run('want.map(fmt)')),'legend uses the same limits');
}
select('snow');range([0,254]);
assert.match($('rangeStateLabel').textContent,/colour stretch.*full packed range/i);
assert.match($('rangeState').title,/clipp/i,'full range cannot restore clipped export values');
for(const [mode,want,interval] of [['robust',[2,98],/2nd.98th/],['detail',[10,90],/10th.90th/]]){
  preset(mode);range(want);
  assert.match($('rangeStateLabel').textContent,interval);
  assert.match($('rangeState').title,/finite.*decod/i);
  assert.match($('rangeState').title,/sample/i);
  assert.match($('rangeState').title,/terrain.grid/i);
  assert.match($('rangeState').title,/not native/i);
  assert.equal(buttons.find(b=>b.dataset.stretch===mode).attrs['aria-pressed'],'true');
  assert.equal(buttons.filter(b=>b.classList.contains('on')).length,1,'preset accent follows selection');
}
$('leMin').value=12;$('leMax').value=80;$('leMin').onchange();range([12,80]);
assert.match($('rangeStateLabel').textContent,/manual/i);
assert.equal(buttons.filter(b=>b.attrs['aria-pressed']==='true').length,0,'custom range clears preset accent');
$('leMin').value=90;$('leMax').value=80;$('leMax').onchange();range([12,80]);
$('leMin').value='';$('leMin').onchange();range([12,80]);
preset('full');range([0,254]);assert.equal(PREF.ranges.snow,undefined);
select('tied');preset('detail');range([0,254]);
assert.match($('rangeStateLabel').textContent,/full packed range/i,'equal sampled limits fall back to packed limits');
assert.match($('rangeState').title,/tied/i);
select('constant');preset('robust');range([7,7]);
assert.match($('rangeStateLabel').textContent,/full packed range/i);
select('empty');preset('robust');range([0,254]);
assert.match($('rangeState').title,/no finite/i,'empty sample fallback is disclosed');
select('signed');preset('detail');
assert.deepEqual([+$('leMin').value,+$('leMax').value,uniforms.uLo,uniforms.uHi],[-65,65,-65,65]);
assert.match($('rangeStateLabel').textContent,/symmetric/i,'diverging limits are expanded around zero');
select('coarse');preset('robust');range([0,100]);
assert.equal(run('floats("coarse").length'),101,'percentiles operate after terrain-grid lifting');
scope.W=300002;run('cache.clear()');
scope.P.arrays.strided=layer(new Array(300002).fill(255).map((v,i)=>i%2?v:1));
assert.deepEqual(Array.from(run('sampledPercentiles("strided",.02,.98)')),[0,0],
  'regular stride samples even cells, not all original colour-grid values');
scope.W=101;run('cache.clear()');

// Stored archive support and exported support use their own grid totals.
// The old DEM denominator must not affect either count.
scope.G={full:[1,8],res_m:3,cell_m:3,origin:[0,0],pixel:[3,-3],dem_valid_fraction:.125};
scope.W=2;scope.H=1;scope.levels=[{w:2,h:1,cell:3}];scope.elev=new Float32Array(2).fill(100);
scope.P.arrays.support=layer([1,0],{valid:1,total:8,cell_m:6});
run('renderInfo("support")');
assert.match($('iBody').innerHTML,/Valid stored[^<]*<button[^>]*>i<\/button><\/td><td class='v'><b>12\.5%<\/b> @ 3 m<br><small>1 \/ 8/);
assert.match($('iBody').innerHTML,/Valid shown/);
assert.match($('iShownValidity').innerHTML,/<b>100\.0%<\/b> @ 3 m<br><small>2 \/ 2/);
assert.match(run('validityRows(P.arrays.support)'),/Valid display.*<b>50\.0%<\/b> @ 6 m<br><small>1 \/ 2/s);
const originalInfo=$('iBody').innerHTML;
scope.G.dem_valid_fraction=0;run('renderInfo("support")');
assert.equal($('iBody').innerHTML,originalInfo,'counts are independent of DEM support');
assert.doesNotMatch(originalInfo,/relative to DEM|coverage|shared cells/);
run('setInfo(false)');assert.equal($('info').style.display,'none');$('reopen').onclick();assert.equal($('info').style.display,'');

let total=0,sites=0;
for(const file of fs.readdirSync(path.join(root,'viewer')).filter(f=>f.endsWith('_explorer.html'))){
  const page=fs.readFileSync(path.join(root,'viewer',file),'utf8');
  scope.P=JSON.parse(page.match(/<script id="payload" type="application\/json">([\s\S]*?)<\/script>/)[1]);
  scope.G=scope.P.grid;scope.W=scope.G.w;scope.H=scope.G.h;
  scope.levels=[{w:scope.W,h:scope.H,cell:scope.G.cell_m}];scope.curLevel=0;
  run('cache.clear()');scope.elev=run('decodeAt({...P.terrain,w:W,h:H})');
  for(const key of Object.keys(scope.P.arrays)){
    scope.key=key;run('renderInfo(key)');
    assert.equal($('iPath').textContent,key);
    assert(!/>\s*(undefined|NaN)\s*</.test($('iBody').innerHTML),'no missing metadata values');total++;
  }
  const key=Object.keys(scope.P.arrays).find(k=>scope.P.arrays[k].domain==='amp');
  scope.node={path:key,kids:new Map()};scope.q='';assert.equal(run('matches(node)'),true);
  scope.q=key.toLowerCase().split('/').pop();assert.equal(run('matches(node)'),true);
  scope.q='no-such-layer';assert.equal(run('matches(node)'),false);
  scope.node={path:'parent',kids:new Map([['child',{path:'parent/no-such-layer',kids:new Map()}]])};
  assert.equal(run('matches(node)'),true,'parents of matching descendants remain visible');sites++;
}
run('renderTabs()');
assert.equal($('tabs').children.length,4,'current pinned DEM, timeline, details and settings');
assert.deepEqual($('tabs').children.slice(0,3).map(x=>x.innerHTML),[
  "<span class='lbl'>Base DEM</span>","<span class='lbl'>Timeline</span>","<span class='lbl'>Details</span>"]);
assert.equal($('tabs').children[2].id,'tabEnd');
assert.equal($('tabs').children[3].attrs['aria-label'],'Open viewer settings');
run('activate("details")');assert.equal($('details').classList.contains('on'),true);assert.equal(scope.glOn,false);
run('activate("timeline")');assert.equal($('timeline').classList.contains('on'),true);assert.equal($('details').classList.contains('on'),false);
run('closeTab("details")');assert.equal($('tabs').children.length,4,'pinned tabs cannot close');
scope.P.arrays.dem=layer([1,2],{leaf:'elevation'});
scope.P.arrays.transient=layer([1,2],{short:'Layer <x>',pol:'VV'});
run('openLayer("transient");openLayer("transient")');
assert.equal($('tabs').children.length,5,'opening an existing layer does not duplicate its tab');
assert.equal(scope.glOn,true);assert.equal($('timeline').classList.contains('on'),false);
const transient=$('tabs').children[2];
assert.match(transient.innerHTML,/Layer &lt;x&gt;/,'tab labels escape exported metadata');
assert.match(transient.innerHTML,/VV/);assert.match(transient.className,/\bon\b/);
transient.onclick({target:{classList:{contains:c=>c==='x'}},stopPropagation:noop});
assert.equal($('tabs').children.length,4,'close action removes only the unpinned tab');
assert.match($('tabs').children[0].className,/\bon\b/,'closing the active layer returns to DEM');
scope.unsafe='<x> & "quoted"';assert.equal(run('esc(unsafe)'), '&lt;x&gt; &amp; &quot;quoted&quot;');
const statics=[...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);
assert.equal(new Set(statics).size,statics.length,'unique static IDs');
assert(!/\d+% of values/.test(html),'no retained-data percentage claims');
console.log('PASS: '+total+' current info renders across '+sites+' sites; presets/custom/tied/constant/empty/strided/lifted/symmetric ranges, stored/shown validity, exported Details counts, filters and pinned controllers (DOM/GL stubs; no browser QA).');
