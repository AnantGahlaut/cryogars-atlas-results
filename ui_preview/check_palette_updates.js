/* Palette edits must update appearance without rebuilding unchanged layer data. */
const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(process.argv[2]||path.join(__dirname,'../explorer_template.html'),'utf8');
function production(name,optional=false){
  const start=source.indexOf('function '+name+'(');
  if(start<0&&optional)return '';
  assert.ok(start>=0,'missing production '+name);
  const end=source.indexOf('\n}',start);
  assert.ok(end>start,'missing closing brace for '+name);
  return source.slice(start,end+2)+'\n';
}
function fixture(colors='smooth'){
  const calls=[],nodes=new Map(),saved=[],data={
    a:new Float32Array([0,NaN,2,3]),b:new Float32Array([5,6,7,8]),
    c:new Float32Array([10,11,12,13])};
  const node=id=>{
    if(!nodes.has(id))nodes.set(id,{textContent:'',innerHTML:'',value:'55',disabled:false,
      style:{setProperty(k,v){this[k]=v;}},querySelectorAll(){return [];}});
    return nodes.get(id);
  };
  const arrays={a:{label:'A',leaf:'veg_height',domain:'lidar',lo:0,hi:3,unit:'m'},
    b:{label:'B',leaf:'snow_depth',domain:'lidar',lo:5,hi:8,unit:'m'},
    c:{label:'C',leaf:'veg_height',domain:'lidar',lo:10,hi:13,unit:'m'}};
  const PREF={palettes:{},customs:{},ranges:{},rangeModes:{},reverse:{},savedPalettes:{},typeActive:{}};
  const U=new Proxy({},{get:(_,key)=>key}),uniforms={};
  const context={Float32Array,Map,Set,Math,Number,JSON,P:{arrays},PREF,U,
    opts:{colors},primKey:null,ovKey:null,editingPreset:null,NODATA:-1e30,
    DOM:{lidar:{n:'Lidar',c:'green'},meta:{n:'Metadata',c:'gray'}},
    CM:{greens:2,viridis:4,custom:10},RAMP:{greens:'green',viridis:'purple'},
    bVal:'primary',bVal2:'overlay',$:node,fmt:String,
    layerCmap:k=>PREF.palettes[k]==='__custom__'?'custom':PREF.palettes[k]||'greens',
    layerRange:k=>PREF.ranges[k]||[arrays[k].lo,arrays[k].hi],
    layerReverse:k=>PREF.reverse[k]?1:0,isDiverging:()=>false,
    angularKind:()=>0,productKind:k=>arrays[k].leaf,
    customStops:k=>PREF.customs[k]?.stops||[],
    hexRgb:h=>[1,3,5].map(i=>parseInt(h.slice(i,i+2),16)/255),
    customRampCss:k=>JSON.stringify(PREF.customs[k]?.stops||[]),
    activeTypePreset:()=>null,makeCustomPalette(){throw new Error('unexpected palette creation');},
    activateTypePreset(){throw new Error('unexpected saved preset activation');},
    floats(k){calls.push(['floats',k]);return data[k];},
    syncRangeStatus(k){calls.push(['range',k]);},
    renderInfo(k){calls.push(['info',k]);node('info').innerHTML=k;},
    syncCellColors(){calls.push(['cells']);if(colors==='cells'){
      for(const key of [context.primKey,context.ovKey])if(key)calls.push(['texture',key]);}},
    syncPaletteSettings(){calls.push(['settings']);},
    syncLegendEditor(){calls.push(['editor']);},
    renderCustomEditor(){calls.push(['customEditor']);},
    draw(){calls.push(['draw']);},
    savePrefs(){calls.push(['save']);saved.push(JSON.parse(JSON.stringify(PREF)));},
    gl:{ARRAY_BUFFER:1,DYNAMIC_DRAW:2,bindBuffer(){},
      bufferData(target,values){calls.push(['upload',Array.from(values)]);},
      uniform1i(key,value){calls.push(['uniform',key]);uniforms[key]=value;},
      uniform1f(key,value){calls.push(['uniform',key]);uniforms[key]=value;},
      uniform1fv(key,value){calls.push(['uniform',key]);uniforms[key]=Array.from(value);},
      uniform3fv(key,value){calls.push(['uniform',key]);uniforms[key]=Array.from(value);}}
  };
  const ctx=vm.createContext(context);
  const toGL=source.slice(source.indexOf('const toGL='),source.indexOf('/* ================= gl'));
  vm.runInContext(toGL+['applyCustomUniforms','setPrimary','setOverlay','saveCustomLive',
    'changePalette','refreshColourType','setLayerRange'].map(name=>production(name)).join('')+
    production('refreshLayerStyle',true),ctx);
  return {ctx,calls,nodes,saved,data,uniforms,PREF,node,count:name=>calls.filter(c=>c[0]===name).length,
    reset(){calls.length=0;saved.length=0;}};
}
function noDataWork(f){
  for(const kind of ['floats','upload','info','cells','texture'])
    assert.equal(f.count(kind),0,'style update unexpectedly performed '+kind);
}

