const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
test('bridge inserts/removes only temporary layers and restores original selection',()=>{
  const {ctx,api}=bridgeHarness({withoutAngular:true}),P=ctx.P,original=P.arrays.dem;
  const choices=api.palettes('dem');assert.equal(choices[0].id,'terrain');
  choices[0].stops[0][1]='#ff0000';assert.equal(ctx.BUILTIN_STOPS.terrain[0][1],'#000000');
  assert.equal(api.context().current,'dem');
  api.show({id:'difference',label:'B minus A',unit:'m',grid:{w:2,h:2,dx:3,dy:-3,left:100,top:200},values:new Float32Array([1,NaN,-1,0]),lo:-1,hi:1,cmap:'diverging'});
  assert.equal(Object.keys(P.arrays).length,2);assert.equal(api.context().current,'dem');
  assert.deepEqual(Array.from(ctx.floats(ctx.primKey)),[1,NaN,-1,0]);
  assert.equal(typeof ctx.angularKind,'undefined');
  const oldKey=ctx.primKey;ctx.PREF.ranges={[oldKey]:[-.1,.1]};
  assert.equal(P.arrays.dem,original);api.clear();
  assert.deepEqual(Object.keys(P.arrays),['dem']);assert.equal(ctx.primKey,'dem');
  for(const values of Object.values(ctx.PREF))assert.equal(Object.keys(values).length,0);
  api.show({id:'difference',label:'New difference',grid:{w:2,h:2,dx:3,dy:-3,left:100,top:200},values:new Float32Array([100,NaN,-100,0]),lo:-100,hi:100});
  assert.notEqual(ctx.primKey,oldKey);assert.deepEqual(Array.from(ctx.PREF.ranges[ctx.primKey]),[-100,100]);
  const mainPreset={mode:'continuous',stops:[[0,'#000000'],[1,'#ffffff']]};ctx.PREF.customs={dem:mainPreset};
  api.show({id:'difference',label:'Styled difference',grid:{w:2,h:2,dx:3,dy:-3,left:100,top:200},values:new Float32Array([100,NaN,-100,0]),lo:-50,hi:100,
    style:{stops:[[0,'#ff0000'],[1,'#0000ff']],reverse:true}});
  const styledKey=ctx.primKey;
  assert.equal(ctx.PREF.palettes[styledKey],'__custom__');assert.equal(ctx.PREF.reverse[styledKey],true);
  assert.deepEqual(Array.from(ctx.PREF.ranges[styledKey]),[-50,100]);assert.equal(ctx.PREF.customs.dem,mainPreset);
  api.clear();assert.equal(ctx.PREF.customs.dem,mainPreset);assert.equal(ctx.PREF.customs[styledKey],undefined);
});

