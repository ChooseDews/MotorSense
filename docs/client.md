# Web mount client (`clients/web/`)

Vue 3 + Vite single-page mount controller. It runs the same two-axis control model as the Python client while talking directly to both boards over Web Bluetooth.

## Run

```sh
cd clients/web
npm install
npm run dev
```

Open the localhost URL in Chrome or Edge. Web Bluetooth requires localhost or HTTPS and is not available in Safari or Firefox.

On first use, choose the controllers separately because browsers require a user gesture for every Bluetooth device picker:

- **Yaw:** `MotorSense Caboose-78`
- **Pitch:** `MotorSense Caboose-158`

On later visits, Chromium's `navigator.bluetooth.getDevices()` API returns the previously authorized boards. The app identifies them by those suffixes and reconnects yaw and pitch automatically, matching the Python client's selection behavior as closely as the browser security model allows.

## Features

- Live yaw/pitch encoder, BLE health, target, duty, and board motion state
- Closed-loop absolute and relative movement using board-side `AXIS MOVE`
- Shortest-path yaw motion and segmentation at the firmware's ±180° command limit
- Stop, home, park, verified `AXIS ZERO`, and app-side mount references
- RA/Dec goto and tracking, plus Sun, Moon, and planet tracking
- Browser geolocation or manually entered observer coordinates
- Sky sync, affine Alt/Az mount model, and persistent encoder calibration
- JSON configuration import/export (configuration is also saved in local storage)
- External camera CSV reference sweeps with result export
- Browser-based `.bin` firmware updates using the board's verified BLE OTA protocol
- Raw per-board terminal, event log, telemetry capture, and CSV export

The astronomy calculations use `astronomy-engine` locally in the browser. No network service is needed after the app loads.

## Browser-specific behavior

The native Python client can scan and connect both known controllers without interaction. A web page cannot grant itself Bluetooth access: each board must be approved through its own browser picker once. Previously authorized devices are selected and reconnected automatically.

For a live external-camera sweep, Chromium's File System Access API is used to reread the rolling tracker CSV. Browsers without that API can upload a CSV snapshot, but must select it again to see later writes.

The Python-only Unix socket, LX200/INDI bridge, and launching a local Stellarium process cannot run inside the browser sandbox. The web client replaces their operator-facing goto, sync, location, and tracking commands directly; external desktop integrations still use the Python bridge.

### Firmware updates

Connect at least one controller and open **Firmware → Update controller**. Select yaw or pitch, choose the MotorSense application `.bin`, and start the upload. Do not choose a merged `flash-all` image. The browser calculates SHA-256, streams the same `MS` binary frames and eight-frame acknowledgement windows as `local_tools/ble_ota.py`, asks the board to verify the image, and then waits for its reboot. Movement polling is suspended during transfer and failures trigger a best-effort `OTA ABORT`.

Keep the page open and the controller powered throughout the update. Reconnect after about 15 seconds, verify the device information and calibration, and re-zero before movement. Firmware hashes detect corruption but do not authenticate the publisher, so only install images you trust.

## Verify

```sh
npm test
npm run build
```

## GitHub Pages

The Pages workflow at `.github/workflows/deploy-web-pages.yml` tests and builds the app on pushes to `main`, uploads `clients/web/dist`, and deploys it to the repository's `github-pages` environment. The Vite base path is `/MotorSense/`, for the project URL `https://choosedews.github.io/MotorSense/`.

In the GitHub repository, set **Settings → Pages → Build and deployment → Source** to **GitHub Actions** once. The workflow can also be started manually from the Actions tab.
