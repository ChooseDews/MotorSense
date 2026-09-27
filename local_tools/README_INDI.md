# Using INDI with the LX200 Telescope Controller

INDI (Instrument Neutral Distributed Interface) is a popular protocol for controlling astronomy equipment. It supports LX200 telescopes and works great with this controller.

## What is INDI?

INDI is a cross-platform framework for controlling astronomical equipment. It's:
- More robust than direct LX200 connections
- Supports multiple clients connecting simultaneously
- Works with KStars, PHD2, CCDciel, and many other apps
- Available on Linux, macOS, and Windows

## Installation

### macOS
```bash
# Install INDI via Homebrew
brew install indi

# Or use KStars which includes INDI
brew install --cask kstars
```

### Linux (Ubuntu/Debian)
```bash
sudo apt-get install indi-bin kstars-bleeding
```

## Setup

### 1. Start the Telescope Controller
```bash
cd local_tools
uv run lx200-telescope
```

### 2. Select Serial Port Mode
- Choose "Serial Port" from the connection type dropdown
- Click "Start LX200 Server"
- Note the displayed port (e.g., `/dev/ttys016`)

### 3. Start INDI Server

**For Serial Port:**
```bash
# Start INDI with LX200 Basic driver
indiserver -v indi_lx200basic

# Or with full LX200 Classic driver
indiserver -v indi_lx200classic
```

**For TCP/IP (alternative):**
```bash
# Use the network driver
indiserver -v indi_lx200_network
```

### 4. Connect via INDI Client

#### Using KStars:
1. Open KStars
2. Go to Tools → Devices → Device Manager
3. Add a new device:
   - **For Serial**: Select "LX200 Basic" or "LX200 Classic"
   - **For Network**: Select "LX200 on Network"
4. Configure device:
   - Serial Port: `/dev/ttys016` (or whatever port is shown)
   - Baud Rate: 9600 (doesn't matter for virtual port)
5. Click "Start INDI"
6. Connect to the telescope

#### Using INDI Control Panel:
```bash
# Start INDI with a specific device
indiserver -v indi_lx200basic &

# Open control panel
indi_getprop

# Or use web interface
# Navigate to http://localhost:7624
```

## Connection Test

You can test the connection using INDI command-line tools:

```bash
# Get all properties
indi_getprop

# Get telescope coordinates
indi_getprop "LX200 Basic.EQUATORIAL_EOD_COORD.*"

# Set target coordinates and slew
indi_setprop "LX200 Basic.TARGET_EOD_COORD.RA=12.5"
indi_setprop "LX200 Basic.TARGET_EOD_COORD.DEC=45.2"
indi_setprop "LX200 Basic.TELESCOPE_SLEW.SLEW=On"
```

## Recommended Configuration

### Best Practice Setup:

1. **Use Serial Port Mode** in the telescope controller
   - More reliable than TCP for local connections
   - Native LX200 protocol support
   - Better compatibility with INDI drivers

2. **Use LX200 Basic Driver** in INDI
   - Simpler protocol
   - Better for custom implementations
   - Less overhead

3. **Connect via KStars or INDI Web Manager**
   - Visual interface
   - Easy device configuration
   - Multiple client support

## Common INDI Drivers for LX200

| Driver | Description | Best For |
|--------|-------------|----------|
| `indi_lx200basic` | Simple LX200 implementation | Custom controllers, testing |
| `indi_lx200classic` | Full Meade LX200 Classic | Full compatibility |
| `indi_lx200autostar` | Meade Autostar compatible | Advanced features |
| `indi_lx200_network` | LX200 over TCP/IP | Network connections |

## Troubleshooting

### INDI Can't Connect to Port
```bash
# Check if port exists
ls -l /dev/ttys*

# Test port manually
echo ":GR#" > /dev/ttys016
cat /dev/ttys016
```

### Permission Denied
```bash
# On Linux, add user to dialout group
sudo usermod -a -G dialout $USER
# Log out and back in

# On macOS, check permissions
ls -l /dev/ttys016
```

### INDI Server Not Starting
```bash
# Check if INDI is installed
which indiserver

# List available drivers
ls /usr/local/share/indi/

# Start with verbose logging
indiserver -v -m 100 indi_lx200basic
```

### Connection Drops
- Check the telescope controller log for errors
- Ensure the virtual serial port hasn't been closed
- Restart both INDI server and telescope controller

## Advanced: Remote Connections

### Setup INDI Server on One Machine
```bash
# Start INDI server (accessible remotely)
indiserver -v -p 7624 indi_lx200basic
```

### Connect from Another Machine
```bash
# Point client to remote INDI server
indi_getprop -h 192.168.1.100 -p 7624
```

### Using with PHD2 (Autoguiding)
1. Start INDI server with LX200 driver
2. In PHD2:
   - Select "INDI Mount" as mount type
   - Set INDI host: localhost
   - Set INDI port: 7624
   - Select "LX200 Basic" device

## Why Use INDI?

**Advantages over direct LX200:**
- ✅ **Multiple Clients**: Connect KStars, PHD2, and imaging software simultaneously
- ✅ **Better Logging**: See all commands and responses in INDI logs
- ✅ **Property System**: More structured than raw LX200 commands
- ✅ **Web Interface**: Control remotely via browser
- ✅ **Standardized**: Works with hundreds of astronomy applications
- ✅ **Robust**: Better error handling and reconnection logic

**When to use direct LX200:**
- Simple testing
- Stellarium (has built-in LX200 support)
- SkySafari direct connection
- Minimal setup required

## Example Workflow with KStars

1. **Start Telescope Controller**
   ```bash
   uv run lx200-telescope
   # Select Serial Port, start server
   # Note: /dev/ttys016
   ```

2. **Configure KStars**
   - Tools → Ekos → Setup
   - Mount: LX200 Basic
   - Port: /dev/ttys016
   - Start INDI

3. **Connect and Use**
   - Click Connect in Ekos
   - Point & click on sky objects
   - Ekos will send GOTO commands
   - Watch encoders move to position!

## Logging and Debugging

Enable verbose logging to see all INDI/LX200 communication:

```bash
# Start with maximum verbosity
indiserver -vvv -m 100 indi_lx200basic 2>&1 | tee indi.log

# Watch the log in real-time
tail -f indi.log
```

You'll see all LX200 commands being sent/received through INDI's framework.

## Resources

- INDI Library: https://www.indilib.org/
- INDI Drivers: https://github.com/indilib/indi
- KStars/Ekos: https://edu.kde.org/kstars/
- INDI Forum: https://www.indilib.org/forum.html
