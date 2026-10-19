import json, re, sys, collections, datetime, numpy as np, statistics
from pathlib import Path
p=Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).resolve().parent.parent/'data'/'touch_compact.jsonl'
rx=re.compile(r'path:\s*(\d+);\s*\{([\d.]+),\s*([\d.]+)\};\s*(.*?)touchID:\s*(0x[\da-fA-F]+)')
rows=[];evs=[]; unique_gen=[]
for i,l in enumerate(p.open(encoding='utf-8-sig')):
 j=json.loads(l);t=datetime.datetime.fromisoformat(j['timestamp']);m=j['message'];gen=re.search(r'gen: (\d+)',m);f=rx.findall(m)
 event=[]
 for path,x,y,ph,ident in f:
  phase='up' if re.search(r'\bup;',ph) else 'down' if re.search(r'\bdown;',ph) else 'range_out' if 'range (out)' in ph else 'move' if 'move' in ph else 'unchanged'
  e={'t':t,'id':int(ident,16),'path':int(path),'x':float(x),'y':float(y),'phase':phase,'i':i}
  rows.append(e);event.append(e)
 evs.append({'t':t,'gen':int(gen.group(1)) if gen else -1,'i':i,'contacts':event})
t0=evs[0]['t'];tn=evs[-1]['t']
for r in rows:r['sec']=(r['t']-t0).total_seconds()
for e in evs:e['sec']=(e['t']-t0).total_seconds()
byid=collections.defaultdict(list)
for r in rows:byid[r['id']].append(r)
print('events',len(evs),'rows',len(rows),'duration', (tn-t0).total_seconds(), 'ids',len(byid))
print('event contacts',collections.Counter(len(e['contacts']) for e in evs))
print('phase',collections.Counter(r['phase'] for r in rows))
print('perID stats')
fst=collections.Counter(); lst=collections.Counter(); has=collections.Counter();lens=[];npts=[];miss_down=[]; miss_up=[]
for k,rr in byid.items():
 fst[rr[0]['phase']]+=1;lst[rr[-1]['phase']]+=1
 h={r['phase'] for r in rr}
 for p in h:has[p]+=1
 if 'down' not in h: miss_down.append(k)
 if 'up' not in h and 'range_out' not in h:miss_up.append(k)
 lens.append(rr[-1]['sec']-rr[0]['sec']);npts.append(len(rr))
print('first',fst,'last',lst,'has',has,'missing down',len(miss_down),'missing end',len(miss_up))
for name,arr in [('durations',lens),('samples',npts)]:print(name, np.quantile(arr,[0,.1,.25,.5,.75,.9,.95,.99,1]), 'mean',np.mean(arr))
print('total starts',sum('down' in {r['phase'] for r in rr} for rr in byid.values()))
# gaps between logged source rows
intvs=np.diff(np.array([e['sec'] for e in evs]))
print('event gaps quantiles',np.quantile(intvs,[0,.01,.1,.5,.9,.95,.99,.999,1]));print('gaps >20ms',sum(intvs>.020),'>50ms',sum(intvs>.05),'>100ms',sum(intvs>.1),'>250ms',sum(intvs>.25));print('largest gaps',sorted([(round(float(x),4),evs[i]['sec']) for i,x in enumerate(intvs)],reverse=True)[:15])
# active count based first and last, time bins 100ms
bounds=sorted((rr[0]['sec'],rr[-1]['sec'],k) for k,rr in byid.items());multimax=0;tm=0;count2=0;count3=0; active_times=[]
for tt in np.arange(0,(tn-t0).total_seconds(),.01):
 n=sum(a<=tt<=b for a,b,_ in bounds)
 if n>multimax:multimax=n;tm=tt
 if n>=2:count2+=1
 if n>=3:count3+=1
 active_times.append(n)
print('maximum estimated concurrent',multimax,round(tm,3),'fraction time >=2',round(count2/len(active_times),3),' >=3',round(count3/len(active_times),3))
print('hist count',collections.Counter(active_times))
print('samples example first and last')
for rr in list(byid.values())[:5]:print(hex(rr[0]['id']),rr[0]['sec'],rr[-1]['sec'],len(rr),rr[0]['phase'],rr[-1]['phase'],(rr[0]['x'],rr[0]['y']))
# activity by time second: start count only (unique IDs) and movement count
persec=collections.defaultdict(lambda:collections.Counter())
for k,rr in byid.items():
 persec[int(rr[0]['sec'])]['new']+=1
for r in rows:
 if r['phase']=='move':persec[int(r['sec'])]['move']+=1
print('per 10 s starts/moves')
for a in range(0,173,10):
 print(a, 'new',sum(persec[b]['new'] for b in range(a,a+10)),'moves',sum(persec[b]['move'] for b in range(a,a+10)))
