import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

from bleak import BleakClient, BleakScanner
from PySide6 import QtCore, QtGui, QtWidgets
from qasync import QEventLoop, asyncSlot

# Nordic UART Service (NUS) UUIDs in canonical string form.
NUS_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # client writes here
NUS_TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # device notifies here


def _normalize_uuid(u: str) -> str:
    return u.strip().lower()


@dataclass(frozen=True)
class DiscoveredDevice:
    name: str
    address: str
    rssi: Optional[int]
    advertises_nus: bool


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self._client: Optional[BleakClient] = None
        self._rx_uuid = _normalize_uuid(NUS_RX_UUID)
        self._tx_uuid = _normalize_uuid(NUS_TX_UUID)
        self._devices: list[DiscoveredDevice] = []
        self._ble_device_by_address: dict[str, Any] = {}

        # Hidden default filter (per request)
        self._name_filter = "MotorSense"

        self._busy = False

        # Encoder display state
        self._enc_re = re.compile(r"\bENC\s+pos\s*=\s*(-?\d+)\b")
        self._enc_max_abs: int = 10
        self._incoming_buf: str = ""

        self._setup_logging()

        self._history: list[str] = []
        self._hist_idx: int = 0

        self.setWindowTitle("BLE Serial (NUS)")
        self.resize(980, 600)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)

        top = QtWidgets.QHBoxLayout()
        root.addLayout(top)

        top.addWidget(QtWidgets.QLabel("Scan (s):"))
        self.scan_timeout = QtWidgets.QDoubleSpinBox()
        self.scan_timeout.setRange(1.0, 30.0)
        self.scan_timeout.setValue(5.0)
        self.scan_timeout.setSingleStep(1.0)
        self.scan_timeout.setFixedWidth(80)
        top.addWidget(self.scan_timeout)

        top.addSpacing(12)
        self.status = QtWidgets.QLabel("Idle")
        top.addWidget(self.status)
        top.addStretch(1)

        self.btn_scan = QtWidgets.QPushButton("Scan")
        self.btn_disconnect = QtWidgets.QPushButton("Disconnect")
        top.addWidget(self.btn_scan)
        top.addWidget(self.btn_disconnect)

        self.btn_scan.clicked.connect(self.on_scan)
        self.btn_disconnect.clicked.connect(self.on_disconnect)

        mid = QtWidgets.QHBoxLayout()
        root.addLayout(mid, 1)

        devices_box = QtWidgets.QVBoxLayout()
        mid.addLayout(devices_box)
        devices_box.addWidget(QtWidgets.QLabel("Devices"))
        self.devices_list = QtWidgets.QListWidget()
        self.devices_list.setMinimumWidth(380)
        devices_box.addWidget(self.devices_list, 1)

        log_box = QtWidgets.QVBoxLayout()
        mid.addLayout(log_box, 1)
        log_box.addWidget(QtWidgets.QLabel("Log"))
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        log_box.addWidget(self.log, 1)

        enc_box = QtWidgets.QGroupBox("Encoder")
        root.addWidget(enc_box)
        enc = QtWidgets.QHBoxLayout(enc_box)
        self.enc_value = QtWidgets.QLabel("ENC pos: —")
        enc.addWidget(self.enc_value)
        self.enc_range = QtWidgets.QLabel("Range: ±10")
        enc.addWidget(self.enc_range)
        self.enc_bar = QtWidgets.QProgressBar()
        self.enc_bar.setRange(-self._enc_max_abs, self._enc_max_abs)
        self.enc_bar.setValue(0)
        self.enc_bar.setTextVisible(False)
        enc.addWidget(self.enc_bar, 1)

        cmd_row = QtWidgets.QHBoxLayout()
        root.addLayout(cmd_row)
        cmd_row.addWidget(QtWidgets.QLabel("Command:"))
        self.cmd = QtWidgets.QLineEdit()
        cmd_row.addWidget(self.cmd, 1)
        self.btn_send = QtWidgets.QPushButton("Send")
        cmd_row.addWidget(self.btn_send)

        self.btn_send.clicked.connect(self.on_send)
        self.cmd.returnPressed.connect(self.on_send)
        self.cmd.installEventFilter(self)

        presets = QtWidgets.QGroupBox("Presets")
        root.addWidget(presets)
        p = QtWidgets.QVBoxLayout(presets)

        row1 = QtWidgets.QHBoxLayout()
        p.addLayout(row1)

        def add_btn(label: str, cmd: str) -> None:
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(lambda: self.send_preset(cmd))
            row1.addWidget(b)

        add_btn("HELP", "HELP")
        add_btn("PING", "PING")
        add_btn("PING ALL", "PING ALL")
        add_btn("STATUS", "STATUS")
        add_btn("LED DANCE", "LED DANCE")
        row1.addStretch(1)

        row2 = QtWidgets.QHBoxLayout()
        p.addLayout(row2)

        row2.addWidget(QtWidgets.QLabel("LED R G B:"))
        self.led_r = QtWidgets.QSpinBox(); self.led_r.setRange(0, 255)
        self.led_g = QtWidgets.QSpinBox(); self.led_g.setRange(0, 255)
        self.led_b = QtWidgets.QSpinBox(); self.led_b.setRange(0, 255)
        row2.addWidget(self.led_r)
        row2.addWidget(self.led_g)
        row2.addWidget(self.led_b)
        self.btn_led = QtWidgets.QPushButton("Send LED")
        self.btn_led.clicked.connect(self.on_send_led)
        row2.addWidget(self.btn_led)

        row2.addSpacing(16)

        row2.addWidget(QtWidgets.QLabel("MOTOR:"))
        self.motor_dir = QtWidgets.QComboBox()
        self.motor_dir.addItems(["S", "F", "B"])
        self.motor_dir.setCurrentText("S")
        row2.addWidget(self.motor_dir)

        row2.addWidget(QtWidgets.QLabel("Duty%:"))
        self.motor_duty = QtWidgets.QSpinBox()
        self.motor_duty.setRange(0, 100)
        self.motor_duty.setValue(30)
        row2.addWidget(self.motor_duty)

        row2.addWidget(QtWidgets.QLabel("Sec:"))
        self.motor_sec = QtWidgets.QDoubleSpinBox()
        self.motor_sec.setRange(0.0, 60.0)
        self.motor_sec.setValue(1.0)
        self.motor_sec.setSingleStep(0.5)
        row2.addWidget(self.motor_sec)

        self.btn_motor = QtWidgets.QPushButton("Send MOTOR")
        self.btn_m = QtWidgets.QPushButton("Send M")
        self.btn_stop = QtWidgets.QPushButton("STOP")
        self.btn_motor.clicked.connect(self.on_send_motor)
        self.btn_m.clicked.connect(self.on_send_m)
        self.btn_stop.clicked.connect(lambda: self.send_preset("MOTOR S"))
        row2.addWidget(self.btn_motor)
        row2.addWidget(self.btn_m)
        row2.addWidget(self.btn_stop)
        row2.addStretch(1)

        self.set_connected(False)

        # Auto-scan + auto-connect on launch
        QtCore.QTimer.singleShot(0, self.on_auto_connect)

    def _setup_logging(self) -> None:
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

        # Bleak + CoreBluetooth diagnostics (can help explain "Failed to connect").
        logging.getLogger("bleak").setLevel(logging.DEBUG)
        logging.getLogger("bleak.backends").setLevel(logging.DEBUG)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        # Best-effort disconnect; don't block window close.
        asyncio.create_task(self._disconnect())
        super().closeEvent(event)

    def eventFilter(self, obj, event) -> bool:  # type: ignore[override]
        if obj is self.cmd and event.type() == QtCore.QEvent.KeyPress:
            if event.key() == QtCore.Qt.Key_Up:
                self.history_up()
                return True
            if event.key() == QtCore.Qt.Key_Down:
                self.history_down()
                return True
        return super().eventFilter(obj, event)

    def append_log(self, text: str) -> None:
        self.log.moveCursor(QtGui.QTextCursor.End)
        self.log.insertPlainText(text)
        self.log.moveCursor(QtGui.QTextCursor.End)

    def _handle_incoming_text(self, text: str) -> None:
        self.append_log(text)

        # Parse line-oriented output for encoder updates.
        self._incoming_buf += text
        while "\n" in self._incoming_buf:
            line, self._incoming_buf = self._incoming_buf.split("\n", 1)
            line = line.rstrip("\r")
            self._maybe_update_encoder_from_line(line)

    def _maybe_update_encoder_from_line(self, line: str) -> None:
        m = self._enc_re.search(line)
        if not m:
            return
        try:
            pos = int(m.group(1))
        except Exception:
            return
        self._update_encoder(pos)

    def _update_encoder(self, pos: int) -> None:
        self.enc_value.setText(f"ENC pos: {pos}")
        max_abs = max(self._enc_max_abs, abs(pos), 1)
        if max_abs != self._enc_max_abs:
            self._enc_max_abs = max_abs
            self.enc_bar.setRange(-max_abs, max_abs)
            self.enc_range.setText(f"Range: ±{max_abs}")
        self.enc_bar.setValue(pos)

    def _log_error(self, msg: str) -> None:
        self.append_log(f"\n[{msg}]\n")

    def set_connected(self, connected: bool) -> None:
        self.btn_disconnect.setEnabled(connected)
        self.btn_send.setEnabled(connected)
        self.cmd.setEnabled(connected)

        for b in self.findChildren(QtWidgets.QPushButton):
            if b.text() in {"Scan", "Disconnect", "Send"}:
                continue
            b.setEnabled(connected)

        self.status.setText("Connected" if connected else "Not connected")

    def _set_busy(self, busy: bool, status: str) -> None:
        self._busy = busy
        self.status.setText(status)
        self.btn_scan.setEnabled(not busy)
        self.btn_disconnect.setEnabled(not busy and self.is_connected())

    def is_connected(self) -> bool:
        return self._client is not None and bool(self._client.is_connected)

    async def _scan(self, timeout_s: float, name_contains: str) -> list[DiscoveredDevice]:
        devices = await BleakScanner.discover(timeout=timeout_s)

        needle = (name_contains or "").strip().lower()
        out: list[DiscoveredDevice] = []
        by_addr: dict[str, Any] = {}
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
            out.append(DiscoveredDevice(name=name, address=d.address, rssi=rssi, advertises_nus=advertises_nus))
            by_addr[d.address] = d

        out.sort(key=lambda x: (not x.advertises_nus, x.name.lower(), x.address))

        # Save the underlying BLEDevice objects for reliable connections.
        self._ble_device_by_address = by_addr
        return out

    async def _connect(self, address: str) -> None:
        await self._disconnect()

        self.append_log(f"[BLE] connect -> {address}\n")

        # Resolve a fresh device object (more reliable on macOS than reusing stale scan results).
        resolved = None
        try:
            resolved = await BleakScanner.find_device_by_address(address, timeout=8.0)
        except Exception as e:
            self.append_log(f"[BLE] find_device_by_address error: {e}\n")

        if resolved is None:
            # Fall back to last scan result, if we have it.
            resolved = self._ble_device_by_address.get(address)

        if resolved is None:
            raise RuntimeError("Device not resolvable (not found)")

        def on_disconnected(_client: BleakClient) -> None:
            QtCore.QTimer.singleShot(0, lambda: self.append_log("\n[BLE] disconnected\n"))

        def on_notify(_, data: bytearray) -> None:
            text = bytes(data).decode("utf-8", errors="replace")
            # Ensure UI update happens on Qt thread.
            QtCore.QTimer.singleShot(0, lambda t=text: self._handle_incoming_text(t))

        client = BleakClient(resolved, disconnected_callback=on_disconnected)

        last_exc: Optional[BaseException] = None
        for attempt in range(1, 4):
            try:
                ok = await client.connect(timeout=20.0)
                if ok and client.is_connected:
                    break
            except Exception as e:
                last_exc = e
            self.append_log(f"[BLE] connect attempt {attempt} failed; retrying...\n")
            await asyncio.sleep(0.6)

        if not client.is_connected:
            try:
                await client.disconnect()
            except Exception:
                pass
            if last_exc is not None:
                raise RuntimeError(f"Connect exception") from last_exc
            raise RuntimeError("Failed to connect")

        await client.start_notify(self._tx_uuid, on_notify)
        self._client = client

    async def _disconnect(self) -> None:
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
        finally:
            pass

    async def _send_line(self, line: str) -> None:
        if self._client is None or not self._client.is_connected:
            raise RuntimeError("Not connected")
        payload = (line + "\n").encode("utf-8")
        await self._client.write_gatt_char(self._rx_uuid, payload, response=False)

    @asyncSlot()
    async def on_scan(self) -> None:
        if self._busy:
            return
        self._set_busy(True, "Scanning...")
        QtWidgets.QApplication.processEvents()

        try:
            devs = await self._scan(float(self.scan_timeout.value()), self._name_filter)
        except Exception as e:
            self._set_busy(False, f"Scan error: {e}")
            self._log_error(f"Scan error: {e}")
            return

        self._on_scan_done(devs)

    def on_auto_connect(self) -> None:
        # Start a scan and connect automatically to the first match.
        self.on_scan()

    def _on_scan_done(self, devs: list[DiscoveredDevice]) -> None:
        self._devices = devs
        self.devices_list.clear()
        for d in devs:
            nus = " NUS" if d.advertises_nus else ""
            rssi = "" if d.rssi is None else f" RSSI={d.rssi}"
            self.devices_list.addItem(f"{d.name} ({d.address}){rssi}{nus}")

        if not devs:
            self._set_busy(False, "No devices found")
            return

        self.devices_list.setCurrentRow(0)
        self._set_busy(False, f"Found {len(devs)}")

        # Auto-connect to the first match.
        QtCore.QTimer.singleShot(0, self._connect_selected_async)

    def _connect_selected_async(self) -> None:
        asyncio.create_task(self._connect_selected())

    async def _connect_selected(self) -> None:
        if self._busy:
            return
        row = self.devices_list.currentRow()
        if row < 0 or row >= len(self._devices):
            self.status.setText("No selection")
            return

        d = self._devices[row]
        self._set_busy(True, f"Connecting to {d.name}...")
        QtWidgets.QApplication.processEvents()

        try:
            await self._connect(d.address)
        except Exception as e:
            self._set_busy(False, f"Connect error: {e}")
            self._log_error(f"Connect error: {e}")
            self.set_connected(False)
            return

        self._on_connect_done(d)

    def _on_connect_done(self, d: DiscoveredDevice) -> None:
        self._set_busy(False, "Connected")
        self.append_log(f"\n[Connected to {d.name}]\n")
        self.set_connected(True)

    @asyncSlot()
    async def on_disconnect(self) -> None:
        if self._busy:
            return
        self._set_busy(True, "Disconnecting...")
        QtWidgets.QApplication.processEvents()

        try:
            await self._disconnect()
        except Exception as e:
            self._set_busy(False, f"Disconnect error: {e}")
            self._log_error(f"Disconnect error: {e}")
            return

        self._on_disconnect_done()

    def _on_disconnect_done(self) -> None:
        self._set_busy(False, "Not connected")
        self.append_log("\n[Disconnected]\n")
        self.set_connected(False)

    # Errors are surfaced via status + log.

    def remember_history(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        if not self._history or self._history[-1] != line:
            self._history.append(line)
        self._hist_idx = len(self._history)

    def history_up(self) -> None:
        if not self._history:
            return
        self._hist_idx = max(0, self._hist_idx - 1)
        self.cmd.setText(self._history[self._hist_idx])

    def history_down(self) -> None:
        if not self._history:
            return
        self._hist_idx = min(len(self._history), self._hist_idx + 1)
        if self._hist_idx == len(self._history):
            self.cmd.setText("")
        else:
            self.cmd.setText(self._history[self._hist_idx])

    def send_preset(self, cmd: str) -> None:
        self.cmd.setText(cmd)
        self.on_send()

    def on_send(self) -> None:
        line = self.cmd.text().strip()
        if not line:
            return
        if not self.is_connected():
            self.status.setText("Not connected")
            return

        self.append_log(f"> {line}\n")
        self.remember_history(line)
        self.cmd.clear()

        asyncio.create_task(self._send_line_bg(line))

    async def _send_line_bg(self, line: str) -> None:
        try:
            await self._send_line(line)
        except Exception as e:
            self.status.setText(f"Send error: {e}")
            self._log_error(f"Send error: {e}")

    def on_send_led(self) -> None:
        r = int(self.led_r.value())
        g = int(self.led_g.value())
        b = int(self.led_b.value())
        self.send_preset(f"LED {r} {g} {b}")

    def on_send_motor(self) -> None:
        d = self.motor_dir.currentText().strip().upper() or "S"
        duty = int(self.motor_duty.value())
        sec = float(self.motor_sec.value())
        if sec <= 0:
            self.send_preset(f"MOTOR {d} {duty}")
        else:
            self.send_preset(f"MOTOR {d} {duty} {sec}")

    def on_send_m(self) -> None:
        d = self.motor_dir.currentText().strip().upper() or "S"
        duty = int(self.motor_duty.value())
        sec = float(self.motor_sec.value())
        if sec <= 0:
            self.send_preset(f"M {d} {duty}")
        else:
            self.send_preset(f"M {d} {duty} {sec}")


def main() -> None:
    app = QtWidgets.QApplication([])
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    w = MainWindow()
    w.show()

    with loop:
        loop.run_forever()
