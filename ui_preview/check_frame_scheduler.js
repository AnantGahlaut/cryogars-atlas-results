const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('explorer_template.html','utf8');
const render=source.slice(source.indexOf('/* ================= render ================= */'),source.indexOf('/* ================= probe ================= */'));
function harness(){
 let time=0,next=0,draws=0;const raf=new Map(),timers=new Map(),events={},elements=new Map(),noop=()=>{};
 const $=id=>{if(!elements.has(id))elements.set(id,{textContent:'',setAttribute(){},style:{}});return elements.get(id);};
 const ctx={performance:{now:()=>time},document:{hidden:false,addEventListener:(k,f)=>events[k]=f},window:{devicePixelRatio:1},
  requestAnimationFrame:f=>{raf.set(++next,f);return next;},cancelAnimationFrame:id=>raf.delete(id),
  setTimeout:(f,delay)=>{timers.set(++next,{f,at:time+delay});return next;},clearTimeout:id=>timers.delete(id),
  ResizeObserver:class{constructor(f){events.resize=f;}observe(){}},
  canvas:{clientWidth:1200,clientHeight:800,width:1200,height:800},
  gl:new Proxy({drawElements(){draws++;}},{get:(o,k)=>k in o?o[k]:noop}),
  opts:{lod:'0',colors:'smooth',disp:'solid',sunAz:315,sunEl:42,relief:.6},
  levels:[{w:632,h:689,cell:24,solidN:2500000}],curLevel:0,shownInfoLevel:0,cellColorLevel:0,
  syncCellColors:noop,syncShownResolution:noop,basis:()=>({e:[0,0,1]}),view:{dist:2,tx:0,ty:0,az:0},
  U:{},mul:()=>new Array(16).fill(0),persp:noop,viewMat:noop,Float32Array,tris:0,drawPits:noop,$};
 vm.createContext(ctx);vm.runInContext(render,ctx);
 return{ctx,$,events,raf,get draws(){return draws;},run:s=>vm.runInContext(s,ctx),
  at(t,fireTimers=true){time=t;if(fireTimers)for(const [id,item]of [...timers])if(item.at<=time){timers.delete(id);item.f();}},
  frame(t){time=t;const jobs=[...raf.values()];raf.clear();for(const f of jobs)f(t);}};
}
test('many redraw requests submit one frame and do not keep drawing while idle',()=>{
 const h=harness();for(let i=0;i<50;i++)h.ctx.draw();
 assert.equal(h.draws,0,'input handlers must not render synchronously');assert.equal(h.raf.size,1);
 h.frame(16);assert.equal(h.draws,1);assert.equal(h.raf.size,0);
 h.at(600);assert.equal(h.$('sFps').textContent,'idle');assert.equal(h.$('sFpsUnit').textContent,'');
 h.frame(700);assert.equal(h.draws,1);
});
test('scheduled draw invokes the late-bound comparison wrapper at frame time',()=>{
 const h=harness();h.ctx.draw();h.ctx.wrapped=0;
 h.run('const original=draw;draw=function(){wrapped++;return original();};');
 h.frame(16);assert.equal(h.ctx.wrapped,1);assert.equal(h.draws,1);
});
test('the actual boot starts one frame rather than an idle RAF loop',()=>{
 const h=harness(),start=source.lastIndexOf('activate("dem");')+'activate("dem");'.length;
 h.run(source.slice(start,source.lastIndexOf('})();')));
 h.frame(16);h.frame(32);assert.equal(h.draws,1);assert.equal(h.raf.size,0);
});
test('idle gaps reset timing but a pending frame delayed by work still reports the stall',()=>{
 const h=harness();h.ctx.draw();h.frame(16);h.at(600);
 h.at(30000);h.ctx.draw();h.frame(30016);
 assert.ok(!Number.isFinite(Number(h.$('sFps').textContent)),'do not report the idle gap as 0 or 1 FPS');
 for(let i=2;i<=32;i++){h.at(30000+i*16);h.ctx.draw();h.frame(30000+i*16);}
 assert.ok(Number(h.$('sFps').textContent)>=59,'idle pause must not become a 1 FPS sample');
 h.at(30600);h.ctx.draw();h.at(32600);h.frame(32600);
 assert.ok(Number(h.$('sFps').textContent)<5,'a requested but blocked frame is a real stall');
});
test('hidden and flat scenes cancel rendering; visibility and CSS resize request one fresh frame',()=>{
 const h=harness();h.ctx.draw();h.ctx.document.hidden=true;
 h.events.visibilitychange();assert.equal(h.raf.size,0);h.ctx.draw();assert.equal(h.raf.size,0);
 h.at(30000);h.ctx.document.hidden=false;h.events.visibilitychange();assert.equal(h.raf.size,1);
 h.frame(30016);assert.equal(h.draws,1);
 h.events.resize();h.events.resize();assert.equal(h.raf.size,1);h.frame(30032);assert.equal(h.draws,2);
 h.run('glOn=false;');h.ctx.draw();assert.equal(h.raf.size,0);assert.equal(h.$('sFps').textContent,'idle');
 h.run('glOn=true;');h.ctx.draw();h.frame(30048);assert.equal(h.draws,3);
});