// Keep the original palette/range and tab functions in these integration checks.
const template=fs.readFileSync('explorer_template.html','utf8');
function templateFunction(name){
  const start=template.indexOf('function '+name+'('),next=template.indexOf('\nfunction ',start+1);
  return template.slice(start,next);
}
function bridgeHarness(options={}){
  const original={w:2,h:2,cell_m:3,label:'Elevation',leaf:'elevation',lo:1,hi:4,unit:'m',b64:'AQACAAMAAAA=',bits:16,valid:2,total:4};
  const stored={},P={site:'test',grid:{w:2,h:2,cell_m:3,origin:[100,200],pixel:[3,-3],full:[2,2],res_m:3,dem_valid_fraction:.5},identification:{common_crs_epsg:32612},arrays:{dem:original},dem_path:'dem'};
  const elements=new Map(),element=id=>{if(!elements.has(id))elements.set(id,{value:'',style:{setProperty(){}},classList:{toggle(){}},setAttribute(){}});return elements.get(id);};
  if(options.info){
    // Browser DOM boundary only: renderInfo below remains the real template function.
    const body=element('iBody');
    Object.defineProperty(body,'innerHTML',{set(html){
      this.rows=[...html.matchAll(/<tr><td class='k'>(.*?)<\/td><td class='v'>(.*?)<\/td><\/tr>/gs)]
        .map(m=>{
          const value={innerHTML:m[2]};
          Object.defineProperty(value,'textContent',{get(){return this.innerHTML.replace(/<[^>]*>/g,'');},set(v){this.innerHTML=String(v);}});
          return {children:[{textContent:m[1].replace(/<[^>]*>/g,'')},value]};
        });
    }});
    body.querySelectorAll=selector=>{assert.equal(selector,'tr');return body.rows;};
  }
  const uniforms={};
  const ctx={P,G:P.grid,W:2,H:2,DEM:'dem',window:{SnowCompareCore:require('./core')},primKey:'dem',ovKey:null,active:'dem',glOn:true,infoOpen:false,
    elev:new Float32Array([100,100,100,100]),shownInfoLevel:-1,opts:{colors:'smooth'},cellRendered:[null,null],
    values:{dem:new Float32Array([1,2,3,4])},floats:k=>ctx.values[k]||new Float32Array([1,2,3,4]),cache:new Map(),tabs:[{id:'dem',kind:'layer',key:'dem',pin:true},{id:'timeline',kind:'timeline',pin:true},{id:'details',kind:'details',pin:true}],
    PREF:{palettes:{},customs:{},ranges:{},rangeModes:{},reverse:{},savedPalettes:{},typeActive:{}},
    BUILTIN_STOPS:{terrain:[[0,'#000000'],[1,'#ffffff']],viridis:[[0,'#440154'],[1,'#fde725']],diverging:[[0,'#b42d1d'],[.5,'#f6f5f0'],[1,'#1d5aa7']]},
    PALETTE_LABELS:{terrain:'Terrain',viridis:'Viridis',diverging:'Diverging'},
    Uint8Array,Float32Array,Map,Math,Number,atob:s=>Buffer.from(s,'base64').toString('binary'),btoa:s=>Buffer.from(s,'binary').toString('base64'),
    setPrimary(k){ctx.primKey=k;ctx.uploaded=Array.from(ctx.floats(k));if(options.info){ctx.syncRangeStatus(k);ctx.renderInfo(k);}ctx.draw();},closeLegendEditor(){},renderDetails(){},renderTimeline(){},
    setOverlay(k){ctx.ovKey=k;ctx.overlayUploaded=k?Array.from(ctx.floats(k)):null;ctx.draw();},$:element,renderTabs(){},nodeAt:()=>null,
    draw(){},pauseRendering(){},syncLegendEditor(){},
    gl:{uniform1i:(k,v)=>{uniforms[k]=v;},uniform1f:(k,v)=>{uniforms[k]=v;}},
    U:Object.fromEntries(['uMaskBinary','uMaskBinary2','uLo','uHi','uLo2','uHi2'].map(k=>[k,k])),
    DOM:{meta:{c:'#ffffff'}},fmt:String,esc:String,levels:[{w:2,h:2,cell:3}],curLevel:0,
    PREF_KEY:'preferences',PREF_WINDOW_PREFIX:'prefs:',localStorage:{setItem(k,v){stored[k]=v;}}};
  vm.createContext(ctx);
  vm.runInContext(template.slice(template.indexOf('function productKind('),template.indexOf('function customRampCss(')),ctx);
  vm.runInContext(template.slice(template.indexOf('function savePrefs('),template.indexOf('applyUiPrefs(false);')),ctx);
  vm.runInContext(templateFunction('activate')+templateFunction('openLayer')+templateFunction('closeTab').split('/* ================= tree')[0],ctx);
  if(options.info){
    vm.runInContext(templateFunction('decodeAt')+template.slice(template.indexOf('const kvRow='),template.indexOf('let infoOpen='))+
      templateFunction('syncShownResolution')+templateFunction('renderInfo'),ctx);
  }
  if(options.realPrimary){
    Object.assign(ctx,{bVal:'values',toGL:v=>v,CM:{custom:0,viridis:1},RAMP:{},applyCustomUniforms(){},syncPaletteSettings(){},syncCellColors(){}});
    ctx.gl.bindBuffer=()=>{};ctx.gl.bufferData=(_target,v)=>{ctx.uploaded=Array.from(v);};
    for(const k of ['uCmap','uReverse','uDiverging','uAngleKind'])ctx.U[k]=k;
    vm.runInContext(templateFunction('customRampCss')+templateFunction('setPrimary'),ctx);
  }
  if(options.realDraw){
    // These integration assertions run the actual frame body synchronously;
    // request scheduling/visibility are covered in check_frame_scheduler.js.
    Object.assign(ctx,{renderingFrame:true,document:{hidden:false,addEventListener(){}},frameStatus(){}});
    Object.assign(ctx,{canvas:{clientWidth:100,clientHeight:100},opts:{lod:'auto',disp:'solid',sunAz:0,sunEl:0,relief:1},
      view:{dist:1,tx:0,ty:0,az:0},basis:()=>({e:[0,0,1]}),mul:()=>[],persp:()=>[],viewMat:()=>[],drawPits(){},frames:0,fpsT:0,performance:{now:()=>0}});
    for(const k of ['viewport','clear','uniformMatrix4fv','uniform3f','bindBuffer','drawElements'])ctx.gl[k]=()=>{};
    vm.runInContext(template.slice(template.indexOf('function autoLevel(){'),template.indexOf('/* ================= layers'))+
      templateFunction('draw'),ctx);
  }
  if(options.cells)vm.runInContext(templateFunction('decodeAt')+templateFunction('cellSamples'),ctx);
  if(options.averages)vm.runInContext(templateFunction('finishCellValues'),ctx);
  if(options.withoutAngular)ctx.angularKind=undefined;
  vm.runInContext(fs.readFileSync('viewer_compare/bridge.js','utf8'),ctx);
  return {ctx,api:ctx.window.SnowCompareViewer,stored,uniforms};
}
const comparison=(overrides={})=>({id:'difference',label:'Difference',unit:'m',grid:{w:2,h:2,dx:3,dy:-3,left:100,top:200},
  values:new Float32Array([-50,0,100,NaN]),lo:-10,hi:20,cmap:'diverging',
  style:{stops:[[0,'#ff0000'],[1,'#0000ff']],reverse:false},paletteName:'Saved · Red blue',rangeLabel:'Custom 25–75% stretch',...overrides});

test('Info separates stored counts from live shown samples and keeps exported counts in Details helpers',()=>{
  const {ctx}=bridgeHarness({info:true});
  ctx.P.arrays.dem={...ctx.P.arrays.dem,w:2,h:1,cell_m:6,valid:1,total:8,bits:8,
    lo:0,hi:254,b64:Buffer.from([1,0]).toString('base64')};
  ctx.values.dem=new Float32Array([0,NaN,1,NaN]);ctx.setPrimary('dem');
  const rows=ctx.$('iBody').rows;
  assert.ok(rows.some(r=>r.children[0].textContent.startsWith('Valid stored')));
  assert.ok(rows.some(r=>r.children[0].textContent.startsWith('Valid shown')),'main card reports current rendering-grid support');
  assert.equal(rows.some(r=>/Valid display|coverage|relative to DEM|shared cells/.test(r.children[0].textContent)),false);
  assert.match(rows.find(r=>r.children[0].textContent.startsWith('Valid stored')).children[1].innerHTML,/<b>12\.5%<\/b> @ 3 m.*1 \/ 8/s);
  assert.match(ctx.$('iShownValidity').innerHTML,/<b>50\.0%<\/b> @ 3 m.*2 \/ 4/s);
  assert.match(ctx.validityRows(ctx.P.arrays.dem),/Valid display.*<b>50\.0%<\/b> @ 6 m.*1 \/ 2/s);
  const raw=JSON.stringify(ctx.P.arrays.dem);ctx.levels.push({w:1,h:1,cell:6});ctx.curLevel=1;
  ctx.PREF.palettes.dem='viridis';ctx.PREF.ranges.dem=[-5,5];ctx.setPrimary('dem');
  assert.equal(ctx.$('iShown').textContent,'1 × 1 @ 6 m');
  assert.match(ctx.$('iShownValidity').innerHTML,/<b>100\.0%<\/b> @ 6 m.*1 \/ 1/s);
  assert.equal(JSON.stringify(ctx.P.arrays.dem),raw);
});

