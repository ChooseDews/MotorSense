"""LX200-over-TCP bridge from INDI's LX200 driver to the telescope socket.

Run this beside ``motor-sense-telescope``.  It is deliberately small: an
existing INDI LX200 Network driver supplies the INDI property layer, while this
process translates its standard LX200 commands to the app's local socket API.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import tty
from pathlib import Path
from typing import Any

from .app import default_socket_path
from .indi_socket_adapter import request


def _format_hms(hours: float) -> str:
    total = round((hours % 24.0) * 3600)
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _format_dms(degrees: float, width: int = 2, lx200_degree: bool = False) -> str:
    sign = "+" if degrees >= 0 else "-"
    total = round(abs(degrees) * 3600)
    d, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    separator = "\xdf" if lx200_degree else "*"
    return f"{sign}{d:0{width}d}{separator}{m:02d}:{s:02d}"


def _parse_sexagesimal(value: str, is_hours: bool = False) -> float:
    match = re.fullmatch(r"([+-]?)(\d+)(?:[:*\xdf](\d+))?(?::(\d+(?:\.\d+)?))?", value.strip())
    if not match:
        raise ValueError(f"Invalid LX200 coordinate: {value}")
    sign = -1 if match.group(1) == "-" else 1
    degrees = float(match.group(2))
    minutes = float(match.group(3) or 0)
    seconds = float(match.group(4) or 0)
    answer = sign * (degrees + minutes / 60 + seconds / 3600)
    if is_hours and not 0 <= answer < 24:
        raise ValueError("RA must be in [0, 24) hours")
    return answer


class Lx200SocketBridge:
    def __init__(self, socket_path: Path) -> None:
        self.socket_path = socket_path
        self.target_ra: float | None = None
        self.target_dec: float | None = None

    async def _socket(self, command: str) -> dict[str, Any]:
        response = await request(self.socket_path, command)
        if not response.get("ok"):
            raise RuntimeError(response.get("error", "socket request failed"))
        return response["result"]

    async def _event(self, text: str) -> None:
        """Mirror useful Stellarium traffic in the GUI's Extra Controls log."""
        try:
            await self._socket(f"log Stellarium: {text}")
        except Exception as exc:
            print(f"Could not forward Stellarium event: {exc}", flush=True)

    async def _radec(self) -> tuple[float, float]:
        """Convert the measured mount Alt/Az back to ICRS for LX200 queries."""
        state = await self._socket("get_state")
        try:
            import astropy.units as u
            from astropy.coordinates import AltAz, EarthLocation, SkyCoord
            from astropy.time import Time
        except ImportError as exc:  # pragma: no cover - pinned app dependency
            raise RuntimeError("astropy is required for LX200 coordinates") from exc
        matrix = state["calibration"]["mount_matrix"]
        offset = state["calibration"]["mount_offset"]
        pitch = state["measured"]["pitch"]
        yaw = state["measured"]["yaw"]
        determinant = matrix[0][0] * matrix[1][1] - matrix[0][1] * matrix[1][0]
        if abs(determinant) < 1e-9:
            raise RuntimeError("mount calibration matrix is singular")
        corrected_pitch, corrected_yaw = pitch - offset[0], yaw - offset[1]
        altitude = (matrix[1][1] * corrected_pitch - matrix[0][1] * corrected_yaw) / determinant
        azimuth = (-matrix[1][0] * corrected_pitch + matrix[0][0] * corrected_yaw) / determinant
        location = state["location"]
        frame = AltAz(
            obstime=Time.now(),
            location=EarthLocation(lat=location["latitude"] * u.deg, lon=location["longitude"] * u.deg,
                                   height=location["elevation_m"] * u.m),
        )
        # AltAz is a spherical frame: positional coordinates are longitude
        # (azimuth) first, then latitude (altitude).
        coordinate = SkyCoord((azimuth % 360.0) * u.deg, altitude * u.deg, frame=frame).icrs
        return float(coordinate.ra.hour), float(coordinate.dec.deg)

    async def handle(self, command: str) -> str:
        if command in {"\x06", "\x06#"}:
            return "P"
        if command == ":GR#":
            ra, _ = await self._radec()
            return _format_hms(ra) + "#"
        if command == ":GD#":
            _, dec = await self._radec()
            return _format_dms(dec, lx200_degree=True) + "#"
        if command == ":GVP#": return "MotorSense Telescope#"
        if command == ":GVN#": return "1.0#"
        if command == ":GVD#": return "2026-09-19#"
        if command in {":Q#", ":Qe#", ":Qw#", ":Qn#", ":Qs#", ":Qall#"}:
            await self._socket("stop")
            await self._event(f"stop command {command}")
            return ""
        if command in {":hP#", ":KA#"}:
            await self._socket("park")
            return ""
        if command.startswith(":Sr") and command.endswith("#"):
            self.target_ra = _parse_sexagesimal(command[3:-1], is_hours=True)
            await self._event(f"target RA {self.target_ra:.6f}h")
            return "1"
        if command.startswith(":Sd") and command.endswith("#"):
            self.target_dec = _parse_sexagesimal(command[3:-1])
            await self._event(f"target Dec {self.target_dec:+.6f}°")
            return "1"
        if command == ":MS#":
            if self.target_ra is None or self.target_dec is None:
                return "1"
            await self._socket(f"goto_radec {self.target_ra:.9f} {self.target_dec:.9f}")
            await self._event(f"GOTO RA {self.target_ra:.6f}h  Dec {self.target_dec:+.6f}°")
            return "0"
        if command == ":CM#":
            if self.target_ra is None or self.target_dec is None:
                return "0"
            await self._socket(f"sync {self.target_ra:.9f} {self.target_dec:.9f}")
            await self._event(f"SYNC applied: RA {self.target_ra:.6f}h  Dec {self.target_dec:+.6f}°")
            return "Coordinates matched.        #"
        if command == ":Gt#":
            return _format_dms((await self._socket("get_state"))["location"]["latitude"]) + "#"
        if command == ":Gg#":
            longitude = (await self._socket("get_state"))["location"]["longitude"]
            return _format_dms(abs(longitude), width=3) + "#"
        if command in {":U#", ":D#", ":RS#"}:
            return ""
        return ""

    async def client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        buffer = ""
        try:
            while data := await reader.read(256):
                async def write(response: str) -> None:
                    writer.write(response.encode("latin-1"))
                    await writer.drain()
                buffer = await self.consume(buffer, data, write)
        finally:
            writer.close()
            await writer.wait_closed()

    async def consume(self, buffer: str, data: bytes, write: Any) -> str:
        """Process complete commands from a TCP or PTY byte stream."""
        # Stellarium's LX200 client sends byte 0xDF as the degree separator.
        # latin-1 preserves it one-for-one; ASCII with errors="ignore" loses it.
        buffer += data.decode("latin-1")
        while buffer:
            if buffer[0] == "\x06":
                command, buffer = "\x06", buffer[1:]
            elif "#" in buffer:
                end = buffer.index("#") + 1
                command, buffer = buffer[:end], buffer[end:]
            else:
                break
            try:
                response = await self.handle(command)
                print(f"LX200 {command!r} -> {response!r}", flush=True)
            except Exception as exc:
                print(f"LX200 {command!r} failed: {exc}", flush=True)
                response = "0" if command.startswith((":Sr", ":Sd")) else "1" if command == ":MS#" else ""
            if response:
                await write(response)
        return buffer


