"""Pitch-board timed tests, with fresh camera reference and guaranteed host stop.

Each --move is direction,duty,seconds. Output is exclusive-create JSONL.
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
VISION = ROOT / 'camera_yaw_pitch_tracker/telescope_angles.csv'

def camera():
    with VISION.open('rb') as f:
        f.seek(max(0, VISION.stat().st_size-4096))
        lines=f.read().decode().splitlines()
    fields=lines[-1].split(',')
    age=(dt.datetime.now().astimezone()-dt.datetime.fromisoformat(fields[0])).total_seconds()
    if not 0 <= age < 2 or not math.isfinite(float(fields[2])):
        raise RuntimeError(f'Stale/invalid pitch: {lines[-1]}, age={age}')
    return lines[-1]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port',default='/dev/cu.wchusbserial12330')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--move',action='append',default=[])
    p.add_argument('--axis-only',action='store_true',help='Validate axis acquisition without depending on motor encoder')
    p.add_argument('--settle',type=float,default=6)
    p.add_argument('--capture',choices=['axis','motor'])
    p.add_argument('--firmware',type=Path,default=ROOT/'build-pitch/mesh-blinker.bin')
    p.add_argument('--allow-decoder-faults',action='store_true',help='Diagnostic short tests only')
    a=p.parse_args()
    moves=[]
    for value in a.move:
        direction,duty,seconds=value.split(','); duty=int(duty); seconds=float(seconds)
        if direction not in ['F','B'] or not 0<duty<=100 or not 0<seconds<=2: p.error('Moves require F|B,1..100,0..2 seconds')
        moves.append((direction,duty,seconds))
    if a.allow_decoder_faults and sum(m[2] for m in moves)>2:p.error('Diagnostic motion limited to 2 seconds total')
    camera()
    def interrupt(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,interrupt)
    s=serial.Serial(port=None,baudrate=115200,timeout=.025);s.dtr=False;s.rts=False;s.port=a.port
    stage='baseline';counts={};baseline_stats={};latest_stats={}
    initial_pitch=float(camera().split(',')[2])
    last_pairs=None;last_progress=time.monotonic()
    with a.output.open('x') as out:
        s.open()
        def log(kind,value):
            out.write(json.dumps(dict(time=dt.datetime.now().astimezone().isoformat(),stage=stage,kind=kind,value=value,vision=camera()))+'\n');out.flush()
        def send(cmd):
            log('command',cmd);s.write((cmd+'\n').encode())
        def read_for(seconds,capture=False):
            nonlocal last_pairs,last_progress
            end=time.monotonic()+seconds;poll=0;start=time.monotonic();captured=False
            while time.monotonic()<end:
                current_pitch=float(camera().split(',')[2])
                if abs(current_pitch-initial_pitch)>18:raise RuntimeError('Exceeded 18 degree test envelope')
                if capture and not captured and time.monotonic()-start>.15:
                    send('ENCODER CAPTURE' if a.capture=='axis' else 'ENCODER MOTORCAPTURE');captured=True;poll=time.monotonic()+3
                if time.monotonic()>=poll:
                    send('STATUS');send('ENCODER STATS');poll=time.monotonic()+.5
                line=s.readline().decode(errors='replace').strip()
                if not line:continue
                log('serial',line)
                m=re.search(r'ENCODER([01]):.*pos=(-?\d+)',line)
                if m:counts[int(m[1])]=int(m[2])
                if line.startswith(('AXIS_STATS ','MOTOR_STATS ')):
                    tag=line.split()[0];stats={k:int(v) for k,v in re.findall(r'(\w+)=(-?\d+)',line)};latest_stats[tag]=stats
                    if tag=='MOTOR_STATS' and not a.axis_only:
                        if stats['pairs']!=last_pairs:last_pairs=stats['pairs'];last_progress=time.monotonic()
                        elif time.monotonic()-last_progress>1.5:raise RuntimeError('Motor DMA sample counter stopped advancing')
                    if stage!='baseline' and not a.allow_decoder_faults and (not a.axis_only or tag=='AXIS_STATS'):
                        keys=['invalid','errors','overflows','bad_order']
                        if any(stats.get(k,0)>baseline_stats[tag].get(k,0) for k in keys):raise RuntimeError('Acquisition fault: '+line)
        def plateau():
            log('plateau',dict(pitch=float(camera().split(',')[2]),positions=counts.copy()))
            print(stage,camera(),counts,latest_stats,flush=True)
        try:
            log('metadata',dict(firmware_sha256=hashlib.sha256(a.firmware.read_bytes()).hexdigest(),axis='pitch',moves=moves))
            send('MOTOR S');read_for(a.settle);plateau()
            if len(counts)!=2 or (not a.axis_only and latest_stats.get('MOTOR_STATS',{}).get('ready')!=1):raise RuntimeError('Missing encoders / DMA not ready')
            baseline_stats={k:v.copy() for k,v in latest_stats.items()}
            for i,(direction,duty,seconds) in enumerate(moves):
                stage=f'{direction}_{i+1:02d}'
                send(f'MOTOR {direction} {duty} {seconds}');read_for(seconds+a.settle,capture=bool(a.capture));plateau()
            stage='final_stationary';read_for(3);plateau()
        finally:
            s.write(b'MOTOR S\n');s.flush();s.close()

if __name__=='__main__':main()
