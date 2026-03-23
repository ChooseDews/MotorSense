/*
 * Minimal BLE "serial" for ESP-IDF (BLE-only targets like ESP32-S3/C3/C5).
 *
 * Implements a Nordic UART Service (NUS)-compatible GATT server:
 * - RX characteristic: Write / Write Without Response
 * - TX characteristic: Notify (client enables CCCD)
 *
 * Send newline-terminated commands to RX; device replies on TX.
 */

#include <ctype.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/stream_buffer.h"

#include "driver/gpio.h"
#include "esp_idf_version.h"
#if ESP_IDF_VERSION_MAJOR >= 5
#include "esp_adc/adc_oneshot.h"
#else
#include "driver/adc.h"
#endif
#include "esp_bt.h"
#include "esp_bt_defs.h"
#include "esp_bt_main.h"
#include "esp_gap_ble_api.h"
#include "esp_gatt_common_api.h"
#include "esp_gatts_api.h"
#include "esp_err.h"
#include "esp_log.h"
#include "nvs_flash.h"

#include "commands.h"
#include "can_rpc.h"
#include "can_iface.h"
#include "serial_console.h"
#include "quadrature_encoder.h"
#include "adc_inputs.h"

#define BLE_REPLY_QUEUE_SIZE 8
#define BLE_REPLY_MAX_LEN 256

#define BLE_CMD_QUEUE_SIZE 8
#define BLE_CMD_MAX_LEN 256

#define TAG "BLE_UART"

#define GPIO_BUTTON 0

#define DEVICE_NAME_BASE "MotorSense Caboose"

static char s_device_name[32];

static void ble_init_device_name(void)
{
    // Append the node ID so multiple units are easy to distinguish when scanning.
    const uint8_t node_id = can_rpc_get_node_id();
    int n = snprintf(s_device_name, sizeof(s_device_name), "%s-%u", DEVICE_NAME_BASE, (unsigned)node_id);
    if (n <= 0) {
        // Fallback
        strncpy(s_device_name, DEVICE_NAME_BASE, sizeof(s_device_name));
        s_device_name[sizeof(s_device_name) - 1] = 0;
    }
}

// NUS UUIDs (little-endian byte order for ESP-IDF raw UUID arrays)
static const uint8_t NUS_SERVICE_UUID[16] = {0x9E, 0xCA, 0xDC, 0x24, 0x0E, 0xE5, 0xA9, 0xE0, 0x93, 0xF3, 0xA3, 0xB5, 0x01, 0x00, 0x40, 0x6E};
static const uint8_t NUS_RX_UUID[16]      = {0x9E, 0xCA, 0xDC, 0x24, 0x0E, 0xE5, 0xA9, 0xE0, 0x93, 0xF3, 0xA3, 0xB5, 0x02, 0x00, 0x40, 0x6E};
static const uint8_t NUS_TX_UUID[16]      = {0x9E, 0xCA, 0xDC, 0x24, 0x0E, 0xE5, 0xA9, 0xE0, 0x93, 0xF3, 0xA3, 0xB5, 0x03, 0x00, 0x40, 0x6E};

enum {
    NUS_IDX_SVC,
    NUS_IDX_CHAR_RX,
    NUS_IDX_CHAR_VAL_RX,
    NUS_IDX_CHAR_TX,
    NUS_IDX_CHAR_VAL_TX,
    NUS_IDX_CHAR_CFG_TX,
    NUS_IDX_NB,
};

enum {
    DIS_IDX_SVC,
    DIS_IDX_CHAR_MANUF,
    DIS_IDX_CHAR_VAL_MANUF,
    DIS_IDX_NB,
};

#define IDX_NB (NUS_IDX_NB + DIS_IDX_NB)

// Map legacy names to new structure for existing code compatibility
#define IDX_CHAR_VAL_TX NUS_IDX_CHAR_VAL_TX
#define IDX_CHAR_VAL_RX NUS_IDX_CHAR_VAL_RX
#define IDX_CHAR_CFG_TX NUS_IDX_CHAR_CFG_TX

