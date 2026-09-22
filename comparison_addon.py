"""Add browser comparison without rebuilding data or restyling original explorers.

python comparison_addon.py --rollout
Each original page is backed up. Index, HDF5 and rendering template stay untouched.
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from build_provenance import capture_sources

_SOURCE_SNAPSHOT = capture_sources(__file__)

ROOT=Path(__file__).resolve().parent
MARKER='\n<!-- SnowEx comparison addon v1 -->\n'
BRIDGE_START='// BEGIN SnowEx comparison bridge v1\n'
BRIDGE_END='// END SnowEx comparison bridge v1\n'
HOOK='draw(); // Start on-demand rendering.'
LEGACY_HOOK='(function spin(){draw();requestAnimationFrame(spin);})();'
PAYLOAD=re.compile(r'<script id="payload" type="application/json">(.*?)</script>',re.S)


def remove_comparison(html):
    if html.count(MARKER)>1 or html.count(BRIDGE_START)>1 or html.count(BRIDGE_END)>1:
        raise ValueError('Ambiguous duplicate comparison markers')
    base=html.split(MARKER,1)[0]
    if BRIDGE_START in base:
        start=base.index(BRIDGE_START)
        end=base.find(BRIDGE_END,start)
        if end<0:raise ValueError('Incomplete comparison bridge')
        base=base[:start]+base[end+len(BRIDGE_END):]
    elif BRIDGE_END in base:raise ValueError('Incomplete comparison bridge')
    if 'id="nxc-panel"' in base or 'window.SnowCompareViewer=' in base:
        raise ValueError('Unmarked comparison code; refusing to duplicate')
    return base


def script(name,text):
    # Standalone pages require no asset server; keep licenses in the distribution.
    text=re.sub(r'//# sourceMappingURL=[^\r\n]*','',text)
    text=re.sub(r'</script',r'<\\/script',text,flags=re.I)
    return '<script id="nxc-'+name+'">\n'+text+'\n</script>\n'


def append_comparison(html):
    base=remove_comparison(html)
    hooks=[hook for hook in (HOOK,LEGACY_HOOK) if hook in base]
    if len(hooks)!=1 or base.count(hooks[0])!=1 or len(PAYLOAD.findall(base))!=1:
        raise ValueError('Expected one stable viewer hook and one payload; nothing changed')
    hook=hooks[0]
    bridge=(ROOT/'viewer_compare/bridge.js').read_text(encoding='utf-8')
    injected=base.replace(hook,BRIDGE_START+bridge+'\n'+BRIDGE_END+hook,1)
    addon=(ROOT/'viewer_compare/panel.html').read_text(encoding='utf-8')+'\n'
    vendor=ROOT/'assets/vendor'
    for name,filename,license_file in [('geotiff','geotiff-2.1.3.js','geotiff-LICENSE'),('proj4','proj4-2.12.1.js','proj4-LICENSE.md')]:
        license_text=(vendor/license_file).read_text(encoding='utf-8').replace('*/','* /')
        addon+=script(name,'/*\n'+license_text+'\n*/\n'+(vendor/filename).read_text(encoding='utf-8'))
    for name in ['core','tiff','export','mask','panel']:
        addon+=script('ui' if name=='panel' else 'export-module' if name=='export' else name,(ROOT/'viewer_compare'/f'{name}.js').read_text(encoding='utf-8'))
    result=injected+MARKER+addon
    if remove_comparison(result)!=base:raise ValueError('Original page changed outside marked additions')
    return result


def validate(candidate):
    subprocess.run(['node',str(ROOT/'viewer_compare/check_page.js'),str(candidate)],check=True,capture_output=True,text=True,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def sha(data):return hashlib.sha256(data).hexdigest()


def rollout(viewer_dir=None,expected_count=8):
    viewer=Path(viewer_dir or ROOT/'viewer').resolve()
    paths=sorted(viewer.glob('*_explorer.html'))
    if len(paths)!=expected_count:raise ValueError(f'Expected {expected_count} site explorers')
    protected=[p for p in [viewer/'index.html',ROOT/'explorer_template.html'] if p.exists()]
    protected_hashes={p:sha(p.read_bytes()) for p in protected}
    jobs=[]
    for path in paths:
        if path.resolve().parent!=viewer:raise ValueError('Page resolves outside viewer directory')
        before=path.read_bytes();source=before.decode('utf-8');match=PAYLOAD.search(source)
        if not match or json.loads(match[1])['site']+'_explorer.html'!=path.name:raise ValueError('Site/file mismatch')
        after=append_comparison(source).encode('utf-8')
        if PAYLOAD.search(after.decode())[1]!=match[1]:raise ValueError('Payload changed')
        jobs.append((path,before,after,sha(match[1].encode())))
    if all(a==b for _,a,b,_ in jobs):return None
    backup=viewer.parent/'backups'/('pre_comparison_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    staged=backup/'validated_candidates';staged.mkdir(parents=True,exist_ok=False)
    for path,before,after,_ in jobs:
        (backup/path.name).write_bytes(before)
        candidate=staged/path.name;candidate.write_bytes(after);validate(candidate)
    (backup/'manifest.json').write_text(json.dumps({'pages':[{'file':p.name,'before_sha256':sha(a),'after_sha256':sha(b),'payload_sha256':d} for p,a,b,d in jobs]},indent=2),encoding='utf-8')
    for path,before,_,_ in jobs:
        if path.read_bytes()!=before:raise RuntimeError('Concurrent page edit; no pages replaced')
    for path,digest in protected_hashes.items():
        if sha(path.read_bytes())!=digest:raise RuntimeError('Protected file changed during preparation; no pages replaced')
    published=[]
    try:
        for path,_,after,_ in jobs:
            (staged/path.name).replace(path);published.append(path)
            if path.read_bytes()!=after:raise RuntimeError('Published page verification failed')
    except BaseException:
        for path in published:shutil.copy2(backup/path.name,path)
        raise
    print(f'Comparison added to {len(jobs)} explorers; exact data and original UI retained. Backup: {backup}')
    return backup


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--rollout',action='store_true');args=parser.parse_args()
    if args.rollout:rollout()
    else:parser.print_help()
