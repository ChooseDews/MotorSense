"""Dual-axis MotorSense telescope application.

The app keeps BLE transport, mount calibration, sky coordinates, Qt UI, and
the local socket API around one TelescopeState instance. Board-side AXIS MOVE
is deliberately used for every physical slew: the microcontrollers own the
fast feedback loop and this process owns only target selection and tracking.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import csv
import json
import math
import os
import re
import shutil
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

import numpy as np
from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from PySide6 import QtCore, QtGui, QtWidgets
from qasync import QEventLoop, asyncSlot

try:
    import astropy.units as u
    from astropy.coordinates import AltAz, EarthLocation, SkyCoord, get_body, get_sun
    from astropy.time import Time
    HAS_ASTROPY = True
except ImportError:  # GUI and direct mount control remain useful without astronomy extras.
    HAS_ASTROPY = False

NUS_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
AXIS_RE = re.compile(r"(?:\bENC\s+pos0=|\bENCODER0:.*?\bpos=|quad_enc: enc=0 pos=)(-?\d+)")
AXIS_STATUS_RE = re.compile(
    r"AXIS: state=(\w+) start=(-?\d+) target=(-?\d+) position=(-?\d+) duty=(-?\d+) corrections=(\d+)"
)
# The board accepts a single AXIS MOVE only within +/-30 degrees, and rejects
# any new move while an axis is still running/settling (main/include/axis_motion.h).
MAX_AXIS_MOVE_SEGMENT_DEGREES = 25.0


def wrap_degrees(value: float) -> float:
    return value % 360.0


def shortest_delta(current: float, target: float) -> float:
    return (target - current + 180.0) % 360.0 - 180.0


def default_socket_path() -> Path:
    return Path.home() / "Library/Application Support/MotorSense/telescope.sock"


def default_config_path() -> Path:
    return Path.home() / "Library/Application Support/MotorSense/telescope.json"


@dataclass
class AxisCalibration:
    degrees_per_tick: float
    reference_ticks: int = 0
    reference_degrees: float = 0.0
    max_duty: int = 80

    def angle_from_ticks(self, ticks: int) -> float:
        return self.reference_degrees + (ticks - self.reference_ticks) * self.degrees_per_tick

    def sync(self, ticks: int, degrees: float) -> None:
        self.reference_ticks = ticks
        self.reference_degrees = degrees


@dataclass
class MountModel:
    """Affine correction from desired Alt/Az to physical pitch/yaw degrees."""

    matrix: np.ndarray = field(default_factory=lambda: np.eye(2))
    offset: np.ndarray = field(default_factory=lambda: np.zeros(2))

    def mount_from_altaz(self, altitude: float, azimuth: float) -> tuple[float, float]:
        pitch, yaw = self.matrix @ np.array([altitude, azimuth]) + self.offset
        return float(pitch), wrap_degrees(float(yaw))

    def altaz_from_mount(self, pitch: float, yaw: float) -> tuple[float, float]:
        altitude, azimuth = np.linalg.solve(self.matrix, np.array([pitch, yaw]) - self.offset)
        return float(altitude), wrap_degrees(float(azimuth))


@dataclass
class AxisState:
    name: Literal["yaw", "pitch"]
    calibration: AxisCalibration
    device: BLEDevice | None = None
    client: BleakClient | None = None
    connected: bool = False
    connecting: bool = False
    ticks: int = 0
    angle: float = 0.0
    motion_state: str = "IDLE"
    target_ticks: int | None = None
    duty: int = 0
    corrections: int = 0
    last_notification: float = 0.0
    last_io: float = 0.0
    error: str | None = None
    buffer: str = ""

    def update_ticks(self, ticks: int) -> None:
        self.ticks = ticks
        self.angle = self.calibration.angle_from_ticks(ticks)
        self.last_notification = time.monotonic()
        # Fresh telemetry means any earlier command-level ERR has been superseded.
        self.error = None

    @property
    def link_connected(self) -> bool:
        """True while the BLE client is actually connected.

        Deliberately independent of telemetry freshness and of command-level ERR
        replies: the board can reject a command, or notifications can pause,
        while the GATT link stays usable for writes. The backend's live
        is_connected() is authoritative, so a spurious disconnect callback is
        corrected here rather than leaving the axis stuck on "Disconnected".
        """
        client = self.client
        if client is None:
            return False
        try:
            live = client.is_connected
        except Exception:
            live = False
        if live:
            self.connected = True
            if self.error == "Disconnected":
                self.error = None
        return live

    @property
    def telemetry_stale(self) -> bool:
        return self.link_connected and time.monotonic() - self.last_notification >= 5.0

    @property
    def healthy(self) -> bool:
        return self.link_connected and not self.telemetry_stale


@dataclass
class TelescopeState:
    yaw: AxisState = field(default_factory=lambda: AxisState("yaw", AxisCalibration(0.03041048252)))
    pitch: AxisState = field(default_factory=lambda: AxisState("pitch", AxisCalibration(0.030361306325309)))
    mount_model: MountModel = field(default_factory=MountModel)
    target_yaw: float | None = None
    target_pitch: float | None = None
    ra_hours: float | None = None
    dec_degrees: float | None = None
    target_object: str | None = None
    tracking: bool = False
    latitude: float = 37.3349
    longitude: float = -122.0090
    elevation_m: float = 0.0
    last_astronomy_update: float = 0.0
    last_command: str = "No mount command sent"

    def snapshot(self) -> dict[str, Any]:
        return {
            "measured": {"yaw": self.yaw.angle, "pitch": self.pitch.angle},
            "target": {"yaw": self.target_yaw, "pitch": self.target_pitch},
            "radec": {"ra_hours": self.ra_hours, "dec_degrees": self.dec_degrees},
            "target_object": self.target_object,
            "tracking": self.tracking,
            "last_command": self.last_command,
            "location": {"latitude": self.latitude, "longitude": self.longitude, "elevation_m": self.elevation_m},
            "ble": {
                "yaw": self._axis_snapshot(self.yaw),
                "pitch": self._axis_snapshot(self.pitch),
            },
            "calibration": {
                "yaw": asdict(self.yaw.calibration),
                "pitch": asdict(self.pitch.calibration),
                "mount_matrix": self.mount_model.matrix.tolist(),
                "mount_offset": self.mount_model.offset.tolist(),
            },
        }

    @staticmethod
    def _axis_snapshot(axis: AxisState) -> dict[str, Any]:
        return {
            "connected": axis.connected, "link_connected": axis.link_connected,
            "healthy": axis.healthy, "telemetry_stale": axis.telemetry_stale,
            "connecting": axis.connecting,
            "device": axis.device.name if axis.device else None,
            "ticks": axis.ticks, "angle": axis.angle,
            "motion_state": axis.motion_state, "duty": axis.duty,
            "error": axis.error,
        }


def load_configuration(state: TelescopeState, path: Path) -> None:
    """Restore the last verified mount references and observer location."""
    if not path.exists():
        return
    try:
        saved = json.loads(path.read_text())
        for name in ("yaw", "pitch"):
            target = getattr(state, name).calibration
            source = saved["calibration"][name]
            target.degrees_per_tick = float(source["degrees_per_tick"])
            target.reference_ticks = int(source["reference_ticks"])
            target.reference_degrees = float(source["reference_degrees"])
            target.max_duty = int(source["max_duty"])
        state.mount_model.matrix = np.asarray(saved["calibration"]["mount_matrix"], dtype=float)
        state.mount_model.offset = np.asarray(saved["calibration"]["mount_offset"], dtype=float)
        location = saved["location"]
        state.latitude, state.longitude = float(location["latitude"]), float(location["longitude"])
        state.elevation_m = float(location["elevation_m"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid telescope configuration at {path}: {exc}") from exc


def save_configuration(state: TelescopeState, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"calibration": state.snapshot()["calibration"], "location": state.snapshot()["location"]}, indent=2) + "\n")


class DualBleMount:
    """Two simultaneous CoreBluetooth/Bleak clients, created from one scan."""

    def __init__(self, state: TelescopeState, log: Callable[[str], None]) -> None:
        self.state = state
        self.log = log
        self._state_callbacks: list[Callable[[], None]] = []
        self._poll_task: asyncio.Task[None] | None = None
        self._write_locks = {"yaw": asyncio.Lock(), "pitch": asyncio.Lock()}

    def on_state_change(self, callback: Callable[[], None]) -> None:
        self._state_callbacks.append(callback)

    def _changed(self) -> None:
        for callback in self._state_callbacks:
            callback()

    async def scan_and_connect(self, timeout: float = 6.0) -> None:
        devices = await BleakScanner.discover(timeout=timeout)
        available = {((d.name or "").lower()): d for d in devices}
        selected: dict[str, BLEDevice] = {}
        for axis, suffix in (("yaw", "-78"), ("pitch", "-158")):
            matches = [d for name, d in available.items() if "motorsense" in name and name.endswith(suffix)]
            if not matches:
                raise RuntimeError(f"Could not find MotorSense {axis} controller ({suffix})")
            selected[axis] = matches[0]
        for axis in selected:
            getattr(self.state, axis).connecting = True
        self._changed()
        await asyncio.gather(*(self._connect_axis(getattr(self.state, name), device) for name, device in selected.items()))
        if self._poll_task is None or self._poll_task.done():
            # Restart if a previous poll loop ever stopped: it is what keeps the
            # telemetry timestamps fresh, independent of the write link.
            self._poll_task = asyncio.create_task(self._poll_loop())

    async def _connect_axis(self, axis: AxisState, device: BLEDevice) -> None:
        await self.disconnect_axis(axis)
        axis.connecting = True
        axis.device = device  # Keep the scan result; do not reconnect by CoreBluetooth UUID/address.

        def disconnected(_: BleakClient) -> None:
            axis.connected = False
            axis.error = "Disconnected"
            self._changed()

        try:
            client = BleakClient(device, disconnected_callback=disconnected)
            await client.connect(timeout=20.0)
            if not client.is_connected:
                raise RuntimeError(f"{axis.name} did not connect")
            axis.client = client
            axis.connected = True
            axis.error = None
            axis.last_notification = 0.0  # do not inherit a stale timestamp across reconnects
            await client.start_notify(NUS_TX_UUID, lambda _, data: self._notification(axis, bytes(data)))
            await self.command(axis, "STATUS")
            await self.command(axis, "AXIS STATUS")
            self.log(f"{axis.name.title()} connected: {device.name}\n")
        finally:
            axis.connecting = False
            self._changed()

    def _notification(self, axis: AxisState, payload: bytes) -> None:
        axis.buffer += payload.decode("utf-8", errors="replace")
        while "\n" in axis.buffer:
            line, axis.buffer = axis.buffer.split("\n", 1)
            self._parse_line(axis, line.rstrip("\r"))

    def _parse_line(self, axis: AxisState, line: str) -> None:
        tick = AXIS_RE.search(line)
        if tick:
            axis.update_ticks(int(tick.group(1)))
        status = AXIS_STATUS_RE.search(line)
        if status:
            axis.motion_state = status.group(1)
            axis.target_ticks = int(status.group(3))
            axis.update_ticks(int(status.group(4)))
            axis.duty = int(status.group(5))
            axis.corrections = int(status.group(6))
        if line.startswith("ERR"):
            axis.error = line
            self.log(f"{axis.name.title()} BLE error: {line}\n")
        self._changed()

    async def command(self, axis: AxisState, command: str) -> None:
        if not axis.client or not axis.client.is_connected:
            raise RuntimeError(f"{axis.name} BLE is not connected")
        async with self._write_locks[axis.name]:
            await axis.client.write_gatt_char(NUS_RX_UUID, (command + "\n").encode(), response=False)
        axis.last_io = time.monotonic()
        if command not in {"STATUS", "AXIS STATUS"}:
            self.state.last_command = f"{axis.name.upper()}: {command}"
            self._changed()

    async def goto_mount(self, yaw: float, pitch: float, duty: int | None = None) -> None:
        if not self.state.yaw.link_connected or not self.state.pitch.link_connected:
            raise RuntimeError("Both axes must be connected")
        self.state.target_yaw, self.state.target_pitch = wrap_degrees(yaw), pitch
        moves = []
        for axis, delta in ((self.state.yaw, shortest_delta(self.state.yaw.angle, yaw)),
                            (self.state.pitch, pitch - self.state.pitch.angle)):
            if abs(delta) >= axis.calibration.degrees_per_tick / 2:
                moves.append((axis, delta))
        if not moves:
            return
        await asyncio.gather(*(self._move_axis(axis, delta, duty) for axis, delta in moves))

    async def _move_axis(self, axis: AxisState, delta: float, duty: int | None) -> None:
        """Send one AXIS MOVE, splitting deltas beyond the board's +/-30 deg limit
        into segments that complete before the next one is sent (a fresh move while
        an axis is still running is refused by the board)."""
        parts = math.ceil(abs(delta) / MAX_AXIS_MOVE_SEGMENT_DEGREES)
        if parts <= 1:
            await self.command(axis, f"AXIS MOVE {delta:.5f} {duty or axis.calibration.max_duty}")
            return
        segment = delta / parts
        for index in range(parts):
            await self.command(axis, f"AXIS MOVE {segment:.5f} {duty or axis.calibration.max_duty}")
            if index < parts - 1:
                await self._wait_axis_idle(axis)

    async def _wait_axis_idle(self, axis: AxisState, timeout: float = 60.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if axis.motion_state in {"IDLE", "DONE"}:
                return
            await asyncio.sleep(0.2)
        raise RuntimeError(f"{axis.name} did not finish an AXIS MOVE segment")

    async def stop(self) -> None:
        self.state.tracking = False
        await asyncio.gather(*(self.command(axis, "AXIS STOP") for axis in (self.state.yaw, self.state.pitch) if axis.link_connected), return_exceptions=True)
        self._changed()

    async def home(self) -> None:
        await self.goto_mount(0.0, 0.0)

    async def disconnect_axis(self, axis: AxisState) -> None:
        client, axis.client = axis.client, None
        axis.connected = False
        axis.connecting = False
        if client:
            with contextlib.suppress(Exception):
                await client.stop_notify(NUS_TX_UUID)
            with contextlib.suppress(Exception):
                await client.disconnect()

    async def close(self) -> None:
        await self.stop()
        if self._poll_task:
            self._poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._poll_task
        await asyncio.gather(*(self.disconnect_axis(axis) for axis in (self.state.yaw, self.state.pitch)))

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.gather(*(self.command(axis, "AXIS STATUS") for axis in (self.state.yaw, self.state.pitch) if axis.link_connected), return_exceptions=True)
            # Refresh even when no reply arrives so a paused telemetry stream is
            # shown as "No telemetry" instead of freezing on a stale label.
            self._changed()
            await asyncio.sleep(0.5)


class AstronomyEngine:
    def __init__(self, state: TelescopeState) -> None:
        self.state = state

    def altaz_for_radec(self, ra_hours: float, dec_degrees: float, object_name: str | None = None) -> tuple[float, float]:
        if not HAS_ASTROPY:
            raise RuntimeError("Install astropy to use RA/Dec and object control")
        now = Time.now()
        location = EarthLocation(lat=self.state.latitude * u.deg, lon=self.state.longitude * u.deg, height=self.state.elevation_m * u.m)
        if object_name:
            body = object_name.lower()
            coordinate = get_sun(now) if body == "sun" else get_body(body, now, location)
        else:
            coordinate = SkyCoord(ra=ra_hours * u.hourangle, dec=dec_degrees * u.deg, frame="icrs")
        altaz = coordinate.transform_to(AltAz(obstime=now, location=location))
        return float(altaz.alt.deg), wrap_degrees(float(altaz.az.deg))

    def sidereal_hours(self) -> float:
        if not HAS_ASTROPY:
            return 0.0
        return float(Time.now().sidereal_time("apparent", longitude=self.state.longitude * u.deg).hour)


class TelescopeController:
    def __init__(self, state: TelescopeState, mount: DualBleMount, log: Callable[[str], None]) -> None:
        self.state, self.mount, self.log = state, mount, log
        self.astronomy = AstronomyEngine(state)
        self._tracking_task: asyncio.Task[None] | None = None

    async def goto_radec(self, ra_hours: float, dec_degrees: float) -> None:
        self.state.ra_hours, self.state.dec_degrees, self.state.target_object = ra_hours, dec_degrees, None
        altitude, azimuth = self.astronomy.altaz_for_radec(ra_hours, dec_degrees)
        pitch, yaw = self.state.mount_model.mount_from_altaz(altitude, azimuth)
        await self.mount.goto_mount(yaw, pitch)

    async def track_radec(self, ra_hours: float, dec_degrees: float, object_name: str | None = None) -> None:
        self.state.ra_hours, self.state.dec_degrees, self.state.target_object = ra_hours, dec_degrees, object_name
        self.state.tracking = True
        if self._tracking_task is None or self._tracking_task.done():
            self._tracking_task = asyncio.create_task(self._tracking_loop())

    async def track_object(self, name: str) -> None:
        if not HAS_ASTROPY:
            raise RuntimeError("Install astropy to track named solar-system bodies")
        if name.lower() not in {"sun", "moon", "mercury", "venus", "mars", "jupiter", "saturn", "uranus", "neptune"}:
            raise ValueError("Supported objects: Sun, Moon, Mercury, Venus, Mars, Jupiter, Saturn, Uranus, Neptune")
        await self.track_radec(0.0, 0.0, name)

    async def stop(self) -> None:
        self.state.tracking = False
        if self._tracking_task and self._tracking_task is not asyncio.current_task():
            self._tracking_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._tracking_task
        await self.mount.stop()

    async def _tracking_loop(self) -> None:
        try:
            while self.state.tracking:
                if self.state.target_object:
                    altitude, azimuth = self.astronomy.altaz_for_radec(0, 0, self.state.target_object)
                else:
                    altitude, azimuth = self.astronomy.altaz_for_radec(self.state.ra_hours or 0, self.state.dec_degrees or 0)
                pitch, yaw = self.state.mount_model.mount_from_altaz(altitude, azimuth)
                # Avoid interrupting board-side correction and ignore sub-resolution updates.
                yaw_error = abs(shortest_delta(self.state.yaw.angle, yaw))
                pitch_error = abs(self.state.pitch.angle - pitch)
                if (self.state.yaw.motion_state in {"IDLE", "DONE"} and self.state.pitch.motion_state in {"IDLE", "DONE"}
                        and max(yaw_error, pitch_error) >= 0.15):
                    await self.mount.goto_mount(yaw, pitch, duty=35)
                self.state.last_astronomy_update = time.monotonic()
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.state.tracking = False
            self.log(f"Tracking stopped: {exc}\n")


class SocketApi:
    def __init__(self, controller: TelescopeController, path: Path, log: Callable[[str], None], config_path: Path | None = None) -> None:
        self.controller, self.path, self.log = controller, path, log
        self.config_path = config_path or default_config_path()
        self.server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            if self.path.is_socket(): self.path.unlink()
            else: raise RuntimeError(f"Socket path exists and is not a socket: {self.path}")
        self.server = await asyncio.start_unix_server(self._client, path=str(self.path))
        self.log(f"Socket API listening at {self.path}\n")

    async def close(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        if self.path.exists() and self.path.is_socket(): self.path.unlink()

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while line := await reader.readline():
                try:
                    result = await self.dispatch(line.decode().strip())
                    response = {"ok": True, "result": result}
                except Exception as exc:
                    response = {"ok": False, "error": str(exc)}
                writer.write((json.dumps(response) + "\n").encode())
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def dispatch(self, raw: str) -> dict[str, Any]:
        request = json.loads(raw) if raw.startswith("{") else {"command": raw}
        raw_command = str(request.get("command", "")).strip()
        command = raw_command.lower()
        parts = command.split()
        if parts and parts[0] == "get_state": return self.controller.state.snapshot()
        if parts and parts[0] == "log":
            self.log(raw_command[4:].strip())
            return self.controller.state.snapshot()
        if parts and parts[0] == "connect": await self.controller.mount.scan_and_connect()
        elif parts and parts[0] == "home": await self.controller.mount.home()
        elif parts and parts[0] == "stop": await self.controller.stop()
        elif parts and parts[0] == "park": await self.controller.stop(); await self.controller.mount.home()
        elif parts and parts[0] == "goto_mount": await self.controller.mount.goto_mount(float(parts[1]), float(parts[2]))
        elif parts and parts[0] == "goto_radec": await self.controller.goto_radec(float(parts[1]), float(parts[2]))
        elif parts and parts[0] == "track_radec": await self.controller.track_radec(float(parts[1]), float(parts[2]))
        elif parts and parts[0] == "track_object": await self.controller.track_object(" ".join(parts[1:]).strip('"'))
        elif parts and parts[0] == "set_location":
            self.controller.state.latitude = float(parts[1])
            self.controller.state.longitude = float(parts[2])
            if len(parts) > 3:
                self.controller.state.elevation_m = float(parts[3])
            save_configuration(self.controller.state, self.config_path)
        elif parts and parts[0] == "sync":
            altitude, azimuth = self.controller.astronomy.altaz_for_radec(float(parts[1]), float(parts[2]))
            pitch, yaw = self.controller.state.mount_model.mount_from_altaz(altitude, azimuth)
            self.controller.state.yaw.calibration.sync(self.controller.state.yaw.ticks, yaw)
            self.controller.state.pitch.calibration.sync(self.controller.state.pitch.ticks, pitch)
            # Reflect the new zero references immediately; waiting for the next
            # encoder notification makes a successful Stellarium sync look inert.
            self.controller.state.yaw.update_ticks(self.controller.state.yaw.ticks)
            self.controller.state.pitch.update_ticks(self.controller.state.pitch.ticks)
            save_configuration(self.controller.state, self.config_path)
            self.controller.mount._changed()
        else: raise ValueError("Commands: connect, log, get_state, set_location, home, goto_mount, goto_radec, track_radec, track_object, stop, park, sync")
        return self.controller.state.snapshot()


class ExtraControlsWindow(QtWidgets.QDialog):
    """The non-essential telescope controls, kept out of the status panel."""
    def __init__(self, panel: "MainWindow") -> None:
        super().__init__(panel)
        self.panel = panel; self.controller = panel.controller; self.mount = panel.mount; self.state = panel.state
        self.setWindowTitle("MotorSense Telescope — Extra Controls")
        self.resize(680, 560)
        layout = QtWidgets.QVBoxLayout(self)
        connection = QtWidgets.QHBoxLayout(); layout.addLayout(connection)
        for title, slot in (("Connect controllers", self.connect), ("Home", self.home), ("External calibration", self.open_external_calibration), ("Launch Stellarium", self.launch_stellarium)):
            button = QtWidgets.QPushButton(title); button.clicked.connect(slot); connection.addWidget(button)
        mount_group = QtWidgets.QGroupBox("Closed-loop mount position"); layout.addWidget(mount_group)
        mount_layout = QtWidgets.QGridLayout(mount_group)
        self.yaw_input = QtWidgets.QDoubleSpinBox(); self.yaw_input.setRange(0, 360); self.yaw_input.setDecimals(3); self.yaw_input.setSuffix("°")
        self.pitch_input = QtWidgets.QDoubleSpinBox(); self.pitch_input.setRange(-90, 90); self.pitch_input.setDecimals(3); self.pitch_input.setSuffix("°")
        self.duty_input = QtWidgets.QSpinBox(); self.duty_input.setRange(10, 100); self.duty_input.setValue(70); self.duty_input.setSuffix(" %")
        mount_layout.addWidget(QtWidgets.QLabel("Yaw target"), 0, 0); mount_layout.addWidget(self.yaw_input, 0, 1)
        mount_layout.addWidget(QtWidgets.QLabel("Pitch target"), 0, 2); mount_layout.addWidget(self.pitch_input, 0, 3)
        mount_layout.addWidget(QtWidgets.QLabel("Max duty"), 0, 4); mount_layout.addWidget(self.duty_input, 0, 5)
        goto = QtWidgets.QPushButton("Goto mount position"); goto.clicked.connect(self.goto_mount); mount_layout.addWidget(goto, 1, 0, 1, 2)
        steps = QtWidgets.QGroupBox("Relative encoder moves"); layout.addWidget(steps)
        step_layout = QtWidgets.QGridLayout(steps)
        for row, (axis, yaw_scale, pitch_scale) in enumerate((("Yaw", 1.0, 0.0), ("Pitch", 0.0, 1.0))):
            step_layout.addWidget(QtWidgets.QLabel(axis), row, 0)
            for column, amount in enumerate((-15, -5, -1, -0.25, 0.25, 1, 5, 15), start=1):
                title = f"{amount:+g}°"
                button = QtWidgets.QPushButton(title)
                button.clicked.connect(lambda _=False, y=amount * yaw_scale, p=amount * pitch_scale: self.move_relative(y, p))
                step_layout.addWidget(button, row, column)
        sky_group = QtWidgets.QGroupBox("Sky target"); layout.addWidget(sky_group)
        sky = QtWidgets.QGridLayout(sky_group)
        self.ra_input, self.dec_input, self.object_input = QtWidgets.QLineEdit("12.0"), QtWidgets.QLineEdit("0.0"), QtWidgets.QLineEdit("Jupiter")
        sky.addWidget(QtWidgets.QLabel("RA hours"), 0, 0); sky.addWidget(self.ra_input, 0, 1)
        sky.addWidget(QtWidgets.QLabel("Dec degrees"), 0, 2); sky.addWidget(self.dec_input, 0, 3)
        sky.addWidget(QtWidgets.QLabel("Object"), 1, 0); sky.addWidget(self.object_input, 1, 1, 1, 3)
        for column, (title, slot) in enumerate((("Goto RA/Dec", self.goto_radec), ("Track RA/Dec", self.track_radec), ("Track object", self.track_object))):
            button = QtWidgets.QPushButton(title); button.clicked.connect(slot); sky.addWidget(button, 2, column)
        self.log_box = QtWidgets.QPlainTextEdit(); self.log_box.setReadOnly(True); self.log_box.setMaximumBlockCount(200); layout.addWidget(self.log_box, 1)
        self.refresh()

    def log(self, text: str) -> None: self.log_box.appendPlainText(text.rstrip())
    def refresh(self) -> None:
        if not self.yaw_input.hasFocus(): self.yaw_input.setValue(wrap_degrees(self.state.yaw.angle))
        if not self.pitch_input.hasFocus(): self.pitch_input.setValue(self.state.pitch.angle)
    @asyncSlot()
    async def connect(self) -> None:
        try: await self.mount.scan_and_connect(); self.log("Both controllers connected")
        except Exception as exc: self.log(f"Connect failed: {exc}")
    @asyncSlot()
    async def home(self) -> None:
        try: await self.mount.home()
        except Exception as exc: self.log(str(exc))
    @asyncSlot()
    async def goto_mount(self) -> None:
        try: await self.mount.goto_mount(self.yaw_input.value(), self.pitch_input.value(), self.duty_input.value())
        except Exception as exc: self.log(f"Mount GOTO failed: {exc}")
    @asyncSlot()
    async def move_relative(self, yaw_delta: float, pitch_delta: float) -> None:
        try: await self.mount.goto_mount(wrap_degrees(self.state.yaw.angle + yaw_delta), self.state.pitch.angle + pitch_delta, self.duty_input.value())
        except Exception as exc: self.log(f"Relative move failed: {exc}")
    @asyncSlot()
    async def goto_radec(self) -> None:
        try: await self.controller.goto_radec(float(self.ra_input.text()), float(self.dec_input.text()))
        except Exception as exc: self.log(str(exc))
    @asyncSlot()
    async def track_radec(self) -> None:
        try: await self.controller.track_radec(float(self.ra_input.text()), float(self.dec_input.text()))
        except Exception as exc: self.log(str(exc))
    @asyncSlot()
    async def track_object(self) -> None:
        try: await self.controller.track_object(self.object_input.text())
        except Exception as exc: self.log(str(exc))
    def calibrate(self) -> None: self.log("Center a known target, then use Stellarium Sync or socket: sync <RA hours> <Dec degrees>.")
    def open_external_calibration(self) -> None:
        if not hasattr(self, "external_calibration"):
            self.external_calibration = ExternalCalibrationWindow(self)
        self.external_calibration.show(); self.external_calibration.raise_(); self.external_calibration.activateWindow()
    def launch_stellarium(self) -> None:
        candidates = [shutil.which("stellarium"), "/Applications/Stellarium.app/Contents/MacOS/Stellarium", "/Applications/stellarium.app/Contents/MacOS/stellarium"]
        stellarium = next((item for item in candidates if item and os.path.exists(item)), None)
        if stellarium: QtCore.QProcess.startDetached(stellarium, [])
        else: self.log("Stellarium was not found in PATH or /Applications.")


class ExternalCalibrationWindow(QtWidgets.QDialog):
    """Settled-position encoder comparison against the relative CV tracker."""
    def __init__(self, controls: ExtraControlsWindow) -> None:
        super().__init__(controls)
        self.controls = controls; self.controller = controls.controller; self.mount = controls.mount; self.state = controls.state
        self.csv_path = Path(__file__).resolve().parents[3] / "camera_yaw_pitch_tracker/telescope_angles.csv"
        self.reference: tuple[float, float, float, float, str] | None = None
        self.result_rows: list[dict[str, float | int | str | None]] = []
        self.task: asyncio.Task[None] | None = None
        self.setWindowTitle("External Calibration — Camera Comparison")
        self.resize(770, 530)
        layout = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel("Camera values are used only as relative displacement from the captured reference. Each point waits for encoder settling and a fresh CV CSV update.")
        note.setWordWrap(True); layout.addWidget(note)
        setup = QtWidgets.QGridLayout(); layout.addLayout(setup)
        self.mode = QtWidgets.QComboBox(); self.mode.addItems(["Yaw", "Pitch", "Yaw + Pitch"])
        self.step = QtWidgets.QDoubleSpinBox(); self.step.setRange(0.1, 30); self.step.setValue(2.0); self.step.setDecimals(2); self.step.setSuffix("° / point")
        self.points = QtWidgets.QSpinBox(); self.points.setRange(1, 20); self.points.setValue(5)
        self.duty = QtWidgets.QSpinBox(); self.duty.setRange(10, 100); self.duty.setValue(35); self.duty.setSuffix(" %")
        self.settle = QtWidgets.QDoubleSpinBox(); self.settle.setRange(0.5, 15); self.settle.setValue(2.0); self.settle.setDecimals(1); self.settle.setSuffix(" s")
        self.return_home = QtWidgets.QCheckBox("Return to reference after sweep"); self.return_home.setChecked(True)
        for row, (label, widget) in enumerate((("Sweep", self.mode), ("Step", self.step), ("Points", self.points), ("Max duty", self.duty), ("Settle", self.settle))):
            setup.addWidget(QtWidgets.QLabel(label), row // 3, (row % 3) * 2); setup.addWidget(widget, row // 3, (row % 3) * 2 + 1)
        setup.addWidget(self.return_home, 2, 0, 1, 3)
        buttons = QtWidgets.QHBoxLayout(); layout.addLayout(buttons)
        reference = QtWidgets.QPushButton("Capture CV reference"); reference.clicked.connect(self.capture_reference); buttons.addWidget(reference)
        run = QtWidgets.QPushButton("Run sweep"); run.clicked.connect(self.start_sweep); buttons.addWidget(run)
        export = QtWidgets.QPushButton("Export CSV…"); export.clicked.connect(self.export_csv); buttons.addWidget(export)
        abort = QtWidgets.QPushButton("STOP"); abort.clicked.connect(self.abort); buttons.addWidget(abort); buttons.addStretch()
        self.cv_status = QtWidgets.QLabel(f"CV file: {self.csv_path}"); layout.addWidget(self.cv_status)
        self.results = QtWidgets.QTableWidget(0, 7); self.results.setHorizontalHeaderLabels(["Point", "Encoder yaw Δ", "CV yaw Δ", "Yaw error", "Encoder pitch Δ", "CV pitch Δ", "Pitch error"])
        self.results.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents); self.results.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers); layout.addWidget(self.results, 1)
        self.summary = QtWidgets.QLabel("Capture a reference, then run a small sweep."); self.summary.setWordWrap(True); layout.addWidget(self.summary)

    def _camera(self) -> tuple[float, float, str]:
        if not self.csv_path.exists():
            raise RuntimeError(f"CV CSV not found: {self.csv_path}")
        rows: list[tuple[float, float, str]] = []
        with self.csv_path.open(newline="") as handle:
            for row in csv.DictReader(handle):
                try:
                    yaw, pitch = float(row["yaw"]), float(row["pitch"])
                    if math.isfinite(yaw) and math.isfinite(pitch): rows.append((yaw, pitch, row["datetime"]))
                except (KeyError, TypeError, ValueError): continue
        if len(rows) < 3: raise RuntimeError("CV needs at least three finite samples")
        if time.time() - self.csv_path.stat().st_mtime > 3.0: raise RuntimeError("CV CSV is stale; start or check the camera tracker")
        # The tracker writes a rolling history.  Use only its newest frames:
        # a median over the whole history makes the CV value lag behind a move.
        recent = rows[-3:]
        return statistics.median(item[0] for item in recent), statistics.median(item[1] for item in recent), recent[-1][2]

    async def _fresh_camera(self, previous: str | None = None) -> tuple[float, float, str]:
        deadline = time.monotonic() + 8.0
        last_error = "CV did not update"
        while time.monotonic() < deadline:
            try:
                camera = self._camera()
                if previous is None or camera[2] != previous: return camera
                last_error = "waiting for a fresh CV row"
            except RuntimeError as exc: last_error = str(exc)
            await asyncio.sleep(0.25)
        raise RuntimeError(last_error)

    async def _capture_reference(self) -> bool:
        try:
            yaw, pitch, stamp = await self._fresh_camera()
            self.reference = (self.state.yaw.angle, self.state.pitch.angle, yaw, pitch, stamp)
            self.cv_status.setText(f"Reference captured — encoder {self.state.yaw.angle:.3f}°, {self.state.pitch.angle:.3f}° | CV {yaw:.3f}°, {pitch:.3f}°")
            self.summary.setText("Reference ready. The sweep compares displacement only; CV zero and absolute offset are ignored.")
            return True
        except Exception as exc: self.summary.setText(f"Reference failed: {exc}")
        return False

    @asyncSlot()
    async def capture_reference(self) -> None:
        await self._capture_reference()

    def start_sweep(self) -> None:
        if self.task and not self.task.done(): return
        self.task = asyncio.create_task(self._run_sweep())

    def abort(self) -> None:
        if self.task and not self.task.done(): self.task.cancel()
        asyncio.create_task(self.controller.stop())
        self.summary.setText("Sweep stopped; motors commanded to stop.")

    async def _wait_settled(self, target_yaw: float, target_pitch: float) -> None:
        """Wait until the encoders hold the requested position for ``settle`` seconds.

        The board considers a move done only within +/-2 ticks, which is tighter
        than the tolerance used here. Requiring the board's DONE state as well
        meant a lingering low-duty correction (state RUNNING/SETTLING) kept
        resetting the stable timer even though the position was already good.
        The encoder position staying inside tolerance is the real definition of
        settled, so the board state is only consulted to surface hard faults.
        """
        deadline = time.monotonic() + 45.0
        stable_since: float | None = None
        yaw_tolerance = max(0.15, self.state.yaw.calibration.degrees_per_tick * 4)
        pitch_tolerance = max(0.15, self.state.pitch.calibration.degrees_per_tick * 4)
        fault_states = {"STALL", "TIMEOUT", "SENSOR_FAULT", "WRONG_DIRECTION"}
        while time.monotonic() < deadline:
            yaw_ready = abs(shortest_delta(self.state.yaw.angle, target_yaw)) <= yaw_tolerance
            pitch_ready = abs(self.state.pitch.angle - target_pitch) <= pitch_tolerance
            if yaw_ready and pitch_ready:
                stable_since = stable_since or time.monotonic()
                if time.monotonic() - stable_since >= self.settle.value(): return
            else:
                stable_since = None
                fault = next((axis for axis in (self.state.yaw, self.state.pitch) if axis.motion_state in fault_states), None)
                if fault is not None:
                    raise RuntimeError(f"{fault.name} axis reported {fault.motion_state}; move did not reach its target")
            await asyncio.sleep(0.15)
        raise RuntimeError("Timed out waiting for the requested encoder positions to settle")

    def _append_result(self, point: int, values: tuple[float | None, ...]) -> None:
        row = self.results.rowCount(); self.results.insertRow(row)
        labels = [str(point)] + [("—" if value is None else f"{value:+.3f}°") for value in values]
        for column, text in enumerate(labels): self.results.setItem(row, column, QtWidgets.QTableWidgetItem(text))

    def export_csv(self) -> None:
        if not self.result_rows:
            self.summary.setText("Run a sweep before exporting results.")
            return
        default = Path.home() / f"MotorSense-external-calibration-{time.strftime('%Y%m%d-%H%M%S')}.csv"
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export external calibration", str(default), "CSV files (*.csv)")
        if not filename: return
        try:
            with Path(filename).open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(self.result_rows[0]))
                writer.writeheader(); writer.writerows(self.result_rows)
            self.summary.setText(f"Exported {len(self.result_rows)} calibration points to {filename}")
        except OSError as exc:
            self.summary.setText(f"CSV export failed: {exc}")

    async def _collect_point(self, point: int, total: int, target_yaw: float, target_pitch: float,
                             base_yaw: float, base_pitch: float, cv_yaw0: float, cv_pitch0: float,
                             yaw_active: bool, pitch_active: bool, mode: str, step: float,
                             yaw_errors: list[float], pitch_errors: list[float]) -> None:
        """Move, settle, then record one sweep datapoint: encoder displacement vs CV displacement."""
        self.summary.setText(f"Point {point}/{total}: moving, then waiting for CV…")
        await self.mount.goto_mount(target_yaw, target_pitch, self.duty.value())
        await self._wait_settled(target_yaw, target_pitch)
        # Ignore frames written while the move was in progress.  The
        # comparison must use a camera sample captured after settling.
        settled_stamp = self._camera()[2]
        cv_yaw, cv_pitch, stamp = await self._fresh_camera(settled_stamp)
        encoder_yaw = shortest_delta(base_yaw, self.state.yaw.angle) if yaw_active else None
        encoder_pitch = self.state.pitch.angle - base_pitch if pitch_active else None
        camera_yaw = shortest_delta(cv_yaw0, cv_yaw) if yaw_active else None
        camera_pitch = cv_pitch - cv_pitch0 if pitch_active else None
        yaw_error = None if encoder_yaw is None else encoder_yaw - camera_yaw
        pitch_error = None if encoder_pitch is None else encoder_pitch - camera_pitch
        if yaw_error is not None: yaw_errors.append(yaw_error)
        if pitch_error is not None: pitch_errors.append(pitch_error)
        self._append_result(point, (encoder_yaw, camera_yaw, yaw_error, encoder_pitch, camera_pitch, pitch_error))
        self.result_rows.append({
            "point": point, "mode": mode, "step_degrees": step, "max_duty_percent": self.duty.value(),
            "settle_seconds": self.settle.value(), "reference_timestamp": self.reference[4], "cv_timestamp": stamp,
            "reference_encoder_yaw_degrees": base_yaw, "reference_encoder_pitch_degrees": base_pitch,
            "reference_cv_yaw_degrees": cv_yaw0, "reference_cv_pitch_degrees": cv_pitch0,
            "encoder_yaw_delta_degrees": encoder_yaw, "cv_yaw_delta_degrees": camera_yaw, "yaw_error_degrees": yaw_error,
            "encoder_pitch_delta_degrees": encoder_pitch, "cv_pitch_delta_degrees": camera_pitch, "pitch_error_degrees": pitch_error,
        })

    async def _run_sweep(self) -> None:
        try:
            if not self.state.yaw.link_connected or not self.state.pitch.link_connected: raise RuntimeError("Connect both controllers first")
            if self.reference is None and not await self._capture_reference(): return
            if self.reference is None: return
            base_yaw, base_pitch, cv_yaw0, cv_pitch0, stamp = self.reference
            mode, step, count = self.mode.currentText(), self.step.value(), self.points.value()
            yaw_active, pitch_active = mode in {"Yaw", "Yaw + Pitch"}, mode in {"Pitch", "Yaw + Pitch"}
            if yaw_active and step * count > 150:
                raise RuntimeError("Keep a yaw sweep at 150° or less so relative CV yaw cannot wrap ambiguously")
            self.results.setRowCount(0); self.result_rows.clear(); yaw_errors: list[float] = []; pitch_errors: list[float] = []
            for point in range(1, count + 1):
                target_yaw = wrap_degrees(base_yaw + (point * step if yaw_active else 0.0))
                target_pitch = base_pitch + (point * step if pitch_active else 0.0)
                await self._collect_point(point, count, target_yaw, target_pitch, base_yaw, base_pitch,
                                          cv_yaw0, cv_pitch0, yaw_active, pitch_active, mode, step,
                                          yaw_errors, pitch_errors)
            if self.return_home.isChecked():
                # Close the loop: returning to the reference position is the
                # final datapoint, measuring drift/closure over the whole sweep.
                return_point = count + 1
                self.summary.setText(f"Returning to the reference position… collecting final closure point {return_point}.")
                await self._collect_point(return_point, return_point, wrap_degrees(base_yaw), base_pitch,
                                          base_yaw, base_pitch, cv_yaw0, cv_pitch0,
                                          yaw_active, pitch_active, f"{mode} — return to reference", step,
                                          yaw_errors, pitch_errors)
            metrics = []
            for name, errors in (("Yaw", yaw_errors), ("Pitch", pitch_errors)):
                if errors: metrics.append(f"{name}: RMS {math.sqrt(sum(error * error for error in errors) / len(errors)):.3f}°, max {max(abs(error) for error in errors):.3f}°")
            self.summary.setText("Relative CV agreement — " + "; ".join(metrics))
            self.controls.log("External calibration complete: " + self.summary.text())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.summary.setText(f"Sweep failed: {exc}"); self.controls.log(self.summary.text())
        finally:
            self.task = None


class AxisDiagram(QtWidgets.QWidget):
    """A deliberately small visual legend for the Alt/Az mount axes."""
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(208, 48)

    def paintEvent(self, _: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self); painter.setRenderHint(QtGui.QPainter.Antialiasing)
        pen = QtGui.QPen(QtGui.QColor("#9aa4ad")); pen.setWidth(2); painter.setPen(pen)
        # Mount base and vertical pitch arm.
        painter.drawLine(12, 34, 93, 34); painter.drawLine(53, 34, 53, 9)
        painter.drawEllipse(QtCore.QPoint(53, 34), 4, 4)
        yaw_pen = QtGui.QPen(QtGui.QColor("#46b5e6")); yaw_pen.setWidth(3); painter.setPen(yaw_pen)
        painter.drawArc(23, 13, 60, 42, 205 * 16, 125 * 16)
        pitch_pen = QtGui.QPen(QtGui.QColor("#c99aef")); pitch_pen.setWidth(3); painter.setPen(pitch_pen)
        painter.drawArc(38, 2, 30, 30, 35 * 16, 110 * 16)
        painter.setPen(QtGui.QColor("#9aa4ad")); painter.setFont(QtGui.QFont("", 10))
        painter.drawText(105, 19, "Yaw  ↻  Azimuth")
        painter.drawText(105, 39, "Pitch ↕  Altitude")


class MainWindow(QtWidgets.QMainWindow):
    changed = QtCore.Signal()
    def __init__(self, controller: TelescopeController, mount: DualBleMount, socket_api: SocketApi) -> None:
        super().__init__()
        self.controller, self.mount, self.socket_api = controller, mount, socket_api; self.state = controller.state
        self._shutting_down = False
        self._shutdown_task: asyncio.Task[None] | None = None
        self.setWindowTitle("MotorSense Mount")
        self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, True)
        self.setMinimumWidth(390); self.resize(410, 300)
        self.setStyleSheet("QLabel#value { font-size:12px; font-weight:600; } QLabel#header { color:#9aa4ad; font-size:10px; font-weight:600; } QPushButton { min-height:26px; }")
        central = QtWidgets.QWidget(); self.setCentralWidget(central); layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(10, 8, 10, 10); layout.setSpacing(4)
        header = QtWidgets.QHBoxLayout(); header.setContentsMargins(0, 0, 0, 0); layout.addLayout(header)
        self.status = QtWidgets.QLabel(); self.status.setObjectName("value"); header.addWidget(self.status, 1)
        header.addWidget(AxisDiagram())
        self.axis_values: dict[str, dict[str, QtWidgets.QLabel]] = {}
        axes = QtWidgets.QGridLayout(); axes.setHorizontalSpacing(7); axes.setVerticalSpacing(1); layout.addLayout(axes)
        for column, title in enumerate(("AXIS", "CURRENT", "TARGET", "MOTOR", "BLE")):
            header = QtWidgets.QLabel(title); header.setObjectName("header"); axes.addWidget(header, 0, column)
        for row, axis in enumerate((self.state.yaw, self.state.pitch), start=1):
            values: dict[str, QtWidgets.QLabel] = {}
            label = QtWidgets.QLabel(axis.name.title()); label.setObjectName("value"); axes.addWidget(label, row, 0)
            for column, key in enumerate(("current", "target", "motor", "ble"), start=1):
                value = QtWidgets.QLabel("—"); value.setObjectName("value"); axes.addWidget(value, row, column); values[key] = value
            self.axis_values[axis.name] = values
        sky = QtWidgets.QGridLayout(); sky.setVerticalSpacing(1); layout.addLayout(sky)
        self.values: dict[str, QtWidgets.QLabel] = {}
        for row, (label, key) in enumerate((("RA", "ra"), ("Dec", "dec"), ("Target", "target"), ("Tracking", "tracking"))):
            column = 0 if row < 2 else 2; local_row = row % 2
            sky.addWidget(QtWidgets.QLabel(label), local_row, column); value = QtWidgets.QLabel("—"); value.setObjectName("value"); sky.addWidget(value, local_row, column + 1); self.values[key] = value
        self.last_command = QtWidgets.QLabel("Last: No mount command sent"); self.last_command.setObjectName("header"); self.last_command.setWordWrap(False); layout.addWidget(self.last_command)
        buttons = QtWidgets.QHBoxLayout(); layout.addLayout(buttons)
        stop = QtWidgets.QPushButton("STOP"); stop.setStyleSheet("background:#b42318; color:white; font-weight:700;"); stop.clicked.connect(self.stop); buttons.addWidget(stop)
        connect = QtWidgets.QPushButton("Connect"); connect.clicked.connect(self.connect); buttons.addWidget(connect)
        extra = QtWidgets.QPushButton("Extra Controls"); extra.clicked.connect(self.open_extra); buttons.addWidget(extra)
        self.extra = ExtraControlsWindow(self)
        self.changed.connect(self.refresh); mount.on_state_change(self.changed.emit); self.refresh()
    def log(self, text: str) -> None: self.extra.log(text)
    def refresh(self) -> None:
        linked = self.state.yaw.link_connected and self.state.pitch.link_connected
        stale = linked and (self.state.yaw.telemetry_stale or self.state.pitch.telemetry_stale)
        connecting = self.state.yaw.connecting or self.state.pitch.connecting
        if linked and not stale:
            state_text, color = "● Mount connected", "#26b56a"
        elif linked and stale:
            state_text, color = "● Mount connected — no telemetry", "#e0aa24"
        elif connecting:
            state_text, color = "● Connecting controllers", "#e0aa24"
        else:
            state_text, color = "● Mount disconnected", "#e05252"
        self.status.setText(state_text); self.status.setStyleSheet(f"color:{color};")
        for axis in (self.state.yaw, self.state.pitch):
            values = self.axis_values[axis.name]
            values["current"].setText(f"{axis.angle:.3f}°")
            target = self.state.target_yaw if axis.name == "yaw" else self.state.target_pitch
            values["target"].setText("—" if target is None else f"{target:.3f}°")
            values["motor"].setText(f"{axis.motion_state}  ·  {axis.duty}%")
            if not axis.link_connected:
                ble_text, ble_color = ("● Connecting", "#e0aa24") if axis.connecting else ("● Disconnected", "#e05252")
            elif axis.telemetry_stale:
                ble_text, ble_color = "● No telemetry", "#e0aa24"
            else:
                ble_text, ble_color = "● Connected", "#26b56a"
            values["ble"].setText(ble_text); values["ble"].setStyleSheet(f"color:{ble_color};")
        self.values["ra"].setText("—" if self.state.ra_hours is None else f"{self.state.ra_hours:.4f}h")
        self.values["dec"].setText("—" if self.state.dec_degrees is None else f"{self.state.dec_degrees:+.4f}°")
        self.values["target"].setText(self.state.target_object or ("RA/Dec target" if self.state.ra_hours is not None else "—"))
        self.values["tracking"].setText("● Tracking" if self.state.tracking else "○ Stopped")
        self.values["tracking"].setStyleSheet(f"color:{'#26b56a' if self.state.tracking else '#e05252'};")
        self.last_command.setText(f"Last: {self.state.last_command}")
        self.extra.refresh()
    @asyncSlot()
    async def stop(self) -> None: await self.controller.stop()
    @asyncSlot()
    async def connect(self) -> None:
        try: await self.mount.scan_and_connect(); self.log("Both controllers connected")
        except Exception as exc: self.log(f"Connect failed: {exc}")
    def open_extra(self) -> None: self.extra.show(); self.extra.raise_(); self.extra.activateWindow()
    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        # Keep the window (and event loop) alive until cleanup has run, but only
        # ever start a single shutdown. Repeated or queued close events must not
        # spawn concurrent shutdown tasks, which race inside mount.close().
        event.ignore()
        if self._shutting_down:
            return
        self._shutting_down = True
        # Retain the task so it is not garbage collected before it finishes.
        self._shutdown_task = asyncio.create_task(self.shutdown())
    async def shutdown(self) -> None:
        try:
            self.extra.close()
            with contextlib.suppress(Exception):
                await self.socket_api.close()
            with contextlib.suppress(Exception):
                await self.mount.close()
        finally:
            # Always leave the Qt/qasync loop, even if cleanup failed.  Under
            # qasync, QApplication.quit() alone can leave run_forever() spinning,
            # so stop the asyncio loop as well.
            QtWidgets.QApplication.quit()
            asyncio.get_running_loop().stop()


def build_application(socket_path: Path) -> tuple[TelescopeState, DualBleMount, TelescopeController, SocketApi]:
    state = TelescopeState()
    load_configuration(state, default_config_path())
    pending_logs: list[str] = []
    def log(message: str) -> None: pending_logs.append(message)
    mount = DualBleMount(state, log)
    controller = TelescopeController(state, mount, log)
    socket_api = SocketApi(controller, socket_path, log)
    return state, mount, controller, socket_api


async def run_gui(socket_path: Path) -> int:
    state, mount, controller, socket_api = build_application(socket_path)
    await socket_api.start()
    window = MainWindow(controller, mount, socket_api)
    mount.log = window.log; socket_api.log = window.log; controller.log = window.log
    window.show()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", type=Path, default=default_socket_path())
    args = parser.parse_args()
    qt_app = QtWidgets.QApplication(sys.argv)
    loop = QEventLoop(qt_app); asyncio.set_event_loop(loop)
    with loop:
        loop.run_until_complete(run_gui(args.socket))
        loop.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
