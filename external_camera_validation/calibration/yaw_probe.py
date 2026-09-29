"""Log yaw-board USB telemetry with vision timestamps and optional timed motion.

Run from any directory. Requires pyserial. Keep the camera tracker running.
Diagnostic --adc/--burst require the diagnostic firmware, not the original image.
"""
import argparse
import datetime as dt
import json
import math
from pathlib import Path
import re
import signal
import time

import serial

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', default='/dev/cu.wchusbserial112330')
    parser.add_argument('--command', default='STATUS')
    parser.add_argument('--seconds', type=float, default=5)
    parser.add_argument('--max-duty', type=int, default=60, choices=range(1,101), metavar='1..100')
    parser.add_argument('--output', type=Path, default=ROOT / 'local_tools/calibration/yaw_capture.jsonl')
    parser.add_argument('--adc', action='store_true')
    parser.add_argument('--burst', action='store_true')
    parser.add_argument('--motor-burst', action='store_true')
    parser.add_argument('--stats', action='store_true')
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args()
    motion = re.fullmatch(r'MOTOR ([FB]) (\d+) (\d+(?:\.\d+)?)', args.command)
    if args.command not in ('STATUS', 'HELP'):
        if not motion or not 0 < int(motion[2]) <= args.max_duty or not 0 < float(motion[3]) <= 5:
            parser.error('Use STATUS, HELP, or MOTOR F|B duty seconds (duty <= --max-duty, seconds <=5).')
        if args.seconds < float(motion[3]) + 1:
            parser.error('Capture must extend at least one second beyond the timed move.')
    if not 0 < args.seconds <= 60:
        parser.error('--seconds must be between 0 and 60.')
    if sum((args.burst, args.motor_burst, args.adc)) > 1:
        parser.error('Use either --burst or --adc, not both.')
    vision = ROOT / 'camera_yaw_pitch_tracker/telescope_angles.csv'

    def latest():
        with vision.open('rb') as file:
            file.seek(max(0, vision.stat().st_size - 1024))
            return file.read().decode().splitlines()[-1]

    def check_vision():
        row = latest()
        timestamp, yaw, pitch = row.split(',')
        age = (dt.datetime.now().astimezone() - dt.datetime.fromisoformat(timestamp)).total_seconds()
        if not 0 <= age < 2 or not math.isfinite(float(yaw)):
            raise RuntimeError('Vision readings are stale or invalid; motor command cancelled.')
        return row

    if motion:
        check_vision()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    def interrupted(*_):
        raise KeyboardInterrupt()

    signal.signal(signal.SIGTERM, interrupted)
    connection = serial.Serial(port=None, baudrate=115200, timeout=.03)
    connection.dtr = False
    connection.rts = False
    connection.port = args.port
    connection.open()
    with args.output.open('a') as output:
        def log(kind, value):
            row = dict(time=dt.datetime.now().astimezone().isoformat(), kind=kind,
                       value=value, vision=latest())
            output.write(json.dumps(row) + '\n')
            output.flush()

        def send(command):
            connection.write((command + '\n').encode())

        try:
            log('command', args.command)
            if args.adc:
                send('ADC STREAM START')
            send(args.command)
            started = time.monotonic()
            poll = started
            burst_sent = False
            while time.monotonic() - started < args.seconds:
                now = time.monotonic()
                if (args.burst or args.motor_burst) and not burst_sent and now - started >= .5:
                    send('ENCODER MOTORCAPTURE' if args.motor_burst else 'ENCODER CAPTURE')
                    burst_sent = True
                    poll = now + 5  # Do not fill the command queue while the burst dumps.
                if now >= poll:
                    if motion:
                        check_vision()
                    send('STATUS')
                    if args.stats:
                        send('ENCODER STATS')
                    poll = now + .5
                line = connection.readline().decode(errors='replace').strip()
                if line:
                    log('serial', line)
                    if args.verbose:
                        print(line, flush=True)
        finally:
            try:
                send('MOTOR S')
                if args.adc:
                    send('ADC STREAM STOP')
                connection.flush()
                log('cleanup', 'MOTOR S; ADC STREAM STOP' if args.adc else 'MOTOR S')
            finally:
                connection.close()
    print('VISION', latest())
    print('CAPTURE', args.output)


if __name__ == '__main__':
    main()