test('actual draw LOD changes refresh shown support with finite terrain and sampled edge positions',()=>{
  const {ctx}=bridgeHarness({info:true,realDraw:true});ctx.W=5;ctx.H=5;
  Object.assign(ctx.G,{w:5,h:5,cell_m:3});
  ctx.levels=[{w:5,h:5,cell:3},{w:3,h:3,cell:6},{w:2,h:2,cell:12},null];
  ctx.values.dem=Float32Array.from({length:25},(_,i)=>i);ctx.values.dem[1]=NaN;ctx.values.dem[12]=NaN;
  ctx.elev=new Float32Array(25).fill(100);ctx.elev[10]=NaN;
  ctx.setPrimary('dem');
  for(const [distance,dimensions,pct,count] of [[1,'5 × 5 @ 3 m','88.0','22 / 25'],[3,'3 × 3 @ 6 m','77.8','7 / 9'],[5,'2 × 2 @ 12 m','100.0','4 / 4']]){
    ctx.view.dist=distance;ctx.draw();
    assert.equal(ctx.$('iShown').textContent,dimensions);
    assert.match(ctx.$('iShownValidity').innerHTML,new RegExp('<b>'+pct.replace('.','\\.')+'%</b>.*'+count,'s'));
  }
  ctx.opts.lod='3';ctx.draw();assert.equal(ctx.curLevel,2,'unavailable mesh falls back to actual existing level');
  ctx.P.arrays.other={...ctx.P.arrays.dem};ctx.values.other=new Float32Array(25).fill(NaN);ctx.values.other[0]=0;
  ctx.openLayer('other');assert.match(ctx.$('iShownValidity').innerHTML,/<b>25\.0%<\/b> @ 12 m.*1 \/ 4/s);
  const shown=ctx.$('iShownValidity').innerHTML;ctx.view.tx=999;ctx.view.ty=-999;ctx.draw();
  assert.equal(ctx.$('iShownValidity').innerHTML,shown,'viewport and camera translation do not change grid support');
  assert.match(ctx.$('iShownValidity').title,/rendering.grid samples/i);
  assert.match(ctx.$('iShownValidity').title,/not visible pixels/i);
});

test('shown support caches by actual samples, terrain identity and level',()=>{
  const {ctx}=bridgeHarness({info:true});
  assert.equal(typeof ctx.shownCounts,'function');
  ctx.levels.push({w:1,h:1,cell:6});
  const values=new Float32Array([0,NaN,2,3]),terrain=new Float32Array([100,100,NaN,100]);
  const first=ctx.shownCounts(values,terrain,0);assert.deepEqual({...first},{valid:2,total:4});
  assert.equal(ctx.shownCounts(values,terrain,0),first,'cached count reused');
  const coarse=ctx.shownCounts(values,terrain,1);assert.deepEqual({...coarse},{valid:1,total:1});
  assert.equal(ctx.shownCounts(values,terrain,1),coarse);
  assert.equal(ctx.shownCounts(values,terrain,0),first,'returning to a level reuses its count');
  assert.deepEqual({...ctx.shownCounts(new Float32Array([NaN,1,2,3]),terrain,0)},{valid:2,total:4});
  const changedTerrain=new Float32Array(4).fill(100);
  assert.deepEqual({...ctx.shownCounts(values,changedTerrain,0)},{valid:3,total:4});
  assert.deepEqual({...ctx.shownCounts(new Float32Array(4).fill(NaN),changedTerrain,0)},{valid:0,total:4});
});

test('legacy 8/16-bit counts include valid zero, reject malformed optional counts and cache by layer object',()=>{
  const {ctx}=bridgeHarness({info:true}),decode=ctx.decodeAt;
  let decodes=0;ctx.decodeAt=L=>{decodes++;return decode(L);};
  for(const bits of [8,16]){
    const data=bits===8?Buffer.from([1,0,2,0]):Buffer.from([1,0,0,0,2,0,0,0]);
    for(const optional of [{},{display_valid:-1,display_total:4},{display_valid:5,display_total:4},
      {display_valid:1.5,display_total:4},{display_valid:'2',display_total:4},
      {display_valid:2,display_total:3},{display_valid:2,display_total:null}]){
      ctx.P.arrays.dem={...ctx.P.arrays.dem,bits,lo:0,hi:(1<<bits)-2,b64:data.toString('base64'),...optional};
      assert.equal(ctx.decodeAt(ctx.P.arrays.dem)[0],0,'code 1 represents a valid numeric zero');
      const old=decodes;ctx.displayCounts(ctx.P.arrays.dem);ctx.displayCounts(ctx.P.arrays.dem);
      assert.equal(decodes,old+1,'one native decode per immutable layer record');
      const html=ctx.validityRows(ctx.P.arrays.dem);
      assert.match(html,/<b>50\.0%<\/b>.*2 \/ 4/s);
    }
  }
  ctx.P.arrays.dem={...ctx.P.arrays.dem,display_valid:2,display_total:4};
  const old=decodes;ctx.displayCounts(ctx.P.arrays.dem);assert.equal(decodes,old,'valid exported counts avoid an extra decode');
  ctx.P.arrays.dem={...ctx.P.arrays.dem,display_valid:undefined,display_total:undefined,b64:Buffer.alloc(8).toString('base64')};
  assert.match(ctx.validityRows(ctx.P.arrays.dem),/<b>0\.0%<\/b>.*0 \/ 4/s);
});

test('missing or malformed stored counts remain unknown',()=>{
  const {ctx}=bridgeHarness({info:true});
  for(const native of [{valid:undefined},{total:undefined},{valid:null},{total:0},{valid:-1},{valid:5,total:4},{valid:'2'},{valid:1.5}]){
    ctx.P.arrays.dem={...ctx.P.arrays.dem,valid:2,total:4,...native};ctx.setPrimary('dem');
    const row=ctx.$('iBody').rows.find(r=>r.children[0].textContent.startsWith('Valid stored'));
    assert.ok(row,'stored count row must identify missing metadata');
    assert.match(row.children[1].innerHTML,/Unknown/);assert.doesNotMatch(row.children[1].innerHTML,/0\.0%|undefined|NaN/);
  }
});