for(const colors of ['smooth','cells'])test(colors+': 100 live color edits preserve data and avoid uploads/info rebuilds',()=>{
  const f=fixture(colors),{ctx,PREF}=f;
  ctx.setPrimary('a');
  PREF.customs.a={mode:'continuous',stops:[[0,'#000000'],[1,'#ffffff']]};
  const before=JSON.stringify(ctx.P.arrays),data=Array.from(f.data.a);
  f.reset();
  for(let i=0;i<100;i++){
    PREF.customs.a.stops[0][1]='#'+(i+1).toString(16).padStart(6,'0');
    ctx.saveCustomLive(false);
  }
  noDataWork(f);
  assert.equal(f.count('settings'),0,'live edits must not recreate palette selectors');
  assert.equal(f.count('editor'),0,'live edits must not recreate the picker');
  assert.equal(f.count('customEditor'),0);
  assert.equal(f.saved.length,100,'each accepted edit persists the latest preference');
  assert.equal(PREF.palettes.a,'__custom__');
  assert.deepEqual(f.saved.at(-1).customs.a,PREF.customs.a);
  assert.equal(f.uniforms.uCmap,10);
  assert.equal(f.uniforms.uCustomN,2);
  assert.ok(Math.abs(f.uniforms.uCustomColors[2]-100/255)<1e-7);
  assert.equal(f.calls.filter(c=>c[0]==='uniform'&&c[1]==='uCustomColors').length,100);
  assert.equal(f.node('legName').textContent,'A');
  assert.equal(f.node('ramp').style.background,JSON.stringify(PREF.customs.a.stops));
  assert.equal(f.node('leCustomRamp').style.background,f.node('ramp').style.background);
  assert.equal(JSON.stringify(ctx.P.arrays),before);
  assert.deepEqual(Array.from(f.data.a),data);
});

test('one layer used as primary and overlay receives both style updates without uploads',()=>{
  const f=fixture('cells'),{ctx,PREF}=f;
  ctx.setPrimary('a');ctx.setOverlay('a');
  PREF.customs.a={mode:'continuous',stops:[[0,'#123456'],[1,'#abcdef']]};
  PREF.palettes.a='__custom__';PREF.ranges.a=[-5,20];PREF.reverse.a=true;
  f.reset();
  assert.equal(typeof ctx.refreshLayerStyle,'function','missing appearance-only refresh');
  ctx.refreshLayerStyle('a',false);
  noDataWork(f);
  assert.equal(f.count('settings'),0);assert.equal(f.count('editor'),0);
  for(const suffix of ['', '2']){
    assert.equal(f.uniforms['uCmap'+suffix],10);
    assert.equal(f.uniforms['uLo'+suffix],-5);assert.equal(f.uniforms['uHi'+suffix],20);
    assert.equal(f.uniforms['uReverse'+suffix],1);
  }
  assert.deepEqual(f.uniforms.uCustomColors,f.uniforms.uCustomColors2);
  assert.equal(ctx.primKey,'a');assert.equal(ctx.ovKey,'a');
});

test('normal layer setters still upload fresh values, including changes to the selected layer',()=>{
  const f=fixture('cells'),{ctx}=f;
  ctx.setPrimary('a');f.reset();
  f.data.a[0]=.75;
  ctx.setPrimary('a');
  assert.equal(f.count('floats'),1);assert.equal(f.count('upload'),1);
  assert.equal(f.calls.find(c=>c[0]==='upload')[1][0],.75);
  assert.ok(f.calls.find(c=>c[0]==='upload')[1][1]<-1e29,'nodata remains the GL sentinel');
  assert.equal(f.count('info'),1);assert.equal(f.count('cells'),1);assert.equal(f.count('texture'),1);
  f.reset();ctx.setOverlay('b');
  assert.equal(f.count('upload'),1);assert.equal(f.count('floats'),1);
  assert.equal(f.count('cells'),1);assert.equal(f.count('texture'),2);
  assert.equal(ctx.ovKey,'b');
});

