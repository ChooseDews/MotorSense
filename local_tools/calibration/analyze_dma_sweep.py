"""Summarize stationary plateaus and evaluate independent reverse-sweep errors."""
import csv
import datetime as dt
import json
from pathlib import Path
import re
import numpy as np

HERE=Path(__file__).resolve().parent
rows=[json.loads(x) for x in (HERE/'dma-20deg-sweep.jsonl').read_text().splitlines()]
groups={}
for r in rows:groups.setdefault(r['stage'],[]).append(r)
points=[]
for stage,records in groups.items():
 end=dt.datetime.fromisoformat(records[-1]['time']).timestamp()
 window=[r for r in records if dt.datetime.fromisoformat(r['time']).timestamp()>=end-2]
 camera={};positions=[[],[]]
 for r in window:
  fields=r['vision'].split(',');camera[fields[0]]=float(fields[1])
  if r['kind']=='serial':
   m=re.search(r'ENCODER([01]):.*pos=(-?\d+)',r['value'])
   if m:positions[int(m[1])].append(int(m[2]))
 if not all(positions):continue
 point=dict(stage=stage,time=records[-1]['time'],yaw=float(np.mean(list(camera.values()))),vision_sd=float(np.std(list(camera.values()),ddof=1)) if len(camera)>1 else 0.,vision_n=len(camera),pos0=float(np.median(positions[0])),pos1=float(np.median(positions[1])),range0=int(np.ptp(positions[0])),range1=int(np.ptp(positions[1])))
 points.append(point)
with (HERE/'dma-20deg-plateaus.csv').open('w') as f:
 writer=csv.DictWriter(f,fieldnames=list(points[0]));writer.writeheader();writer.writerows(points)
base=points[0];forward=[p for p in points if p['stage'].startswith('forward')];reverse=[p for p in points if p['stage'].startswith('reverse')]
def summarize(e):
 e=np.array(e);return dict(n=len(e),rmse_deg=float(np.sqrt(np.mean(e**2))),mae_deg=float(np.mean(abs(e))),max_abs_deg=float(max(abs(e))),bias_deg=float(np.mean(e)))
results=dict(initial_yaw=base['yaw'],span_deg=max(p['yaw'] for p in points)-min(p['yaw'] for p in points),forward_points=len(forward),reverse_points=len(reverse),settled_vision_sd_median=float(np.median([p['vision_sd'] for p in points])),encoders={})
for i in [0,1]:
 key=f'pos{i}';x=np.array([p[key]-base[key] for p in forward]);y=np.array([p['yaw']-base['yaw'] for p in forward]);k=float(x@y/(x@x))
 def error(p):return (p[key]-base[key])*k-(p['yaw']-base['yaw'])
 stable_reverse=[p for p in reverse if p['vision_sd']<=0.1 and p['range0']<=1 and p['range1']<=1]
 result=dict(deg_per_tick=k,ticks_per_degree=1/k,forward_fit=summarize([error(p) for p in forward]),reverse_validation=summarize([error(p) for p in reverse]),stable_reference_reverse=summarize([error(p) for p in stable_reverse]),return_error_deg=error(reverse[-1]),final_stationary_error_deg=error(points[-1]),return_ticks=reverse[-1][key]-base[key],return_vision_deg=reverse[-1]['yaw']-base['yaw'])
 results['encoders'][str(i)]=result
 for p in points:p[f'error{i}']=error(p);p[f'predicted{i}']=(p[key]-base[key])*k+base['yaw']
x=np.array([p['pos0']-base['pos0'] for p in forward]);y=np.array([p['pos1']-base['pos1'] for p in forward]);ratio=float(x@y/(x@x));k=results['encoders']['0']['deg_per_tick']
results['cross_encoder']=dict(motor_ticks_per_axis_tick=ratio,reverse_validation=summarize([((p['pos1']-base['pos1'])/ratio-(p['pos0']-base['pos0']))*k for p in reverse]))
for tag in ['MOTOR_STATS','AXIS_STATS']:
 stats=[r for r in rows if r['kind']=='serial' and r['value'].startswith(tag+' ')]
 first={k:int(v) for k,v in re.findall(r'(\w+)=(-?\d+)',stats[0]['value'])};last={k:int(v) for k,v in re.findall(r'(\w+)=(-?\d+)',stats[-1]['value'])}
 results[tag]=dict(first=first,last=last,delta={k:last[k]-first[k] for k in first if k in last})
results['quality_excluded_stages']=[p['stage'] for p in reverse if p['vision_sd']>0.1 or p['range0']>1 or p['range1']>1]
results['outbound_span_deg']=max(p['yaw'] for p in forward)-base['yaw']
results['points']=points
(HERE/'dma-20deg-results.json').write_text(json.dumps(results,indent=2)+'\n')
print(json.dumps({k:v for k,v in results.items() if k!='points'},indent=2))
