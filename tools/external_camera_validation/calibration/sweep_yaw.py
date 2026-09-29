"""Collect a >=20-degree camera-referenced out-and-back yaw sweep.

Each move is independently timed on the board and followed by 5 s settling.
Stops on stale vision, invalid quadrature transitions, or DMA data loss.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import re
import signal
import time
import serial

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'local_tools/calibration/dma-20deg-sweep.jsonl'
VISION = ROOT / 'camera_yaw_pitch_tracker/telescope_angles.csv'

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args()
    previous=[json.loads(line) for line in OUT.read_text().splitlines()] if args.resume else []
    if previous and not previous[-1]['stage'].startswith('forward_'):
        parser.error('Resume currently supports an interrupted outbound sweep only.')
    def interrupted(*_):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupted)
    s = serial.Serial(port=None, baudrate=115200, timeout=.025)
    s.dtr = False; s.rts = False
    s.port = '/dev/cu.wchusbserial112330'
    s.open()
    stage = previous[-1]['stage'] if previous else 'baseline'
    latest_stats = {}
    counts = {}
    def vision():
        rows = VISION.read_text().splitlines()
        fields = rows[-1].split(',')
        age = (dt.datetime.now().astimezone() - dt.datetime.fromisoformat(fields[0])).total_seconds()
        if not 0 <= age < 2 or not math.isfinite(float(fields[1])):
            raise RuntimeError(f'Camera readings stale or invalid: age={age:.3f}s, row={rows[-1]}')
        return rows[-1]
    with OUT.open('a' if args.resume else 'x') as out:
        def log(kind, value):
            row = dict(time=dt.datetime.now().astimezone().isoformat(), stage=stage, kind=kind, value=value, vision=vision())
            out.write(json.dumps(row)+'\n'); out.flush()
        def send(cmd):
            log('command',cmd)
            s.write((cmd+'\n').encode())
        def read_for(seconds):
            deadline = time.monotonic()+seconds; poll=0
            while time.monotonic()<deadline:
                if time.monotonic()>=poll:
                    send('STATUS'); send('ENCODER STATS'); poll=time.monotonic()+.5
                line=s.readline().decode(errors='replace').strip()
                if not line:continue
                log('serial',line)
                if line.startswith(('MOTOR_STATS','AXIS_STATS')):
                    name=line.split()[0]
                    stats={k:int(v) for k,v in re.findall(r'(\w+)=(-?\d+)',line)}
                    latest_stats[name]=stats
                    if stats.get('invalid',0) or (name=='MOTOR_STATS' and any(stats.get(k,0) for k in ('overflows','errors','bad_order'))):
                        raise RuntimeError('Decoder or DMA fault: '+line)
                m=re.search(r'ENCODER([01]):.*pos=(-?\d+)',line)
                if m:counts[int(m[1])]=int(m[2])
        try:
            if not previous:
                log('metadata',dict(firmware_sha256=hashlib.sha256((ROOT/'build-unified/motorsense.bin').read_bytes()).hexdigest(),duty=60,motor_pair_rate=40000,axis_rate=1000))
            send('MOTOR S'); read_for(6)
            if latest_stats.get('MOTOR_STATS',{}).get('ready')!=1 or len(counts)!=2:
                raise RuntimeError('Missing encoder telemetry')
            if previous:
                base=next(r['value'] for r in previous if r['stage']=='baseline' and r['kind']=='plateau')
                initial=base['yaw']; baseline_counts={int(k):v for k,v in base['positions'].items()}
                next_step=int(stage.split('_')[1])+1
                yaw=float(vision().split(',')[1])
                log('plateau',dict(yaw=yaw,positions=counts.copy()))
                print('RESUME',stage,'yaw',yaw,'original baseline',initial,flush=True)
            else:
                initial=float(vision().split(',')[1]); baseline_counts=counts.copy()
                log('plateau',dict(yaw=initial,positions=counts.copy()))
                print('BASELINE',initial,counts,flush=True)
                yaw=initial; next_step=1
            for step in range(next_step,16):
                stage=f'forward_{step:02d}'
                send('MOTOR F 60 2'); read_for(7)
                yaw=float(vision().split(',')[1])
                log('plateau',dict(yaw=yaw,positions=counts.copy()))
                print(stage,'yaw',yaw,'span',yaw-initial,'positions',counts,flush=True)
                if yaw-initial>=21:break
                if yaw<initial-1 or yaw>initial+26:raise RuntimeError('Unexpected camera direction/span')
            if yaw-initial<20:raise RuntimeError('Did not reach 20 degrees')
            peak=yaw
            for step in range(1,17):
                stage=f'reverse_{step:02d}'
                remaining=yaw-initial
                if remaining<.2:break
                seconds=min(2.,max(.3,remaining/1.1))
                send(f'MOTOR B 60 {seconds:.3f}'); read_for(seconds+5)
                yaw=float(vision().split(',')[1])
                log('plateau',dict(yaw=yaw,positions=counts.copy()))
                print(stage,'yaw',yaw,'remaining',yaw-initial,'positions',counts,flush=True)
                if yaw<initial-3 or yaw>peak+1:raise RuntimeError('Unexpected return trajectory')
            stage='final_stationary'; read_for(10)
            log('plateau',dict(yaw=float(vision().split(',')[1]),positions=counts.copy()))
            print('DONE',dict(initial=initial,peak=peak,final=yaw,initial_counts=baseline_counts,final_counts=counts),flush=True)
        finally:
            s.write(b'MOTOR S\n'); s.flush(); s.close()

if __name__=='__main__':main()