test('temporary A/B and difference Info shows actual terrain-grid support and omits stored duplicates',()=>{
  const {ctx,api}=bridgeHarness({info:true});ctx.setPrimary('dem');
  const originalRows=JSON.stringify(ctx.$('iBody').rows),originalRange=ctx.$('rangeStateLabel').textContent;
  const a=[0,2,3,NaN],b=[0,NaN,NaN,4],difference=require('./core').difference(a,b).values;
  for(const [id,values] of [['a',a],['b',b],['difference',difference]]){
    api.show(comparison({id,values:new Float32Array(values)}));
    const rows=ctx.$('iBody').rows,display=rows.find(r=>r.children[0].textContent.startsWith('Valid shown'));
    assert.ok(display);assert.equal(rows.some(r=>/Valid stored|coverage|relative to DEM|shared cells/.test(r.children[0].textContent)),false);
    const valid=values.filter(Number.isFinite).length;
    assert.match(ctx.$('iShownValidity').innerHTML,new RegExp('<b>'+valid*25+'\\.0%</b> @ 3 m.*'+valid+' / 4','s'));
    assert.equal(ctx.P.arrays[ctx.primKey].comparisonTemporary,true);
    assert.equal(ctx.P.arrays[ctx.primKey].valid,valid,'legacy result-grid alias remains numeric');
    assert.equal(ctx.P.arrays[ctx.primKey].total,4);
    assert.equal(ctx.P.arrays[ctx.primKey].display_valid,valid);
  }
  ctx.openLayer('dem');assert.equal(JSON.stringify(ctx.$('iBody').rows),originalRows);
  assert.equal(ctx.$('rangeStateLabel').textContent,originalRange);
});

test('legacy templates keep finite result-grid aliases and accurately label A/B result availability',()=>{
  for(const label of ['coverage','valid-cell count relative to DEM']){
    const {ctx,api}=bridgeHarness(),row={children:[{textContent:label},{}]};
    ctx.$('iBody').querySelectorAll=()=>[row];
    api.show(comparison({id:'a',values:new Float32Array([0,1,2,NaN])}));
    const L=ctx.P.arrays[ctx.primKey];
    assert.equal(100*L.valid/L.total,75,'old Info arithmetic stays finite');
    assert.equal(row.children[0].textContent,'result cells');
    assert.equal(row.children[1].textContent,'3 / 4');
    assert.match(row.children[1].title,/selected.*result/i);
    assert.doesNotMatch(row.children[1].title,/shared finite/);
  }
});

test('mask mode and passing cutoff leave display validity on the original packed fraction grid',()=>{
  const {ctx,api}=bridgeHarness({info:true});
  ctx.P.arrays.mask={...ctx.P.arrays.dem,leaf:'coherence_mask',bits:8,lo:0,hi:254,b64:Buffer.from([1,2,3,0]).toString('base64')};
  ctx.values.mask=new Float32Array([0,.2,.8,NaN]);ctx.openLayer('mask');
  const original=JSON.stringify(ctx.$('iBody').rows),shown=ctx.$('iShownValidity').innerHTML;
  for(const cutoff of [0,.5,1]){api.setMaskOptions('mask',{mode:'binary',cutoff});assert.equal(JSON.stringify(ctx.$('iBody').rows),original);assert.equal(ctx.$('iShownValidity').innerHTML,shown);}
  api.setMaskOptions('mask',{mode:'average',cutoff:.5});assert.equal(JSON.stringify(ctx.$('iBody').rows),original);
});

test('temporary legend preserves comparison percentile provenance through tab return and range edits',()=>{
  const {ctx,api}=bridgeHarness({info:true});
  const exact='Robust 2–98% stretch · zero-centred limits';
  api.show(comparison({rangeLabel:exact}));const key=ctx.primKey,tab=ctx.active;
  assert.equal(ctx.$('rangeStateLabel').textContent,exact);
  assert.match(ctx.$('rangeState').title,/comparison/i);
  ctx.openLayer('dem');ctx.activate(tab);
  assert.equal(ctx.$('rangeStateLabel').textContent,exact);
  ctx.PREF.ranges[key]=[-5,15];ctx.setPrimary(key);
  assert.equal(ctx.$('rangeStateLabel').textContent,'Custom value limits');
  for(const mode of ['robust','detail']){
    ctx.PREF.rangeModes[key]=mode;ctx.setPrimary(key);
    assert.match(ctx.$('rangeStateLabel').textContent,/3D legend.*Difference terrain samples \(sampled ranks\)/);
    assert.notEqual(ctx.$('rangeStateLabel').textContent,exact);
  }
  ctx.PREF.rangeModes[key]='full';delete ctx.PREF.ranges[key];ctx.setPrimary(key);
  assert.equal(ctx.$('rangeStateLabel').textContent,'Full 0–100% stretch');
  ctx.PREF.rangeModes[key]='unknown';ctx.setPrimary(key);
  assert.equal(ctx.$('rangeStateLabel').textContent,'Custom value limits');
  api.show(comparison({rangeLabel:undefined}));
  assert.equal(ctx.$('rangeStateLabel').textContent,'Custom value limits');
});

