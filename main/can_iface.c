#include "can_iface.h"

#include <inttypes.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "driver/gpio.h"
#include "driver/twai.h"
#include "esp_err.h"
#include "esp_log.h"

#include "can_rpc.h"

#define TAG "CAN"

#define CAN_TX_GPIO GPIO_NUM_17
#define CAN_RX_GPIO GPIO_NUM_18

#ifndef CAN_RX_TASK_STACK
#define CAN_RX_TASK_STACK 8192
#endif

#ifndef CAN_RX_TASK_PRIO
#define CAN_RX_TASK_PRIO 5
#endif

static can_iface_ble_sink_fn s_ble_sink;
static can_iface_rx_handler_fn s_rx_handler;
static TaskHandle_t s_rx_task;

void can_iface_set_ble_sink(can_iface_ble_sink_fn sink)
{
    s_ble_sink = sink;
}

void can_iface_ble_write(const char *s)
{
    if (s_ble_sink == NULL || s == NULL) {
        return;
    }
    s_ble_sink(s);
}

void can_iface_set_rx_handler(can_iface_rx_handler_fn handler)
{
    s_rx_handler = handler;
}

static void can_logf(bool to_ble, const char *fmt, ...)
{
    char buf[192];

    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);

    // Monitor
    ESP_LOGI(TAG, "%s", buf);

    // BLE (best-effort, only if requested to avoid re-entrancy)
    if (to_ble && s_ble_sink != NULL) {
        s_ble_sink(buf);
        // Ensure BLE clients see message boundaries.
        s_ble_sink("\n");
    }
}

static void can_rx_task(void *arg)
{
    (void)arg;

    can_logf(false, "TWAI RX task started (TX=%d RX=%d, 500k)", (int)CAN_TX_GPIO, (int)CAN_RX_GPIO);

    while (true) {
        twai_message_t msg;
        esp_err_t err = twai_receive(&msg, pdMS_TO_TICKS(1000));
        if (err == ESP_OK) {
            if (s_rx_handler != NULL) {
                s_rx_handler(&msg);
            }

            char data_hex[3 * 8 + 1];
            size_t pos = 0;
            for (int i = 0; i < msg.data_length_code && i < 8; i++) {
                pos += (size_t)snprintf(data_hex + pos, sizeof(data_hex) - pos, "%02X ", msg.data[i]);
                if (pos >= sizeof(data_hex)) {
                    break;
                }
            }
            if (pos > 0 && pos < sizeof(data_hex)) {
                data_hex[pos - 1] = 0; // trim trailing space
            } else {
                data_hex[0] = 0;
            }

            const bool is_ext = (msg.flags & TWAI_MSG_FLAG_EXTD) != 0;
            const bool is_rtr = (msg.flags & TWAI_MSG_FLAG_RTR) != 0;

            // Don't spam logs (especially over BLE) with CAN-RPC chunk frames.
            // The CAN RPC module will print reconstructed responses instead.
            if (is_ext && can_rpc_is_id(msg.identifier)) {
                continue;
            }

            // RX frames are forwarded to BLE (safe, from dedicated task)
            can_logf(
                true,
                "RX %s id=0x%08" PRIX32 " dlc=%d %s data=[%s]",
                is_ext ? "EXT" : "STD",
                (uint32_t)msg.identifier,
                (int)msg.data_length_code,
                is_rtr ? "RTR" : "",
                data_hex);
            continue;
        }

        if (err == ESP_ERR_TIMEOUT) {
            continue;
        }

        can_logf(true, "twai_receive error: %s", esp_err_to_name(err));
        vTaskDelay(pdMS_TO_TICKS(250));
    }
}

bool can_iface_send(uint32_t id, bool ext_id, const uint8_t *data, size_t data_len)
{
    if (!ext_id && id > 0x7FF) {
        return false;
    }
    if (ext_id && id > 0x1FFFFFFF) {
        return false;
    }
    if (data_len > 8) {
        return false;
    }

    twai_message_t msg = {0};
    msg.identifier = id;
    msg.data_length_code = (uint8_t)data_len;
    if (ext_id) {
        msg.flags |= TWAI_MSG_FLAG_EXTD;
    }
    if (data != NULL && data_len > 0) {
        memcpy(msg.data, data, data_len);
    }

    esp_err_t err = twai_transmit(&msg, pdMS_TO_TICKS(100));
    if (err != ESP_OK) {
        // TX errors: monitor only (no BLE to avoid re-entrancy if called from BLE command context)
        can_logf(false, "TX %s id=0x%08" PRIX32 " dlc=%d ERR=%s", ext_id ? "EXT" : "STD", id, (int)data_len, esp_err_to_name(err));
        return false;
    }

    char data_hex[3 * 8 + 1];
    size_t pos = 0;
    for (size_t i = 0; i < data_len && i < 8; i++) {
        pos += (size_t)snprintf(data_hex + pos, sizeof(data_hex) - pos, "%02X ", (unsigned)data[i]);
        if (pos >= sizeof(data_hex)) {
            break;
        }
    }
    if (pos > 0 && pos < sizeof(data_hex)) {
        data_hex[pos - 1] = 0;
    } else {
        data_hex[0] = 0;
    }

    // TX success: monitor only (no BLE to avoid re-entrancy if called from BLE command context)
    can_logf(false, "TX %s id=0x%08" PRIX32 " dlc=%d data=[%s]", ext_id ? "EXT" : "STD", id, (int)data_len, data_hex);
    return true;
}

bool can_iface_get_status(twai_status_info_t *out_status)
{
    if (out_status == NULL) {
        return false;
    }
    esp_err_t err = twai_get_status_info(out_status);
    return err == ESP_OK;
}

void can_iface_start(void)
{
    if (s_rx_task != NULL) {
        return;
    }

    // 500 kbit/s is a common default; adjust if your bus differs.
    twai_general_config_t g_config = TWAI_GENERAL_CONFIG_DEFAULT(CAN_TX_GPIO, CAN_RX_GPIO, TWAI_MODE_NORMAL);
    twai_timing_config_t t_config = TWAI_TIMING_CONFIG_500KBITS();
    twai_filter_config_t f_config = TWAI_FILTER_CONFIG_ACCEPT_ALL();

    esp_err_t err = twai_driver_install(&g_config, &t_config, &f_config);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        ESP_LOGE(TAG, "twai_driver_install failed: %s", esp_err_to_name(err));
        return;
    }

    err = twai_start();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        ESP_LOGE(TAG, "twai_start failed: %s", esp_err_to_name(err));
        return;
    }

    xTaskCreate(can_rx_task, "can_rx", CAN_RX_TASK_STACK, NULL, CAN_RX_TASK_PRIO, &s_rx_task);
}
