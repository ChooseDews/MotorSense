# MotorSense firmware

Both ESP32-S3 controllers run the same `motorsense-v1.0.0.bin`. The role is a
persistent `YAW` or `PITCH` string in the versioned `motorsense/config_v1` NVS
configuration. BLE names include the role and a MAC suffix. There is no CAN/TWAI
transport or axis-specific build configuration.

## Build and initial migration

Use ESP-IDF 5.5.x and a board with at least 2 MB flash:

```sh
idf.py -B build -D SDKCONFIG=sdkconfig.unified build
idf.py -B build -p PORT flash monitor
```

**Always pass `-D SDKCONFIG=sdkconfig.unified`.** The tracked `firmware/sdkconfig`
at the project root is a stale leftover (single-app partition table, OTA rollback
disabled), so a plain `idf.py build` would silently use it. `sdkconfig.unified` is
the current production config; the old `sdkconfig.pitch` / `sdkconfig.yaw-control`
axis variants are legacy and unused. `CMakeLists.txt` sets firmware version
`1.0.0`; change `PROJECT_VER` for releases. The build produces both
`motorsense.bin` and `motorsense-v1.0.0.bin` (identical application bytes).
The full build/flash/OTA reference lives in the
[firmware README](../firmware/README.md).

Existing factory-only boards need this **one wired migration**, including the
bootloader, partition table, application and initial OTA metadata. Do not send
just the application to the old firmware, and do not erase flash. Back up NVS
before migration. NVS remains at `0x9000`, size `0x6000`, and PHY data remains at
`0xf000`. The normal flash command does not overwrite either region. Old
compile-time calibration cannot be recovered from NVS: provision each board's
measured settings explicitly after migration. New/unconfigured boards default
to YAW; configure the PITCH board before using it.

| Partition | Offset | Size |
| --- | --- | --- |
| nvs | 0x9000 | 24 KiB |
| phy_init | 0xf000 | 4 KiB |
| ota_0 | 0x10000 | 960 KiB |
| ota_1 | 0x100000 | 960 KiB |
| otadata | 0x1f0000 | 8 KiB |

## Configuration and reporting

Send newline-terminated commands through BLE NUS or the serial console:

```text
DEVICE INFO
VERSION
DEVICE ROLE
DEVICE ROLE PITCH
DEVICE HW rev-A
DEVICE SET motor_invert 1
DEVICE SET axis_invert 0
DEVICE SET encoder_motor_invert 1
DEVICE SET motor_encoder_enabled 0
DEVICE SET motor_center 3800
DEVICE SET motor_hysteresis 100
DEVICE SET min_deg -90
DEVICE SET max_deg 90
DEVICE SET zero_deg 0
DEVICE SET counts_rev 11840
DEVICE REBOOT
CALIBRATION
AXIS ZERO
AXIS MOVE 5 60
AXIS STATUS
AXIS STOP
```

The example contains settings for commissioning a pitch board; confirm its
actual travel limits and direction before driving it. Role changes affect the
name, but deliberately do not overwrite measured mechanical settings. Limits,
direction, thresholds and the angle assigned by `AXIS ZERO` are explicit
per-device NVS settings. Configuration writes require an idle motor and are
committed atomically; reboot applies them. Reporting shows the active settings
and `pending_reboot=1` when saved changes await reboot. Hardware revision is a
user-provisioned label, initially `unknown`, not the ESP chip revision.

The default mechanical encoder (encoder 0, GPIO11/12) calibration is exactly
**11,840 counts/revolution**, or **32.8888889 counts/degree** and
**0.0304054054 degrees/count**. Reporting computes reciprocals from one stored
counts/revolution value. Explicit measured calibration overrides persist over
OTA. Motor encoder 1 (GPIO9/10) is diagnostic, disabled by default; its threshold,
hysteresis and inversion are also persistent settings.

Positions are incremental, not absolute. After every boot, physically place the
axis at its known reference and issue `AXIS ZERO`. This assigns the configured
`zero_deg`; it does not search for a home switch. Relative moves require zeroing
and an endpoint within the configured limits. Defaults are -180 to +180 degrees
and zero=0. `MOTOR F|B|S` remains an open-loop commissioning command and bypasses
position limits; motor inversion applies to both manual and closed-loop drive.

Existing encoder statistics/capture, LED and ADC diagnostics remain available
through `HELP`. Legacy `ENCODER n INVERT` mutations now direct users to persistent
`DEVICE SET` commands. CAN discovery, ID routing and CAN debug commands are gone.

