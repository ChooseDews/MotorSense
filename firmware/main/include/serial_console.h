#pragma once

// Starts a task that reads commands from the console UART (USB serial)
// and dispatches them to the shared command handler.
void serial_console_start(void);
