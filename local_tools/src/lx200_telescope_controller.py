"""
LX200 Telescope Controller

Connects to two BLE encoder devices and exposes an LX200 protocol server
for telescope control software (e.g., Stellarium, SkySafari, etc.)
"""

import asyncio
import logging
import os
import pty
import re
import socket
import sys
import termios
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Callable

from bleak import BleakClient, BleakScanner
from PySide6 import QtCore, QtGui, QtWidgets
from qasync import QEventLoop, asyncSlot

# Nordic UART Service (NUS) UUIDs
NUS_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"


def _normalize_uuid(u: str) -> str:
    return u.strip().lower()


class AxisType(Enum):
    ALTITUDE = "Altitude"
    AZIMUTH = "Azimuth"


@dataclass
class DiscoveredDevice:
    name: str
    address: str
    rssi: Optional[int]
    advertises_nus: bool


@dataclass
class TelescopePosition:
    """Current telescope position in degrees"""
    altitude: float = 0.0  # 0-90 degrees (elevation)
    azimuth: float = 0.0   # 0-360 degrees
    
    def to_lx200_alt(self) -> str:
        """Convert altitude to LX200 format: sDD*MM:SS"""
        sign = "+" if self.altitude >= 0 else "-"
        abs_alt = abs(self.altitude)
        deg = int(abs_alt)
        min_frac = (abs_alt - deg) * 60
        minutes = int(min_frac)
        seconds = int((min_frac - minutes) * 60)
        return f"{sign}{deg:02d}*{minutes:02d}:{seconds:02d}"
    
    def to_lx200_az(self) -> str:
        """Convert azimuth to LX200 format: DDD*MM:SS"""
        deg = int(self.azimuth)
        min_frac = (self.azimuth - deg) * 60
        minutes = int(min_frac)
        seconds = int((min_frac - minutes) * 60)
        return f"{deg:03d}*{minutes:02d}:{seconds:02d}"
    
    @staticmethod
    def parse_lx200_angle(text: str) -> Optional[float]:
        """Parse LX200 format angle: sDD*MM:SS or DDD*MM:SS"""
        # Remove # if present
        text = text.strip().replace('#', '')
        
        # Match sDD*MM:SS or DDD*MM:SS
        m = re.match(r'([+-])?(\d+)\*(\d+):(\d+)', text)
        if not m:
            return None
        
        sign = -1 if m.group(1) == '-' else 1
        deg = int(m.group(2))
        minutes = int(m.group(3))
        seconds = int(m.group(4))
        
        return sign * (deg + minutes / 60.0 + seconds / 3600.0)


class PIDController:
    """PID controller for smooth position tracking"""
    
    def __init__(self, kp: float = 1.0, ki: float = 0.1, kd: float = 0.05):
        self.kp = kp  # Proportional gain
        self.ki = ki  # Integral gain
        self.kd = kd  # Derivative gain
        
        self._integral = 0.0
        self._prev_error = 0.0
        self._prev_time = time.time()
    
    def reset(self) -> None:
        """Reset controller state"""
        self._integral = 0.0
        self._prev_error = 0.0
        self._prev_time = time.time()
    
    def update(self, current: float, target: float) -> float:
        """
        Calculate control output
        Returns value in range -1.0 to 1.0 (motor direction/speed)
        """
        current_time = time.time()
        dt = current_time - self._prev_time
        if dt <= 0:
            dt = 0.01
        
        # Calculate error
        error = target - current
        
        # Proportional term
        p_term = self.kp * error
        
        # Integral term (with anti-windup)
        self._integral += error * dt
        # Clamp integral to prevent windup
        self._integral = max(-10.0, min(10.0, self._integral))
        i_term = self.ki * self._integral
        
        # Derivative term
        d_term = self.kd * (error - self._prev_error) / dt
        
        # Calculate output
        output = p_term + i_term + d_term
        
        # Clamp output to -1.0 to 1.0
        output = max(-1.0, min(1.0, output))
        
        # Update state
        self._prev_error = error
        self._prev_time = current_time
        
        return output


