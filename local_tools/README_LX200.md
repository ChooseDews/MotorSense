# LX200 Telescope Controller

A Python GUI application that connects to two BLE encoder devices and exposes an LX200 protocol server for telescope control software.

## Features

- **Dual BLE Device Support**: Connect to two separate encoder devices
  - One for Altitude (elevation) axis
  - One for Azimuth axis
  
- **Device Configuration**: Easily assign which device controls which axis

- **Calibration System**:
  - Set counts-per-degree for each axis
  - Set current position to calibrate zero points
  - Real-time position display in degrees

- **LX200 Protocol Server**: 
  - Implements the LX200 telescope control protocol
  - **Dual connection modes:**
    - **TCP/IP**: Network connection (modern astronomy software)
    - **Serial Port**: Traditional RS-232 connection (classic LX200)
  - Compatible with popular astronomy software:
    - Stellarium
    - SkySafari
    - Cartes du Ciel
    - KStars
    - And other ASCOM/LX200-compatible applications

## Installation

From the `local_tools` directory:

```bash
uv sync
```

## Usage

Run the application:

```bash
uv run lx200-telescope
```

Or with python directly:

```bash
uv run python src/lx200_telescope_controller.py
```

## Quick Start Guide

### Understanding Position Polling

When you connect astronomy software, you'll see repeated `:GR#` (Get RA) and `:GD#` (Get DEC) commands. **This is normal!** Astronomy software continuously polls the telescope position to:
- Update the display
- Track slewing progress
- Synchronize sky maps
- Monitor for manual movements

Typical polling rate: 2-10 times per second

### 1. Scan for BLE Devices
- Click "Scan BLE Devices" to discover nearby MotorSense devices
- Wait for the scan to complete (default 5 seconds)

### 2. Connect Devices
- **Altitude Device**: Select a device from the left list and click "Connect Altitude"
- **Azimuth Device**: Select a different device from the right list and click "Connect Azimuth"

### 3. Calibrate
Both axes need calibration before accurate position reporting:

**Method 1: Set Counts Per Degree**
1. Physically move the telescope through a known angle
2. Note the encoder count change
3. Calculate: `counts_per_degree = count_change / angle_in_degrees`
4. Enter this value in the "Counts/Degree" field

**Method 2: Set Current Position**
1. Point the telescope at a known position
2. Enter the current altitude and azimuth angles
3. Click "Set Altitude Position" and "Set Azimuth Position"
4. The system will calibrate the zero offset automatically

### 4. Start LX200 Server

**Choose Connection Type:**

**Option A: TCP/IP (Network Connection)**
- Select "TCP/IP (Network)" from dropdown
- Set the port (default: 4030)
- Click "Start LX200 Server"
- Server accepts network connections from any device

**Option B: Serial Port (Traditional)**
- Select "Serial Port" from dropdown
- Choose a serial port from the list (or click Refresh)
- Select baud rate (default: 9600)
- Click "Start LX200 Server"
- Connect astronomy software to the selected serial port

**Note**: On macOS/Linux, you may need to create a virtual serial port pair using `socat`:
```bash
# Create virtual serial port pair
socat -d -d pty,raw,echo=0 pty,raw,echo=0
# This creates two linked ports like /dev/ttys001 and /dev/ttys002
# Use one in this app, connect software to the other
```

### 5. Connect Astronomy Software