static uint16_t handle_table[IDX_NB];

static esp_gatt_if_t s_gatts_if = ESP_GATT_IF_NONE;
static uint16_t s_conn_id;
static bool s_connected;
static bool s_tx_notify_enabled;

static bool s_adv_data_ready;
static bool s_scan_rsp_ready;

static QueueHandle_t s_ble_reply_queue;
static TaskHandle_t s_ble_reply_task;

static QueueHandle_t s_ble_cmd_queue;
static TaskHandle_t s_ble_cmd_task;

static StreamBufferHandle_t s_rx_stream;
static TaskHandle_t s_process_task;

static TaskHandle_t s_ble_adc_task;
static TaskHandle_t s_ble_enc_task;

typedef struct {
    uint32_t count;
    double mean;
    double m2;
    int last;
} adc_stats_t;

static void adc_stats_reset(adc_stats_t *s)
{
    if (s == NULL) {
        return;
    }
    s->count = 0;
    s->mean = 0.0;
    s->m2 = 0.0;
    s->last = 0;
}

static void adc_stats_push(adc_stats_t *s, int x)
{
    if (s == NULL) {
        return;
    }
    s->last = x;
    s->count++;
    double dx = (double)x - s->mean;
    s->mean += dx / (double)s->count;
    double dx2 = (double)x - s->mean;
    s->m2 += dx * dx2;
}

static void ble_cmd_enqueue(const char *line)
{
    if (line == NULL || s_ble_cmd_queue == NULL) {
        return;
    }

    char buf[BLE_CMD_MAX_LEN];
    size_t len = strlen(line);
    if (len >= BLE_CMD_MAX_LEN) {
        len = BLE_CMD_MAX_LEN - 1;
    }
    memcpy(buf, line, len);
    buf[len] = 0;

    (void)xQueueSend(s_ble_cmd_queue, buf, 0);
}

static uint8_t s_manuf_data[] = "DewsReid Solutions";

static esp_ble_adv_data_t s_adv_data = {
    .set_scan_rsp = false,
    .include_name = true,
    .include_txpower = false,
    .service_uuid_len = sizeof(NUS_SERVICE_UUID),
    .p_service_uuid = (uint8_t *)NUS_SERVICE_UUID,
    .flag = ESP_BLE_ADV_FLAG_GEN_DISC | ESP_BLE_ADV_FLAG_BREDR_NOT_SPT,
};

static esp_ble_adv_data_t s_scan_rsp_data = {
    .set_scan_rsp = true,
    .include_name = false,
    .include_txpower = true,
    .manufacturer_len = sizeof(s_manuf_data),
    .p_manufacturer_data = s_manuf_data,
    .service_uuid_len = 0,
    .p_service_uuid = NULL,
    .flag = ESP_BLE_ADV_FLAG_GEN_DISC | ESP_BLE_ADV_FLAG_BREDR_NOT_SPT,
};

static esp_ble_adv_params_t s_adv_params = {
    .adv_int_min = 0x20,
    .adv_int_max = 0x40,
    .adv_type = ADV_TYPE_IND,
    .own_addr_type = BLE_ADDR_TYPE_PUBLIC,
    .channel_map = ADV_CHNL_ALL,
    .adv_filter_policy = ADV_FILTER_ALLOW_SCAN_ANY_CON_ANY,
};

static void ble_uart_send_bytes(const uint8_t *data, size_t len)
{
    if (data == NULL || len == 0) {
        return;
    }
    if (s_gatts_if == ESP_GATT_IF_NONE || !s_connected || !s_tx_notify_enabled) {
        return;
    }

    esp_err_t err = esp_ble_gatts_send_indicate(
        s_gatts_if,
        s_conn_id,
        handle_table[IDX_CHAR_VAL_TX],
        (uint16_t)len,
        (uint8_t *)data,
        false);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "send_indicate failed: %s", esp_err_to_name(err));
    }
}

