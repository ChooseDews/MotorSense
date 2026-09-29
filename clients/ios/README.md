# MotorSense iOS

## Phone-mounted telescope alignment

Open the **Align** tab after connecting both motor controllers. Mount the iPhone
in portrait orientation with its top edge pointing along the telescope's optical
axis. The app fuses the gyroscope, accelerometer, and compass to show that edge's
azimuth and altitude. Tap **Align telescope to iPhone** once the readings settle.
The app stops and zeroes both controllers, then rebases their encoder readings to
the measured horizontal coordinates. True north is preferred when Core Motion
provides it; the screen identifies a magnetic-north fallback and compass quality.

The simulator can exercise the interface but does not provide representative
motion/compass data. Verify direction signs and magnetic interference on the
actual phone and telescope before relying on GOTO or tracking.

## BLE firmware updates

Open **Settings → Firmware update**:

1. Select **Yaw** or **Pitch**, then connect that controller. The other board can be off.
2. Paste a direct **HTTPS** `.bin` link and tap **Download firmware**, or choose
   a `motorsense-vX.Y.Z.bin` from Files (including downloaded/iCloud files).
3. Check the version, filename and size, then tap **Update Yaw/Pitch**.
4. Keep the app in the foreground and the controller powered. Progress counts
   bytes acknowledged by the firmware, not bytes merely handed to Bluetooth.
5. After upload, the app waits 15 seconds for reboot/startup checks, reconnects to
   the same peripheral, and checks its reported role, project and version.

URL downloads show separate progress and can be cancelled. The app follows HTTPS
redirects and accepts direct download endpoints with query parameters or download
filenames. Sharing/HTML pages, unsuccessful HTTP responses, incomplete responses,
and payloads larger than 960 KiB are rejected. Downloaded bytes undergo the same
image/header validation and SHA-256 calculation as local files. Downloading never
starts flashing automatically: review the image and tap **Update Yaw/Pitch**.
The URL is not saved; HTTP-only links are unsupported. Network requests time out
after 60 seconds of inactivity or 180 seconds overall.

The same application image works on either controller. Existing boards must
first receive the unified firmware, OTA partition table and rollback-enabled
bootloader over a wired connection. The app cannot perform that migration.
The importer rejects non-`.bin` files, images over the 960 KiB partition size,
non-ESP32-S3 headers and files without the MotorSense application descriptor.
The board performs final SHA-256 and ESP-IDF image validation. Neither file
inspection nor SHA-256 authenticates the publisher of unsigned firmware.

Starting an update stops motion, disables tracking and locks the mount. Normal
commands and polling pause until the update finishes. The mount remains locked
on success or failure: re-establish the physical reference and zero the updated
axis before unlocking. Auto-lock is disabled during the operation and restored
afterward. Do not switch away from the app during installation.

**Cancel update** disconnects the selected board, which aborts its incomplete
OTA session. Cancellation is unavailable once final verification/reboot starts.
A failed transfer is restarted from byte zero; no partial-upload resume is
claimed. If the final acknowledgement or reconnect is lost, the app reports an
unverified result rather than claiming success. A different version after
reconnect is reported as a possible rollback. Version checking cannot distinguish
two different images built with the same version; increment release versions.

Discovery recognizes `MotorSense-YAW-XXXX` / `MotorSense-PITCH-XXXX` and retains
legacy `-78` / `-158` name support. Multiple matching controllers are rejected;
power on only the intended controller of each role while connecting.

## Implementation

- `FirmwareUpdate.swift`: application-image validation/hash and pipelined binary OTA protocol.
- `BLEMount.swift`: serial BLE queue, ATT fragmentation, response writes, per-command
  timeouts, cancellation, reconnect and version confirmation.
- `FirmwareDownload.swift`: bounded HTTPS download, validation, progress and cancellation.
- `FirmwareUpdateView.swift`: URL input, Files picker, controller choice and transfer status.
- `MountController.swift`: update state, movement lock and tracking coordination.

The updater sends `DEVICE INFO`, `OTA ABORT`, and `OTA BIN BEGIN <size> <sha256>`.
Firmware data uses binary `MS` frames containing a little-endian offset and length;
up to eight MTU-sized frames are pipelined before one cumulative application ACK.
`OTA END` verifies the image SHA-256 and requests reboot. ATT acknowledgements only
release CoreBluetooth writes and never advance upload progress. A 120-second
timeout covers partition preparation; window ACKs have 30 seconds, reconnect has
60 seconds.

## Validation

From this directory, with Xcode selected:

```sh
xcodebuild -project MotorSense.xcodeproj -scheme MotorSense \
  -sdk iphonesimulator -configuration Debug CODE_SIGNING_ALLOWED=NO build
xcrun swiftc MotorSense/TelescopeMath.swift MotorSense/FirmwareUpdate.swift \
  Tests/FirmwareTransferTests.swift -o /tmp/motorsense-ota-tests
/tmp/motorsense-ota-tests
```

Optionally pass a built application `.bin` to the test executable to check its
actual header and SHA-256. Tests cover both roles, exact binary-frame reconstruction,
non-multiple-of-MTU image sizes, wrong offsets/duplicate ACKs, malformed files,
wrong controller identity, firmware errors and rollback/version mismatch.

Before release, exercise BLE transfers on an iPhone and both physical controllers:
normal update, cancellation, radio/power loss, backgrounding, lost final ACK,
boot rollback, and unchanged role/calibration after update. Simulator builds and
host protocol tests do not validate CoreBluetooth against physical hardware.

Download tests (mock HTTP responses; no external server required):

```sh
xcrun swiftc MotorSense/TelescopeMath.swift MotorSense/FirmwareUpdate.swift \
  MotorSense/FirmwareDownload.swift Tests/FirmwareDownloadTests.swift \
  -o /tmp/motorsense-download-tests
/tmp/motorsense-download-tests
```
