/* Numerical display-block aggregation checks; no viewer, browser or archives. */
const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const html=fs.readFileSync(process.argv[2]||path.join(__dirname,'../explorer_template.html'),'utf8');
const start=html.indexOf('function averageCellValues(');
assert.ok(start>=0,'missing production averageCellValues function');
const end=html.indexOf('\n}',start);
assert.ok(end>start,'missing averageCellValues closing brace');
const scope=vm.createContext({Float32Array});
vm.runInContext(html.slice(start,end+2),scope);
const average=scope.averageCellValues;
const grid=(w,h=1,pixel=[1,-1],origin=[0,0],extent)=>({w,h,pixel,origin,...(extent?{extent}:{})});
const close=(actual,expected,tolerance=1e-5)=>{
 assert.equal(actual.length,expected.length);
 actual.forEach((value,i)=>Number.isNaN(expected[i])?assert.ok(Number.isNaN(value),'cell '+i+' should be missing'):
  assert.ok(Math.abs(value-expected[i])<=tolerance,'cell '+i+': '+value+' ≠ '+expected[i]));
};

test('24 m to 192 m averages all 64 cells rather than decimating one sample',()=>{
 const values=Float32Array.from({length:64},(_,i)=>i);
 const result=average(values,grid(8,8,[24,-24]),grid(1,1,[192,-192]));
 assert.ok(result instanceof Float32Array);close(result,[31.5]);assert.notEqual(result[0],values[0]);
});

test('finite zero contributes; missing values do not dilute or fill empty blocks',()=>{
 close(average(new Float32Array([0,NaN,6,Infinity]),grid(4),grid(1,1,[4,-1])),[3]);
 close(average(new Float32Array([NaN,NaN,0,NaN]),grid(4),grid(2,1,[2,-1])),[NaN,0]);
 close(average(new Float32Array([NaN,Infinity,-Infinity]),grid(3),grid(1,1,[3,-1])),[NaN]);
});

test('fractional source offsets use overlap area and each block has its own valid denominator',()=>{
 close(average(new Float32Array([0,10]),grid(2,1,[1,-1],[.5,10]),grid(2,1,[2,-1],[0,10])),[10/3,10]);
 // Source-cell overlap areas are 1, 1/2, 1/2 and 1/4; weighted mean = 10.
 close(average(new Float32Array([0,10,20,30]),grid(2,2,[1,-1],[.5,1.5]),grid(1,1,[2,-2],[0,2])),[10]);
});

test('non-integer spacing ratios use all intersected source cells',()=>{
 close(average(new Float32Array([0,12]),grid(2,1,[3,-1]),grid(3,1,[2,-1])),[0,6,12]);
 close(average(new Float32Array([2,8]),grid(2,1,[2,-2]),grid(4,2)),[2,2,8,8,2,2,8,8]);
});

test('outside source extent and source holes remain missing',()=>{
 close(average(new Float32Array([1,NaN,3]),grid(3),grid(5,1,[1,-1],[-1,0])),[NaN,1,NaN,3,NaN]);
 close(average(new Float32Array([5]),grid(1),grid(1,1,[1,-1],[0,1])),[NaN]);
});

test('padded target edge blocks exclude source overhang beyond retained terrain extent',()=>{
 close(average(new Float32Array([0,2,4,100]),grid(4),grid(2,1,[2,-1],[0,0],[3,-1])),[1,4]);
 close(average(new Float32Array([3,100,100,100]),grid(2,2),grid(1,1,[2,-2],[0,0],[1,-1])),[3]);
 close(average(new Float32Array([1,2,3,4]),grid(4),grid(3,1,[2,-1],[0,0],[3,-1])),[1.5,3,NaN]);
});

test('aspect and phase average directions across their seams, with canonical output',()=>{
 const source=grid(2),target=grid(1,1,[2,-1]);
 const aspect=average(new Float32Array([359,1]),source,target,1);
 assert.ok(aspect[0]>=0&&aspect[0]<360);close(aspect,[0]);
 const phase=average(new Float32Array([Math.PI-.1,-Math.PI+.1]),source,target,2);
 assert.ok(phase[0]>=-Math.PI-1e-6&&phase[0]<Math.PI);close(phase,[-Math.PI]);
 close(average(new Float32Array([350,NaN]),source,target,1),[350]);
 close(average(new Float32Array([0,Math.PI/2]),source,target,2),[Math.PI/4]);
});

test('circular cancellation is missing and unequal overlap favors the supported direction',()=>{
 const source=grid(2),target=grid(1,1,[2,-1]);
 close(average(new Float32Array([0,180]),source,target,1),[NaN]);
 close(average(new Float32Array([0,Math.PI]),source,target,2),[NaN]);
 close(average(new Float32Array([0,180]),grid(2,1,[1,-1],[.5,0]),target,1),[0]);
 close(average(new Float32Array([NaN,NaN]),source,target,1),[NaN]);
});

test('binary classes use area-weighted majority, with inclusive ties choosing one',()=>{
 close(average(new Float32Array([0,1]),grid(2),grid(1,1,[2,-1]),0,1),[1]);
 close(average(new Float32Array([0,1]),grid(2,1,[1,-1],[.5,0]),grid(1,1,[2,-1]),0,1),[0]);
 close(average(new Float32Array([1,NaN]),grid(2),grid(1,1,[2,-1]),0,1),[1]);
 close(average(new Float32Array([NaN,NaN]),grid(2),grid(1,1,[2,-1]),0,1),[NaN]);
});

test('transition classes use unique weighted mode rather than net-change averaging',()=>{
 close(average(new Float32Array([-1,-1,1]),grid(3),grid(1,1,[3,-1]),0,2),[-1]);
 close(average(new Float32Array([-1,1]),grid(2),grid(1,1,[2,-1]),0,2),[NaN]);
 close(average(new Float32Array([-1,0,1]),grid(3),grid(1,1,[3,-1]),0,2),[NaN]);
 close(average(new Float32Array([-1,1]),grid(2,1,[1,-1],[.5,0]),grid(1,1,[2,-1]),0,2),[-1]);
 close(average(new Float32Array([NaN,0,NaN]),grid(3),grid(1,1,[3,-1]),0,2),[0]);
});