test('appearance refresh for an inactive layer does not change the selected layers',()=>{
  const f=fixture('cells'),{ctx}=f;
  ctx.setPrimary('b');ctx.setOverlay('c');f.reset();
  assert.equal(typeof ctx.refreshLayerStyle,'function','missing appearance-only refresh');
  ctx.refreshLayerStyle('a');
  noDataWork(f);assert.equal(ctx.primKey,'b');assert.equal(ctx.ovKey,'c');
  assert.equal(f.count('uniform'),0);
});

test('palette selection and color range changes persist and update both roles without data work',()=>{
  const f=fixture('cells'),{ctx,PREF}=f;
  ctx.setPrimary('a');ctx.setOverlay('a');f.reset();
  ctx.changePalette('a','viridis');
  assert.equal(PREF.palettes.a,'viridis');assert.equal(f.saved.at(-1).palettes.a,'viridis');
  assert.equal(f.uniforms.uCmap,4);assert.equal(f.uniforms.uCmap2,4);
  noDataWork(f);f.reset();
  assert.equal(ctx.setLayerRange('a',[-2,7]),true);
  assert.deepEqual(Array.from(PREF.ranges.a),[-2,7]);
  assert.deepEqual(f.saved.at(-1).ranges.a,[-2,7]);
  assert.equal(f.uniforms.uLo,-2);assert.equal(f.uniforms.uLo2,-2);
  assert.equal(f.uniforms.uHi,7);assert.equal(f.uniforms.uHi2,7);
  noDataWork(f);
});

test('type palette refresh updates distinct primary and overlay layers without data work',()=>{
  const f=fixture('cells'),{ctx,PREF}=f;
  ctx.setPrimary('a');ctx.setOverlay('c');
  PREF.palettes.a='viridis';PREF.palettes.c='viridis';f.reset();
  ctx.refreshColourType('veg_height');
  noDataWork(f);
  assert.equal(f.uniforms.uCmap,4);assert.equal(f.uniforms.uCmap2,4);
  assert.equal(ctx.primKey,'a');assert.equal(ctx.ovKey,'c');
});

test('actual mask bridge keeps categorical colors and bounds; cutoff changes still reupload data',()=>{
  const f=fixture('cells'),{ctx,PREF}=f;
  Object.assign(ctx,{window:{SnowCompareCore:require('../viewer_compare/core'),
    SnowCompareExport:require('../viewer_compare/export')},
    tabs:[{id:'a',kind:'layer',key:'a'}],active:'a',activate(){},closeTab(){}});
  ctx.P.arrays.a.leaf='coherence_mask';ctx.P.arrays.a.unit='';
  ctx.P.arrays.a.lo=0;ctx.P.arrays.a.hi=1;
  f.data.a.set([.2,.5,.8,NaN]);const original=Array.from(f.data.a);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../viewer_compare/bridge.js'),'utf8'),ctx);
  const api=ctx.window.SnowCompareViewer;
  ctx.setPrimary('a');ctx.setOverlay('a');
  api.setMaskOptions('a',{mode:'binary',cutoff:.5});
  PREF.customs.a={mode:'continuous',stops:[[0,'#aa2200'],[1,'#00bbff']]};
  PREF.ranges.a=[-9,9];f.reset();ctx.saveCustomLive(false);
  noDataWork(f);assert.equal(f.count('settings'),0);
  assert.equal(f.uniforms.uMaskBinary,1);assert.equal(f.uniforms.uMaskBinary2,1);
  assert.equal(f.uniforms.uLo,0);assert.equal(f.uniforms.uHi,1);
  assert.equal(f.uniforms.uLo2,0);assert.equal(f.uniforms.uHi2,1);
  assert.deepEqual(f.uniforms.uCustomColors,f.uniforms.uCustomColors2);
  assert.match(f.node('legDom').textContent,/0–1 block state/);
  assert.match(f.node('rangeStateLabel').textContent,/fraction ≥ 0\.5/);
  assert.deepEqual(Array.from(f.data.a),original);
  f.reset();api.setMaskOptions('a',{mode:'binary',cutoff:.75});
  assert.equal(f.count('upload'),2,'cutoff updates both active data buffers');
  for(const call of f.calls.filter(c=>c[0]==='upload')){
    assert.deepEqual(call[1].slice(0,3),[0,0,1]);assert.ok(call[1][3]<-1e29);
  }
  assert.ok(f.count('cells')>0,'changed cutoff refreshes the displayed cell classes');
  assert.deepEqual(Array.from(f.data.a),original);
});
