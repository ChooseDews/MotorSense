"""Plot camera-referenced pitch calibration and held-out return errors."""
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
p=Path(__file__).with_name('pitch-sweep-results.json')
r=json.loads(p.read_text());points=r['points'];x=range(len(points))
fig,ax=plt.subplots(2,1,figsize=(10,7),sharex=True,layout='constrained')
ax[0].plot(x,[q['pitch'] for q in points],'ko-',label='Camera pitch')
for i,color in [(0,'#2878b5'),(1,'#d95f02')]:
 ax[0].plot(x,[q[f'predicted{i}'] for q in points],'.--',color=color,label=f'Encoder {i}')
 ax[1].plot(x,[q[f'error{i}'] for q in points],'o-',color=color,label=f'Encoder {i}')
ax[0].set_ylabel('Pitch (degrees)');ax[0].set_title('Pitch calibration — outbound fit, independent return validation')
ax[1].set_ylabel('Encoder − camera (degrees)');ax[1].axhline(0,color='gray',linewidth=.8)
for a in ax:
 a.grid(alpha=.2);a.legend();a.axvline(r['forward_points']+.5,color='gray',linestyle=':')
ax[1].set_xticks(list(x),[q['stage'] for q in points],rotation=60,ha='right');ax[1].set_xlabel('Settled measurement; dotted line marks start of return')
fig.savefig(p.with_name('pitch-accuracy.png'),dpi=160)
