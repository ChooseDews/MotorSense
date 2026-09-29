"""Summarize settled plateaus, fit forward sweep, validate reverse sweep."""
import datetime as dt,json,re,sys
import numpy as np
path=sys.argv[1] if len(sys.argv)>1 else 'local_tools/calibration/yaw_capture.jsonl'
groups=[]
for line in open(path):
 r=json.loads(line)
 if r['kind']=='command':groups.append({'command':r['value'],'rows':[]})
 if groups:groups[-1]['rows'].append(r)
points=[]
for g in groups:
 samples=[]
 for r in g['rows']:
  m=re.search(r'ENCODER0:.*pos=(-?\d+)',r['value'])
  if m:samples.append((dt.datetime.fromisoformat(r['time']).timestamp(),int(m[1]),float(r['vision'].split(',')[1]),r['vision']))
 if not samples:continue
 end=samples[-1][0];subset=[x for x in samples if x[0]>=end-1]
 yaw={x[3]:x[2] for x in subset}
 points.append(dict(command=g['command'],pos=float(np.median([x[1] for x in subset])),yaw=float(np.mean(list(yaw.values())))))
print(json.dumps(points,indent=2))
f=[p for p in points if p['command']=='MOTOR F 60 2'];b=[p for p in points if p['command']=='MOTOR B 60 2']
if len(f)>=2:
 slope,offset=np.polyfit([p['pos'] for p in f],[p['yaw'] for p in f],1)
 print('Forward fit deg/tick',slope,'ticks/deg',1/slope)
 for label,arr in [('forward fit',f),('reverse held out',b)]:
  if arr:
   err=np.array([slope*p['pos']+offset-p['yaw'] for p in arr]);print(label,'RMSE',np.sqrt(np.mean(err**2)),'max_abs',max(abs(err)),'errors',err)
if len(points)>3:
 a=points[1];z=points[-1];print('round trip delta ticks',z['pos']-a['pos'],'delta yaw',z['yaw']-a['yaw'])

if len(points)>3 and len(f)>=2:
 zero=points[1]
 for label,arr in [('forward initial-zero',f),('reverse initial-zero',b)]:
  if arr:
   err=np.array([(p['pos']-zero['pos'])*slope-(p['yaw']-zero['yaw']) for p in arr])
   print(label,'RMSE',np.sqrt(np.mean(err**2)),'max_abs',max(abs(err)))