static void ble_uart_send_str(const char *s)
{
    if (s == NULL || s_ble_reply_queue == NULL) {
        return;
    }

    // Chunk long replies into multiple queued packets instead of truncating.
    // This keeps the BLE reply queue item size fixed while allowing arbitrarily
    // long outputs (e.g., HELP text).
    const char *p = s;
    while (*p != 0) {
        char buf[BLE_REPLY_MAX_LEN];
        size_t chunk_len = strnlen(p, BLE_REPLY_MAX_LEN - 1);
        memcpy(buf, p, chunk_len);
        buf[chunk_len] = 0;

        // Queue for deferred sending (non-blocking)
        if (xQueueSend(s_ble_reply_queue, buf, 0) != pdTRUE) {
            // If we can't enqueue, drop the remainder to avoid blocking.
            break;
        }
        p += chunk_len;
    }
}

static void ble_reply_task_fn(void *arg)
{
    (void)arg;
    char buf[BLE_REPLY_MAX_LEN];

    while (true) {
        if (xQueueReceive(s_ble_reply_queue, buf, portMAX_DELAY) == pdTRUE) {
            // Send from a safe task context (not inside GATTS event handler)
            ble_uart_send_bytes((const uint8_t *)buf, strlen(buf));
        }
    }
}

static void ble_cmd_task_fn(void *arg)
{
    (void)arg;
    char line[BLE_CMD_MAX_LEN];

    while (true) {
        if (xQueueReceive(s_ble_cmd_queue, line, portMAX_DELAY) == pdTRUE) {
            commands_handle_line(line, ble_uart_send_str);
        }
    }
}

static void ble_process_task_fn(void *arg)
{
    (void)arg;
    uint8_t rx_byte;
    char line_buf[256];
    size_t line_len = 0;

    while (true) {
        size_t n = xStreamBufferReceive(s_rx_stream, &rx_byte, 1, portMAX_DELAY);
        if (n > 0) {
            char c = (char)rx_byte;
            if (c == '\n' || c == '\r') {
                if (line_len > 0) {
                    line_buf[line_len] = 0;
                    if (s_ble_cmd_queue != NULL) {
                        if (xQueueSend(s_ble_cmd_queue, line_buf, 0) != pdTRUE) {
                            ble_uart_send_str("ERR busy\n");
                        }
                    }
                    line_len = 0;
                }
            } else {
                if (line_len + 1 < sizeof(line_buf)) {
                    line_buf[line_len++] = c;
                } else {
                    line_len = 0;
                    ble_uart_send_str("ERR line too long\n");
                }
            }
        }
    }
}

static void ble_button_task_fn(void *arg)
{
    (void)arg;
    gpio_config_t io_conf = {
        .intr_type = GPIO_INTR_DISABLE,
        .mode = GPIO_MODE_INPUT,
        .pin_bit_mask = (1ULL << GPIO_BUTTON),
        .pull_down_en = 0,
        .pull_up_en = 1,
    };
    gpio_config(&io_conf);

    int last_state = 1; // Pulled up

    while (true) {
        int current_state = gpio_get_level(GPIO_BUTTON);
        if (last_state == 1 && current_state == 0) {
            // Falling edge
            vTaskDelay(pdMS_TO_TICKS(50)); // Debounce
            if (gpio_get_level(GPIO_BUTTON) == 0) {
                // Still pressed
                ESP_LOGI(TAG, "Button pressed: Running CAN TEST");
                if (s_ble_cmd_queue != NULL) {
                    ble_cmd_enqueue("CAN TEST");
                }

                // Wait for release
                while (gpio_get_level(GPIO_BUTTON) == 0) {
                    vTaskDelay(pdMS_TO_TICKS(100));
                }
            }
        }
        last_state = current_state;
        vTaskDelay(pdMS_TO_TICKS(50));
    }
}