test('zero-centred differences keep symmetric effective limits through native legend edits',()=>{
  const {ctx,api,uniforms}=bridgeHarness({info:true,realPrimary:true});
  api.show(comparison({zeroCentered:true,lo:-.564,hi:.564}));
  const key=ctx.primKey,raw=JSON.stringify(ctx.P.arrays[key]);
  for(const [mode,bounds,want] of [
    ['robust',[-.564,.220],[-.564,.564]],['detail',[-.2,.1],[-.2,.2]],
    ['custom',[-4,2],[-4,4]],['full',undefined,[-100,100]],
    ['robust',[3,3],[-100,100]],['custom',[1e-9,2e-9],[-1e-6,1e-6]],
  ]){
    ctx.PREF.rangeModes[key]=mode;ctx.PREF.ranges[key]=bounds;ctx.setPrimary(key);
    const a=api.appearance('difference');
    assert.deepEqual([a.lo,a.hi],want);assert.equal(a.zeroCentered,true);
    assert.deepEqual(Array.from(vm.runInContext('layerRange(primKey)',ctx)),want);
    assert.deepEqual([uniforms.uLo,uniforms.uHi],want);
    assert.equal(ctx.$('t1').textContent,'0');assert.equal(uniforms.uDiverging,0);
    assert.match(ctx.$('rangeStateLabel').textContent,/zero-centred limits/);
    assert.deepEqual(require('./export').rgb(0,a.lo,a.hi,true),[246,245,240,255]);
  }
  assert.equal(JSON.stringify(ctx.P.arrays[key]),raw,'display edits leave packed bounds and samples unchanged');
  for(const id of ['a','b','difference']){
    api.show(comparison({id,zeroCentered:id!=='difference',lo:-4,hi:2}));
    const a=api.appearance(id);assert.deepEqual([a.lo,a.hi],[-4,2]);assert.equal(a.zeroCentered,false);
  }
});

test('a comparison with no shared finite cells leaves original Info and legend intact',()=>{
  const {ctx,api}=bridgeHarness({info:true});ctx.setPrimary('dem');
  const originalRows=JSON.stringify(ctx.$('iBody').rows),originalRange=ctx.$('rangeStateLabel').textContent;
  assert.throws(()=>api.show(comparison({values:new Float32Array(4).fill(NaN)})),/no finite values/);
  assert.equal(ctx.primKey,'dem');assert.equal(api.selection().available,false);
  assert.equal(JSON.stringify(ctx.$('iBody').rows),originalRows);
  assert.equal(ctx.$('rangeStateLabel').textContent,originalRange);
});

test('selection observers follow actual layer and flat tab activation without discarding the comparison',()=>{
  const {ctx,api}=bridgeHarness(),events=[];
  assert.equal(typeof api.onSelection,'function');
  const stop=api.onSelection(s=>events.push({...s}));
  assert.deepEqual(events,[{key:'dem',tab:'dem',comparison:null,available:false}]);
  ctx.activate('dem');ctx.closeTab('dem');assert.equal(events.length,1);
  api.show(comparison());const key=ctx.primKey,tab=ctx.active;
  assert.equal(events.length,2);assert.deepEqual(events.at(-1),{key,tab,comparison:'difference',available:true});
  api.show(comparison({id:'a'}));
  assert.equal(events.length,3);assert.equal(events.at(-1).comparison,'a');
  ctx.P.arrays.vegetation={...ctx.P.arrays.dem,leaf:'veg_height'};ctx.openLayer('vegetation');
  assert.deepEqual(events.at(-1),{key:'vegetation',tab:ctx.active,comparison:null,available:true});
  for(const id of ['details','timeline']){
    ctx.activate(tab);ctx.activate(id);
    assert.equal(ctx.primKey,key);assert.deepEqual(events.at(-1),{key:null,tab:id,comparison:null,available:true});
    assert.equal(api.appearance('a'),null);
  }
  ctx.activate(tab);assert.equal(api.selection().comparison,'a');
  assert.equal(ctx.P.arrays[key].label,'Difference');
  const count=events.length;ctx.activate(tab);ctx.activate('missing');assert.equal(events.length,count);
  stop();ctx.openLayer('dem');assert.equal(events.length,count);
});

test('clearing after a new layer is selected preserves that layer and its overlay',()=>{
  for(const options of [undefined,{restore:false}]){
    const {ctx,api}=bridgeHarness();
    ctx.P.arrays.snow={...ctx.P.arrays.dem,leaf:'snow_depth'};ctx.P.arrays.vegetation={...ctx.P.arrays.dem,leaf:'veg_height'};
    ctx.openLayer('snow');ctx.setOverlay('snow');api.show(comparison());const key=ctx.primKey;
    ctx.openLayer('vegetation');ctx.setOverlay('vegetation');const tab=ctx.active;
    api.clear(options);
    assert.equal(ctx.primKey,'vegetation');assert.equal(ctx.active,tab);assert.equal(ctx.ovKey,'vegetation');
    assert.equal(ctx.P.arrays[key],undefined);assert.equal(ctx.tabs.some(t=>t.key===key),false);
    for(const field of ['palettes','customs','ranges','rangeModes','reverse'])assert.equal(ctx.PREF[field][key],undefined);
    assert.equal(api.selection().available,false);
  }
});

test('clearing on Details or Timeline does not reopen the old reference or overlay',()=>{
  for(const id of ['details','timeline']){
    const {ctx,api}=bridgeHarness();ctx.setOverlay('dem');api.show(comparison());
    ctx.activate(id);api.clear();
    assert.equal(ctx.active,id);assert.equal(ctx.glOn,false);assert.equal(ctx.ovKey,null);
    assert.equal(api.selection().key,null);assert.equal(api.selection().available,false);
  }
});

test('active comparison clear restores a reference whose original tab was closed and emits only final state',()=>{
  const {ctx,api}=bridgeHarness(),events=[];
  ctx.P.arrays.snow={...ctx.P.arrays.dem,leaf:'snow_depth'};ctx.openLayer('snow');ctx.setOverlay('snow');
  const sourceTab=ctx.active;api.show(comparison());ctx.closeTab(sourceTab);
  assert.equal(typeof api.onSelection,'function');
  api.onSelection(s=>events.push({...s}));api.clear();
  assert.equal(events.length,2);assert.equal(events.at(-1).key,'snow');assert.equal(events.at(-1).available,false);
  assert.equal(ctx.ovKey,'snow');assert.equal(ctx.tabs.some(t=>t.key==='snow'),true);
  assert.equal(Object.keys(ctx.P.arrays).filter(k=>k.startsWith('__temporary_comparison__/')).length,0);
});

