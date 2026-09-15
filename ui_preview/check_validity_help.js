const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../explorer_template.html'),'utf8');
function harness(){
  const nodes=new Map(),events={},observers=[];let focused=null;
  function node(id){if(!nodes.has(id))nodes.set(id,{id,hidden:id==='metricHelp'||id==='nxn-panel',attrs:{},style:{display:'',visibility:'visible'},scrollTop:91,
    setAttribute(k,v){this.attrs[k]=v;},focus(){focused=this;},click(){this.onclick?.({stopPropagation(){}});}});return nodes.get(id);}
  node('nxn-close').onclick=()=>{node('nxn-panel').hidden=true;node('nxn-trigger').setAttribute('aria-expanded','false');node('nxn-trigger').focus();};
  const scope=vm.createContext({$:node,getComputedStyle:n=>n.style,
    document:{addEventListener(type,fn,capture){events[type]={fn,capture};}},
    MutationObserver:class{constructor(fn){this.fn=fn;observers.push(this);}observe(target,options){this.target=target;this.options=options;}}});
  const begin=source.indexOf('/* ================= validity help ================= */');
  if(begin>=0)vm.runInContext(source.slice(begin,source.indexOf('/* ================= render ================= */',begin)),scope);
  return{scope,node,nodes,events,observers,get focused(){return focused;}};
}
test('help opens independently, closes Product notes, and restores focus to the current trigger',()=>{
  const h=harness();h.node('nxn-panel').hidden=false;h.node('nxn-trigger').attrs['aria-expanded']='true';
  h.scope.setMetricHelp(true);
  assert.equal(h.node('metricHelp').hidden,false);assert.equal(h.node('nxn-panel').hidden,true);
  assert.equal(h.node('nxn-trigger').attrs['aria-expanded'],'false');assert.equal(h.node('iHelp').attrs['aria-expanded'],'true');
  assert.equal(h.focused,h.node('metricHelpClose'));assert.equal(h.node('metricHelpContent').scrollTop,0);
  h.nodes.delete('iHelp');const newTrigger=h.node('iHelp');h.node('metricHelpClose').click();
  assert.equal(h.node('metricHelp').hidden,true);assert.equal(newTrigger.attrs['aria-expanded'],'false');assert.equal(h.focused,newTrigger);
});
test('Escape closes help and panel key presses never reach terrain shortcuts',()=>{
  const h=harness();h.scope.setMetricHelp(true);
  const event=key=>({key,stopped:false,prevented:false,stopPropagation(){this.stopped=true;},preventDefault(){this.prevented=true;}});
  const r=event('r');h.node('metricHelp').onkeydown(r);assert(r.stopped);assert(!h.node('metricHelp').hidden);
  const escape=event('Escape');h.node('metricHelp').onkeydown(escape);assert(escape.stopped&&escape.prevented);assert(h.node('metricHelp').hidden);
});
test('opening Product notes closes help without stealing focus from its controls',()=>{
  const h=harness();h.scope.setMetricHelp(true);h.node('nxn-trigger').focus();
  assert.equal(h.events.click.capture,true);
  h.events.click.fn({target:{closest:selector=>selector==='#nxn-trigger'?h.node('nxn-trigger'):null}});
  assert(h.node('metricHelp').hidden);assert.equal(h.node('iHelp').attrs['aria-expanded'],'false');assert.equal(h.focused,h.node('nxn-trigger'));
});
test('hiding Info or switching to a flat tab closes help without focusing a hidden control',()=>{
  for(const style of [{display:'none'},{visibility:'hidden'}]){
    const h=harness();h.scope.setMetricHelp(true);Object.assign(h.node('info').style,style);
    const observer=h.observers.find(o=>o.target===h.node('info'));assert(observer);observer.fn();
    assert(h.node('metricHelp').hidden);assert.equal(h.node('iHelp').attrs['aria-expanded'],'false');assert.notEqual(h.focused,h.node('iHelp'));
  }
});
