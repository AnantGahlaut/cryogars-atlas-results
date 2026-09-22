const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('explorer_template.html','utf8');
function fn(name){const a=source.indexOf('function '+name+'(');assert.ok(a>=0,'missing '+name);const b=source.indexOf('\nfunction ',a+1);return source.slice(a,b);}
test('cell coordinates honor physical pixel centers, independent grids and all terrain detail levels',()=>{
 const G={origin:[600000,4900000],pixel:[24,-24],cell_m:24},W=9,H=7,scale=2/192;
 const ctx={G,W,H,scale};vm.createContext(ctx);vm.runInContext(fn('cellGridMap'),ctx);
 for(const L of [{cell_m:24},{cell_m:48},{cell_m:6,origin:[600018,4899982],pixel:[6,-6]}]){
  const origin=L.origin||G.origin,pixel=L.pixel||G.pixel.map(v=>v*L.cell_m/G.cell_m),m=ctx.cellGridMap(L);
  for(const step of [1,2,4,8])for(let j=0;j<H;j+=step)for(let i=0;i<W;i+=step){
   const x=(i*24-(W-1)*12)*scale,z=(j*24-(H-1)*12)*scale;
   assert.ok(Math.abs(x*m[0]+m[2]-(G.origin[0]+(i+.5)*24-origin[0])/pixel[0])<1e-9);
   assert.ok(Math.abs(z*m[1]+m[3]-(G.origin[1]-(j+.5)*24-origin[1])/pixel[1])<1e-9);
  }
 }
});
test('native cell samples retain their exported dimensions and missing zero codes',()=>{
 const P={arrays:{x:{w:3,h:1,bits:8,lo:0,hi:254,b64:Buffer.from([1,0,255]).toString('base64')}}};
 const ctx={P,Float32Array,atob:s=>Buffer.from(s,'base64').toString('binary')};vm.createContext(ctx);
 vm.runInContext(fn('decodeAt')+fn('cellSamples'),ctx);
 assert.deepEqual(Array.from(ctx.cellSamples('x')),[0,NaN,254]);
});


