/* Executed inside the existing viewer closure. All changes are in-memory only. */
{
  const temporary=new Map(), temporaryCells=new Map(), originalFloats=floats, originalSavePrefs=savePrefs;
  let restoreKey=null, restoreOverlay=null, lastShown=null;
  const instance=Date.now().toString(36);
  let generation=0;
  const prefix='__temporary_comparison__/';
  const styleFields=['palettes','customs','ranges','rangeModes','reverse'];
  const copy=stops=>stops.map(s=>[s[0],s[1]]);
  const clearStyle=k=>{for(const field of styleFields)if(PREF[field])delete PREF[field][k];};
  const listeners=new Set();let changing=0,lastSelection='';
  const maskPrefs=new Map(),maskCache=new Map(),maskListeners=new Set();
  const isMask=k=>!!P.arrays[k]&&productKind(k)==='coherence_mask';
  const maskOptions=k=>isMask(k)?window.SnowCompareCore.maskOptions(maskPrefs.get(k)):null;
  const category=k=>temporary.has(k)?(P.arrays[k].maskCategory==='transition'?2:P.arrays[k].maskBinary?1:0):
    isMask(k)&&window.SnowCompareCore&&maskOptions(k).mode==='binary'?1:0;
  const categoryRange=k=>category(k)===2?[-1,1]:category(k)===1?[0,1]:null;
  const maskSupported=()=>U.uMaskBinary!==undefined&&U.uMaskBinary!==null;
  function maskTargets(){
    const state=selection();if(!state.key)return [];
    const key=state.comparison!==null?lastShown.maskKey:state.key,targets=[];
    if(isMask(key))targets.push({key,label:(state.comparison!==null?'Comparison reference · ':'Primary · ')+P.arrays[key].label});
    if(isMask(ovKey)&&ovKey!==key)targets.push({key:ovKey,label:'Overlay · '+P.arrays[ovKey].label});
    return targets;
  }
  function maskLegend(k){
    if(!isMask(k)&&!category(k))return;
    const bounds=categoryRange(k)||layerRange(k),[lo,hi]=bounds;
    $('t0').textContent=fmt(lo);$('t1').textContent=fmt((lo+hi)/2);$('t2').textContent=fmt(hi);
    if(isMask(k)){
      const options=maskOptions(k);
      $('legDom').textContent='InSAR · '+(options.mode==='binary'?'0–1 block state':'passing fraction');
      $('rangeStateLabel').textContent=options.mode==='binary'?'0–1 · fraction ≥ '+options.cutoff:
        'Average · '+$('rangeStateLabel').textContent;
      const coarse=typeof opts!=='undefined'&&opts.colors==='cells';
      const quantity=coarse?'area-weighted mean of exported passing fractions':'decoded valid-cell passing fraction';
      $('rangeState').title=options.mode==='binary'?'1 when the '+quantity+' is at least '+options.cutoff+'. Missing blocks remain blank.':
        coarse?'Area-weighted mean of exported passing fractions within each current Detail block.':
        'Passing fraction among valid cells in each exported block. '+$('rangeState').title;
    }
    if(category(k)&&window.SnowCompareExport){
      const name=layerCmap(k),style=name==='custom'?{stops:customStops(k)}:{builtin:name};
      const color=window.SnowCompareExport.colorMapper(style),n=category(k)===2?3:2;
      const stops=[];
      for(let i=0;i<n;i++){
        const rgb='rgb('+color(i/(n-1)).slice(0,3).join(',')+')';
        const a=i===0?0:(i-.5)/(n-1)*100,b=i===n-1?100:(i+.5)/(n-1)*100;
        stops.push(rgb+' '+a+'%',rgb+' '+b+'%');
      }
      $('ramp').style.background='linear-gradient(90deg,'+stops.join(',')+')';
    }
  }
  // The CPU classifies original fractions. The shader only keeps interpolated
  // class values categorical; it never receives the user's cutoff.
  const originalDraw=draw;
  draw=function(...args){
    for(const [k,overlay] of [[primKey,false],[ovKey,true]]){
      const flag=overlay?U.uMaskBinary2:U.uMaskBinary;
      if(flag!==undefined&&flag!==null)gl.uniform1i(flag,category(k));
      const bounds=categoryRange(k);
      if(bounds){gl.uniform1f(overlay?U.uLo2:U.uLo,bounds[0]);gl.uniform1f(overlay?U.uHi2:U.uHi,bounds[1]);}
    }
    return originalDraw.apply(this,args);
  };
  const originalSyncLegendEditor=syncLegendEditor;
  syncLegendEditor=function(...args){
    const result=originalSyncLegendEditor.apply(this,args),bounds=categoryRange(primKey);
    for(const [i,id] of ['leMin','leMax'].entries())if($(id)){
      $(id).disabled=!!bounds;if(bounds)$(id).value=bounds[i];
    }
    for(const button of $('legendEditor')?.querySelectorAll?.('.le-presets button[data-stretch]')||[])button.disabled=!!bounds;
    return result;
  };
  function selection(){
    const tab=tabs.find(t=>t.id===active),key=tab&&tab.kind==='layer'?primKey:null;
    const available=!!(lastShown&&temporary.has(lastShown.key)&&tabs.some(t=>t.key===lastShown.key));
    return {key,tab:active,comparison:available&&key===lastShown.key&&tab.key===lastShown.key?lastShown.id:null,available};
  }
  function notifySelection(){
    if(changing)return;
    const state=selection(),signature=JSON.stringify(state);if(signature===lastSelection)return;
    lastSelection=signature;for(const listener of listeners)listener({...state});
  }
  // A close can activate DEM, and clear can close a tab then restore its source.
  // Publish only the completed operation so consumers never see those intermediate scenes.
  const notifyAfter=fn=>function(...args){
    changing++;try{return fn.apply(this,args);}finally{changing--;notifySelection();}
  };
  activate=notifyAfter(activate);closeTab=notifyAfter(closeTab);
  lastSelection=JSON.stringify(selection());
  floats=function(k){
    if(temporary.has(k))return temporary.get(k);
    const source=originalFloats(k);
    if(!isMask(k)||!window.SnowCompareCore)return source;
    const options=maskOptions(k),cached=maskCache.get(k);
    if(cached&&cached.source===source&&cached.mode===options.mode&&cached.cutoff===options.cutoff)return cached.values;
    const values=window.SnowCompareCore.maskPreview(source,options);
    if(maskCache.size>=14)maskCache.delete(maskCache.keys().next().value);
    maskCache.set(k,{source,...options,values});return values;
  };
  // Average original exported fractions before classifying a coarse Cells block.
  // Keep the earlier, unaggregated Cells template compatible with this add-on.
  const averagesCells=typeof finishCellValues==='function';
  if(typeof cellSamples==='function'){
    const originalCellSamples=cellSamples;
    cellSamples=function(k){
      if(temporaryCells.has(k))return temporaryCells.get(k);
      const source=originalCellSamples(k);
      return !averagesCells&&isMask(k)&&window.SnowCompareCore?window.SnowCompareCore.maskPreview(source,maskOptions(k)):source;
    };
  }
  if(averagesCells){
    const originalFinishCellValues=finishCellValues;
    finishCellValues=function(k,values){
      return isMask(k)&&window.SnowCompareCore?window.SnowCompareCore.maskPreview(values,maskOptions(k)):
        originalFinishCellValues(k,values);
    };
  }
  if(typeof syncCellColors==='function'){
    const originalSyncCellColors=syncCellColors;
    syncCellColors=function(...args){const result=originalSyncCellColors.apply(this,args);maskLegend(primKey);return result;};
  }
  const originalSetOverlay=setOverlay;
  setOverlay=function(...args){
    const result=originalSetOverlay.apply(this,args);
    for(const listener of maskListeners)listener({key:null,options:null});
    return result;
  };
  if(typeof angularKind==='function'){
    const originalAngularKind=angularKind;
    angularKind=k=>temporary.has(k)?P.arrays[k].comparisonAngleKind:originalAngularKind(k);
  }
  savePrefs=function(...args){
    const removed=[];
    for(const field of styleFields)for(const k of Object.keys(PREF[field]||{}))if(k.startsWith(prefix)){
      removed.push([PREF[field],k,PREF[field][k]]);delete PREF[field][k];
    }
    try{return originalSavePrefs.apply(this,args);}
    finally{for(const [map,k,value] of removed)map[k]=value;}
  };
  window.SnowCompareViewer={
    selection,
    maskOptions,maskTargets,maskSupported,
    onMaskOptions(listener){maskListeners.add(listener);return()=>maskListeners.delete(listener);},
    setMaskOptions(key,options){
      if(!isMask(key))throw Error('Select a coherence mask layer.');
      const next=window.SnowCompareCore.maskOptions(options);
      if(next.mode==='binary'&&!maskSupported())throw Error('This viewer needs the updated mask renderer for 0–1 mode.');
      const previous=maskOptions(key);
      if(next.mode===previous.mode&&next.cutoff===previous.cutoff)return;
      maskPrefs.set(key,next);maskCache.delete(key);
      if(primKey===key)setPrimary(key);
      if(ovKey===key)setOverlay(key);
      for(const listener of maskListeners)listener({key,options:{...next}});
    },
    onSelection(listener){listeners.add(listener);listener(selection());return()=>listeners.delete(listener);},
    palettes(key){
      const fromConfig=c=>c.mode==='binary'?[[0,c.colors[0]],[.499,c.colors[0]],[.5,c.colors[1]],[1,c.colors[1]]]:copy(c.stops||[]);
      const choices=Object.keys(BUILTIN_STOPS).map(id=>({id,builtin:id,name:id==='diverging'?'Diverging':PALETTE_LABELS[id]||id,stops:copy(BUILTIN_STOPS[id])}));
      if(P.arrays[key]){
        const name=layerCmap(key),stops=name==='custom'?customStops(key):BUILTIN_STOPS[name];
        if(stops)choices.push({id:'__source__',name:'Current reference layer palette',stops:copy(stops),reverse:!!layerReverse(key),...(name!=='custom'?{builtin:name}:{})});
      }
      for(const p of Object.values(PREF.savedPalettes||{}))if(p&&p.mode){
        choices.push({id:'saved:'+p.id,name:'Saved · '+(p.name||p.id)+' · '+(p.type||''),stops:fromConfig(p)});
      }
      return choices;
    },
    context(){return {site:P.site,identification:P.identification,grid:G,
      current:primKey&&primKey.startsWith(prefix)?restoreKey:primKey,
      layers:Object.keys(P.arrays).filter(k=>!k.startsWith(prefix)).map(k=>({key:k,...P.arrays[k],kind:productKind(k)}))};},
    appearance(id){
      if(selection().comparison!==id)return null;
      const name=layerCmap(primKey),stops=name==='custom'?customStops(primKey):BUILTIN_STOPS[name];
      const style={stops:copy(stops||BUILTIN_STOPS.viridis),reverse:!!layerReverse(primKey)};
      if(name!=='custom')style.builtin=name;
      let [lo,hi]=layerRange(primKey);
      const categorical=categoryRange(primKey);if(categorical)[lo,hi]=categorical;
      if(isDiverging(primKey)){const m=Math.max(Math.abs(lo),Math.abs(hi),1e-6);lo=-m;hi=m;}
      const mode=rangeMode(primKey),sameStyle=JSON.stringify(style)===JSON.stringify(lastShown.style);
      const sameRange=lo===lastShown.lo&&hi===lastShown.hi&&mode==='custom';
      const r=PREF.ranges[primKey],validRange=r&&r.length===2&&isFinite(r[0])&&isFinite(r[1])&&r[1]>r[0];
      const fallback=(mode==='robust'||mode==='detail')&&!validRange;
      const terrainSamples=({a:'A',b:'B',difference:'Difference'}[lastShown.id]||lastShown.id)+' terrain samples (sampled ranks)';
      const zeroCentered=!!P.arrays[primKey].comparisonZeroCentered;
      const rangeLabel=categorical&&lastShown.rangeLabel||sameRange&&lastShown.rangeLabel||(fallback?'Full 0–100% stretch · fallback: percentile limits missing, invalid or tied':
        ({full:'Full 0–100% stretch',robust:'3D legend Robust 2–98% stretch · '+terrainSamples,detail:'3D legend Detail 10–90% stretch · '+terrainSamples}[mode]||'Custom value limits'));
      return {lo,hi,style,zeroCentered,
        paletteName:sameStyle&&lastShown.paletteName|| (name==='custom'?'Custom legend palette':PALETTE_LABELS[name]||name),
        rangeLabel:zeroCentered&&!rangeLabel.includes('zero-centred limits')?rangeLabel+' · zero-centred limits':rangeLabel};
    },
    show:notifyAfter(function(result){
      const identity=instance+'_'+generation;
      const k=prefix+identity,rg=result.grid,n=rg.w*rg.h;
      if((result.maskBinary||result.maskCategory==='transition')&&!maskSupported())throw Error('This viewer needs the updated mask renderer for categorical comparison.');
      if(result.values.length!==n)throw Error('Temporary result/grid mismatch.');
      let lo=Infinity,hi=-Infinity,valid=0;
      for(const value of result.values)if(Number.isFinite(value)){lo=Math.min(lo,value);hi=Math.max(hi,value);valid++;}
      if(!valid)throw Error('Temporary result has no finite values.');
      if(!restoreKey){restoreKey=primKey||DEM;restoreOverlay=ovKey;}
      const raw=new Uint8Array(n*2),span=hi-lo||1;
      for(let i=0;i<n;i++)if(Number.isFinite(result.values[i])){
        const q=1+Math.round(Math.max(0,Math.min(1,(result.values[i]-lo)/span))*65534);
        raw[2*i]=q&255;raw[2*i+1]=q>>8;
      }
      let binary='';for(let i=0;i<raw.length;i+=8192)binary+=String.fromCharCode(...raw.subarray(i,i+8192));
      P.arrays[k]={w:rg.w,h:rg.h,cell_m:rg.dx,origin:[rg.left,rg.top],pixel:[rg.dx,rg.dy],lo,hi,bits:16,b64:btoa(binary),
        // Result-grid aliases keep older templates' Info arithmetic compatible.
        valid,total:n,comparisonTemporary:true,display_valid:valid,display_total:n,leaf:'comparison_'+identity,label:result.label,short:result.label,
        comparisonAngleKind:result.id==='difference'?0:result.mode==='degrees'?1:result.mode==='radians'?2:0,
        comparisonZeroCentered:result.id==='difference'&&result.zeroCentered===true&&!result.maskBinary&&!result.maskCategory,
        maskBinary:!!result.maskBinary,maskCategory:result.maskCategory,
        unit:result.unit||'',domain:'meta',cmap:result.cmap||'diverging'};
      clearStyle(k);
      for(const field of styleFields)if(!PREF[field])PREF[field]={};
      PREF.ranges[k]=[result.lo,result.hi];PREF.rangeModes[k]='custom';
      if(result.style){
        PREF.palettes[k]=result.style.builtin||'__custom__';
        if(!result.style.builtin)PREF.customs[k]={mode:'continuous',stops:copy(result.style.stops)};
        PREF.reverse[k]=!!result.style.reverse;
        // Palette choice is independent of the explicit comparison centring policy.
        P.arrays[k].cmap='viridis';
      }
      // Map result-cell centers onto terrain-cell centers, not rounded size ratios.
      const displayed=new Float32Array(W*H).fill(NaN);
      for(let j=0;j<H;j++)for(let i=0;i<W;i++){
        const x=G.origin[0]+(i+.5)*G.pixel[0],y=G.origin[1]+(j+.5)*G.pixel[1];
        const c=Math.floor((x-rg.left)/rg.dx),r=Math.floor((y-rg.top)/rg.dy);
        if(c>=0&&r>=0&&c<rg.w&&r<rg.h)displayed[j*W+i]=result.values[r*rg.w+c];
      }
      temporary.set(k,displayed);
      temporaryCells.set(k,result.values);
      lastShown={key:k,id:result.id,lo:result.lo,hi:result.hi,paletteName:result.paletteName,rangeLabel:result.rangeLabel,maskKey:result.maskKey,
        style:result.style&&{stops:copy(result.style.stops),reverse:!!result.style.reverse,...(result.style.builtin?{builtin:result.style.builtin}:{})}};
      // A difference must not inherit an unrelated overlay blend.
      setOverlay(null);if($('ovSel'))$('ovSel').value='__none__';
      openLayer(k);
    }),
    clear:notifyAfter(function({restore=true}={}){
      const owned=selection().comparison!==null;
      for(const k of temporary.keys()){
        const t=tabs.find(t=>t.key===k);if(t)closeTab(t.id);
        delete P.arrays[k];cache.delete(k);
        clearStyle(k);
      }
      temporary.clear();temporaryCells.clear();
      lastShown=null;
      generation++;
      if(restore&&owned){
        if(restoreKey&&P.arrays[restoreKey])openLayer(restoreKey);
        if(restoreOverlay&&P.arrays[restoreOverlay]){setOverlay(restoreOverlay);if($('ovSel'))$('ovSel').value=restoreOverlay;}
      }
      restoreKey=null;restoreOverlay=null;
    })
  };
  // Keep original widgets and report exact comparison stretch provenance.
  // The template reads the temporary result-grid counts above for Valid display.
  const originalSetPrimary=setPrimary;
  setPrimary=function(k,...args){
    const returned=originalSetPrimary.call(this,k,...args);
    maskLegend(k);
    if(!temporary.has(k)||!lastShown||lastShown.key!==k)return returned;
    const appearance=window.SnowCompareViewer.appearance(lastShown.id);
    if(!appearance)return returned;
    if($('rangeStateLabel'))$('rangeStateLabel').textContent=appearance.rangeLabel;
    if($('rangeState'))$('rangeState').title=appearance.rangeLabel+
      '. Comparison display limits; out-of-range colours saturate. Data and statistics are unchanged.';
    // Older templates retain their existing Info layout until template integration.
    if(typeof validityRows!=='function'){
      const body=$('iBody');
      const row=body&&typeof body.querySelectorAll==='function'&&Array.from(body.querySelectorAll('tr'))
        .find(r=>r.children[0]&&['coverage','valid-cell count relative to DEM'].includes(r.children[0].textContent.trim()));
      if(row&&row.children[1]){
        const L=P.arrays[k];
        row.children[0].textContent='result cells';
        row.children[1].textContent=L.valid.toLocaleString()+' / '+L.total.toLocaleString();
        row.children[1].title='Finite cells in the selected result / all cells in the comparison grid; no stored archive dataset.';
      }
    }
    return returned;
  };
}
