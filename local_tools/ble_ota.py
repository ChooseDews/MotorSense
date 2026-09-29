#!/usr/bin/env python3
"""Upload a MotorSense application image using BLE NUS (pip install bleak)."""
import argparse
import asyncio
import hashlib
import struct
from pathlib import Path

RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

async def upload(address, image):
    from bleak import BleakClient
    firmware = image.read_bytes()
    lines = asyncio.Queue()
    buffer = bytearray()
    def notify(_, data):
        buffer.extend(data)
        while b"\n" in buffer:
            line, _, rest = buffer.partition(b"\n")
            buffer[:] = rest
            lines.put_nowait(line.decode(errors="replace").strip())
    async with BleakClient(address) as client:
        await client.start_notify(TX, notify)
        mtu = getattr(client, "mtu_size", 0) or 23
        # Frames ride directly in RX writes. A cumulative notification closes
        # each eight-frame window instead of acknowledging every image chunk.
        chunk_size = min(500, max(1, mtu - 3 - 9))
        window_size = 8

        async def response(prefix):
            while True:
                line = await lines.get()
                if line.startswith("ERR"):
                    raise RuntimeError(line)
                if line.startswith(prefix):
                    return line
        async def command(text, prefix, timeout=30):
            packet = (text + "\n").encode()
            limit = max(20, mtu - 3)
            # One GATT write per MTU-sized segment; only the final segment pays
            # for a write-with-response round trip.
            for offset in range(0, len(packet), limit):
                part = packet[offset:offset + limit]
                final = offset + limit >= len(packet)
                await client.write_gatt_char(RX, part, response=final)
            return await asyncio.wait_for(response(prefix), timeout)
        await command("OTA ABORT", "OK OTA ABORT")
        try:
            await command(f"OTA BIN BEGIN {len(firmware)} {hashlib.sha256(firmware).hexdigest()}", "OK OTA BIN BEGIN", 120)
            for window_start in range(0, len(firmware), chunk_size * window_size):
                frames = []
                offset = window_start
                window_end = min(window_start + chunk_size * window_size, len(firmware))
                while offset < window_end:
                    chunk = firmware[offset:min(offset + chunk_size, window_end)]
                    offset += len(chunk)
                    flags = 1 if offset == window_end else 0
                    frames.append(struct.pack("<2sIHB", b"MS", offset - len(chunk), len(chunk), flags) + chunk)
                for frame in frames:
                    await client.write_gatt_char(RX, frame, response=False)
                reply = await asyncio.wait_for(response("OK OTA BIN "), 30)
                if reply != f"OK OTA BIN {window_end}":
                    raise RuntimeError(f"Unexpected acknowledgement: {reply}")
                print(f"\r{window_end/len(firmware):.0%}", end="", flush=True)
            await command("OTA END", "OK OTA REBOOT")
            print("\nImage verified; reboot requested. Reconnect after 15 seconds and check DEVICE INFO / OTA STATUS.")
        except Exception:
            if client.is_connected:
                try:
                    await command("OTA ABORT", "OK OTA ABORT", 5)
                except Exception:
                    pass
            raise

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("address", help="BLE address or macOS peripheral UUID")
    parser.add_argument("image", type=Path)
    args = parser.parse_args()
    asyncio.run(upload(args.address, args.image))