test('closing active or inactive comparison tabs reports loss once and tolerates synchronous cleanup',()=>{
  for(const activeComparison of [true,false]){
    const {ctx,api}=bridgeHarness(),events=[];
    ctx.P.arrays.vegetation={...ctx.P.arrays.dem,leaf:'veg_height'};api.show(comparison());const tab=ctx.active,key=ctx.primKey;
    if(!activeComparison){ctx.openLayer('vegetation');ctx.setOverlay('vegetation');}
    assert.equal(typeof api.onSelection,'function');
    api.onSelection(s=>{events.push({...s});if(!s.available)api.clear({restore:false});});
    ctx.closeTab(tab);
    assert.equal(events.length,2);assert.equal(events.at(-1).available,false);assert.equal(events.at(-1).comparison,null);
    assert.equal(ctx.primKey,activeComparison?'dem':'vegetation');assert.equal(ctx.ovKey,activeComparison?null:'vegetation');
    assert.equal(ctx.P.arrays[key],undefined);assert.equal(api.appearance('difference'),null);
  }
});

test('switching comparison maps reuses one temporary tab and replaces its values',()=>{
  const {ctx,api}=bridgeHarness();api.show(comparison());const key=ctx.primKey;
  api.show(comparison({id:'a',label:'Reference A',values:new Float32Array([1,2,3,4])}));
  assert.equal(ctx.primKey,key);assert.equal(ctx.tabs.filter(t=>!t.pin).length,1);
  assert.equal(Object.keys(ctx.P.arrays).length,2);assert.deepEqual(Array.from(ctx.floats(key)),[1,2,3,4]);
  assert.equal(ctx.P.arrays[key].label,'Reference A');api.clear();
  assert.equal(ctx.primKey,'dem');assert.equal(ctx.tabs.filter(t=>!t.pin).length,0);
  api.show(comparison());assert.notEqual(ctx.primKey,key);
});

test('temporary payload retains full finite bounds while display keeps percentile limits',()=>{
  const {ctx,api}=bridgeHarness();api.show(comparison());const key=ctx.primKey,L=ctx.P.arrays[key];
  assert.deepEqual([L.lo,L.hi],[-50,100]);assert.deepEqual(Array.from(ctx.PREF.ranges[key]),[-10,20]);
  const decoded=require('./core').decode(L);assert.equal(decoded[0],-50);assert.equal(decoded[2],100);assert.ok(Number.isNaN(decoded[3]));
  delete ctx.PREF.ranges[key];ctx.PREF.rangeModes[key]='full';
  const shown=api.appearance('difference');assert.deepEqual([shown.lo,shown.hi],[-50,100]);assert.match(shown.rangeLabel,/Full/);
});

test('appearance readback preserves provenance and returns copies only for the active map',()=>{
  const {ctx,api}=bridgeHarness(),result=comparison();api.show(result);
  assert.equal(typeof api.appearance,'function');const shown=api.appearance('difference');
  assert.deepEqual([shown.lo,shown.hi],[-10,20]);assert.equal(shown.paletteName,'Saved · Red blue');
  assert.equal(shown.rangeLabel,'Custom 25–75% stretch');assert.equal(shown.style.reverse,false);
  shown.style.stops[0][1]='#ffffff';assert.equal(ctx.PREF.customs[ctx.primKey].stops[0][1],'#ff0000');
  result.style.stops[1][1]='#ffffff';assert.equal(api.appearance('difference').paletteName,'Saved · Red blue');
  assert.equal(api.appearance('a'),null);ctx.primKey='dem';assert.equal(api.appearance('difference'),null);
});

test('appearance follows original legend palette, reversal and range modes',()=>{
  const {ctx,api}=bridgeHarness();api.show(comparison());const key=ctx.primKey;
  ctx.PREF.palettes[key]='viridis';ctx.PREF.reverse[key]=true;ctx.PREF.ranges[key]=[-5,15];
  let shown=api.appearance('difference');assert.equal(shown.paletteName,'Viridis');assert.equal(shown.style.reverse,true);
  assert.equal(shown.style.builtin,'viridis');
  assert.equal(shown.style.stops[0][1],'#440154');assert.deepEqual([shown.lo,shown.hi],[-5,15]);assert.equal(shown.rangeLabel,'Custom value limits');
  for(const [mode,label]of [['robust','2–98%'],['detail','10–90%']]){
    ctx.PREF.rangeModes[key]=mode;const labelText=api.appearance('difference').rangeLabel;
    assert.ok(labelText.includes(label));assert.match(labelText,/3D legend.*Difference terrain samples \(sampled ranks\)/);
  }
  // The shader checks the scientific diverging flag, not the selected colour palette.
  ctx.P.arrays[key].cmap='diverging';ctx.PREF.rangeModes[key]='custom';
  shown=api.appearance('difference');assert.deepEqual([shown.lo,shown.hi],[-15,15]);
});

test('unusable percentile limits disclose full-range fallback and retain it after restyling',()=>{
  for(const mode of ['robust','detail'])for(const bounds of [undefined,null,[],[4],[3,3],[8,-2],[NaN,8],[-8,Infinity],[1,2,3]]){
    const {ctx,api}=bridgeHarness({info:true});api.show(comparison());
    ctx.PREF.rangeModes[ctx.primKey]=mode;ctx.PREF.ranges[ctx.primKey]=bounds;ctx.setPrimary(ctx.primKey);
    const shown=api.appearance('difference');
    assert.deepEqual([shown.lo,shown.hi],[-50,100]);
    assert.match(shown.rangeLabel,/full.*fallback/i,`${mode}: ${String(bounds)}`);
    assert.doesNotMatch(shown.rangeLabel,/Robust 2–98%|Detail 10–90%/);
    assert.equal(ctx.$('rangeStateLabel').textContent,shown.rangeLabel);
    api.show(comparison(shown));
    assert.equal(api.appearance('difference').rangeLabel,shown.rangeLabel);
  }
  for(const mode of ['robust','detail'])for(const bounds of [[-5,15],['-5','15']]){
    const {ctx,api}=bridgeHarness();api.show(comparison());
    ctx.PREF.rangeModes[ctx.primKey]=mode;ctx.PREF.ranges[ctx.primKey]=bounds;
    const shown=api.appearance('difference');
    assert.deepEqual([shown.lo,shown.hi],bounds);
    assert.match(shown.rangeLabel,mode==='robust'?/Robust 2–98%/:/Detail 10–90%/);
    assert.doesNotMatch(shown.rangeLabel,/fallback/i);
  }
});

