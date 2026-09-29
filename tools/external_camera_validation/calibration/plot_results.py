"""Reproduce calibration and raw waveform figures (requires numpy, matplotlib)."""
from pathlib import Path
import json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
text = (HERE / 'baseline-analysis.txt').read_text()
points = json.loads(text[:text.index('\nForward')])
zero = points[1]
points = points[1:]
slope = 0.03079532815888024
vision = np.array([p['yaw'] - zero['yaw'] for p in points])
estimate = np.array([(p['pos'] - zero['pos']) * slope for p in points])
fig, axs = plt.subplots(1, 2, figsize=(11, 4.3), layout='constrained')
for sl, label, color in [(slice(0,5), 'Forward / calibration', '#2474b5'), (slice(4,None), 'Reverse / validation', '#d06a21')]:
 axs[0].plot(vision[sl], estimate[sl], 'o-', label=label, color=color)
 axs[1].plot(np.arange(len(points))[sl], (estimate-vision)[sl], 'o-', color=color, label=label)
axs[0].plot([0,10],[0,10], '--', color='gray', label='Agreement')
axs[0].set(xlabel='Vision relative yaw (°)', ylabel='Encoder relative yaw (°)', title='Encoder 0: 32.47 ticks/degree')
axs[0].legend(fontsize=8)
axs[1].axhline(0,color='gray',ls='--')
axs[1].set(xlabel='Settled measurement (0 = initial zero)', ylabel='Encoder minus vision (°)', title='Same initial zero throughout the round trip')
for ax in axs:ax.grid(alpha=.2)
fig.suptitle('Yaw calibration · original firmware · 17 September 2026',fontsize=13)
fig.savefig(HERE/'yaw-calibration.png',dpi=180)

bursts=[]
for line in (HERE/'burst-capture.jsonl').read_text().splitlines():
 row=json.loads(line)
 if row['kind']=='command':bursts.append([row['value'],[]])
 if row['value'].startswith('BURST '):
  try:bursts[-1][1].append([int(x) for x in row['value'].split()[1:]])
  except ValueError:pass
fig,axs=plt.subplots(3,1,figsize=(11,8),layout='constrained')
for ax,(burst_index,channel,window,title) in zip(axs,[(0,1,300,'Encoder 0 / GPIO11–12: slow axis signal, 60% forward'),(0,3,12,'Encoder 1 / GPIO9–10: fast signal, 60% forward'),(2,3,25,'Encoder 1 / GPIO9–10: reduced speed, 45% forward')]):
 data=np.array(bursts[burst_index][1]);t=(data[:,0]-data[0,0])/1000;mask=t<window
 ax.plot(t[mask],data[mask,channel],'.-',lw=1,ms=3,label='A')
 ax.plot(t[mask],data[mask,channel+1],'.-',lw=1,ms=3,label='B')
 ax.axhline(4095,color='#b22',ls=':',label='ADC ceiling')
 ax.axhline(2800,color='gray',ls='--',lw=.8,label='Offline Schmitt center')
 ax.set(title=title,xlabel='Device sample time (ms)',ylabel='Raw ADC counts',ylim=(1000,4250))
 ax.legend(loc='lower right',ncol=4,fontsize=8);ax.grid(alpha=.2)
fig.suptitle('Buffered samples from diagnostic firmware; A/B conversions are sequential',fontsize=13)
fig.savefig(HERE/'yaw-waveforms.png',dpi=180)