test('Cells averages to live Detail, aligns both textures, and updates validity before drawing',()=>{
 const elements=new Map(),buttons=['smooth','cells'].map(c=>({dataset:{c},setAttribute(k,v){this[k]=v;}}));
 const $=id=>{if(!elements.has(id))elements.set(id,{disabled:false,textContent:'',style:{},setAttribute(){},querySelectorAll:()=>buttons});return elements.get(id);};
 const layers={a:{w:16,h:16,cell_m:24},b:{w:2,h:2,cell_m:192}};
 const values={a:Float32Array.from({length:256},(_,i)=>Math.floor(i/16)%8*8+i%8+(i%16>=8?100:0)+(i>=128?200:0)),
   b:new Float32Array([.25,.5,.75,1])};
 values.a[0]=NaN;for(let r=8;r<16;r++)for(let c=8;c<16;c++)values.a[r*16+c]=NaN;
 const uploads=[],maps=[],uniforms={},calls=[],events={};
 const ctx={P:{arrays:layers},G:{origin:[100,200],pixel:[24,-24],cell_m:24},W:16,H:16,scale:1/192,
   opts:{colors:'smooth',lod:'3',disp:'solid',sunAz:0,sunEl:0,relief:1},
   levels:[{w:16,h:16,cell:24},{w:8,h:8,cell:48},{w:4,h:4,cell:96},{w:2,h:2,cell:192}],curLevel:3,
   primKey:'a',ovKey:'b',cellTextureLimit:2048,cellTextures:['t0','t1'],$,
   cellSamples:k=>{calls.push(k);return values[k];},floats:k=>values[k],angularKind:()=>0,
   U:{uCellColors:'mode',uCellMap:'map0',uCellMap2:'map1'},
   gl:{activeTexture(){},bindTexture(){},texImage2D(...args){uploads.push(args);},uniform4fv(k,v){maps.push([k,v]);},uniform1i(k,v){uniforms[k]=v;}},
   elev:new Float32Array(256).fill(100),shownValidityCache:new WeakMap(),shownInfoKey:'a',
   glOn:true,renderingFrame:true,document:{hidden:false,addEventListener(){}},frameStatus(){},
   window:{},canvas:{clientWidth:100,clientHeight:100},view:{dist:1,tx:0,ty:0,az:0},
   basis:()=>({e:[0,0,1]}),mul:()=>[],persp:()=>[],viewMat:()=>[],drawPits(){},frames:0,fpsT:0,performance:{now:()=>0},
   seg(id,attr,callback){events[id]=callback;}};
 ctx.elev[128]=NaN;
 for(const k of ['viewport','clear','uniformMatrix4fv','uniform3f','uniform1f','bindBuffer','drawElements'])ctx.gl[k]=()=>{};
 vm.createContext(ctx);
 vm.runInContext(source.slice(source.indexOf('function averageCellValues('),source.indexOf('function setPrimary('))+
   fn('cellCounts')+fn('validityValue')+fn('shownCounts')+fn('syncShownResolution')+fn('cellProbeValue')+
   source.slice(source.indexOf('function autoLevel(){'),source.indexOf('/* ================= layers'))+fn('draw'),ctx);
 for(const name of ['colorSeg','lodSeg'])vm.runInContext(source.match(new RegExp('seg\\("'+name+'",[^\\n]+'))[0],ctx);
 ctx.syncCellColors();assert.equal(uniforms.mode,0);assert.deepEqual(calls,[]);
 assert.match($('iShownValidity').innerHTML,/<b>25\.0%<\/b> @ 192 m/,'Smooth retains stride-point counts');
 events.colorSeg('cells');assert.equal(uniforms.mode,1);assert.deepEqual(calls,['a','b']);
 assert.deepEqual(uploads.map(v=>[v[3],v[4]]),[[2,2],[2,2]],'192m means two by two blocks, not16x16');
 assert.deepEqual(Array.from(uploads[0][8]),[32,131.5,231.5,NaN],'64-source-cell averages, not decimated vertices');
 assert.deepEqual(Array.from(uploads[1][8]),[.25,.5,.75,1]);
 assert.deepEqual(Array.from(maps[0][1]),Array.from(maps[1][1]),'both layers use the current detail-grid footprint');
 assert.match($('cellColorNote').textContent,/192 m block/);
 assert.equal(ctx.cellProbeValue(0,{x:-.75,z:-.75}),32);
 assert.equal(ctx.cellProbeValue(0,{x:-.25,z:-.25}),32,'hover matches the same averaged block at different positions');
 assert.equal(ctx.cellProbeValue(0,{x:.75,z:-.75}),131.5);
 assert(Number.isNaN(ctx.cellProbeValue(0,{x:.75,z:.75})),'empty block hover stays missing');
 assert.equal(ctx.cellProbeValue(1,{x:-.75,z:-.75}),.25);
 assert.match($('iShownValidity').innerHTML,/<b>50\.0%<\/b> @ 192 m/,'partly missing blocks gain valid averages');
 for(const id of ['sunAz','sunEl','relief'])assert.equal($(id).disabled,true);
 const firstMean=uploads[0][8];
 ctx.draw();ctx.draw();assert.equal(uploads.length,2,'stable frames do no averaging or uploads');
 events.lodSeg('2');assert.equal(ctx.curLevel,2);assert.deepEqual(uploads.slice(-2).map(v=>[v[3],v[4]]),[[4,4],[4,4]]);
 assert.match($('cellColorNote').textContent,/96 m block/);assert.match($('iShownValidity').innerHTML,/@ 96 m/);
 ctx.view.dist=7;events.lodSeg('auto');assert.equal(ctx.curLevel,3);assert.equal(uploads.at(-2)[8],firstMean,'cached mean reused when returning to a detail');
 const used=calls.length;ctx.draw();assert.equal(calls.length,used);
 events.colorSeg('smooth');assert.equal(uniforms.mode,0);assert.match($('iShownValidity').innerHTML,/<b>25\.0%<\/b> @ 192 m/);
 for(const id of ['sunAz','sunEl','relief'])assert.equal($(id).disabled,false);
 ctx.cellTextureLimit=1;events.colorSeg('cells');assert.equal(uniforms.mode,0);assert.equal(ctx.opts.colors,'smooth');
 assert.match($('cellColorNote').textContent,/texture limit/);assert.equal(buttons[0]['aria-pressed'],'true');
});
