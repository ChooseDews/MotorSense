#pragma once

#include <stdint.h>
#include <stdbool.h>

typedef void (*commands_reply_fn)(const char *s);

typedef enum {
	COMMANDS_INDICATOR_OFF = 0,
	COMMANDS_INDICATOR_JOINABLE,
	COMMANDS_INDICATOR_CONNECTED,
} commands_indicator_state_t;

// Initializes peripherals used by commands (LED strip + H-bridge GPIO) and restores
// persisted settings when available.
void commands_init(void);

// Controls the indicator LED behavior (second LED on the strip).
void commands_indicator_set_state(commands_indicator_state_t state);

// Notifies the indicator LED that a message/command was received.
// Causes a brief activity flash overlay (timer resets on each call).
void commands_indicator_notify_activity(void);

// Publishes BLE state so the STATUS command can report it.
void commands_status_set_ble_adv_joinable(bool joinable);
void commands_status_set_ble_connected(bool connected, uint16_t conn_id);
void commands_status_set_ble_notify_enabled(bool enabled);

// Parses and executes a single command line (in-place mutable buffer is ok).
void commands_handle_line(char *line, commands_reply_fn reply);
