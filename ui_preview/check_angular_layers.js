/* Exercise actual layer-selection functions; shader pixels are checked separately. */
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(path.join(__dirname,'../explorer_template.html'),'utf8');
function extract(start,end){
  const a=html.indexOf(start),b=html.indexOf(end,a+start.length);
  assert(a>=0&&b>a,start);return html.slice(a,b);
}
const calls={},nodes=new Map(),noop=()=>{};
const node=id=>{
  if(!nodes.has(id))nodes.set(id,{style:{setProperty:noop},value:'50'});
  return nodes.get(id);
};
const arrays={
  aspect:{leaf:'aspect',unit:'',source:'storedAspect',cmap:'cyclic'},
  degrees:{leaf:'aspect',unit:'deg',cmap:'gray'},
  phase:{leaf:'int ∠phase',unit:'rad',cmap:'gray'},
  phaseFullUnit:{leaf:'int ∠phase',unit:'radians'},
  scalar:{leaf:'snow_depth',unit:'m',cmap:'cyclic'},
  slope:{leaf:'slope',unit:'degrees',cmap:'cyclic'},
  unwrapped:{leaf:'unw',unit:'rad',cmap:'cyclic'},
  magnitude:{leaf:'int |magnitude|',unit:'',cmap:'cyclic'},
  unknown:{leaf:'other',unit:'degrees',cmap:'cyclic'},
  missingUnit:{leaf:'aspect'},
  wrongUnit:{leaf:'aspect',unit:'m',source:'storedAspect'}
};
const scope=vm.createContext({P:{arrays},primKey:null,ovKey:null,
  nodeAt:p=>p==='storedAspect'?{attrs:{units:'degrees clockwise from north'}}:undefined,
  $:node,DOM:{meta:{}},CM:{gray:7,cyclic:6,custom:10},RAMP:{},
  bVal:{},bVal2:{},U:new Proxy({},{get:(_,key)=>key}),
  gl:{bindBuffer:noop,bufferData:noop,uniform1f:noop,uniform1i:(u,v)=>{calls[u]=v;}},
  layerCmap:k=>scope.palette||arrays[k].cmap||'gray',
  layerRange:()=>[0,360],layerReverse:()=>0,isDiverging:()=>false,
  floats:()=>new Float32Array([359,1]),toGL:a=>a,applyCustomUniforms:noop,
  customRampCss:()=>'',fmt:String,syncRangeStatus:noop,renderInfo:noop,
  syncPaletteSettings:noop,syncLegendEditor:noop,syncCellColors:noop,draw:noop
});
vm.runInContext(extract('function productKind(k){','const PRODUCT_CMAP='),scope);
vm.runInContext(extract('function setPrimary(k,','/* ================= info card'),scope);
let cases=0;
for(const palette of ['gray','cyclic','custom']){
  scope.palette=palette;
  for(const [key,want] of Object.entries({aspect:1,degrees:1,phase:2,phaseFullUnit:2,
    scalar:0,slope:0,unwrapped:0,magnitude:0,unknown:0,missingUnit:0,wrongUnit:0})){
    scope.key=key;
    vm.runInContext('setPrimary(key);setOverlay(key)',scope);
    assert.equal(calls.uAngleKind,want,`primary ${key} with ${palette}`);
    assert.equal(calls.uAngleKind2,want,`overlay ${key} with ${palette}`);
    cases++;
  }
}
vm.runInContext('setOverlay("aspect");setOverlay("__none__")',scope);
assert.equal(calls.uAngleKind2,0,'disabling overlay resets angular mode');
console.log(`PASS: ${cases} quantity/unit/palette combinations in both actual layer setters, including mode resets.`);