class BLEAxisController:
    """Manages a single BLE encoder device for one axis"""
    
    def __init__(self, axis_type: AxisType, log_callback: Callable[[str], None]):
        self.axis_type = axis_type
        self._log = log_callback
        self._client: Optional[BleakClient] = None
        self._rx_uuid = _normalize_uuid(NUS_RX_UUID)
        self._tx_uuid = _normalize_uuid(NUS_TX_UUID)
        
        # Encoder state
        self._encoder_position: int = 0
        self._encoder_re = re.compile(r"\bENC\s+pos\s*=\s*(-?\d+)\b")
        self._incoming_buf: str = ""
        
        # Calibration
        self._counts_per_degree: float = 100.0  # Default, will be calibrated
        self._zero_offset: int = 0  # Encoder position at zero degrees
        self._current_angle: float = 0.0  # Current angle in degrees
        
        # Motor control
        self._target_angle: Optional[float] = None
        self._is_slewing = False
        self._pid = PIDController(kp=2.0, ki=0.1, kd=0.3)
        self._slew_task: Optional[asyncio.Task] = None
        self._position_tolerance: float = 0.1  # degrees
        
    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected
    
    @property
    def encoder_position(self) -> int:
        return self._encoder_position
    
    @property
    def current_angle(self) -> float:
        """Current angle in degrees"""
        return self._current_angle
    
    @property
    def is_slewing(self) -> bool:
        """Check if currently slewing to target"""
        return self._is_slewing
    
    @property
    def target_angle(self) -> Optional[float]:
        """Current target angle"""
        return self._target_angle
    
    def set_calibration(self, counts_per_degree: float, zero_offset: int = None) -> None:
        """Set calibration parameters"""
        self._counts_per_degree = counts_per_degree
        if zero_offset is not None:
            self._zero_offset = zero_offset
        self._update_angle()
    
    def set_current_position(self, angle_degrees: float) -> None:
        """Set the current encoder position as a specific angle"""
        self._zero_offset = self._encoder_position - int(angle_degrees * self._counts_per_degree)
        self._update_angle()
        self._log(f"[{self.axis_type.value}] Set position to {angle_degrees:.2f}°\n")
    
    def _update_angle(self) -> None:
        """Calculate angle from encoder position"""
        counts_from_zero = self._encoder_position - self._zero_offset
        self._current_angle = counts_from_zero / self._counts_per_degree
    
    async def connect(self, address: str) -> None:
        """Connect to BLE device"""
        await self.disconnect()
        
        self._log(f"[{self.axis_type.value}] Connecting to {address}...\n")
        
        resolved = None
        try:
            resolved = await BleakScanner.find_device_by_address(address, timeout=8.0)
        except Exception as e:
            self._log(f"[{self.axis_type.value}] Find device error: {e}\n")
        
        if resolved is None:
            raise RuntimeError(f"Device {address} not found")
        
        def on_disconnected(_client: BleakClient) -> None:
            self._log(f"[{self.axis_type.value}] Disconnected\n")
        
        def on_notify(_, data: bytearray) -> None:
            text = bytes(data).decode("utf-8", errors="replace")
            self._handle_incoming_text(text)
        
        client = BleakClient(resolved, disconnected_callback=on_disconnected)
        
        # Retry connection
        for attempt in range(1, 4):
            try:
                ok = await client.connect(timeout=20.0)
                if ok and client.is_connected:
                    break
            except Exception as e:
                self._log(f"[{self.axis_type.value}] Connect attempt {attempt} failed\n")
            await asyncio.sleep(0.6)
        
        if not client.is_connected:
            raise RuntimeError(f"Failed to connect to {self.axis_type.value} device")
        
        await client.start_notify(self._tx_uuid, on_notify)
        self._client = client
        self._log(f"[{self.axis_type.value}] Connected\n")
    
    async def disconnect(self) -> None:
        """Disconnect from BLE device"""
        await self.stop_slewing()
        
        if self._client is None:
            return
        
        client = self._client
        self._client = None
        
        try:
            if client.is_connected:
                try:
                    await client.stop_notify(self._tx_uuid)
                except Exception:
                    pass
                await client.disconnect()
        except Exception as e:
            self._log(f"[{self.axis_type.value}] Disconnect error: {e}\n")
    
    async def send_command(self, cmd: str) -> None:
        """Send command to device"""
        if not self.is_connected:
            raise RuntimeError(f"{self.axis_type.value} not connected")
        payload = (cmd + "\n").encode("utf-8")
        await self._client.write_gatt_char(self._rx_uuid, payload, response=False)
    
    def _handle_incoming_text(self, text: str) -> None:
        """Handle incoming data from device"""
        self._incoming_buf += text
        while "\n" in self._incoming_buf:
            line, self._incoming_buf = self._incoming_buf.split("\n", 1)
            line = line.rstrip("\r")
            self._parse_encoder_line(line)
    
    def _parse_encoder_line(self, line: str) -> None:
        """Parse encoder position from line"""
        m = self._encoder_re.search(line)
        if m:
            try:
                pos = int(m.group(1))
                self._encoder_position = pos
                self._update_angle()
            except Exception:
                pass
    
    async def slew_to_angle(self, target_degrees: float) -> None:
        """Start slewing to target angle"""
        if not self.is_connected:
            raise RuntimeError(f"{self.axis_type.value} not connected")
        
        # Stop any current slew
        await self.stop_slewing()
        
        self._target_angle = target_degrees
        self._is_slewing = True
        self._pid.reset()
        
        self._log(f"[{self.axis_type.value}] Slewing to {target_degrees:.2f}°\n")
        
        # Start slew control loop
        self._slew_task = asyncio.create_task(self._slew_control_loop())
    
    async def stop_slewing(self) -> None:
        """Stop any active slewing"""
        self._is_slewing = False
        
        if self._slew_task:
            self._slew_task.cancel()
            try:
                await self._slew_task
            except asyncio.CancelledError:
                pass
            self._slew_task = None
        
        # Stop motor
        if self.is_connected:
            try:
                await self.send_command("MOTOR S")
            except Exception as e:
                self._log(f"[{self.axis_type.value}] Error stopping motor: {e}\n")
    
    async def _slew_control_loop(self) -> None:
        """Control loop for slewing to target position"""
        try:
            while self._is_slewing and self._target_angle is not None:
                # Calculate error
                error = self._target_angle - self._current_angle
                
                # Check if we've reached target
                if abs(error) < self._position_tolerance:
                    self._log(f"[{self.axis_type.value}] Target reached: {self._current_angle:.2f}°\n")
                    await self.stop_slewing()
                    break
                
                # Get PID output (-1.0 to 1.0)
                control = self._pid.update(self._current_angle, self._target_angle)
                
                # Convert to motor command
                await self._send_motor_command(control)
                
                # Update at ~10Hz
                await asyncio.sleep(0.1)
            
            # Ensure motor stops at end
            if self.is_connected:
                await self.send_command("MOTOR S")
        
        except asyncio.CancelledError:
            # Stop motor on cancel
            if self.is_connected:
                try:
                    await self.send_command("MOTOR S")
                except Exception:
                    pass
            raise
        
        except Exception as e:
            self._log(f"[{self.axis_type.value}] Slew error: {e}\n")
            self._is_slewing = False
            if self.is_connected:
                try:
                    await self.send_command("MOTOR S")
                except Exception:
                    pass
    
    async def _send_motor_command(self, control: float) -> None:
        """
        Send motor command based on control value (-1.0 to 1.0)
        Positive = forward, Negative = backward
        """
        # Deadband to prevent oscillation
        if abs(control) < 0.05:
            await self.send_command("MOTOR S")
            return
        
        # Determine direction
        direction = "F" if control > 0 else "B"
        
        # Calculate duty cycle (0-100%)
        # Use non-linear mapping for better control at low speeds
        abs_control = abs(control)
        if abs_control < 0.2:
            duty = int(abs_control * 150)  # 0-30% for fine control
        else:
            duty = int(30 + (abs_control - 0.2) * 87.5)  # 30-100% for larger movements
        
        duty = max(0, min(100, duty))
        
        # Send command: MOTOR <dir> <duty>
        # For timed operation, we control in the loop
        cmd = f"M {direction} {duty}"
        await self.send_command(cmd)
    
    async def jog(self, angle_delta: float) -> None:
        """
        Jog the axis by a relative amount
        Positive = increase angle, Negative = decrease angle
        """
        if not self.is_connected:
            raise RuntimeError(f"{self.axis_type.value} not connected")
        
        target = self._current_angle + angle_delta
        self._log(f"[{self.axis_type.value}] Jog {angle_delta:+.2f}° to {target:.2f}°\n")
        await self.slew_to_angle(target)


