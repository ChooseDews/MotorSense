# Architecture

MotorSense is a closed-loop alt-az telescope mount. Two ESP32-S3 boards (one per axis) read analog quadrature encoders and drive DC motors, exposing a tiny newline-terminated command protocol over BLE (Nordic UART Service). Hosts — a web app, Python tools, and an iOS app — connect over BLE; planetarium software connects through an LX200/INDI bridge.

![Architecture](images/architecture.svg)

## The layers

- **Firmware** (`main/`) — BLE GATT server (NUS), serial console, and a shared command dispatcher. `AXIS MOVE` runs a closed-loop profile on the board using live encoder feedback, so the fast control loop never leaves the microcontroller. See [firmware.md](firmware.md).
- **Host tools** (`local_tools/`) — the daily-driver control GUI (`motor-sense-telescope`), a barebones BLE console (`ble-serial`), an ADC plotter, plus an LX200 server and INDI bridges so Stellarium/SkySafari/KStars can do GOTO. See [tools.md](tools.md).
- **Web client** (`client/`) — browser-based control using Web Bluetooth (Chrome/Edge). Encoder plots, data logging, and a 3-D telescope view. See [client.md](client.md).
- **iOS app** (`local_tools/ios_app/`) — SwiftUI + CoreBluetooth mount remote with RA/Dec GOTO and a sky view.
- **Camera tracker** (`camera_yaw_pitch_tracker/`) — independent ground truth. A webcam watches four AprilTags (two on the rotating base, one floor reference, one on the telescope) and writes true yaw/pitch to `telescope_angles.csv`. The telescope app's *external calibration* sweeps compare encoder motion against this file to measure closed-loop accuracy. See [tracker.md](tracker.md).

## The protocol

Every client uses the same text protocol on the BLE NUS characteristic:

```
AXIS MOVE <deg> [max_duty]   # closed-loop relative move, encoder feedback
AXIS STOP                    # cancel
AXIS STATUS                  # position, state, errors
ENCODER CAPTURE              # raw encoder sample burst
```

Responses arrive as NUS notifications. The same parser also serves the USB serial console, so you can debug without BLE.
