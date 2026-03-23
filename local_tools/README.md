# local_tools: BLE serial CLI

This folder contains a small Python CLI that connects to the ESP-IDF BLE "serial" (Nordic UART Service / NUS) implemented in [main/bt_main.c](../main/bt_main.c).

## Run with uv

From the repo root:

- `cd local_tools`
- `uv run ble-serial`

To run the GUI:

- `cd local_tools`
- `uv run ble-serial-gui`

The GUI auto-scans on launch and auto-connects to the first device whose name contains `MotorSense`.

That will:
- create an isolated environment
- install dependencies
- run the CLI

## Usage

- The tool scans and lists nearby BLE devices.
- By default it filters to device names containing `MotorSense`.
- If exactly one matching device is found, it auto-connects.
- Devices advertising the NUS service UUID are tagged with `NUS`.
- Select an index to connect.
- Type commands; they are sent newline-terminated to the RX characteristic.
- Notifications from the TX characteristic are printed to stdout.

Exit the session with `/exit`.

The prompt supports command history (Up/Down arrows) and persists history to `~/.ble-serial-history`.

## Web Bluetooth (HTML5) POC

There is also a browser-based proof-of-concept that uses Chrome/Edge Web Bluetooth:

- See [web_ble_poc/README.md](web_ble_poc/README.md)

### Options

- `uv run ble-serial --scan-timeout 8`
- `uv run ble-serial --name-contains MotorSense`
- `uv run ble-serial --name-contains ''` (disable name filtering)
- `uv run ble-serial --no-auto-connect` (always ask which device)
- `uv run ble-serial --rx-uuid 6e400002-b5a3-f393-e0a9-e50e24dcca9e --tx-uuid 6e400003-b5a3-f393-e0a9-e50e24dcca9e`