static void ble_adc_task_fn(void *arg)
{
    (void)arg;

    if (!adc_inputs_init()) {
        ESP_LOGE(TAG, "adc_inputs_init failed; ADC stats task exiting");
        vTaskDelete(NULL);
        return;
    }

    // Sample over a window, then print stats (ESP_LOGI only; no BLE output).
    // Keep sampling very modest to avoid impacting the encoder task.
    const TickType_t report_period_ticks = pdMS_TO_TICKS(2000);
    const uint32_t samples_per_period = 16;
    const TickType_t sample_delay_ticks = pdMS_TO_TICKS(2000 / samples_per_period);
    const TickType_t min_sample_delay_ticks = (sample_delay_ticks == 0) ? 1 : sample_delay_ticks;

    adc_stats_t s11;
    adc_stats_t s12;
    adc_stats_reset(&s11);
    adc_stats_reset(&s12);

    while (true) {
        // Only produce BLE output when a client is actually listening.
        if (!s_connected || !s_tx_notify_enabled) {
            vTaskDelay(report_period_ticks);
            continue;
        }

        adc_stats_reset(&s11);
        adc_stats_reset(&s12);

        TickType_t start = xTaskGetTickCount();
        for (uint32_t i = 0; i < samples_per_period; i++) {
            int v11 = 0;
            int v12 = 0;

            (void)adc_inputs_read_gpio11_gpio12(&v11, &v12);
            adc_stats_push(&s11, v11);
            adc_stats_push(&s12, v12);
            vTaskDelay(min_sample_delay_ticks);
        }

        double var11 = 0.0;
        double var12 = 0.0;
        if (s11.count > 1) {
            var11 = s11.m2 / (double)(s11.count - 1);
        }
        if (s12.count > 1) {
            var12 = s12.m2 / (double)(s12.count - 1);
        }

        // Raw ADC counts (unitless). Mean/variance over the last sample window.
        ESP_LOGI(TAG,
             "ADC11 last=%d avg=%.1f var=%.1f | ADC12 last=%d avg=%.1f var=%.1f",
             s11.last,
             s11.mean,
             var11,
             s12.last,
             s12.mean,
             var12);

        // Keep overall period close to 0.5s even if sampling overruns slightly.
        TickType_t elapsed = xTaskGetTickCount() - start;
        if (elapsed < report_period_ticks) {
            vTaskDelay(report_period_ticks - elapsed);
        }
    }
}

static void ble_enc_task_fn(void *arg)
{
    (void)arg;

    int32_t last_sent0 = INT32_MIN;
    int32_t last_sent1 = INT32_MIN;
    const TickType_t period = pdMS_TO_TICKS(50); // <= 20Hz updates

    while (true) {
        if (s_connected && s_tx_notify_enabled && s_ble_reply_queue != NULL) {
            const int32_t pos0 = quadrature_encoder_get_position_index(0);
            const int32_t pos1 = quadrature_encoder_get_position_index(1);
            if (pos0 != last_sent0 || pos1 != last_sent1) {
                char out[64];
                snprintf(out, sizeof(out), "ENC pos0=%ld pos1=%ld\n", (long)pos0, (long)pos1);
                ble_uart_send_str(out);
                last_sent0 = pos0;
                last_sent1 = pos1;
            }
        }
        vTaskDelay(period);
    }
}

