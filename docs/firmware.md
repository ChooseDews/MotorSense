# Firmware (`main/`)

ESP-IDF project for the two mount boards (ESP32-S3). Built from the ESP-MESH example — the project is still named `mesh-blinker` and `mesh_main.c` survives from it, but the working transport is BLE.

## Modules

| File | Purpose |
| --- | --- |
| `bt_main.c` | BLE NUS GATT server (write to RX, notifications on TX) |
| `serial_console.c` | USB serial console task |
| `commands.c` | Shared command dispatcher for both transports |
| `axis_motion.h` | Closed-loop axis state machine (run/settle/stall/timeout) |
| `quadrature_encoder.c` | Quadrature decoding — encoder 0 by 1 kHz oneshot ADC sampling, encoder 1 by continuous ADC-DMA |
| `encoder_schmitt.h` | Hysteresis decoder for the analog encoder sine signals |
| `adc_inputs.c` | ADC channel setup and sampling |
| `can_iface.c`, `can_rpc.c` | TWAI (CAN) bus and RPC between boards |

## Build and flash

```sh
idf.py set-target esp32s3
idf.py build flash monitor
```

Configuration inputs are `sdkconfig.defaults` and `sdkconfig.yaw-control.defaults` (both tracked). Generated `sdkconfig*` files are gitignored.

## Command set

Send newline-terminated ASCII over BLE or USB serial (`HELP` lists everything):

```
AXIS MOVE <deg> [max_duty]   closed-loop relative move
AXIS STOP / AXIS STATUS
ENCODER / ENCODER CAPTURE    live position / sample burst
MOTOR ...                    raw drive (open loop)
ADC STREAM ...               stream raw encoder signals
CAN STATUS / CAN TEST / ...  inter-board bus debug
LED SOLID|DANCE|OFF
```

`AXIS MOVE` is what every host uses: the board runs the move with encoder feedback and reports when settled, so hosts only issue high-level commands.