## BLE OTA

Install `bleak` in your Python environment, then upload the **same application
file** to either board (address is a peripheral UUID on macOS):

```sh
python tools/ble_ota.py ADDRESS build/motorsense-v1.0.0.bin
```

The uploader subscribes to NUS TX
`6e400003-b5a3-f393-e0a9-e50e24dcca9e`, and writes to NUS RX
`6e400002-b5a3-f393-e0a9-e50e24dcca9e`. Control commands are newline-terminated
ASCII; firmware data uses binary frames sent with write-without-response. Prepared
writes are unsupported.

```text
OTA BIN BEGIN <decimal_image_size> <64_hex_SHA256>
OK OTA BIN BEGIN
binary frame: "MS" + uint32_le offset + uint16_le length + uint8 flags + payload
OK OTA BIN <next_byte_offset> (after each cumulative window ACK)
OTA END
OK OTA REBOOT
```

The frame payload fills the negotiated ATT write size (up to 500 bytes), and up
to eight frames are pipelined before requesting one cumulative ACK. Bit 0 of the
flags byte requests that ACK. Offsets must be contiguous; missing, malformed or
oversized frames are rejected. The legacy ASCII `OTA BEGIN` / `OTA DATA` command
path remains available for serial recovery. On an uncertain ACK use `OTA STATUS`
to inspect `received`, or abort and restart. The bundled uploader aborts on
errors rather than guessing whether a write succeeded.
`OTA ABORT` releases an incomplete transfer. Disconnect also aborts the transfer;
partial lines and queued commands are tagged with their BLE session so they
cannot cross into a new connection. OTA commands are case-sensitive.

BEGIN stops the motor; movement and configuration commands are blocked until
END or ABORT. The updater writes only the inactive OTA slot. END requires exact
length, matching SHA-256, ESP-IDF image validation and matching MotorSense project
identity before selecting the new boot partition and rebooting. NVS is not
written or erased by OTA; NVS initialization errors fail boot rather than erase
calibration. Loss of power before boot selection leaves the previous app active.

Rollback is enabled in the bootloader. A new OTA image stays pending while a
10-second boot diagnostic checks the control resources, BLE service/tasks and
advancing encoder acquisition without read errors. Success marks it valid;
failure requests rollback and reboot. A crash/reset before confirmation also
rolls back. The task watchdog is configured to panic/reset on detected stalls.
These are startup diagnostics, not proof of correct mechanical motion. After
upload, reconnect after 15 seconds and check `DEVICE INFO`, `CALIBRATION`, and
`OTA STATUS`; verify the version/role and re-zero before movement. Rollback needs
a previously valid OTA image; the first wired installation establishes it.

## Signing roadmap

Current SHA-256 and image/project checks detect corruption and accidental wrong
images; they do **not** authenticate the publisher. BLE OTA is currently available
to connected clients without an application-level authorization step.

For production, provision a MotorSense signing key and enable ESP-IDF signed-app
verification (`CONFIG_SECURE_SIGNED_APPS_NO_SECURE_BOOT`,
`CONFIG_SECURE_SIGNED_ON_UPDATE_NO_SECURE_BOOT`, and the signing key configuration), or provision
Secure Boot V2 for boot-time enforcement as well. Validate bootloader size,
partition offsets and key provisioning on dedicated hardware first. Keep private
keys outside the repository; distribute signed application images and test key
rotation/rejection. `esp_ota_end` and boot selection use IDF verification so this
path can enforce signing once configured. No signing key is generated, no eFuses
are burned, and unsigned firmware is not claimed to be authenticated.

Reference: [ESP-IDF 5.5 OTA and rollback documentation](https://docs.espressif.com/projects/esp-idf/en/v5.5/esp32s3/api-reference/system/ota.html).

## Verification

```sh
python3 tests/firmware/test_host.py
idf.py -B build -D SDKCONFIG=sdkconfig.unified build
```

Host tests compile the actual configuration and OTA modules against injected
NVS/OTA/SHA stubs and cover persistence, invalid inputs, transfer ordering,
write/image/hash failures, wrong-project rejection, abort and successful boot
selection. They do not substitute for hardware tests or test SHA-256 itself.

Before deployment, test both board roles, inversion and zero/limit behavior;
perform OTA in both directions between slots; interrupt transfer/power; try a
truncated/corrupt/wrong-project image; and use a deliberately failing boot image
to verify automatic rollback and unchanged NVS. Hardware tests have not been
performed as part of this change.