static void gap_event_handler(esp_gap_ble_cb_event_t event, esp_ble_gap_cb_param_t *param)
{
    switch (event) {
    case ESP_GAP_BLE_ADV_DATA_SET_COMPLETE_EVT:
    case ESP_GAP_BLE_SCAN_RSP_DATA_SET_COMPLETE_EVT:
        if (event == ESP_GAP_BLE_ADV_DATA_SET_COMPLETE_EVT) {
            s_adv_data_ready = true;
        } else {
            s_scan_rsp_ready = true;
        }
        if (s_adv_data_ready && s_scan_rsp_ready) {
            esp_ble_gap_start_advertising(&s_adv_params);
        }
        break;
    case ESP_GAP_BLE_ADV_START_COMPLETE_EVT:
        if (param->adv_start_cmpl.status != ESP_BT_STATUS_SUCCESS) {
            ESP_LOGE(TAG, "Advertising start failed, status=%d", param->adv_start_cmpl.status);
            commands_status_set_ble_adv_joinable(false);
        } else {
            ESP_LOGI(TAG, "Advertising started");
            commands_indicator_set_state(COMMANDS_INDICATOR_JOINABLE);
            commands_status_set_ble_adv_joinable(true);
        }
        break;
    case ESP_GAP_BLE_ADV_STOP_COMPLETE_EVT:
        ESP_LOGI(TAG, "Advertising stopped, status=%d", param->adv_stop_cmpl.status);
        commands_status_set_ble_adv_joinable(false);
        break;
    default:
        break;
    }
}

static void handle_rx_bytes(const uint8_t *data, uint16_t len)
{
    if (s_rx_stream != NULL && len > 0) {
        size_t sent = xStreamBufferSend(s_rx_stream, data, len, 0);
        if (sent != len) {
            ESP_LOGW(TAG, "RX stream buffer full, dropped %d bytes", len - sent);
        }
    }
}

static const uint16_t primary_service_uuid = ESP_GATT_UUID_PRI_SERVICE;
static const uint16_t character_declaration_uuid = ESP_GATT_UUID_CHAR_DECLARE;
static const uint16_t client_characteristic_config_uuid = ESP_GATT_UUID_CHAR_CLIENT_CONFIG;
static const uint16_t device_info_svc_uuid = ESP_GATT_UUID_DEVICE_INFO_SVC;
static const uint16_t manufacturer_name_uuid = ESP_GATT_UUID_MANU_NAME;

static const esp_gatt_perm_t rx_perm = ESP_GATT_PERM_WRITE;
static const esp_gatt_perm_t tx_perm = ESP_GATT_PERM_READ;
static const esp_gatt_perm_t cccd_perm = ESP_GATT_PERM_READ | ESP_GATT_PERM_WRITE;
static const esp_gatt_perm_t manuf_perm = ESP_GATT_PERM_READ;

static const uint8_t rx_prop = ESP_GATT_CHAR_PROP_BIT_WRITE | ESP_GATT_CHAR_PROP_BIT_WRITE_NR;
static const uint8_t tx_prop = ESP_GATT_CHAR_PROP_BIT_NOTIFY | ESP_GATT_CHAR_PROP_BIT_READ;
static const uint8_t manuf_prop = ESP_GATT_CHAR_PROP_BIT_READ;

static uint8_t tx_value[1] = {0};
static uint8_t rx_value[1] = {0};
static uint8_t cccd_value[2] = {0x00, 0x00};
static uint8_t manuf_value[] = "DewsReid Solutions";

