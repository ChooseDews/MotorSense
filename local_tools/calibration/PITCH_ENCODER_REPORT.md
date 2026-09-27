# Pitch firmware and camera-referenced calibration

**18 September 2026 — ESP32-S3 90:70:69:1C:48:4C, CAN ID 158**

The improved firmware is installed and the motor was verified stopped (`dir=S duty=0%`). Calibration at **35% duty** covered **10.656° outward**, followed by an independent eight-point return validation. Motor-encoder return RMS error against the camera was **0.095°**; axis-encoder RMS error was **0.126°**. These are settled-position comparisons against the supplied CV reference, not independently established absolute pointing accuracy.

## Accuracy

Eight outward plateaus determined each degrees-per-tick factor by a least-squares fit through the initial reference, with no fitted offset. Eight return plateaus were held out. Each plateau uses the last two seconds after six seconds of settling: median encoder positions and the mean of 4–5 unique camera pitch observations. The median camera standard deviation was **0.022°**. All eight return points passed the existing quality screen (camera SD ≤0.1°, encoder ranges ≤1 tick); **no points were excluded**.

| Measurement | Encoder 0: GPIO11/12 | Encoder 1: GPIO9/10 |
|---|---:|---:|
| Observed role | Slow axis encoder | Fast motor encoder |
| Ticks per degree | **32.936659** | **4,618.842234** |
| Degrees per tick | **0.03036130633** | **0.0002165044722** |
| Outward fit RMS error | 0.103° | 0.080° |
| **Held-out return RMS error** | **0.126°** | **0.095°** |
| Return maximum absolute error | 0.232° | 0.190° |
| Return mean error | +0.089° | +0.008° |

![Pitch position and error](pitch-accuracy.png)

The initial camera pitch was **5.149°**, peak **15.806°**, and last return plateau **4.796°**. Timed return moves slightly overshot the initial pitch; this was not a closed-loop return-to-zero test. Relative to the initial reference, the final return counts changed by −4 axis ticks and −751 motor ticks, while camera pitch changed by −0.353°. The corresponding residuals were +0.232° and +0.190°. The motor remained stopped afterward, at approximately 4.8° camera pitch.

The observed outward count ratio was **140.238 motor ticks per axis tick**. Their held-out return disagreement was **0.087° RMS**, maximum **0.119°**, expressed in axis-derived degrees. This supports the same connector roles as yaw; no connector swap was required. The ratio is an empirical count conversion, not a verified mechanical gear ratio. Positive counts correspond to increasing camera pitch; `MOTOR B` raises pitch and `MOTOR F` lowers it with the tested wiring and default inversion settings (encoder 0 false, encoder 1 true).

## Debugging and firmware changes

1. **Pitch needs different analog thresholds.** The yaw setting, motor center 3600 ±100, produced 166 invalid transitions in the initial 60% reverse test. A repeat diagnostic added 157. A full-rate capture at 60% had channel minima of 3181/3253 ADC counts, substantially above the earlier yaw waveform. A 40% test with the yaw threshold still added 13 invalid transitions.
2. **A higher threshold alone did not validate high speed.** A 3950 ±50 setting decoded the captured 40% segment cleanly but produced one invalid transition in a live test. The final **3800 ±100** setting also produced one invalid transition in a 40% test. **35% duty** then passed the bidirectional short test and full calibration with no additional invalid transitions. At 30%, motion was weak and asymmetric. The final firmware does not enforce a 35% cap: higher-duty encoder accuracy remains unvalidated and should not be assumed from this calibration.
3. **Startup acquisition could silently stall.** One intermediate boot stopped advancing the DMA pair counter at 10,240 despite `ready=1` and zero overflow/error counters. Encoder acquisition now starts after Bluetooth initialization, avoiding overlap with radio initialization. Subsequent tested boots and the complete sweep acquired normally; this supports the ordering fix but is not an exhaustive boot-reliability study. DMA read timeouts now increment the acquisition-error counter instead of being ignored. The collector also independently rejects a stalled sample counter.
4. **Timed-motion failure now stops safely.** A timed request is rejected before applying motion when its timer is unavailable; failure to arm the timer immediately stops the motor.

Motor Schmitt settings are now build-configurable. The default remains the yaw setting; the separate pitch build uses [pitch-sdkconfig.defaults](pitch-sdkconfig.defaults). Encoder 0 remains at 2800 ±100 and approximately 1,000 A/B pairs/s. Encoder 1 uses ADC1 DMA at nominally 40,000 pairs/s. No wiring or camera-tracker source was changed.

## Acquisition and limitations

During the calibration log, encoder 1 acquired **4,947,200 A/B pairs**, with **zero new invalid transitions, DMA overflows, acquisition errors, or channel-order errors**. Encoder 0 acquired **122,692 pairs**, with **zero new invalid transitions or read errors**. The motor invalid counter was **1 before and 1 after** this sweep; the pre-existing event belongs to the separate 40% diagnostic, not the calibration. Adjacent-state jitter was observed at some stationary positions; plateau medians and reported encoder ranges retain its effects.