**For TCP/IP:**
Configure your astronomy software to connect to:
- **Host**: `localhost` (or your computer's IP address if connecting remotely)
- **Port**: `4030` (or whatever you configured)
- **Protocol**: LX200 or Meade LX200

**For Serial Port:**
Configure your astronomy software to connect to:
- **Port**: The same serial port shown in the app
- **Baud Rate**: 9600 (or whatever you configured)
- **Protocol**: LX200 or Meade LX200

#### Stellarium TCP Example:
1. Press `Ctrl+0` or go to Configuration → Plugins
2. Enable "Telescope Control"
3. Click "Configure" on the Telescope Control plugin
4. Add a new telescope:
   - Type: External software or remote computer
   - Connection: Local, LX200
   - Host: localhost
   - Port: 4030
5. Click "Connect"

**Expected behavior:** You'll see rapid `:GR#` commands as Stellarium polls position

#### SkySafari Serial Example:
1. Go to Settings → Telescope
2. Select "Meade LX200 Classic" as telescope type
3. Select "Serial" as connection type
4. Choose the serial port (e.g., `/dev/ttys016`)
5. Set baud rate to 9600
6. Connect

#### INDI (Recommended for Advanced Users)

INDI (Instrument Neutral Distributed Interface) is a more robust option. See [README_INDI.md](README_INDI.md) for complete instructions.

**Quick INDI Setup:**
```bash
# Install INDI (macOS with Homebrew)
brew install indi

# Start telescope controller in Serial Port mode
uv run lx200-telescope
# Note the port: /dev/ttys016

# Start INDI server
indiserver -v indi_lx200basic

# Configure in KStars or other INDI client
# Port: /dev/ttys016
```

**Why use INDI?**
- Multiple clients can connect simultaneously
- Better logging and diagnostics
- Works with PHD2, KStars, CCDciel, and more
- More robust error handling

## LX200 Protocol Support

The following LX200 commands are implemented:

### Position Queries
- `:GA#` - Get telescope altitude
- `:GZ#` - Get telescope azimuth  
- `:GR#` - Get RA (returns azimuth for alt-az mount)
- `:GD#` - Get DEC (returns altitude for alt-az mount)

### Control Commands
- `:Sr...#` - Set target RA (accepted but not used for slewing)
- `:Sd...#` - Set target DEC (accepted but not used for slewing)
- `:MS#` - Slew to target (acknowledged but no motion)
- `:Q#` - Halt all slewing
- `:RS#` - Resume slewing

### Information
- `:GVP#` - Get product name
- `:GVN#` - Get product number
- `:GVF#` - Get firmware version

## Position Display

The GUI continuously displays:
- **Altitude**: Current elevation angle (0° to 90°)
- **Azimuth**: Current rotation angle (0° to 360°)
- **Encoder Counts**: Raw encoder position for debugging

## Troubleshooting

### Device Won't Connect
- Make sure the device is powered on and advertising
- Try rescanning for devices
- Check that the device name contains "MotorSense"
- On macOS, ensure Bluetooth permissions are granted

### Serial Port Issues
- **Port not showing up**: Click the "Refresh" button to rescan
- **Permission denied**: On Linux, add your user to the dialout group:
  ```bash
  sudo usermod -a -G dialout $USER
  # Log out and back in for this to take effect
  ```
- **macOS serial permissions**: Grant terminal/app access in System Preferences → Security & Privacy
- **Virtual serial ports**: Use `socat` to create a virtual serial port pair for testing

### TCP Port Issues
- **Port already in use**: 
  - Check if another application is using port 4030
  - Try a different port number (e.g., 4031, 10001)
  - On macOS/Linux: `lsof -i :4030` to see what's using it

### Position Not Updating
- Verify both devices are connected (check the status)
- Check the log for encoder position updates (look for "ENC pos=" lines)
- Send a command to the device (e.g., "STATUS") to verify communication
- If you only see `:GR#` commands repeatedly, this is normal - the software is polling position

### Only Seeing :GR# Commands
This is **normal behavior**! Astronomy software polls telescope position constantly. You should see:
```
[LX200] Cmd: :GR# → 180*30:00#
[LX200] Cmd: :GR# → 180*30:15#
[LX200] Cmd: :GR# → 180*30:30#
```

To test full functionality:
1. Use the GUI's joystick controls to move the telescope
2. Watch the position values change
3. The astronomy software should follow the movement
4. Try a GOTO command from the software - you should see `:Sr`, `:Sd`, and `:MS#` commands

### Calibration Issues
- Ensure encoders are properly zeroed before setting position
- Try moving the telescope and verify encoder counts change
- Re-calibrate if the telescope was moved while disconnected

### LX200 Server Connection Issues
- Check that the port isn't already in use
- Verify firewall settings allow the connection
- Try a different port number
- Check the log for connection attempts from clients

## Architecture

The application consists of three main components:

1. **BLEAxisController**: Manages connection to a single encoder device
   - Handles BLE communication via Nordic UART Service (NUS)
   - Parses encoder position from device output
   - Converts encoder counts to angles using calibration

2. **LX200Server**: Dual-mode server implementing LX200 protocol
   - **TCP mode**: Listens for network connections from astronomy software
   - **Serial mode**: Communicates via RS-232 serial port
   - Processes LX200 commands
   - Returns current telescope position

3. **MainWindow**: Qt-based GUI
   - Device discovery and connection management
   - Real-time position display
   - Calibration controls
   - Server control and logging

## Future Enhancements

Possible improvements:
- [ ] Save/load calibration settings
- [ ] Motor control integration for actual slewing
- [ ] Position logging and tracking
- [ ] Alignment star system
- [ ] GOTO functionality with actual motor control
- [ ] Support for more LX200 commands
- [ ] Multiple client connections with broadcast
- [ ] Web-based interface option

## Technical Details

- **Python**: 3.11+
- **GUI Framework**: PySide6 (Qt6)
- **BLE Library**: Bleak
- **Async Framework**: asyncio + qasync
- **Protocol**: LX200 (Meade telescope command set)
- **BLE Service**: Nordic UART Service (NUS)

## License

Part of the mesh-blinker project.
