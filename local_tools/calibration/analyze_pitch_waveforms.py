"""Replay pitch DMA captures over candidate Schmitt settings (diagnostic only)."""
import json
from pathlib import Path
import numpy as np
HERE=Path(__file__).resolve().parent
for name in ['pitch-motor-diagnostic.jsonl','pitch-40pct-diagnostic.jsonl']:
 rows=map(json.loads,(HERE/name).read_text().splitlines())
 v=np.array([[int(x) for x in r['value'].split()[1:]] for r in rows if r['kind']=='serial' and r['value'].startswith('MOTORADC ') and len(r['value'].split())==4])
 print(name,'samples',len(v),'min/max',np.min(v[:,1:],axis=0),np.max(v[:,1:],axis=0))
 for c,h in [(3600,100),(3800,100),(3950,50)]:
  state=None;forward=reverse=invalid=0
  delta=[0,1,-1,0,-1,0,0,1,1,0,0,-1,0,-1,1,0]
  for _,a,b in v:
   if state is None:state=int(a>=c)|(int(b>=c)<<1);continue
   sa=state&1;sb=(state>>1)&1
   if a>c+h:sa=1
   elif a<c-h:sa=0
   if b>c+h:sb=1
   elif b<c-h:sb=0
   nxt=sa|(sb<<1);invalid+=(state^nxt)==3
   step=delta[state*4+nxt];forward+=step>0;reverse+=step<0;state=nxt
  print('center',c,'hysteresis',h,'forward',forward,'reverse',reverse,'invalid',invalid)