These results cover this pitch interval, load, direction changes, firmware, and 35% duty. They do not establish high-speed tracking accuracy, full-range linearity, temperature stability, or sub-millidegree absolute accuracy. Camera systematic errors and mechanical backlash/compliance remain in the measured result. The camera's approximately 0.53-second update period supports settled comparisons rather than synchronized high-speed validation.

## Using the calibration

[Machine-readable calibration](pitch-dma-calibration.json) contains the scales and firmware identity. Scales are supplied for host software; the firmware still reports encoder counts. Take a fresh stationary reference after reboot or changing inversion:

```text
pitch0 = reference_pitch + (position0 - reference_position0) * 0.0303613063253
pitch1 = reference_pitch + (position1 - reference_position1) * 0.000216504472182
```

The installed application is saved as [pitch-dma-validated.bin](pitch-dma-validated.bin), SHA-256:

```text
d84003c68a59b656432ab8f3956a2c55c12a8ab4b277035f66d6d72c1d9ca53f
```

It advertises with the existing naming scheme, **MotorSense Caboose-158**. Flash transfer/hash verification, the ESP-IDF build, host Schmitt decoder test, Python compilation, and `git diff --check` passed. No original-flash backup was completed; backup attempts were cancelled before flashing as requested.

## Axis-only fast positioning

The current pitch build disables the GPIO9/10 motor encoder entirely. GPIO11/12 (encoder 0) is the only feedback source for positioning and fault checks. This avoids treating the motor encoder's high-speed waveform as a control dependency.

`AXIS MOVE <signed_degrees> [max_duty]` performs a board-side relative move. Positive degrees use `MOTOR B` and negative degrees use `MOTOR F` on this board. The controller uses the calibrated **0.0303613063253° per axis tick** scale, ramps toward the requested duty ceiling, reduces duty as the target approaches, waits 350 ms for coast-down, then makes low-duty corrections. `AXIS STATUS` reports its current state; `AXIS STOP` and any `MOTOR` command cancel a pending axis move.

It immediately stops on an axis ADC read error, invalid quadrature transition, no progress for 1.2 seconds, sustained wrong-direction feedback, or a 30-second overall timeout. The tested build has a 35–100% duty range and ±30° relative-move limit.

On 19 September, four axis-only runs at up to full duty passed:

| Request | Maximum duty | Target / final axis ticks | Final encoder error | Axis faults added |
|---|---:|---:|---:|---:|
| +5° | 90% | 165 / 163 | −0.061° | 0 |
| −5° | 90% | −2 / 0 | +0.061° | 0 |
| +10° | 100% | 329 / 327 | −0.061° | 0 |
| −10° | 100% | −2 / 0 | +0.061° | 0 |

The 10° out-and-back returned to the original axis count exactly. Each run used three low-duty corrections. The motor decoder reported `pairs=0`, `ready=0`, and zero motor faults because it was intentionally disabled. This demonstrates encoder-count stability and closed-loop endpoint performance for the tested unloaded setup; it does not replace a fresh camera-referenced high-speed accuracy test.

The current closed-loop application image is SHA-256 `3cb51b98ead3a4b899afd7a8daf67a47e4971cba879f5af38701791801c9e19d`; it was flashed and hash-verified after these tests. The controller was subsequently checked idle with motor state `S`, zero duty, zero axis errors, zero invalid transitions, and motor acquisition still disabled.

## Data and reproduction

- [Complete sweep log](pitch-sweep.jsonl), [plateau table](pitch-sweep-plateaus.csv), and [numerical results](pitch-sweep-results.json).
- [Final status](pitch-final-status.jsonl), [flash log](pitch-flash.log), and [build log](pitch-build.log).
- [Collector](pitch_probe.py), [analysis](analyze_pitch_sweep.py), [plot](plot_pitch_sweep.py), and [waveform replay](analyze_pitch_waveforms.py).
- Separate `pitch-*-test*.jsonl` and diagnostic files retain unsuccessful trials; they are not mixed into the accuracy dataset.

```sh
local_tools/.venv/bin/python local_tools/calibration/analyze_pitch_sweep.py local_tools/calibration/pitch-sweep.jsonl
uv run --quiet --no-project --with matplotlib --with numpy python local_tools/calibration/plot_pitch_sweep.py
# Existing local pitch configuration:
# source ~/esp/esp-idf/export.sh
# idf.py -B build-pitch -D SDKCONFIG=sdkconfig.pitch build
```

For a fresh pitch configuration, copy the project's sdkconfig to a separate pitch configuration and apply the two settings in `pitch-sdkconfig.defaults`; do not replace the yaw configuration. The collector refuses to overwrite existing experiment files, checks fresh finite **pitch**, uses board-timed moves, enforces an 18° camera excursion envelope, and always sends a host stop on exit.
