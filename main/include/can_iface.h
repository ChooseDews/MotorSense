#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "driver/twai.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef void (*can_iface_ble_sink_fn)(const char *s);
typedef void (*can_iface_rx_handler_fn)(const twai_message_t *msg);

// Initialize and start TWAI(CAN) receive task.
// - TXD: GPIO17 (to TJA1050 D)
// - RXD: GPIO18 (from TJA1050 R)
// - Bitrate: 500 kbit/s (adjust in code if your bus differs)
void can_iface_start(void);

// Transmit a CAN frame (best-effort). If ext_id is false, id must be <= 0x7FF.
// Data length must be <= 8.
bool can_iface_send(uint32_t id, bool ext_id, const uint8_t *data, size_t data_len);

// Read current TWAI controller status.
bool can_iface_get_status(twai_status_info_t *out_status);

// Optional: set a sink to also forward CAN logs over BLE.
// The sink should be safe to call from a FreeRTOS task context.
void can_iface_set_ble_sink(can_iface_ble_sink_fn sink);

// Best-effort write to the configured BLE sink (if set). Safe to call from task context.
void can_iface_ble_write(const char *s);

// Optional: set a handler that is called for each received CAN frame.
// The handler is invoked from the dedicated TWAI RX task context.
void can_iface_set_rx_handler(can_iface_rx_handler_fn handler);

#ifdef __cplusplus
}
#endif