test('new renderer receives angular A and B quantities while signed differences stay scalar',()=>{
  const {ctx,api}=bridgeHarness();
  ctx.P.arrays.aspect={leaf:'aspect',unit:'deg'};ctx.P.arrays.phase={leaf:'int_phase',unit:'rad'};
  assert.equal(ctx.angularKind('aspect'),1);assert.equal(ctx.angularKind('phase'),2);assert.equal(ctx.angularKind('dem'),0);
  for(const [id,mode,expected]of [['a','degrees',1],['b','radians',2],['difference','degrees',0],['difference','radians',0],['a','linear',0]]){
    api.show(comparison({id,mode}));assert.equal(ctx.angularKind(ctx.primKey),expected);
  }
  assert.equal(ctx.angularKind('aspect'),1);assert.equal(ctx.angularKind('phase'),2);
});

test('captured terrain-sample range provenance survives restyling the same map',()=>{
  const {ctx,api}=bridgeHarness();api.show(comparison({id:'a'}));
  ctx.PREF.rangeModes[ctx.primKey]='robust';ctx.PREF.ranges[ctx.primKey]=[-5,15];
  const captured=api.appearance('a');assert.match(captured.rangeLabel,/A terrain samples \(sampled ranks\)/);
  api.show(comparison({id:'a',...captured}));assert.equal(api.appearance('a').rangeLabel,captured.rangeLabel);
});

test('palette choices distinguish native shader palettes from saved custom colours',()=>{
  const {ctx,api}=bridgeHarness();
  const preset={id:'mine',name:'Mine',type:'elevation',mode:'continuous',stops:[[0,'#ff0000'],[1,'#0000ff']]};
  ctx.PREF.savedPalettes.mine=preset;
  let choices=api.palettes('dem');
  assert.equal(choices.find(p=>p.id==='terrain').builtin,'terrain');
  assert.equal(choices.find(p=>p.id==='__source__').builtin,'terrain');
  assert.equal(Object.hasOwn(choices.find(p=>p.id==='saved:mine'),'builtin'),false);
  ctx.PREF.palettes.dem='__custom__';ctx.PREF.customs.dem=preset;
  choices=api.palettes('dem');assert.equal(Object.hasOwn(choices.find(p=>p.id==='__source__'),'builtin'),false);
});

test('native display round trips preserve shader selection and supplied palette provenance',()=>{
  const {ctx,api}=bridgeHarness();
  const result=comparison({paletteName:'Native selection',style:{stops:[[0,'#440154'],[1,'#fde725']],reverse:true,builtin:'viridis'}});
  api.show(result);const key=ctx.primKey;
  assert.equal(ctx.PREF.palettes[key],'viridis');assert.equal(ctx.PREF.customs[key],undefined);
  const shown=api.appearance('difference');
  assert.equal(shown.style.builtin,'viridis');assert.equal(shown.style.reverse,true);
  assert.equal(shown.paletteName,'Native selection');assert.deepEqual([shown.lo,shown.hi],[-10,20]);
  assert.equal(ctx.P.arrays[key].cmap,'viridis');
  api.show({...result,...shown});assert.equal(ctx.PREF.palettes[key],'viridis');
  api.show(comparison());assert.equal(Object.hasOwn(api.appearance('difference').style,'builtin'),false);
});

test('saving original preferences excludes temporary entries and restores active memory',()=>{
  const {ctx,api,stored}=bridgeHarness();api.show(comparison());const key=ctx.primKey;
  const preset={name:'Saved original',mode:'continuous',stops:[[0,'#000000'],[1,'#ffffff']]};
  ctx.PREF.savedPalettes.original=preset;ctx.PREF.customs.dem=preset;ctx.PREF.palettes.dem='__custom__';
  ctx.PREF.ranges.dem=[1,4];ctx.PREF.rangeModes.dem='full';ctx.PREF.reverse.dem=true;
  const activeStyle=ctx.PREF.customs[key],customMap=ctx.PREF.customs;
  ctx.savePrefs();const saved=JSON.parse(stored.preferences);
  for(const field of ['palettes','customs','ranges','rangeModes','reverse']){
    assert.equal(saved[field][key],undefined);assert.deepEqual(saved[field].dem,ctx.PREF[field].dem);
    assert.ok(Object.hasOwn(ctx.PREF[field],key));
  }
  assert.equal(ctx.PREF.customs,customMap);assert.equal(ctx.PREF.customs[key],activeStyle);
  assert.deepEqual(saved.savedPalettes.original,preset);assert.ok(ctx.PREF.savedAt>0);
  assert.doesNotMatch(ctx.window.name,/__temporary_comparison__/);
});
test('PNG colour mapping treats NaN as transparent and centers differences on zero',()=>{
  const E=require('./export.js');
  assert.deepEqual(E.rgb(NaN,-2,2,true),[0,0,0,0]);
  assert.deepEqual(E.rgb(0,-2,2,true),[246,245,240,255]);
  assert.deepEqual(E.rgb(-2,-2,2,true),[180,45,29,255]);
  assert.deepEqual(E.rgb(2,-2,2,true),[29,90,167,255]);
});

