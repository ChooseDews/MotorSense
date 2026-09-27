"""Generate report figures from the saved 20-degree sweep analysis."""
import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
HERE=Path(__file__).resolve().parent
r=json.loads((HERE/'dma-20deg-results.json').read_text());p=r['points'];y=np.array([x['yaw'] for x in p]);y-=y[0]
fig,axs=plt.subplots(2,2,figsize=(12,8),layout='constrained')
colors=['#1868ac','#d16425']
for i,c in enumerate(colors):
 pred=np.array([x[f'predicted{i}'] for x in p])-r['initial_yaw']
 axs[0,0].plot(y,pred,'o-',ms=3,color=c,label=f'Encoder {i}')
 for direction,marker,ls in [('forward','o','-'),('reverse','s','--')]:
  subset=[x for x in p if x['stage'].startswith(direction)]
  axs[0,1].plot([x['yaw']-r['initial_yaw'] for x in subset],[x[f'error{i}'] for x in subset],marker+ls,ms=4,color=c,label=f'Encoder {i} {direction}')
 axs[1,0].plot([x[f'error{i}'] for x in p], 'o-',ms=4,color=c,label=f'Encoder {i}')
axs[0,0].plot([min(y),max(y)],[min(y),max(y)],'--',color='gray',lw=1)
axs[0,0].set(title='Position agreement across the full span',xlabel='Vision relative yaw (°)',ylabel='Encoder relative yaw (°)')
axs[0,1].set(title='Outbound fit; independent return validation',xlabel='Vision relative yaw (°)',ylabel='Encoder minus vision (°)')
axs[1,0].set(title='Error through the measurement sequence',xlabel='Settled plateau number',ylabel='Encoder minus vision (°)')
ratio=r['cross_encoder']['motor_ticks_per_axis_tick'];k=r['encoders']['0']['deg_per_tick'];p0=p[0]
disagreement=[((x['pos1']-p0['pos1'])/ratio-(x['pos0']-p0['pos0']))*k for x in p]
axs[1,1].plot(disagreement,'o-',color='#277e58',ms=4,label='Motor minus axis')
axs[1,1].set(title=f'Encoder-to-encoder check · ratio {ratio:.3f}:1',xlabel='Settled plateau number',ylabel='Equivalent yaw disagreement (°)')
for ax in axs.flat:
 ax.grid(alpha=.2);ax.legend(fontsize=8)
 if ax!=axs[0,0]:ax.axhline(0,color='gray',ls='--',lw=.8)
fig.suptitle(f'Yaw accuracy · {r["span_deg"]:.2f}° measured span · final DMA firmware',fontsize=15)
fig.savefig(HERE/'dma-20deg-accuracy.png',dpi=180)