class Lx200Pty:
    """A serial device endpoint for ``indi_lx200basic`` on macOS."""
    def __init__(self, bridge: Lx200SocketBridge) -> None:
        self.bridge = bridge
        self.master, self.slave = os.openpty()
        tty.setraw(self.slave)
        self.path = os.ttyname(self.slave)
        # Keep a slave descriptor open.  Without it macOS returns EIO for a
        # master write during the serial client's reconnect window, dropping
        # LX200 responses and preventing Stellarium from reaching :CM#.
        self.buffer = ""
        self._consume_lock = asyncio.Lock()
        self.loop = asyncio.get_running_loop()
        self.loop.add_reader(self.master, self._ready)

    def _ready(self) -> None:
        try:
            data = os.read(self.master, 256)
        except OSError:
            return
        if data:
            asyncio.create_task(self._consume(data))

    async def _consume(self, data: bytes) -> None:
        async def write(response: str) -> None:
            os.write(self.master, response.encode("latin-1"))
        try:
            async with self._consume_lock:
                self.buffer = await self.bridge.consume(self.buffer, data, write)
        except OSError as exc:
            print(f"LX200 PTY write failed: {exc}", flush=True)

    def close(self) -> None:
        self.loop.remove_reader(self.master)
        os.close(self.master)
        os.close(self.slave)


async def run(host: str, port: int, socket_path: Path, pty: bool) -> None:
    bridge = Lx200SocketBridge(socket_path)
    server = await asyncio.start_server(bridge.client, host, port)
    sockets = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    print(f"LX200 bridge listening at {sockets}; telescope socket {socket_path}", flush=True)
    serial = Lx200Pty(bridge) if pty else None
    if serial:
        print(f"LX200 serial PTY: {serial.path}", flush=True)
    try:
        async with server:
            await server.serve_forever()
    finally:
        if serial:
            serial.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4030)
    parser.add_argument("--pty", action="store_true", help="also expose a virtual serial port for indi_lx200basic")
    parser.add_argument("--socket", type=Path, default=default_socket_path())
    args = parser.parse_args()
    asyncio.run(run(args.host, args.port, args.socket, args.pty))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