test('mask options reclassify original fractions and remain isolated by layer',()=>{
  const {ctx,api,uniforms}=bridgeHarness();
  for(const key of ['maskA','maskB']){
    ctx.P.arrays[key]={...ctx.P.arrays.dem,leaf:'coherence_mask',label:key,unit:'',lo:.2,hi:.8};
    ctx.values[key]=new Float32Array([.2,.5,.8,NaN]);
  }
  assert.equal(typeof api.setMaskOptions,'function');
  assert.deepEqual({...api.maskOptions('maskA')},{mode:'average',cutoff:.5});
  ctx.openLayer('maskA');const original=Array.from(ctx.floats('maskA'));
  ctx.PREF.ranges.maskA=[.7,.8];
  api.setMaskOptions('maskA',{mode:'binary',cutoff:.5});
  assert.deepEqual(ctx.uploaded,[0,1,1,NaN]);
  assert.equal(uniforms.uMaskBinary,1);assert.equal(uniforms.uLo,0);assert.equal(uniforms.uHi,1);
  api.setMaskOptions('maskA',{mode:'binary',cutoff:.75});
  assert.deepEqual(ctx.uploaded,[0,0,1,NaN]);
  ctx.openLayer('maskB');assert.equal(uniforms.uMaskBinary,0);
  assert.deepEqual(Array.from(ctx.floats('maskB')),original);
  api.setMaskOptions('maskA',{mode:'average',cutoff:.75});
  assert.deepEqual(Array.from(ctx.floats('maskA')),original);
  assert.deepEqual(Array.from(ctx.values.maskA),original);
  const copied=api.maskOptions('maskA');copied.cutoff=0;
  assert.equal(api.maskOptions('maskA').cutoff,.75);
  assert.throws(()=>api.setMaskOptions('maskA',{mode:'binary',cutoff:NaN}),/cutoff/i);
  assert.equal(api.maskOptions('maskA').cutoff,.75);
  assert.equal(api.maskOptions('dem'),null);
  assert.throws(()=>api.setMaskOptions('dem',{mode:'binary',cutoff:.5}),/mask/i);
});

test('overlay mask flags reset and temporary transitions retain categorical bounds',()=>{
  const {ctx,api,uniforms}=bridgeHarness();
  ctx.P.arrays.mask={...ctx.P.arrays.dem,leaf:'coherence_mask',unit:''};
  ctx.values.mask=new Float32Array([0,.5,1,NaN]);
  api.setMaskOptions('mask',{mode:'binary',cutoff:1});ctx.setOverlay('mask');
  assert.deepEqual(ctx.overlayUploaded,[0,0,1,NaN]);assert.equal(uniforms.uMaskBinary2,1);
  ctx.setOverlay(null);assert.equal(uniforms.uMaskBinary2,0);
  api.show(comparison({maskKey:'mask',maskCategory:'transition',values:new Float32Array([-1,0,1,NaN]),lo:-1,hi:1}));
  assert.equal(uniforms.uMaskBinary,2);
  ctx.PREF.ranges[ctx.primKey]=[.2,.3];ctx.draw();
  assert.equal(uniforms.uLo,-1);assert.equal(uniforms.uHi,1);
  assert.deepEqual([api.appearance('difference').lo,api.appearance('difference').hi],[-1,1]);
  assert.equal(api.maskTargets()[0].key,'mask');
  ctx.openLayer('dem');assert.equal(uniforms.uMaskBinary,0);
  assert.deepEqual(Array.from(api.maskTargets()),[]);
});

module.exports={bridgeHarness};

test('Cells mode receives exact native comparison cells and transform without terrain resampling or packing',()=>{
 const {ctx,api}=bridgeHarness({cells:true});
 const values=new Float32Array([.123456789,NaN,0,.987654321,4,5,6,7]);
 api.show(comparison({grid:{w:4,h:2,dx:1.5,dy:-1.5,left:100.75,top:199.25},values}));
 const k=ctx.primKey,L=ctx.P.arrays[k];
 assert.deepEqual(Array.from(ctx.cellSamples(k)),Array.from(values),'keep exact result-grid values');
 assert.deepEqual(Array.from(L.origin),[100.75,199.25]);assert.deepEqual(Array.from(L.pixel),[1.5,-1.5]);
 assert.equal(ctx.floats(k).length,4,'Smooth and Valid shown still use terrain samples');
 api.clear();assert.equal(ctx.P.arrays[k],undefined);
});
test('Cells mask cutoff classifies original exported fractions and preserves valid zero and holes',()=>{
 const {ctx,api}=bridgeHarness({cells:true});
 const k='science/UAVSAR/INTERFEROMETRY/pair/grids/coherence_mask';
 ctx.P.arrays[k]={w:3,h:1,cell_m:6,bits:8,lo:0,hi:1,b64:Buffer.from([1,128,0]).toString('base64'),leaf:'coherence_mask'};
 assert.deepEqual(Array.from(ctx.cellSamples(k)),[0,.5,NaN]);
 api.setMaskOptions(k,{mode:'binary',cutoff:.6});assert.deepEqual(Array.from(ctx.cellSamples(k)),[0,0,NaN]);
 api.setMaskOptions(k,{mode:'binary',cutoff:.5});assert.deepEqual(Array.from(ctx.cellSamples(k)),[0,1,NaN]);
 api.setMaskOptions(k,{mode:'average',cutoff:.5});assert.deepEqual(Array.from(ctx.cellSamples(k)),[0,.5,NaN]);
});

test('Cells mask averaging receives original fractions and applies the cutoff once afterward',()=>{
 const {ctx,api}=bridgeHarness({cells:true,averages:true});
 const k='science/UAVSAR/INTERFEROMETRY/pair/grids/coherence_mask';
 ctx.P.arrays[k]={w:3,h:1,cell_m:6,bits:8,lo:0,hi:1,b64:Buffer.from([1,128,255]).toString('base64'),leaf:'coherence_mask'};
 api.setMaskOptions(k,{mode:'binary',cutoff:.6});
 assert.deepEqual(Array.from(ctx.cellSamples(k)),[0,.5,1],'never classify source cells before averaging');
 const averaged=new Float32Array([.5,NaN,0]);
 assert.deepEqual(Array.from(ctx.finishCellValues(k,averaged)),[0,NaN,0]);
 api.setMaskOptions(k,{mode:'binary',cutoff:.5});
 assert.deepEqual(Array.from(ctx.finishCellValues(k,averaged)),[1,NaN,0]);
 assert.deepEqual(Array.from(averaged),[.5,NaN,0],'cutoff changes leave cached mean fractions untouched');
});
