"""Analyze buffered samples, avoiding UART sampling/arrival-time assumptions."""
import json,numpy as np
G=[]
for l in open('local_tools/calibration/burst-capture.jsonl'):
 r=json.loads(l)
 if r['kind']=='command':G.append([r,[]])
 try:
  if r['value'].startswith('BURST '):G[-1][1].append([int(x) for x in r['value'].split()[1:]])
 except ValueError:pass
for r,rows in G:
 a=np.array(rows)
 if len(a)<2:continue
 t=a[:,0]*1e-6;t-=t[0]
 print(r['value'],'N',len(a),'duration',t[-1],'median_dt_us',np.median(np.diff(t))*1e6,'max_gap_us',max(np.diff(t))*1e6)
 for i in [1,3]:
  v=a[:,i:i+2];sign=v[0]>=2800;q=[]
  for pair in v:
   sign=np.where(pair>2900,True,np.where(pair<2700,False,sign));q.append(0 if sign.all() else 1 if sign[1] else 3 if sign[0] else 2)
  d=np.diff(q)%4
  print('enc',i//2,'positive',sum(d==1),'negative',sum(d==3),'invalid',sum(d==2),'either_channel_clipped',np.mean((v>=4093).any(axis=1)))
  freqs=np.linspace(5,1750,3491);z=np.exp(-2j*np.pi*freqs[:,None]*t)@(v-v.mean(axis=0));power=(abs(z)**2).sum(axis=1);ii=power.argmax();print('dominant_Hz',freqs[ii],'phase_A_minus_B_deg',np.angle(z[ii,0]/z[ii,1],deg=True))
