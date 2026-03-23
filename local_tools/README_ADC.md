# ADC Live Plotter for Encoder Debugging

This tool streams raw ADC values from all 4 encoder channels and plots them in real-time to help debug encoder issues.

## Installation

```bash
cd /Users/john/Documents/projects/mesh-blinker/local_tools
pip install -e .
```

This will install:
- `adc-plotter` command
- Required dependencies (pyserial, pyqtgraph, numpy, PyQt5)

## Usage

### 1. Flash the Firmware

Build and flash the updated firmware to your ESP32-S3:

```bash
cd /Users/john/Documents/projects/mesh-blinker
idf.py build flash
```

### 2. Find Your Serial Port

List available ports:

```bash
adc-plotter --list
```

Or on macOS:
```bash
ls /dev/tty.usbserial-* /dev/cu.usbserial-*
```

### 3. Start the ADC Plotter

```bash
# Replace /dev/ttyUSB0 with your actual port
adc-plotter /dev/ttyUSB0

# Or with custom settings
adc-plotter /dev/ttyUSB0 --baudrate 115200 --buffer-size 2000
```

### 4. Start ADC Streaming

The plotter will automatically send `ADC STREAM START` when it connects.

You can also control streaming manually via serial console:
```
ADC STREAM START   - Start streaming ADC values
ADC STREAM STOP    - Stop streaming  
ADC STREAM         - Check current status
```

## What You'll See

The plotter shows 4 real-time graphs:

- **Top Left**: GPIO9 (Encoder 1, Channel A) - Green
- **Top Right**: GPIO10 (Encoder 1, Channel B) - Yellow
- **Bottom Left**: GPIO11 (Encoder 0, Channel A) - Cyan
- **Bottom Right**: GPIO12 (Encoder 0, Channel B) - Magenta

### What to Look For

**Good Encoder Signals:**
- Smooth sinusoidal waves (90° phase shift between A and B)
- Amplitudes around 1000-3000 ADC counts
- Clean transitions, no clipping at 0 or 4095
- Consistent amplitude as encoder rotates

**Problems to Watch For:**
- **Clipping**: Waveforms hitting 0 or 4095 (rails) - reduce signal amplitude or adjust biasing
- **Noise**: Jagged, erratic signals - check wiring/grounding
- **Weak Signal**: Amplitude < 500 counts - increase signal strength
- **Phase Issues**: A and B not 90° apart - mechanical misalignment
- **Dropped Samples**: Gaps in the data stream - system overload

## Debugging Missing Steps

If you're missing encoder steps, use this tool to:

1. **Verify Signal Quality**: Check that waveforms are clean sine waves
2. **Check Amplitude**: Ensure signals are strong enough (> 800 counts peak-to-peak)
3. **Confirm Quadrature**: A and B should be 90° out of phase
4. **Monitor Transitions**: Watch for glitches during direction changes
5. **Check Sampling Rate**: The data stream should be continuous at ~1kHz

## Encoder Inversion

If one encoder counts backwards, use the ENCODER command:

```
ENCODER 0 INVERT 1    # Flip encoder 0 direction
ENCODER 1 INVERT 1    # Flip encoder 1 direction
ENCODER 0 INVERT 0    # Restore encoder 0 to normal
```

Or use the toggle buttons in the web UI's encoder display.

## Other Useful Commands

```
ENCODER STATS         # Show sample count and overflow stats
ENCODER 0             # Get encoder 0 position and invert state  
ENCODER 1             # Get encoder 1 position and invert state
STATUS                # Overall system status
```

## Performance Notes

- ADC streaming runs at ~1kHz (1ms intervals)
- Encoder sampling runs at 4kHz (250μs) with priority 20
- The streaming task has priority 18 to minimize impact on encoder sampling
- Buffer holds last 2000 samples (~2 seconds at 1kHz)

## Troubleshooting

**No data appearing:**
- Check serial port connection
- Verify baud rate (should be 115200)
- Try manually sending `ADC STREAM START` in serial console

**Plot is laggy:**
- Reduce buffer size: `adc-plotter /dev/ttyUSB0 --buffer-size 1000`
- Close other applications using CPU/GPU

**"Permission denied" on serial port (Linux):**
```bash
sudo usermod -a -G dialout $USER
# Then log out and back in
```

**Missing Python packages:**
```bash
pip install pyserial pyqtgraph numpy PyQt5
```

## Stopping

- Close the plot window, or
- Press Ctrl+C in the terminal

The plotter will automatically send `ADC STREAM STOP` on exit.
