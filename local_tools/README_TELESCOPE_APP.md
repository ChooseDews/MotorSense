# MotorSense Telescope App

The app connects to both boards from one BLE scan and retains the returned
`BLEDevice` objects for simultaneous yaw/pitch `BleakClient` connections.
This matters on macOS, where CoreBluetooth device identifiers are not MAC
addresses. It sends the boards' `AXIS MOVE` and `AXIS STOP` commands, so the
microcontrollers keep the fast axis feedback loops.

```sh
cd local_tools
uv run motor-sense-telescope
```

The current board identities are `MotorSense Caboose-78` (yaw) and
`MotorSense Caboose-158` (pitch). The GUI scans, then connects both with the
**Connect controllers** button. Its **Closed-loop mount control** section has
absolute yaw/pitch GOTO, a maximum-duty control, and several yaw/pitch step
sizes. All
of these issue `AXIS MOVE` commands, so they use the ESP32's encoder feedback
rather than raw open-loop motor drive.

## External camera calibration

In **Extra Controls**, choose **External calibration** to compare settled
encoder moves with `camera_yaw_pitch_tracker/telescope_angles.csv`. Capture a
reference with the camera tracker running, choose a yaw, pitch, or combined
yaw-and-pitch sweep, then press **Run sweep**. The window waits for each
board-side move to reach its requested encoder position, settle, and then for
a fresh camera CSV update. Its table shows
encoder displacement, camera displacement, and their error at every point;
the summary reports RMS and maximum error. **Export CSV** saves the full
per-point data, sweep settings, and reference values. Camera yaw and pitch are always
relative to the captured reference, so the CV tracker's absolute zero or
offset cannot alter the result. Yaw sweeps are capped at 150° to avoid an
ambiguous wraparound measurement.

The local Unix socket defaults to:

```text
~/Library/Application Support/MotorSense/telescope.sock
```

It accepts one line per request and returns one JSON line. Requests can be
plain commands or a JSON object with `command`.

```sh
uv run indi-custom-mount get_state
uv run indi-custom-mount connect
uv run indi-custom-mount goto_mount 213.428 48.291
uv run indi-custom-mount goto_radec 12.5167 42.18
uv run indi-custom-mount track_radec 12.5167 42.18
uv run indi-custom-mount track_object jupiter
uv run indi-custom-mount set_location 37.3349 -122.0090 0
uv run indi-custom-mount sync 12.5167 42.18
uv run indi-custom-mount stop
```

`goto_mount` takes yaw then pitch, in degrees. `goto_radec`, `track_radec`,
and `sync` take RA hours then declination degrees. Astropy converts RA/Dec to
Alt/Az at the configured observer location; planets and Moon are refreshed
each tracking cycle. `set_location` takes latitude, longitude, and an optional
elevation in metres. The default observer location is Cupertino and should be
set before observing. After centering a known target, `sync` updates the
encoder references in the same corrected mount coordinate system used for
GOTO and tracking.

## Stellarium

Start the app and then the LX200 bridge:

```sh
cd local_tools
uv run motor-sense-stellarium-bridge --pty --port 4030
```

In Stellarium, enable the **Telescope Control** plug-in, add a telescope of
type **Meade LX200**, select **Stellarium, directly through a serial port**,
and enter the printed `LX200 serial PTY` path. Stellarium's slew action then
sends standard LX200 RA/Dec GOTO commands through the bridge to the app. The
TCP endpoint at `127.0.0.1:4030` remains available for LX200 clients that
support network connections. The GUI's **Launch Stellarium** button opens the
native app at `/Applications/Stellarium.app`.

## INDI / Ekos

The working INDI path uses an existing LX200 INDI driver as the property layer
and a local LX200-to-socket bridge. Start the telescope app first, then in
another terminal:

```sh
cd local_tools
uv run motor-sense-lx200-bridge --port 4030
```

If `indi_lx200_network` is installed, start it with
`indiserver -v indi_lx200_network`, then in Ekos select **LX200 on Network**
and set host `127.0.0.1`, port `4030`.

When a compatible `indi_lx200basic` executable is available, use the bridge's
virtual serial endpoint instead:

```sh
uv run motor-sense-lx200-bridge --pty
/Applications/kstars.app/Contents/MacOS/indiserver -v /Applications/kstars.app/Contents/MacOS/indi_lx200basic
```

Copy the printed `LX200 serial PTY` path into the LX200 Basic driver's serial
port field in Ekos. Standard INDI GOTO, SYNC, and ABORT actions become
`goto_radec`, `sync`, and `stop` requests to the app.
The bridge reports measured RA/Dec by transforming the calibrated mount angles
back from Alt/Az at the configured location.

`indi-custom-mount` remains a direct socket command client for diagnostics and
for a future native custom INDI driver.
