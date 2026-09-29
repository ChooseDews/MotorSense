# MotorSense Firmware

ESP32-S3 firmware for the MotorSense motor controller. It drives a motorised
camera axis (YAW or PITCH), reads a quadrature encoder and ADC inputs, exposes
a command console over BLE UART and USB/serial, and supports SHA-256-verified
OTA firmware updates with bootloader rollback.

- Target: **ESP32-S3**, 2 MB flash (DIO, 80 MHz)
- ESP-IDF: **v5.5.x** (CI builds with v5.5.1)
- Project name / version: `motorsense` / `1.0.0` (set in `CMakeLists.txt`)

## Building

Install ESP-IDF v5.5.x first
([install guide](https://docs.espressif.com/projects/esp-idf/en/v5.5.1/esp32s3/get-started/)),
then from this directory:

```bash
. $HOME/esp/esp-idf/export.sh    # or open the ESP-IDF terminal / VS Code env

idf.py -B build-unified -D SDKCONFIG=sdkconfig.unified build
```

This produces, in `build-unified/`:

- `motorsense.bin` — the app image
- `motorsense-v1.0.0.bin` — same image, versioned copy (made by the `release_image` target)
- `motorsense.elf` / `motorsense.map` — for symbol decoding with `addr2line`
- `bootloader/bootloader.bin`, `partition_table/partition-table.bin`, `ota_data_initial.bin`

> **Important:** always pass a variant `SDKCONFIG` explicitly. The `sdkconfig`
> file at the project root is a stale leftover (single-app partition table, OTA
> rollback disabled), so a plain `idf.py build` on a fresh checkout would
> silently build an image without working OTA.

### sdkconfig variants

| File | Purpose |
| --- | --- |
| `sdkconfig.defaults` | Minimal source-of-truth defaults (BT, ESP32-S3, 2 MB flash, custom OTA partition table, rollback). Used when `sdkconfig` is (re)generated. |
| `sdkconfig.unified` | The current, full production config. The one firmware image everyone builds — this is what CI and the docs use. |
| `sdkconfig.pitch`, `sdkconfig.yaw-control` | Legacy leftovers from when the axis role was compile-time. Not built by CI; kept only for reference and safe to delete. |

There is intentionally only **one** firmware image: the axis role (`YAW` or
`PITCH`), calibration and motor settings are runtime settings stored in NVS —
see [Setting a device to YAW or PITCH mode](#setting-a-device-to-yaw-or-pitch-mode).

### Changing settings

Open menuconfig against the variant you build with (it saves back to that file):

```bash
idf.py -B build-unified -D SDKCONFIG=sdkconfig.unified menuconfig
```

To start over from a clean tree:

```bash
idf.py -B build-unified fullclean
```

## Setting a device to YAW or PITCH mode

The axis role is a runtime setting in NVS — the same firmware image works for
both axes. Connect with a serial terminal at 115200 baud (or over the BLE UART
service, advertising as `MotorSense-<ROLE>-XXXX`) and run:

```
DEVICE ROLE PITCH        # or YAW — saved to NVS immediately
DEVICE REBOOT            # the role applies after reboot
```

`DEVICE INFO` shows the current role, firmware version and a `pending_reboot`
flag; `CALIBRATION` shows the stored axis calibration.

Other per-device settings are set the same way and also apply on reboot:

```
DEVICE SET counts_rev 11840      # encoder counts per revolution
DEVICE SET min_deg -45           # travel limits
DEVICE SET max_deg 45
DEVICE SET zero_deg 0            # axis zero position
DEVICE SET motor_center 3600     # motor centre / deadband PWM
DEVICE SET motor_hysteresis 100
DEVICE SET motor_invert 0        # direction inversion flags (0|1)
DEVICE SET axis_invert 0
DEVICE SET encoder_motor_invert 0
DEVICE SET motor_encoder_enabled 0
```

Setting commands are rejected while the motor is moving (`ERR motor busy`) —
send `AXIS STOP` first.

## Flashing

Four ways, depending on what you have at hand. Serial flashing uses the usual
ESP32-S3 USB-serial boot process; if auto-reset into download mode fails, hold
**BOOT**, tap **EN/RST**, release **BOOT** and retry.

On macOS the port is typically `/dev/cu.usbmodem*`; on Linux `/dev/ttyACM0` or
`/dev/ttyUSB0`.

### 1. `idf.py` (development workflow)

Once a build directory exists, its `SDKCONFIG` is cached, so flashing is just:

```bash
idf.py -B build-unified -p /dev/cu.usbmodemXXX flash monitor
```

(`monitor` is optional; exit with `Ctrl-]`.)

### 2. `esptool.py` with individual images

Same partitions `idf.py flash` writes, at the offsets from `flasher_args.json`:

```bash
esptool.py --chip esp32s3 -p /dev/cu.usbmodemXXX --baud 921600 \
  --before default_reset --after hard_reset \
  write_flash \
  0x0      build-unified/bootloader/bootloader.bin \
  0x8000   build-unified/partition_table/partition-table.bin \
  0x10000  build-unified/motorsense.bin \
  0x1f0000 build-unified/ota_data_initial.bin
```

### 3. Single merged image (CI artifact / production)

`idf.py merge-bin` (run automatically in CI) merges bootloader, partition
table, otadata and app into one file that flashes at offset `0x0`:

```bash
esptool.py --chip esp32s3 -p /dev/cu.usbmodemXXX write_flash 0x0 merged-binary.bin
```

CI uploads this as `motorsense-<variant>-flash-all.bin`.

### 4. OTA (no cable needed)

A running device can be updated over its BLE-UART / serial command protocol.
The payload is the **app image only** (`motorsense-v1.0.0.bin` — not the merged
image; it must fit one OTA slot, 960 KB). Flow:

- `OTA BIN BEGIN <size> <sha256>` → stream the raw binary → `OTA BIN END`
  (`OTA BIN ABORT` / `OTA BIN STATUS` also available; this is the BLE-updater path)
- Legacy hex mode: `OTA BEGIN <size> <sha256>` then repeated `OTA DATA <offset> <hex>`

The data is written to the inactive slot (`ota_0`/`ota_1`) and verified against
the SHA-256 given at begin. The bootloader runs with rollback enabled
(`CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE`), so if the new image fails to confirm
itself the device falls back to the previous firmware.

## Partition layout

From `partition_table/partitionTable.csv`:

| Name | Offset | Size | Type |
| --- | --- | --- | --- |
| `nvs` | 0x9000 | 24 KB | data (settings/config) |
| `phy_init` | 0xf000 | 4 KB | data (PHY calib) |
| `ota_0` | 0x10000 | 960 KB | app slot 0 |
| `ota_1` | 0x100000 | 960 KB | app slot 1 |
| `otadata` | 0x1f0000 | 8 KB | data (active-slot selection) |

## Console / BLE interface

The same command set is served over the USB/serial console (115200 baud) and a
BLE UART service advertising as `MotorSense-<ROLE>-XXXX`. `HELP` lists
everything; the main groups are:

- `MOTOR F|B|S [duty_percent] [seconds]` / `M …` — drive motor
- `AXIS MOVE <signed_degrees> [max_duty]`, `AXIS STATUS`, `AXIS STOP`, `AXIS ZERO`
- `LED R G B`, `LED DANCE`
- `ENCODER <0|1>`, `ENCODER STATS`, `ENCODER CAPTURE`
- `ADC STREAM [START|STOP]`
- `DEVICE INFO|ROLE|HW|SET …`, `CALIBRATION`, `VERSION`, `DEVICE REBOOT`
- `OTA …` (see above)

## Continuous Integration

`.github/workflows/build-firmware.yml` builds the firmware from
`sdkconfig.unified` with the official `espressif/idf:v5.5.1` Docker image on
pushes/PRs that touch `firmware/`, and on manual `workflow_dispatch`. It
uploads a single `motorsense-firmware` artifact containing the app binary, the
ELF (debug symbols for decoding crash backtraces — never flashed), and the
merged flash-all image.

## Source layout (`main/`)

| File | Role |
| --- | --- |
| `bt_main.c` | BLE stack init, UART service, command transport |
| `commands.c` | Command parser/dispatcher (all `HELP` commands) |
| `serial_console.c` | USB/serial console transport |
| `device_config.c` | Runtime role/calibration config in NVS |
| `axis_motion.h` | Axis motion state machine types/helpers (used by `commands.c`) |
| `quadrature_encoder.c` | Quadrature decoding + index capture |
| `adc_inputs.c` | ADC sampling |
| `ota_update.c` | OTA slot writes, SHA-256 verification |
| `mesh_main.c` | Legacy ESP-MESH experiment, not compiled (not in `SRCS`) |