static const esp_gatts_attr_db_t gatt_db_nus[NUS_IDX_NB] = {
    // Service Declaration
    [NUS_IDX_SVC] =
        {{ESP_GATT_AUTO_RSP}, {ESP_UUID_LEN_16, (uint8_t *)&primary_service_uuid, ESP_GATT_PERM_READ,
                                ESP_UUID_LEN_128, ESP_UUID_LEN_128, (uint8_t *)NUS_SERVICE_UUID}},

    // RX Characteristic Declaration
    [NUS_IDX_CHAR_RX] =
        {{ESP_GATT_AUTO_RSP}, {ESP_UUID_LEN_16, (uint8_t *)&character_declaration_uuid, ESP_GATT_PERM_READ,
                                sizeof(rx_prop), sizeof(rx_prop), (uint8_t *)&rx_prop}},
    // RX Characteristic Value
    [NUS_IDX_CHAR_VAL_RX] =
        {{ESP_GATT_RSP_BY_APP}, {ESP_UUID_LEN_128, (uint8_t *)NUS_RX_UUID, rx_perm,
                                    sizeof(rx_value), sizeof(rx_value), rx_value}},

    // TX Characteristic Declaration
    [NUS_IDX_CHAR_TX] =
        {{ESP_GATT_AUTO_RSP}, {ESP_UUID_LEN_16, (uint8_t *)&character_declaration_uuid, ESP_GATT_PERM_READ,
                                sizeof(tx_prop), sizeof(tx_prop), (uint8_t *)&tx_prop}},
    // TX Characteristic Value
    [NUS_IDX_CHAR_VAL_TX] =
        {{ESP_GATT_AUTO_RSP}, {ESP_UUID_LEN_128, (uint8_t *)NUS_TX_UUID, tx_perm,
                                sizeof(tx_value), sizeof(tx_value), tx_value}},
    // TX CCCD
    [NUS_IDX_CHAR_CFG_TX] =
        {{ESP_GATT_RSP_BY_APP}, {ESP_UUID_LEN_16, (uint8_t *)&client_characteristic_config_uuid, cccd_perm,
                                    sizeof(cccd_value), sizeof(cccd_value), cccd_value}},
};

static const esp_gatts_attr_db_t gatt_db_dis[DIS_IDX_NB] = {
    // Device Information Service
    [DIS_IDX_SVC] =
        {{ESP_GATT_AUTO_RSP}, {ESP_UUID_LEN_16, (uint8_t *)&primary_service_uuid, ESP_GATT_PERM_READ,
                                ESP_UUID_LEN_16, ESP_UUID_LEN_16, (uint8_t *)&device_info_svc_uuid}},
    
    // Manufacturer Name Declaration
    [DIS_IDX_CHAR_MANUF] =
        {{ESP_GATT_AUTO_RSP}, {ESP_UUID_LEN_16, (uint8_t *)&character_declaration_uuid, ESP_GATT_PERM_READ,
                                sizeof(manuf_prop), sizeof(manuf_prop), (uint8_t *)&manuf_prop}},
    
    // Manufacturer Name Value
    [DIS_IDX_CHAR_VAL_MANUF] =
        {{ESP_GATT_AUTO_RSP}, {ESP_UUID_LEN_16, (uint8_t *)&manufacturer_name_uuid, manuf_perm,
                                sizeof(manuf_value), sizeof(manuf_value), manuf_value}},
};