class LX200Server:
    """LX200 Protocol Server - supports both TCP and Virtual Serial Port"""
    
    def __init__(self, position_callback: Callable[[], TelescopePosition], 
                 log_callback: Callable[[str], None],
                 slew_callback: Callable[[float, float], None]):
        self._get_position = position_callback
        self._log = log_callback
        self._slew_callback = slew_callback
        
        # TCP server
        self._tcp_server: Optional[asyncio.Server] = None
        self._tcp_clients: list[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = []
        self._tcp_port = 4030
        
        # Virtual serial port (PTY)
        self._pty_master: Optional[int] = None
        self._pty_slave: Optional[int] = None
        self._pty_name: Optional[str] = None
        self._pty_task: Optional[asyncio.Task] = None
        
        # Target position for slewing
        self._target_ra: Optional[float] = None  # Actually azimuth for alt-az
        self._target_dec: Optional[float] = None  # Actually altitude for alt-az
        
        self._running = False
        
    async def start_tcp(self, port: int = 4030) -> None:
        """Start LX200 TCP server"""
        self._tcp_port = port
        self._tcp_server = await asyncio.start_server(
            self._handle_tcp_client, 
            '0.0.0.0', 
            port
        )
        self._running = True
        self._log(f"[LX200] TCP server started on port {port}\n")
        self._log(f"[LX200] Connect with: localhost:{port}\n")
    
    async def start_serial(self) -> str:
        """
        Start LX200 virtual serial port (PTY)
        Returns the slave device path that clients should connect to
        """
        try:
            # Create a pseudo-terminal pair
            self._pty_master, self._pty_slave = pty.openpty()
            
            # Get the slave device name
            self._pty_name = os.ttyname(self._pty_slave)
            
            # Set raw mode on the PTY
            attrs = termios.tcgetattr(self._pty_master)
            attrs[3] = attrs[3] & ~termios.ECHO  # Disable echo
            termios.tcsetattr(self._pty_master, termios.TCSANOW, attrs)
            
            # Make the slave non-blocking
            os.set_blocking(self._pty_master, False)
            
            self._running = True
            self._pty_task = asyncio.create_task(self._handle_pty())
            
            self._log(f"[LX200] Virtual serial port created: {self._pty_name}\n")
            self._log(f"[LX200] Connect your astronomy software to: {self._pty_name}\n")
            
            return self._pty_name
            
        except Exception as e:
            self._log(f"[LX200] Failed to create virtual serial port: {e}\n")
            raise
    
    async def stop(self) -> None:
        """Stop LX200 server (both TCP and Serial)"""
        self._running = False
        
        # Stop TCP server
        if self._tcp_server:
            self._tcp_server.close()
            await self._tcp_server.wait_closed()
            self._log("[LX200] TCP server stopped\n")
        
        for reader, writer in self._tcp_clients:
            writer.close()
            await writer.wait_closed()
        self._tcp_clients.clear()
        
        # Stop PTY server
        if self._pty_task:
            self._pty_task.cancel()
            try:
                await self._pty_task
            except asyncio.CancelledError:
                pass
            self._pty_task = None
        
        if self._pty_master is not None:
            try:
                os.close(self._pty_master)
            except Exception:
                pass
            self._pty_master = None
        
        if self._pty_slave is not None:
            try:
                os.close(self._pty_slave)
            except Exception:
                pass
            self._pty_slave = None
            
        if self._pty_name:
            self._log(f"[LX200] Virtual serial port closed: {self._pty_name}\n")
            self._pty_name = None
    
    @property
    def is_running(self) -> bool:
        """Check if server is running"""
        return self._running
    
    async def _handle_tcp_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Handle LX200 TCP client connection"""
        addr = writer.get_extra_info('peername')
        self._log(f"[LX200] TCP client connected: {addr}\n")
        self._tcp_clients.append((reader, writer))
        
        try:
            buffer = ""
            while self._running:
                data = await reader.read(100)
                if not data:
                    break
                
                text = data.decode('ascii', errors='ignore')
                buffer += text
                
                # Process commands
                while '#' in buffer:
                    idx = buffer.index('#')
                    cmd = buffer[:idx+1]
                    buffer = buffer[idx+1:]
                    
                    response = self._process_lx200_command(cmd)
                    if response:
                        writer.write(response.encode('ascii'))
                        await writer.drain()
        
        except Exception as e:
            self._log(f"[LX200] TCP client error: {e}\n")
        finally:
            self._log(f"[LX200] TCP client disconnected: {addr}\n")
            writer.close()
            await writer.wait_closed()
            if (reader, writer) in self._tcp_clients:
                self._tcp_clients.remove((reader, writer))
    
    async def _handle_pty(self) -> None:
        """Handle LX200 PTY (virtual serial port) communication"""
        buffer = ""
        
        try:
            while self._running and self._pty_master is not None:
                try:
                    # Read available data (non-blocking)
                    data = os.read(self._pty_master, 1024)
                    if data:
                        text = data.decode('ascii', errors='ignore')
                        buffer += text
                        
                        # Process commands
                        while '#' in buffer:
                            idx = buffer.index('#')
                            cmd = buffer[:idx+1]
                            buffer = buffer[idx+1:]
                            
                            response = self._process_lx200_command(cmd)
                            if response:
                                os.write(self._pty_master, response.encode('ascii'))
                
                except BlockingIOError:
                    # No data available, this is normal for non-blocking I/O
                    pass
                except OSError as e:
                    if e.errno == 5:  # Input/output error (client disconnected)
                        self._log("[LX200] Client disconnected from virtual serial port\n")
                        await asyncio.sleep(0.5)  # Wait before trying again
                    else:
                        raise
                
                # Small delay to prevent busy loop
                await asyncio.sleep(0.01)
        
        except Exception as e:
            self._log(f"[LX200] Virtual serial port error: {e}\n")
        finally:
            self._log("[LX200] Virtual serial port handler stopped\n")
    
    def _process_lx200_command(self, cmd: str) -> Optional[str]:
        """Process LX200 command and return response"""
        # Skip logging empty or just # commands
        if cmd.strip() and cmd.strip() != '#':
            self._log(f"[LX200] Cmd: {cmd.strip()}")
        
        # Get current position
        pos = self._get_position()
        
        # Handle ACK (06h) character - some software sends this
        if cmd == '\x06' or cmd == '\x06#':
            return "P"  # Respond with alignment mode (Polar)
        
        # LX200 commands (simplified subset)
        if cmd == ":GR#":  # Get RA
            # For Alt-Az mount, return azimuth as RA
            response = pos.to_lx200_az() + "#"
            self._log(f" → {response}\n")
            return response
        
        elif cmd == ":GD#":  # Get DEC
            # For Alt-Az mount, return altitude as DEC
            response = pos.to_lx200_alt() + "#"
            self._log(f" → {response}\n")
            return response
        
        elif cmd == ":GA#":  # Get telescope altitude
            response = pos.to_lx200_alt() + "#"
            self._log(f" → {response}\n")
            return response
        
        elif cmd == ":GZ#":  # Get telescope azimuth
            response = pos.to_lx200_az() + "#"
            self._log(f" → {response}\n")
            return response
        
        elif cmd.startswith(":Sr"):  # Set target RA (azimuth)
            # Extract angle from :SrHH:MM:SS# format
            angle_str = cmd[3:].replace('#', '')
            angle = TelescopePosition.parse_lx200_angle(angle_str)
            if angle is not None:
                self._target_ra = angle
                self._log(f" Target Az={angle:.2f}° → 1\n")
                return "1"
            self._log(f" (invalid) → 0\n")
            return "0"
        
        elif cmd.startswith(":Sd"):  # Set target DEC (altitude)
            # Extract angle from :SdsDD*MM:SS# format
            angle_str = cmd[3:].replace('#', '')
            angle = TelescopePosition.parse_lx200_angle(angle_str)
            if angle is not None:
                self._target_dec = angle
                self._log(f" Target Alt={angle:.2f}° → 1\n")
                return "1"
            self._log(f" (invalid) → 0\n")
            return "0"
        
        elif cmd == ":MS#":  # Slew to target
            if self._target_ra is not None and self._target_dec is not None:
                self._log(f" Slew to Az={self._target_ra:.2f}° Alt={self._target_dec:.2f}° → 0\n")
                # Call the slew callback (will be handled by main window)
                asyncio.create_task(self._execute_slew())
                return "0"  # Slew is possible
            self._log(f" No target → 1\n")
            return "1"  # Object below horizon (no target set)
        
        elif cmd == ":Q#":  # Halt all slewing
            self._log(" Halt all → \n")
            asyncio.create_task(self._halt_slew())
            return ""
        
        elif cmd in (":Qe#", ":Qw#", ":Qn#", ":Qs#"):  # Halt specific direction
            self._log(f" Halt {cmd[2]} → \n")
            asyncio.create_task(self._halt_slew())
            return ""
        
        elif cmd == ":RS#":  # Resume slewing (not typically used)
            self._log(" Resume → \n")
            return ""
        
        elif cmd == ":GVP#":  # Get product name
            response = "Mesh-Blinker#"
            self._log(f" → {response}\n")
            return response
        
        elif cmd == ":GVN#":  # Get product number
            response = "v1.0#"
            self._log(f" → {response}\n")
            return response
        
        elif cmd == ":GVF#":  # Get firmware version
            response = "Mar 2026#"
            self._log(f" → {response}\n")
            return response
        
        elif cmd == ":GVD#":  # Get firmware date
            response = "Mar 24 2026#"
            self._log(f" → {response}\n")
            return response
        
        elif cmd == ":GVT#":  # Get firmware time
            response = "12:00:00#"
            self._log(f" → {response}\n")
            return response
        
        elif cmd == ":D#":  # Distance bars (alignment)
            self._log(" Distance bars → \n")
            return ""
        
        elif cmd == ":CM#":  # Sync (set current position as target)
            self._log(" Sync → \n")
            return "Coordinates matched.        #"
        
        elif cmd == ":U#":  # Toggle precision
            self._log(" Toggle precision → \n")
            return ""
        
        elif cmd == "#" or cmd.strip() == "":  # Empty or just delimiter
            return ""
        
        else:
            if cmd.strip():
                self._log(f" (unknown) → \n")
            return ""  # Unknown command, return empty
    
    async def _execute_slew(self) -> None:
        """Execute slew to target position"""
        if self._target_ra is not None and self._target_dec is not None:
            try:
                await self._slew_callback(self._target_dec, self._target_ra)
            except Exception as e:
                self._log(f"[LX200] Slew error: {e}\n")
    
    async def _halt_slew(self) -> None:
        """Halt any active slewing"""
        try:
            # Forward halt request to main window via callback
            await self._slew_callback(None, None)
        except Exception as e:
            self._log(f"[LX200] Halt error: {e}\n")


class MainWindow(QtWidgets.QMainWindow):
    """Main application window"""
    
    def __init__(self) -> None:
        super().__init__()
        
        # Controllers
        self._alt_controller = BLEAxisController(AxisType.ALTITUDE, self.append_log)
        self._az_controller = BLEAxisController(AxisType.AZIMUTH, self.append_log)
        self._lx200_server = LX200Server(
            self._get_telescope_position, 
            self.append_log,
            self._slew_to_position
        )
        
        # Device discovery
        self._devices: list[DiscoveredDevice] = []
        self._busy = False
        
        # Jog settings
        self._jog_amount = 1.0  # degrees
        
        self._setup_logging()
        self._setup_ui()
        
        # Auto-scan on startup
        QtCore.QTimer.singleShot(500, self.on_scan)
    
    def _setup_logging(self) -> None:
        """Setup logging handlers"""
        class _GuiLogHandler(logging.Handler):
            def __init__(self, emit_fn) -> None:
                super().__init__()
                self._emit_fn = emit_fn
            
            def emit(self, record: logging.LogRecord) -> None:
                try:
                    msg = self.format(record)
                except Exception:
                    msg = record.getMessage()
                QtCore.QTimer.singleShot(0, lambda m=msg: self._emit_fn(m + "\n"))
        
        root_logger = logging.getLogger()
        if not any(isinstance(h, _GuiLogHandler) for h in root_logger.handlers):
            h = _GuiLogHandler(self.append_log)
            h.setLevel(logging.INFO)
            h.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
            root_logger.addHandler(h)
            root_logger.setLevel(logging.INFO)
    
    def _setup_ui(self) -> None:
        """Setup user interface"""
        self.setWindowTitle("LX200 Telescope Controller")
        self.resize(1400, 800)
        
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        main_layout = QtWidgets.QHBoxLayout(central)
        
        # === Left Panel: Controls ===
        left_panel = QtWidgets.QVBoxLayout()
        main_layout.addLayout(left_panel, 2)
        
        # === Left Panel: Controls ===
        left_panel = QtWidgets.QVBoxLayout()
        main_layout.addLayout(left_panel, 2)
        
        # === Top Controls ===
        top_layout = QtWidgets.QHBoxLayout()
        left_panel.addLayout(top_layout)
        
        top_layout.addWidget(QtWidgets.QLabel("Scan (s):"))
        self.scan_timeout = QtWidgets.QDoubleSpinBox()
        self.scan_timeout.setRange(1.0, 30.0)
        self.scan_timeout.setValue(5.0)
        self.scan_timeout.setFixedWidth(80)
        top_layout.addWidget(self.scan_timeout)
        
        self.btn_scan = QtWidgets.QPushButton("Scan BLE Devices")
        self.btn_scan.clicked.connect(self.on_scan)
        top_layout.addWidget(self.btn_scan)
        
        top_layout.addStretch()
        
        self.status_label = QtWidgets.QLabel("Ready")
        top_layout.addWidget(self.status_label)
        
        # === Device Selection ===
        devices_group = QtWidgets.QGroupBox("BLE Device Assignment")
        left_panel.addWidget(devices_group)
        devices_layout = QtWidgets.QHBoxLayout(devices_group)
        
        # Altitude device
        alt_box = QtWidgets.QVBoxLayout()
        devices_layout.addLayout(alt_box, 1)
        alt_box.addWidget(QtWidgets.QLabel("Altitude Device:"))
        self.alt_device_list = QtWidgets.QListWidget()
        self.alt_device_list.setMaximumHeight(150)
        alt_box.addWidget(self.alt_device_list)
        self.btn_connect_alt = QtWidgets.QPushButton("Connect Altitude")
        self.btn_connect_alt.clicked.connect(self.on_connect_altitude)
        alt_box.addWidget(self.btn_connect_alt)
        self.btn_disconnect_alt = QtWidgets.QPushButton("Disconnect")
        self.btn_disconnect_alt.clicked.connect(self.on_disconnect_altitude)
        alt_box.addWidget(self.btn_disconnect_alt)
        
        # Azimuth device
        az_box = QtWidgets.QVBoxLayout()
        devices_layout.addLayout(az_box, 1)
        az_box.addWidget(QtWidgets.QLabel("Azimuth Device:"))
        self.az_device_list = QtWidgets.QListWidget()
        self.az_device_list.setMaximumHeight(150)
        az_box.addWidget(self.az_device_list)
        self.btn_connect_az = QtWidgets.QPushButton("Connect Azimuth")
        self.btn_connect_az.clicked.connect(self.on_connect_azimuth)
        az_box.addWidget(self.btn_connect_az)
        self.btn_disconnect_az = QtWidgets.QPushButton("Disconnect")
        self.btn_disconnect_az.clicked.connect(self.on_disconnect_azimuth)
        az_box.addWidget(self.btn_disconnect_az)
        
        # === Position Display ===
        position_group = QtWidgets.QGroupBox("Current Position")
        left_panel.addWidget(position_group)
        pos_layout = QtWidgets.QGridLayout(position_group)
        
        pos_layout.addWidget(QtWidgets.QLabel("Altitude:"), 0, 0)
        self.alt_value_label = QtWidgets.QLabel("—")
        self.alt_value_label.setStyleSheet("font-size: 18pt; font-weight: bold;")
        pos_layout.addWidget(self.alt_value_label, 0, 1)
        pos_layout.addWidget(QtWidgets.QLabel("°"), 0, 2)
        
        pos_layout.addWidget(QtWidgets.QLabel("Encoder:"), 0, 3)
        self.alt_encoder_label = QtWidgets.QLabel("—")
        pos_layout.addWidget(self.alt_encoder_label, 0, 4)
        
        pos_layout.addWidget(QtWidgets.QLabel("Azimuth:"), 1, 0)
        self.az_value_label = QtWidgets.QLabel("—")
        self.az_value_label.setStyleSheet("font-size: 18pt; font-weight: bold;")
        pos_layout.addWidget(self.az_value_label, 1, 1)
        pos_layout.addWidget(QtWidgets.QLabel("°"), 1, 2)
        
        pos_layout.addWidget(QtWidgets.QLabel("Encoder:"), 1, 3)
        self.az_encoder_label = QtWidgets.QLabel("—")
        pos_layout.addWidget(self.az_encoder_label, 1, 4)
        
        # Slew status
        pos_layout.addWidget(QtWidgets.QLabel("Status:"), 2, 0)
        self.slew_status_label = QtWidgets.QLabel("Idle")
        pos_layout.addWidget(self.slew_status_label, 2, 1, 1, 2)
        
        self.btn_halt = QtWidgets.QPushButton("HALT All")
        self.btn_halt.clicked.connect(self.on_halt_slewing)
        self.btn_halt.setStyleSheet("background-color: #cc0000; color: white; font-weight: bold;")
        pos_layout.addWidget(self.btn_halt, 2, 3, 1, 2)
        
        # === Calibration ===
        calib_group = QtWidgets.QGroupBox("Calibration")
        left_panel.addWidget(calib_group)
        calib_layout = QtWidgets.QGridLayout(calib_group)
        
        # Altitude calibration
        calib_layout.addWidget(QtWidgets.QLabel("Altitude"), 0, 0)
        calib_layout.addWidget(QtWidgets.QLabel("Encoder→Degree Factor:"), 0, 1)
        self.alt_counts_per_deg = QtWidgets.QDoubleSpinBox()
        self.alt_counts_per_deg.setRange(0.01, 100000.0)
        self.alt_counts_per_deg.setValue(100.0)
        self.alt_counts_per_deg.setDecimals(2)
        self.alt_counts_per_deg.setSuffix(" counts/°")
        calib_layout.addWidget(self.alt_counts_per_deg, 0, 2)
        
        calib_layout.addWidget(QtWidgets.QLabel("Set Position:"), 0, 3)
        self.alt_current_angle = QtWidgets.QDoubleSpinBox()
        self.alt_current_angle.setRange(-90.0, 90.0)
        self.alt_current_angle.setValue(0.0)
        self.alt_current_angle.setDecimals(2)
        self.alt_current_angle.setSuffix(" °")
        calib_layout.addWidget(self.alt_current_angle, 0, 4)
        
        self.btn_set_alt_position = QtWidgets.QPushButton("Apply")
        self.btn_set_alt_position.clicked.connect(self.on_set_altitude_position)
        calib_layout.addWidget(self.btn_set_alt_position, 0, 5)
        
        # Azimuth calibration
        calib_layout.addWidget(QtWidgets.QLabel("Azimuth"), 1, 0)
        calib_layout.addWidget(QtWidgets.QLabel("Encoder→Degree Factor:"), 1, 1)
        self.az_counts_per_deg = QtWidgets.QDoubleSpinBox()
        self.az_counts_per_deg.setRange(0.01, 100000.0)
        self.az_counts_per_deg.setValue(100.0)
        self.az_counts_per_deg.setDecimals(2)
        self.az_counts_per_deg.setSuffix(" counts/°")
        calib_layout.addWidget(self.az_counts_per_deg, 1, 2)
        
        calib_layout.addWidget(QtWidgets.QLabel("Set Position:"), 1, 3)
        self.az_current_angle = QtWidgets.QDoubleSpinBox()
        self.az_current_angle.setRange(0.0, 360.0)
        self.az_current_angle.setValue(0.0)
        self.az_current_angle.setDecimals(2)
        self.az_current_angle.setSuffix(" °")
        calib_layout.addWidget(self.az_current_angle, 1, 4)
        
        self.btn_set_az_position = QtWidgets.QPushButton("Apply")
        self.btn_set_az_position.clicked.connect(self.on_set_azimuth_position)
        calib_layout.addWidget(self.btn_set_az_position, 1, 5)
        
        # === LX200 Server ===
        lx200_group = QtWidgets.QGroupBox("LX200 Protocol Server")
        left_panel.addWidget(lx200_group)
        lx200_layout = QtWidgets.QVBoxLayout(lx200_group)
        
        # Connection type selector
        type_layout = QtWidgets.QHBoxLayout()
        lx200_layout.addLayout(type_layout)
        
        type_layout.addWidget(QtWidgets.QLabel("Connection Type:"))
        self.lx200_type = QtWidgets.QComboBox()
        self.lx200_type.addItems(["TCP/IP (Network)", "Serial Port"])
        self.lx200_type.currentIndexChanged.connect(self._on_lx200_type_changed)
        type_layout.addWidget(self.lx200_type)
        type_layout.addStretch()
        
        # TCP settings
        self.tcp_widget = QtWidgets.QWidget()
        tcp_layout = QtWidgets.QHBoxLayout(self.tcp_widget)
        tcp_layout.setContentsMargins(0, 0, 0, 0)
        tcp_layout.addWidget(QtWidgets.QLabel("TCP Port:"))
        self.lx200_port = QtWidgets.QSpinBox()
        self.lx200_port.setRange(1024, 65535)
        self.lx200_port.setValue(4030)
        tcp_layout.addWidget(self.lx200_port)
        tcp_layout.addStretch()
        lx200_layout.addWidget(self.tcp_widget)
        
        # Serial settings (virtual port info)
        self.serial_widget = QtWidgets.QWidget()
        serial_layout = QtWidgets.QVBoxLayout(self.serial_widget)
        serial_layout.setContentsMargins(0, 0, 0, 0)
        
        info_label = QtWidgets.QLabel(
            "A virtual serial port will be created.\n"
            "Connect your astronomy software to the displayed port."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #888; font-style: italic;")
        serial_layout.addWidget(info_label)
        
        port_display_layout = QtWidgets.QHBoxLayout()
        serial_layout.addLayout(port_display_layout)
        port_display_layout.addWidget(QtWidgets.QLabel("Virtual Port:"))
        self.lx200_serial_port_display = QtWidgets.QLineEdit()
        self.lx200_serial_port_display.setReadOnly(True)
        self.lx200_serial_port_display.setPlaceholderText("(not created yet)")
        port_display_layout.addWidget(self.lx200_serial_port_display, 1)
        
        lx200_layout.addWidget(self.serial_widget)
        self.serial_widget.hide()
        
        # Control buttons
        btn_layout = QtWidgets.QHBoxLayout()
        lx200_layout.addLayout(btn_layout)
        
        self.btn_start_server = QtWidgets.QPushButton("Start LX200 Server")
        self.btn_start_server.clicked.connect(self.on_start_lx200_server)
        btn_layout.addWidget(self.btn_start_server)
        
        self.btn_stop_server = QtWidgets.QPushButton("Stop Server")
        self.btn_stop_server.clicked.connect(self.on_stop_lx200_server)
        self.btn_stop_server.setEnabled(False)
        btn_layout.addWidget(self.btn_stop_server)
        
        btn_layout.addStretch()
        
        # === Log ===
        log_group = QtWidgets.QGroupBox("Log")
        left_panel.addWidget(log_group, 1)
        log_layout = QtWidgets.QVBoxLayout(log_group)
        self.log_text = QtWidgets.QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumBlockCount(1000)
        log_layout.addWidget(self.log_text)
        
        # === Right Panel: Joystick Control ===
        right_panel = QtWidgets.QVBoxLayout()
        main_layout.addLayout(right_panel, 1)
        
        # === Joystick Control ===
        joystick_group = QtWidgets.QGroupBox("Manual Control (Joystick)")
        right_panel.addWidget(joystick_group)
        joy_main_layout = QtWidgets.QVBoxLayout(joystick_group)
        
        # Movement amount settings
        joy_settings = QtWidgets.QHBoxLayout()
        joy_main_layout.addLayout(joy_settings)
        
        joy_settings.addWidget(QtWidgets.QLabel("Movement Amount:"))
        self.jog_amount = QtWidgets.QDoubleSpinBox()
        self.jog_amount.setRange(0.01, 90.0)
        self.jog_amount.setValue(1.0)
        self.jog_amount.setDecimals(2)
        self.jog_amount.setSuffix(" °")
        self.jog_amount.valueChanged.connect(self._on_jog_amount_changed)
        joy_settings.addWidget(self.jog_amount)
        
        # Quick presets
        joy_settings.addWidget(QtWidgets.QLabel("   Quick:"))
        for preset_val, preset_label in [(0.1, "0.1°"), (0.5, "0.5°"), (1.0, "1°"), (5.0, "5°"), (10.0, "10°")]:
            btn = QtWidgets.QPushButton(preset_label)
            btn.clicked.connect(lambda checked, v=preset_val: self.jog_amount.setValue(v))
            joy_settings.addWidget(btn)
        
        joy_settings.addStretch()
        
        # Joystick control area with centered layout
        joy_control = QtWidgets.QWidget()
        joy_main_layout.addWidget(joy_control, 1)
        joy_layout = QtWidgets.QGridLayout(joy_control)
        joy_layout.setSpacing(10)
        
        # Create telescope icon in center (simple ASCII art label)
        self.telescope_icon = QtWidgets.QLabel("🔭")
        self.telescope_icon.setAlignment(QtCore.Qt.AlignCenter)
        self.telescope_icon.setStyleSheet("""
            font-size: 48pt;
            border: 3px solid #555;
            border-radius: 10px;
            background-color: #2a2a2a;
            min-width: 120px;
            min-height: 120px;
        """)
        joy_layout.addWidget(self.telescope_icon, 2, 2)
        
        # UP button (Altitude increase)
        self.btn_up = QtWidgets.QPushButton("▲\nUP\n(Alt +)")
        self.btn_up.setMinimumSize(100, 80)
        self.btn_up.setStyleSheet(self._get_direction_button_style())
        self.btn_up.clicked.connect(self.on_jog_up)
        joy_layout.addWidget(self.btn_up, 1, 2, QtCore.Qt.AlignCenter)
        
        # DOWN button (Altitude decrease)
        self.btn_down = QtWidgets.QPushButton("▼\nDOWN\n(Alt -)")
        self.btn_down.setMinimumSize(100, 80)
        self.btn_down.setStyleSheet(self._get_direction_button_style())
        self.btn_down.clicked.connect(self.on_jog_down)
        joy_layout.addWidget(self.btn_down, 3, 2, QtCore.Qt.AlignCenter)
        
        # LEFT button (Azimuth decrease)
        self.btn_left = QtWidgets.QPushButton("◄\nLEFT\n(Az -)")
        self.btn_left.setMinimumSize(100, 80)
        self.btn_left.setStyleSheet(self._get_direction_button_style())
        self.btn_left.clicked.connect(self.on_jog_left)
        joy_layout.addWidget(self.btn_left, 2, 1, QtCore.Qt.AlignCenter)
        
        # RIGHT button (Azimuth increase)
        self.btn_right = QtWidgets.QPushButton("►\nRIGHT\n(Az +)")
        self.btn_right.setMinimumSize(100, 80)
        self.btn_right.setStyleSheet(self._get_direction_button_style())
        self.btn_right.clicked.connect(self.on_jog_right)
        joy_layout.addWidget(self.btn_right, 2, 3, QtCore.Qt.AlignCenter)
        
        # Add some spacing
        joy_layout.setRowMinimumHeight(0, 20)
        joy_layout.setRowMinimumHeight(4, 20)
        joy_layout.setColumnMinimumWidth(0, 20)
        joy_layout.setColumnMinimumWidth(4, 20)
        
        # HALT button below joystick
        halt_layout = QtWidgets.QHBoxLayout()
        joy_main_layout.addLayout(halt_layout)
        
        self.btn_halt = QtWidgets.QPushButton("⏹  HALT ALL MOTION  ⏹")
        self.btn_halt.clicked.connect(self.on_halt_slewing)
        self.btn_halt.setStyleSheet("""
            background-color: #cc0000;
            color: white;
            font-weight: bold;
            font-size: 14pt;
            padding: 15px;
            border-radius: 5px;
        """)
        self.btn_halt.setMinimumHeight(60)
        halt_layout.addWidget(self.btn_halt)
        
        # === Goto Control ===
        goto_group = QtWidgets.QGroupBox("Goto Position")
        right_panel.addWidget(goto_group)
        goto_layout = QtWidgets.QGridLayout(goto_group)
        
        goto_layout.addWidget(QtWidgets.QLabel("Target Altitude:"), 0, 0)
        self.test_alt = QtWidgets.QDoubleSpinBox()
        self.test_alt.setRange(-90, 90)
        self.test_alt.setValue(45.0)
        self.test_alt.setDecimals(2)
        self.test_alt.setSuffix(" °")
        goto_layout.addWidget(self.test_alt, 0, 1)
        
        goto_layout.addWidget(QtWidgets.QLabel("Target Azimuth:"), 1, 0)
        self.test_az = QtWidgets.QDoubleSpinBox()
        self.test_az.setRange(0, 360)
        self.test_az.setValue(180.0)
        self.test_az.setDecimals(2)
        self.test_az.setSuffix(" °")
        goto_layout.addWidget(self.test_az, 1, 1)
        
        self.btn_test_slew = QtWidgets.QPushButton("Slew to Position")
        self.btn_test_slew.clicked.connect(self.on_test_slew)
        self.btn_test_slew.setStyleSheet("padding: 10px; font-weight: bold;")
        goto_layout.addWidget(self.btn_test_slew, 2, 0, 1, 2)
        
        right_panel.addStretch()
        
        # Start position update timer
        self._position_timer = QtCore.QTimer()
        self._position_timer.timeout.connect(self._update_position_display)
        self._position_timer.start(100)  # Update every 100ms
        
        # Update button states
        self._update_button_states()
    
    def _get_direction_button_style(self) -> str:
        """Get stylesheet for direction buttons"""
        return """
            QPushButton {
                background-color: #4a4a4a;
                color: white;
                font-size: 12pt;
                font-weight: bold;
                border: 2px solid #666;
                border-radius: 8px;
                padding: 10px;
            }
            QPushButton:hover {
                background-color: #5a5a5a;
                border: 2px solid #888;
            }
            QPushButton:pressed {
                background-color: #3a3a3a;
                border: 2px solid #444;
            }
            QPushButton:disabled {
                background-color: #2a2a2a;
                color: #666;
                border: 2px solid #444;
            }
        """
    
    def _on_jog_amount_changed(self, value: float) -> None:
        """Handle jog amount change"""
        self._jog_amount = value
    
    def _on_lx200_type_changed(self, index: int) -> None:
        """Handle LX200 connection type change"""
        if index == 0:  # TCP
            self.tcp_widget.show()
            self.serial_widget.hide()
        else:  # Virtual Serial
            self.tcp_widget.hide()
            self.serial_widget.show()
    
    def closeEvent(self, event) -> None:
        """Handle window close"""
        asyncio.create_task(self._cleanup())
        super().closeEvent(event)
    
    async def _cleanup(self) -> None:
        """Clean up connections"""
        await self._lx200_server.stop()
        await self._alt_controller.disconnect()
        await self._az_controller.disconnect()
    
    def append_log(self, text: str) -> None:
        """Append text to log"""
        self.log_text.moveCursor(QtGui.QTextCursor.End)
        self.log_text.insertPlainText(text)
        self.log_text.moveCursor(QtGui.QTextCursor.End)
    
    def _get_telescope_position(self) -> TelescopePosition:
        """Get current telescope position"""
        return TelescopePosition(
            altitude=self._alt_controller.current_angle,
            azimuth=self._az_controller.current_angle % 360.0
        )
    
    def _update_position_display(self) -> None:
        """Update position display"""
        # Altitude
        if self._alt_controller.is_connected:
            self.alt_value_label.setText(f"{self._alt_controller.current_angle:.2f}")
            self.alt_encoder_label.setText(str(self._alt_controller.encoder_position))
        else:
            self.alt_value_label.setText("—")
            self.alt_encoder_label.setText("—")
        
        # Azimuth
        if self._az_controller.is_connected:
            az_angle = self._az_controller.current_angle % 360.0
            self.az_value_label.setText(f"{az_angle:.2f}")
            self.az_encoder_label.setText(str(self._az_controller.encoder_position))
        else:
            self.az_value_label.setText("—")
            self.az_encoder_label.setText("—")
        
        # Slew status
        status_parts = []
        if self._alt_controller.is_slewing:
            target = self._alt_controller.target_angle
            status_parts.append(f"Alt→{target:.1f}°")
        if self._az_controller.is_slewing:
            target = self._az_controller.target_angle
            status_parts.append(f"Az→{target:.1f}°")
        
        if status_parts:
            self.slew_status_label.setText(f"Slewing: {', '.join(status_parts)}")
            self.slew_status_label.setStyleSheet("color: #00aa00; font-weight: bold;")
        else:
            self.slew_status_label.setText("Idle")
            self.slew_status_label.setStyleSheet("")
    
    def _update_button_states(self) -> None:
        """Update button enabled states"""
        alt_connected = self._alt_controller.is_connected
        az_connected = self._az_controller.is_connected
        both_connected = alt_connected and az_connected
        
        self.btn_test_slew.setEnabled(both_connected)
        self.btn_halt.setEnabled(both_connected)
        
        # Joystick buttons
        self.btn_up.setEnabled(alt_connected)
        self.btn_down.setEnabled(alt_connected)
        self.btn_left.setEnabled(az_connected)
        self.btn_right.setEnabled(az_connected)
    
    @asyncSlot()
    async def on_scan(self) -> None:
        """Scan for BLE devices"""
        if self._busy:
            return
        
        self._busy = True
        self.status_label.setText("Scanning...")
        self.btn_scan.setEnabled(False)
        
        try:
            timeout = self.scan_timeout.value()
            devices = await self._scan_devices(timeout, "MotorSense")
            
            self._devices = devices
            self._populate_device_lists()
            
            self.status_label.setText(f"Found {len(devices)} device(s)")
            self.append_log(f"[Scan] Found {len(devices)} device(s)\n")
        
        except Exception as e:
            self.status_label.setText(f"Scan error: {e}")
            self.append_log(f"[Scan] Error: {e}\n")
        
        finally:
            self._busy = False
            self.btn_scan.setEnabled(True)
    
    async def _scan_devices(self, timeout_s: float, name_filter: str = "") -> list[DiscoveredDevice]:
        """Scan for BLE devices"""
        devices = await BleakScanner.discover(timeout=timeout_s)
        
        needle = name_filter.strip().lower()
        result = []
        
        for d in devices:
            name = d.name or "(unknown)"
            if needle and needle not in name.lower():
                continue
            
            md = getattr(d, "metadata", None) or {}
            uuids = []
            if isinstance(md, dict):
                uuids = md.get("uuids") or []
            
            advertises_nus = any(_normalize_uuid(u) == NUS_SERVICE_UUID for u in uuids)
            rssi = getattr(d, "rssi", None)
            
            result.append(DiscoveredDevice(
                name=name,
                address=d.address,
                rssi=rssi,
                advertises_nus=advertises_nus
            ))
        
        result.sort(key=lambda x: (not x.advertises_nus, x.name.lower(), x.address))
        return result
    
    def _populate_device_lists(self) -> None:
        """Populate device list widgets"""
        self.alt_device_list.clear()
        self.az_device_list.clear()
        
        for dev in self._devices:
            tag = " [NUS]" if dev.advertises_nus else ""
            rssi = f" ({dev.rssi})" if dev.rssi else ""
            text = f"{dev.name} - {dev.address}{rssi}{tag}"
            
            self.alt_device_list.addItem(text)
            self.az_device_list.addItem(text)
    
    @asyncSlot()
    async def on_connect_altitude(self) -> None:
        """Connect to altitude device"""
        selected = self.alt_device_list.currentRow()
        if selected < 0 or selected >= len(self._devices):
            self.append_log("[Altitude] No device selected\n")
            return
        
        device = self._devices[selected]
        
        try:
            await self._alt_controller.connect(device.address)
            self._alt_controller.set_calibration(self.alt_counts_per_deg.value())
            self.status_label.setText("Altitude connected")
            self._update_button_states()
        except Exception as e:
            self.status_label.setText(f"Alt connect error: {e}")
            self.append_log(f"[Altitude] Connection error: {e}\n")
    
    @asyncSlot()
    async def on_disconnect_altitude(self) -> None:
        """Disconnect altitude device"""
        await self._alt_controller.disconnect()
        self.status_label.setText("Altitude disconnected")
        self._update_button_states()
    
    @asyncSlot()
    async def on_connect_azimuth(self) -> None:
        """Connect to azimuth device"""
        selected = self.az_device_list.currentRow()
        if selected < 0 or selected >= len(self._devices):
            self.append_log("[Azimuth] No device selected\n")
            return
        
        device = self._devices[selected]
        
        try:
            await self._az_controller.connect(device.address)
            self._az_controller.set_calibration(self.az_counts_per_deg.value())
            self.status_label.setText("Azimuth connected")
            self._update_button_states()
        except Exception as e:
            self.status_label.setText(f"Az connect error: {e}")
            self.append_log(f"[Azimuth] Connection error: {e}\n")
    
    @asyncSlot()
    async def on_disconnect_azimuth(self) -> None:
        """Disconnect azimuth device"""
        await self._az_controller.disconnect()
        self.status_label.setText("Azimuth disconnected")
        self._update_button_states()
    
    def on_set_altitude_position(self) -> None:
        """Set current altitude position"""
        angle = self.alt_current_angle.value()
        counts_per_deg = self.alt_counts_per_deg.value()
        
        self._alt_controller.set_calibration(counts_per_deg)
        self._alt_controller.set_current_position(angle)
    
    def on_set_azimuth_position(self) -> None:
        """Set current azimuth position"""
        angle = self.az_current_angle.value()
        counts_per_deg = self.az_counts_per_deg.value()
        
        self._az_controller.set_calibration(counts_per_deg)
        self._az_controller.set_current_position(angle)
    
    @asyncSlot()
    async def on_start_lx200_server(self) -> None:
        """Start LX200 server"""
        try:
            if self.lx200_type.currentIndex() == 0:  # TCP
                port = self.lx200_port.value()
                await self._lx200_server.start_tcp(port)
                self.status_label.setText(f"LX200 TCP server running on port {port}")
            else:  # Virtual Serial
                pty_name = await self._lx200_server.start_serial()
                self.lx200_serial_port_display.setText(pty_name)
                self.status_label.setText(f"LX200 Virtual serial port: {pty_name}")
            
            self.btn_start_server.setEnabled(False)
            self.btn_stop_server.setEnabled(True)
            self.lx200_type.setEnabled(False)
            self.lx200_port.setEnabled(False)
        except Exception as e:
            self.status_label.setText(f"Server start error: {e}")
            self.append_log(f"[LX200] Start error: {e}\n")
    
    @asyncSlot()
    async def on_stop_lx200_server(self) -> None:
        """Stop LX200 server"""
        await self._lx200_server.stop()
        self.btn_start_server.setEnabled(True)
        self.btn_stop_server.setEnabled(False)
        self.lx200_type.setEnabled(True)
        self.lx200_port.setEnabled(True)
        self.lx200_serial_port_display.clear()
        self.status_label.setText("LX200 server stopped")
    
    async def _slew_to_position(self, altitude: float, azimuth: float) -> None:
        """Slew both axes to target position"""
        # Handle halt request (None values)
        if altitude is None or azimuth is None:
            self.append_log("[Slew] Halt requested\n")
            await asyncio.gather(
                self._alt_controller.stop_slewing(),
                self._az_controller.stop_slewing()
            )
            return
        
        if not (self._alt_controller.is_connected and self._az_controller.is_connected):
            self.append_log("[Slew] Both devices must be connected\n")
            return
        
        self.append_log(f"[Slew] Moving to Alt={altitude:.2f}°, Az={azimuth:.2f}°\n")
        
        # Start both axes slewing
        await asyncio.gather(
            self._alt_controller.slew_to_angle(altitude),
            self._az_controller.slew_to_angle(azimuth)
        )
    
    @asyncSlot()
    async def on_test_slew(self) -> None:
        """Test manual slew"""
        altitude = self.test_alt.value()
        azimuth = self.test_az.value()
        
        await self._slew_to_position(altitude, azimuth)
    
    @asyncSlot()
    async def on_halt_slewing(self) -> None:
        """Halt all slewing"""
        self.append_log("[Halt] Stopping all motion\n")
        await asyncio.gather(
            self._alt_controller.stop_slewing(),
            self._az_controller.stop_slewing()
        )
    
    @asyncSlot()
    async def on_jog_up(self) -> None:
        """Jog altitude up"""
        try:
            await self._alt_controller.jog(self._jog_amount)
        except Exception as e:
            self.append_log(f"[Jog] Error: {e}\n")
    
    @asyncSlot()
    async def on_jog_down(self) -> None:
        """Jog altitude down"""
        try:
            await self._alt_controller.jog(-self._jog_amount)
        except Exception as e:
            self.append_log(f"[Jog] Error: {e}\n")
    
    @asyncSlot()
    async def on_jog_left(self) -> None:
        """Jog azimuth left (counter-clockwise)"""
        try:
            await self._az_controller.jog(-self._jog_amount)
        except Exception as e:
            self.append_log(f"[Jog] Error: {e}\n")
    
    @asyncSlot()
    async def on_jog_right(self) -> None:
        """Jog azimuth right (clockwise)"""
        try:
            await self._az_controller.jog(self._jog_amount)
        except Exception as e:
            self.append_log(f"[Jog] Error: {e}\n")


def main():
    """Main entry point"""
    import sys
    
    app = QtWidgets.QApplication(sys.argv)
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)
    
    window = MainWindow()
    window.show()
    
    with loop:
        loop.run_forever()


if __name__ == "__main__":
    main()
