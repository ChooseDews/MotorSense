# Web client (`client/`)

Vue 3 + Vite single-page app that connects to the mount boards with Web Bluetooth. Chrome or Edge required (Safari/Firefox don't ship Web Bluetooth).

## Run

```sh
cd client
npm install
npm run dev
```

Open the printed localhost URL, click **Connect**, and pick the `MotorSense` device.

## Components

| Component | What it does |
| --- | --- |
| `BleManager.vue` | Scan / connect over NUS |
| `DeviceControl.vue` | Send `AXIS MOVE` / `AXIS STOP`, manual jog |
| `EncoderBar.vue`, `YawReadout.vue` | Live encoder position readouts |
| `DataLogger.vue` | Stream to CSV |
| `TelescopeVisualizer.vue` | 3-D telescope view (three.js) |

The BLE store (`src/stores/ble.js`) wraps the NUS write/notify plumbing; all components share it via Pinia.
