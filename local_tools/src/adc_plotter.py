#!/usr/bin/env python3
"""
Real-time ADC Plotter for Encoder Debugging
Plots all 4 ADC channels live from serial port
"""

import sys
import serial
import serial.tools.list_ports
import argparse
from collections import deque
import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets


class ADCPlotter:
    def __init__(self, port, baudrate=115200, buffer_size=10000):
        self.port = port
        self.baudrate = baudrate
        self.buffer_size = buffer_size
        
        # Use numpy arrays for better performance
        self.max_points = buffer_size
        self.data_idx = 0
        self.timestamps = np.zeros(self.max_points)
        self.gpio9 = np.zeros(self.max_points)
        self.gpio10 = np.zeros(self.max_points)
        self.gpio11 = np.zeros(self.max_points)
        self.gpio12 = np.zeros(self.max_points)
        
        self.start_time = None
        self.points_collected = 0
        
        # Serial connection
        self.serial = None
        self.running = False
        
        # Performance tracking
        self.last_update = 0
        self.update_interval = 16  # ~60 FPS
        
        # Setup GUI
        self.setup_gui()
        
    def setup_gui(self):
        """Initialize the PyQtGraph GUI"""
        self.app = QtWidgets.QApplication(sys.argv)
        self.win = pg.GraphicsLayoutWidget(show=True, title="ADC Live Plotter")
        self.win.resize(1600, 1000)
        self.win.setWindowTitle('ESP32-S3 ADC Encoder Debugger - High Speed')
        
        pg.setConfigOptions(antialias=False)  # Disable antialiasing for speed
        
        # Create 4 plots (one for each ADC channel)
        self.plot1 = self.win.addPlot(title="Encoder 1 - GPIO9 (A)", row=0, col=0)
        self.plot2 = self.win.addPlot(title="Encoder 1 - GPIO10 (B)", row=0, col=1)
        self.plot3 = self.win.addPlot(title="Encoder 0 - GPIO11 (A)", row=1, col=0)
        self.plot4 = self.win.addPlot(title="Encoder 0 - GPIO12 (B)", row=1, col=1)
        
        # Configure plots for better performance
        for plot in [self.plot1, self.plot2, self.plot3, self.plot4]:
            plot.setYRange(0, 4095)  # 12-bit ADC range
            plot.showGrid(x=True, y=True, alpha=0.3)
            plot.setLabel('left', 'ADC Value', units='counts')
            plot.setLabel('bottom', 'Time', units='ms')
            plot.enableAutoRange(axis='x', enable=True)
            plot.setAutoVisible(y=True)
            # Enable mouse interaction
            plot.setMouseEnabled(x=True, y=True)
            plot.enableAutoRange(axis='y', enable=False)
        
        # Create curve objects with optimizations
        self.curve1 = self.plot1.plot(pen=pg.mkPen(color=(0, 255, 0), width=1.5), 
                                      connect='finite', skipFiniteCheck=True)
        self.curve2 = self.plot2.plot(pen=pg.mkPen(color=(255, 255, 0), width=1.5),
                                      connect='finite', skipFiniteCheck=True)
        self.curve3 = self.plot3.plot(pen=pg.mkPen(color=(0, 255, 255), width=1.5),
                                      connect='finite', skipFiniteCheck=True)
        self.curve4 = self.plot4.plot(pen=pg.mkPen(color=(255, 0, 255), width=1.5),
                                      connect='finite', skipFiniteCheck=True)
        
        # Add status label
        self.status_label = QtWidgets.QLabel(f"Connecting to {self.port}...")
        self.status_label.setStyleSheet("font-size: 12pt; padding: 5px;")
        proxy = QtWidgets.QGraphicsProxyWidget()
        proxy.setWidget(self.status_label)
        self.win.addItem(proxy, row=2, col=0, colspan=2)
        
        # Timer for reading serial data - faster polling
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_data)
        self.timer.setInterval(5)  # Poll every 5ms
        
    def connect_serial(self):
        """Connect to the serial port"""
        try:
            self.serial = serial.Serial(self.port, self.baudrate, timeout=0.01)
            self.serial.reset_input_buffer()
            
            # Send command to start ADC streaming
            self.serial.write(b"ADC STREAM START\n")
            
            self.status_label.setText(f"Connected to {self.port} @ {self.baudrate} baud")
            self.running = True
            self.timer.start()
            return True
        except Exception as e:
            self.status_label.setText(f"ERROR: {e}")
            return False
    
    def parse_adc_line(self, line):
        """Parse ADC data line: ADC <timestamp> <v9> <v10> <v11> <v12>"""
        try:
            parts = line.strip().split()
            if len(parts) >= 6 and parts[0] == 'ADC':
                ts = float(parts[1]) / 1000.0  # Convert microseconds to milliseconds
                v9 = int(parts[2])
                v10 = int(parts[3])
                v11 = int(parts[4])
                v12 = int(parts[5])
                return ts, v9, v10, v11, v12
        except (ValueError, IndexError):
            pass
        return None
    
    def update_data(self):
        """Read serial data and update plots"""
        if not self.serial or not self.running:
            return
        
        import time
        current_time = time.time() * 1000  # ms
        
        try:
            # Read data from serial - limit processing per frame
            lines_processed = 0
            max_lines_per_update = 200
            
            while self.serial.in_waiting > 0 and lines_processed < max_lines_per_update:
                try:
                    line = self.serial.readline().decode('utf-8', errors='ignore')
                    result = self.parse_adc_line(line)
                    
                    if result:
                        ts, v9, v10, v11, v12 = result
                        
                        # Initialize start time on first data point
                        if self.start_time is None:
                            self.start_time = ts
                        
                        rel_time = ts - self.start_time
                        
                        # Circular buffer - overwrite old data
                        idx = self.data_idx % self.max_points
                        self.timestamps[idx] = rel_time
                        self.gpio9[idx] = v9
                        self.gpio10[idx] = v10
                        self.gpio11[idx] = v11
                        self.gpio12[idx] = v12
                        
                        self.data_idx += 1
                        self.points_collected = min(self.data_idx, self.max_points)
                        
                    lines_processed += 1
                except UnicodeDecodeError:
                    continue
            
            # Only update plots at specified interval to avoid lag
            if current_time - self.last_update >= self.update_interval and self.points_collected > 0:
                self.last_update = current_time
                
                # Get the valid data range
                if self.data_idx < self.max_points:
                    # Haven't filled buffer yet
                    t = self.timestamps[:self.points_collected]
                    d9 = self.gpio9[:self.points_collected]
                    d10 = self.gpio10[:self.points_collected]
                    d11 = self.gpio11[:self.points_collected]
                    d12 = self.gpio12[:self.points_collected]
                else:
                    # Buffer is full, show in circular order
                    idx = self.data_idx % self.max_points
                    t = np.concatenate([self.timestamps[idx:], self.timestamps[:idx]])
                    d9 = np.concatenate([self.gpio9[idx:], self.gpio9[:idx]])
                    d10 = np.concatenate([self.gpio10[idx:], self.gpio10[:idx]])
                    d11 = np.concatenate([self.gpio11[idx:], self.gpio11[:idx]])
                    d12 = np.concatenate([self.gpio12[idx:], self.gpio12[:idx]])
                
                # Downsample if we have too many points (for performance)
                downsample_factor = max(1, len(t) // 5000)
                if downsample_factor > 1:
                    t = t[::downsample_factor]
                    d9 = d9[::downsample_factor]
                    d10 = d10[::downsample_factor]
                    d11 = d11[::downsample_factor]
                    d12 = d12[::downsample_factor]
                
                # Update curves efficiently
                self.curve1.setData(t, d9)
                self.curve2.setData(t, d10)
                self.curve3.setData(t, d11)
                self.curve4.setData(t, d12)
                
                # Update status less frequently
                if self.points_collected > 0:
                    latest_idx = (self.data_idx - 1) % self.max_points
                    self.status_label.setText(
                        f"Connected | Points: {self.points_collected}/{self.max_points} | "
                        f"Rate: ~{lines_processed * 1000 / self.update_interval:.0f} Hz | "
                        f"G9={int(self.gpio9[latest_idx])} G10={int(self.gpio10[latest_idx])} "
                        f"G11={int(self.gpio11[latest_idx])} G12={int(self.gpio12[latest_idx])}"
                    )
                
        except Exception as e:
            print(f"Update error: {e}")
            import traceback
            traceback.print_exc()
    
    def closeEvent(self, event):
        """Handle window close"""
        self.cleanup()
        event.accept()
    
    def cleanup(self):
        """Stop streaming and close serial"""
        self.running = False
        if self.serial and self.serial.is_open:
            try:
                self.serial.write(b"ADC STREAM STOP\n")
                self.serial.close()
            except:
                pass
    
    def run(self):
        """Start the application"""
        if self.connect_serial():
            # Connect close event
            self.win.closeEvent = self.closeEvent
            
            # Start Qt event loop
            sys.exit(self.app.exec())
        else:
            print("Failed to connect to serial port")
            sys.exit(1)


def list_ports():
    """List available serial ports"""
    ports = serial.tools.list_ports.comports()
    print("\nAvailable serial ports:")
    for p in ports:
        print(f"  {p.device} - {p.description}")
    print()


def main():
    parser = argparse.ArgumentParser(description='Real-time ADC plotter for encoder debugging')
    parser.add_argument('port', nargs='?', help='Serial port (e.g., /dev/ttyUSB0 or COM3)')
    parser.add_argument('-b', '--baudrate', type=int, default=115200, help='Baud rate (default: 115200)')
    parser.add_argument('-s', '--buffer-size', type=int, default=10000, help='Buffer size (default: 10000)')
    parser.add_argument('-l', '--list', action='store_true', help='List available serial ports')
    
    args = parser.parse_args()
    
    if args.list:
        list_ports()
        return
    
    if not args.port:
        print("Error: Serial port required")
        print()
        list_ports()
        parser.print_help()
        sys.exit(1)
    
    print(f"Starting ADC Plotter on {args.port} @ {args.baudrate} baud")
    print("Press Ctrl+C or close the window to exit")
    
    plotter = ADCPlotter(args.port, args.baudrate, args.buffer_size)
    plotter.run()


if __name__ == '__main__':
    main()