static void gatts_event_handler(esp_gatts_cb_event_t event, esp_gatt_if_t gatts_if, esp_ble_gatts_cb_param_t *param)
{
    switch (event) {
    case ESP_GATTS_REG_EVT: {
        ESP_LOGI(TAG, "GATTS_REG_EVT status=%d app_id=%d", param->reg.status, param->reg.app_id);

        s_gatts_if = gatts_if;

        esp_ble_gap_set_device_name(s_device_name);
        ESP_ERROR_CHECK(esp_ble_gap_config_adv_data(&s_adv_data));
        ESP_ERROR_CHECK(esp_ble_gap_config_adv_data(&s_scan_rsp_data));

        // Create NUS table first
        ESP_ERROR_CHECK(esp_ble_gatts_create_attr_tab(gatt_db_nus, gatts_if, NUS_IDX_NB, 0));
        break;
    }
    case ESP_GATTS_CREAT_ATTR_TAB_EVT: {
        if (param->add_attr_tab.status != ESP_GATT_OK) {
            ESP_LOGE(TAG, "create_attr_tab failed, status=%d", param->add_attr_tab.status);
            break;
        }

        if (param->add_attr_tab.num_handle == NUS_IDX_NB) {
            ESP_LOGI(TAG, "NUS GATT table created");
            memcpy(handle_table, param->add_attr_tab.handles, NUS_IDX_NB * sizeof(uint16_t));
            
            ESP_ERROR_CHECK(esp_ble_gatts_start_service(handle_table[NUS_IDX_SVC]));

            // Now create DIS table (using service instance ID 1 to be distinct, though likely not strictly needed with diff UUIDs)
            ESP_ERROR_CHECK(esp_ble_gatts_create_attr_tab(gatt_db_dis, gatts_if, DIS_IDX_NB, 1));

        } else if (param->add_attr_tab.num_handle == DIS_IDX_NB) {
             ESP_LOGI(TAG, "DIS GATT table created");
             memcpy(&handle_table[NUS_IDX_NB], param->add_attr_tab.handles, DIS_IDX_NB * sizeof(uint16_t));
             ESP_ERROR_CHECK(esp_ble_gatts_start_service(handle_table[NUS_IDX_NB + DIS_IDX_SVC]));
        }
        break;
    }
    case ESP_GATTS_CONNECT_EVT:
        s_conn_id = param->connect.conn_id;
        s_connected = true;
        s_tx_notify_enabled = false;
        ESP_LOGI(TAG, "Connected: conn_id=%d", s_conn_id);
        commands_indicator_set_state(COMMANDS_INDICATOR_CONNECTED);
        commands_status_set_ble_connected(true, s_conn_id);
        commands_status_set_ble_notify_enabled(false);
        commands_status_set_ble_adv_joinable(false);
        esp_ble_gap_stop_advertising();
        break;
    case ESP_GATTS_DISCONNECT_EVT:
        ESP_LOGI(TAG, "Disconnected; restarting advertising");
        s_connected = false;
        s_tx_notify_enabled = false;
        // s_rx_line_len reset managed by task logic if we wanted, but simple accumulation OK.
        commands_status_set_ble_connected(false, 0);
        commands_status_set_ble_notify_enabled(false);
        commands_indicator_set_state(COMMANDS_INDICATOR_JOINABLE);
        esp_ble_gap_start_advertising(&s_adv_params);
        break;
    case ESP_GATTS_WRITE_EVT: {
        const uint16_t handle = param->write.handle;

        if (handle == handle_table[IDX_CHAR_CFG_TX]) {
            // CCCD write: 0x0001 enables notifications.
            if (param->write.len == 2) {
                uint16_t cccd = (uint16_t)(param->write.value[1] << 8) | param->write.value[0];
                s_tx_notify_enabled = (cccd & 0x0001) != 0;
                ESP_LOGI(TAG, "TX notify %s", s_tx_notify_enabled ? "ENABLED" : "DISABLED");
                commands_status_set_ble_notify_enabled(s_tx_notify_enabled);
                if (s_tx_notify_enabled) {
                    ble_uart_send_str("READY\n");
                }
            }

            if (param->write.need_rsp) {
                esp_gatt_rsp_t rsp = {0};
                rsp.attr_value.handle = handle;
                rsp.attr_value.len = param->write.len;
                memcpy(rsp.attr_value.value, param->write.value, param->write.len);
                esp_ble_gatts_send_response(gatts_if, param->write.conn_id, param->write.trans_id, ESP_GATT_OK, &rsp);
            }
            break;
        }

        if (handle == handle_table[IDX_CHAR_VAL_RX]) {
            // Process commands with deferred reply to avoid re-entrancy
            handle_rx_bytes(param->write.value, param->write.len);
            if (param->write.need_rsp) {
                esp_ble_gatts_send_response(gatts_if, param->write.conn_id, param->write.trans_id, ESP_GATT_OK, NULL);
            }
            break;
        }

        if (param->write.need_rsp) {
            esp_ble_gatts_send_response(gatts_if, param->write.conn_id, param->write.trans_id, ESP_GATT_OK, NULL);
        }
        break;
    }
    case ESP_GATTS_READ_EVT:
        // AUTO_RSP handles TX value reads; CCCD reads are RSP_BY_APP so just OK.
        if (param->read.handle == handle_table[IDX_CHAR_CFG_TX]) {
            esp_gatt_rsp_t rsp = {0};
            rsp.attr_value.handle = param->read.handle;
            rsp.attr_value.len = 2;
            rsp.attr_value.value[0] = s_tx_notify_enabled ? 0x01 : 0x00;
            rsp.attr_value.value[1] = 0x00;
            esp_ble_gatts_send_response(gatts_if, param->read.conn_id, param->read.trans_id, ESP_GATT_OK, &rsp);
        }
        break;
    case ESP_GATTS_MTU_EVT:
        ESP_LOGI(TAG, "MTU updated: %d", param->mtu.mtu);
        break;
    default:
        break;
    }
}

void app_main(void)
{
    ESP_LOGI(TAG, "BUILD: %s %s", __DATE__, __TIME__);

    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ESP_ERROR_CHECK(nvs_flash_init());
    }

    quadrature_encoder_start();

    commands_init();
    serial_console_start();
    can_iface_set_ble_sink(ble_uart_send_str);
    can_iface_start();
    can_rpc_init();
    ble_init_device_name();

    // Create BLE reply queue and task to avoid re-entrancy
    s_ble_reply_queue = xQueueCreate(BLE_REPLY_QUEUE_SIZE, BLE_REPLY_MAX_LEN);
    if (s_ble_reply_queue == NULL) {
        ESP_LOGE(TAG, "Failed to create BLE reply queue");
    } else {
        xTaskCreate(ble_reply_task_fn, "ble_reply", 3072, NULL, 5, &s_ble_reply_task);
    }

    // Periodic encoder position output over BLE
    xTaskCreate(ble_enc_task_fn, "ble_enc", 3072, NULL, 5, &s_ble_enc_task);

    // Command execution task (keeps heavy command parsing off the BLE RX task stack).
    s_ble_cmd_queue = xQueueCreate(BLE_CMD_QUEUE_SIZE, BLE_CMD_MAX_LEN);
    if (s_ble_cmd_queue == NULL) {
        ESP_LOGE(TAG, "Failed to create BLE cmd queue");
    } else {
        xTaskCreate(ble_cmd_task_fn, "ble_cmd", 8192, NULL, 5, &s_ble_cmd_task);
    }

    // Create RX stream buffer and processing task
    s_rx_stream = xStreamBufferCreate(1024, 1);
    if (s_rx_stream == NULL) {
        ESP_LOGE(TAG, "Failed to create RX stream buffer");
    } else {
        // RX collection only (command parsing is done in ble_cmd task)
        xTaskCreate(ble_process_task_fn, "ble_rx", 4096, NULL, 5, &s_process_task);
    }
    
    // Create button task
    xTaskCreate(ble_button_task_fn, "ble_button", 4096, NULL, 5, NULL);

    // Periodic ADC stats (ESP_LOGI only; no BLE output)
    xTaskCreate(ble_adc_task_fn, "ble_adc", 4096, NULL, 5, &s_ble_adc_task);

    // BLE-only targets should release Classic BT memory.
    ESP_ERROR_CHECK(esp_bt_controller_mem_release(ESP_BT_MODE_CLASSIC_BT));

    esp_bt_controller_config_t bt_cfg = BT_CONTROLLER_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_bt_controller_init(&bt_cfg));
    ESP_ERROR_CHECK(esp_bt_controller_enable(ESP_BT_MODE_BLE));

    ESP_ERROR_CHECK(esp_bluedroid_init());
    ESP_ERROR_CHECK(esp_bluedroid_enable());

    ESP_ERROR_CHECK(esp_ble_gatt_set_local_mtu(517));

    ESP_ERROR_CHECK(esp_ble_gap_register_callback(gap_event_handler));
    ESP_ERROR_CHECK(esp_ble_gatts_register_callback(gatts_event_handler));
    ESP_ERROR_CHECK(esp_ble_gatts_app_register(0));

    ESP_LOGI(TAG, "BLE UART started (device name: %s)", s_device_name);
}
