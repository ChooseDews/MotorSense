#include "commands.h"

#include <ctype.h>
#include <errno.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "driver/ledc.h"
#include "driver/gpio.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_chip_info.h"
#include "esp_mac.h"
#include "esp_system.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "led_strip.h"
#include "nvs.h"

#include "can_iface.h"
#include "can_rpc.h"
#include "quadrature_encoder.h"
#include "adc_inputs.h"
#include "sdkconfig.h"
#include "axis_motion.h"

// Forward declarations
static void adc_stream_task_fn(void *arg);

#define TAG "COMMANDS"

// Keep these aligned with the existing wiring used elsewhere in this repo.
#define GPIO_LED 39
#define LED_COUNT 2
#define LED_BRIGHTNESS_DIVISOR 10

#define USER_LED_IDX 0
#define INDICATOR_LED_IDX 1

#define HBRIDGE_PIN_A 6
#define HBRIDGE_PIN_B 7

#define MOTOR_PWM_FREQ_HZ 1000
#define MOTOR_PWM_RES LEDC_TIMER_10_BIT
#define MOTOR_PWM_MAX_DUTY ((1U << 10) - 1)
#define MOTOR_LEDC_SPEED_MODE LEDC_LOW_SPEED_MODE
#define MOTOR_LEDC_TIMER LEDC_TIMER_0
#define MOTOR_LEDC_CH_A LEDC_CHANNEL_0
#define MOTOR_LEDC_CH_B LEDC_CHANNEL_1

static led_strip_handle_t s_led_strip;

static TaskHandle_t s_led_dance_task;
static volatile bool s_led_dance_enabled;

static uint8_t s_user_led_r;
static uint8_t s_user_led_g;
static uint8_t s_user_led_b;

static TaskHandle_t s_led_indicator_task;
static volatile commands_indicator_state_t s_indicator_state;
static SemaphoreHandle_t s_led_mutex;

static volatile TickType_t s_indicator_activity_until;

static volatile bool s_ble_joinable;
static volatile bool s_ble_connected;
static volatile bool s_ble_notify_enabled;
static volatile uint16_t s_ble_conn_id;

typedef enum {
    MOTOR_STOP = 0,
    MOTOR_FORWARD,
    MOTOR_BACKWARD,
} motor_state_t;

static motor_state_t s_motor_state = MOTOR_STOP;
static uint8_t s_motor_duty_percent = 100;

// ADC Streaming for debugging
static bool s_adc_stream_enabled = false;
static TaskHandle_t s_adc_stream_task = NULL;

static SemaphoreHandle_t s_motor_mutex;
static esp_timer_handle_t s_motor_stop_timer;
static void motor_apply(motor_state_t state, uint8_t duty_percent);

#ifdef CONFIG_AXIS_MOTION_ENABLED
static axis_motion_t s_axis_motion;
static TaskHandle_t s_axis_motion_task;
static uint32_t s_axis_start_errors;
static uint32_t s_axis_start_invalid;

/* Pitch calibration: encoder 0 is the mechanical axis (GPIO11/12). */
static void axis_motion_task(void *arg)
{
    (void)arg;
    for (;;) {
        if (axis_motion_active(&s_axis_motion)) {
            uint32_t samples, errors, invalid;
            quadrature_encoder_axis_health(&samples, &errors, &invalid);
            bool healthy = samples > 0 && errors == s_axis_start_errors &&
                           invalid == s_axis_start_invalid;
            int duty = axis_motion_step(&s_axis_motion,
                                        quadrature_encoder_get_position_index(0),
                                        esp_timer_get_time(), healthy);
            if (s_motor_mutex) xSemaphoreTake(s_motor_mutex, portMAX_DELAY);
            if (axis_motion_active(&s_axis_motion) && duty != 0) {
                bool positive = duty > 0;
#ifdef CONFIG_AXIS_POSITIVE_BACKWARD
                motor_apply(positive ? MOTOR_BACKWARD : MOTOR_FORWARD,
                            (uint8_t)abs(duty));
#else
                motor_apply(positive ? MOTOR_FORWARD : MOTOR_BACKWARD,
                            (uint8_t)abs(duty));
#endif
            } else {
                /* A manual MOTOR command cancels the controller before it
                 * takes the motor mutex. Do not overwrite that handoff. */
                if (s_axis_motion.state != AXIS_CANCELLED) {
                    motor_apply(MOTOR_STOP, 0);
                }
            }
            if (s_motor_mutex) xSemaphoreGive(s_motor_mutex);
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

static void axis_motion_cancel(axis_motion_state_t reason)
{
    if (axis_motion_active(&s_axis_motion)) axis_motion_stop(&s_axis_motion, reason);
}
#else
static void axis_motion_cancel(int reason) { (void)reason; }
#endif

static void motor_apply(motor_state_t state, uint8_t duty_percent);

static void motor_stop_timer_cb(void *arg)
{
    (void)arg;

    if (s_motor_mutex) {
        xSemaphoreTake(s_motor_mutex, portMAX_DELAY);
    }
    motor_apply(MOTOR_STOP, 0);
    if (s_motor_mutex) {
        xSemaphoreGive(s_motor_mutex);
    }
}

static void trim_right(char *s)
{
    if (s == NULL) {
        return;
    }
    size_t n = strlen(s);
    while (n > 0 && (s[n - 1] == '\n' || s[n - 1] == '\r' || s[n - 1] == ' ' || s[n - 1] == '\t')) {
        s[n - 1] = 0;
        n--;
    }
}

static void motor_set_pwm(ledc_channel_t channel, uint8_t duty_percent)
{
    uint32_t duty = 0;
    if (duty_percent >= 100) {
        duty = MOTOR_PWM_MAX_DUTY;
    } else {
        duty = (MOTOR_PWM_MAX_DUTY * (uint32_t)duty_percent) / 100U;
    }

    ESP_ERROR_CHECK(ledc_set_duty(MOTOR_LEDC_SPEED_MODE, channel, duty));
    ESP_ERROR_CHECK(ledc_update_duty(MOTOR_LEDC_SPEED_MODE, channel));
}

static void motor_apply(motor_state_t state, uint8_t duty_percent)
{
    if (duty_percent > 100) {
        duty_percent = 100;
    }

    // Always force both pins low first to avoid shoot-through during transitions.
    motor_set_pwm(MOTOR_LEDC_CH_A, 0);
    motor_set_pwm(MOTOR_LEDC_CH_B, 0);
    gpio_set_level(HBRIDGE_PIN_A, 0);
    gpio_set_level(HBRIDGE_PIN_B, 0);

    switch (state) {
    case MOTOR_STOP:
        break;
    case MOTOR_FORWARD:
        // Drive A with PWM, keep B low.
        motor_set_pwm(MOTOR_LEDC_CH_A, duty_percent);
        break;
    case MOTOR_BACKWARD:
        // Drive B with PWM, keep A low.
        motor_set_pwm(MOTOR_LEDC_CH_B, duty_percent);
        break;
    default:
        break;
    }

    s_motor_state = state;
    s_motor_duty_percent = duty_percent;
}

static void led_apply_rgb(uint8_t r, uint8_t g, uint8_t b)
{
    if (!s_led_strip) {
        return;
    }

    if (s_led_mutex) {
        xSemaphoreTake(s_led_mutex, portMAX_DELAY);
    }

    uint8_t rr = (uint8_t)(r / LED_BRIGHTNESS_DIVISOR);
    uint8_t gg = (uint8_t)(g / LED_BRIGHTNESS_DIVISOR);
    uint8_t bb = (uint8_t)(b / LED_BRIGHTNESS_DIVISOR);

    led_strip_set_pixel(s_led_strip, USER_LED_IDX, rr, gg, bb);
    led_strip_refresh(s_led_strip);

    s_user_led_r = r;
    s_user_led_g = g;
    s_user_led_b = b;

    if (s_led_mutex) {
        xSemaphoreGive(s_led_mutex);
    }
}

static void indicator_set_rgb(uint8_t r, uint8_t g, uint8_t b)
{
    if (!s_led_strip) {
        return;
    }

    if (s_led_mutex) {
        xSemaphoreTake(s_led_mutex, portMAX_DELAY);
    }

    uint8_t rr = (uint8_t)(r / LED_BRIGHTNESS_DIVISOR);
    uint8_t gg = (uint8_t)(g / LED_BRIGHTNESS_DIVISOR);
    uint8_t bb = (uint8_t)(b / LED_BRIGHTNESS_DIVISOR);

    led_strip_set_pixel(s_led_strip, INDICATOR_LED_IDX, rr, gg, bb);
    led_strip_refresh(s_led_strip);

    if (s_led_mutex) {
        xSemaphoreGive(s_led_mutex);
    }
}

static void led_dance_task(void *arg)
{
    (void)arg;

    while (1) {
        if (s_led_dance_enabled && s_led_strip) {
            uint8_t r = (uint8_t)(esp_random() & 0xFF);
            uint8_t g = (uint8_t)((esp_random() >> 8) & 0xFF);
            uint8_t b = (uint8_t)((esp_random() >> 16) & 0xFF);
            led_apply_rgb(r, g, b);
            vTaskDelay(pdMS_TO_TICKS(200));
        } else {
            vTaskDelay(pdMS_TO_TICKS(100));
        }
    }
}

static const char *indicator_state_to_str(commands_indicator_state_t s)
{
    switch (s) {
    case COMMANDS_INDICATOR_OFF:
        return "OFF";
    case COMMANDS_INDICATOR_JOINABLE:
        return "JOINABLE";
    case COMMANDS_INDICATOR_CONNECTED:
        return "CONNECTED";
    default:
        return "UNKNOWN";
    }
}

static const char *motor_state_to_str(motor_state_t s)
{
    switch (s) {
    case MOTOR_STOP:
        return "S";
    case MOTOR_FORWARD:
        return "F";
    case MOTOR_BACKWARD:
        return "B";
    default:
        return "?";
    }
}

static void led_indicator_task(void *arg)
{
    (void)arg;

    // Keep a local copy so we can detect transitions.
    commands_indicator_state_t last = COMMANDS_INDICATOR_OFF;
    bool did_connected_flash = false;
    int breathe = 0;
    int breathe_dir = 1;

    while (1) {
        commands_indicator_state_t cur = s_indicator_state;
        const TickType_t now = xTaskGetTickCount();
        const bool activity = (s_indicator_activity_until != 0) && (now < s_indicator_activity_until);

        if (cur != last) {
            did_connected_flash = false;
            breathe = 0;
            breathe_dir = 1;
            last = cur;
        }

        if (cur == COMMANDS_INDICATOR_OFF) {
            indicator_set_rgb(0, 0, 0);
            vTaskDelay(pdMS_TO_TICKS(250));
            continue;
        }

        if (cur == COMMANDS_INDICATOR_JOINABLE) {
            // Solid blue while advertising/joinable.
            indicator_set_rgb(activity ? 0 : 0, activity ? 80 : 0, 255);
            vTaskDelay(pdMS_TO_TICKS(250));
            continue;
        }

        if (cur == COMMANDS_INDICATOR_CONNECTED) {
            if (!did_connected_flash) {
                // Quick flash to indicate connection.
                indicator_set_rgb(0, 0, 255);
                vTaskDelay(pdMS_TO_TICKS(80));
                indicator_set_rgb(0, 0, 0);
                vTaskDelay(pdMS_TO_TICKS(80));
                did_connected_flash = true;
            }

            // Breathing blue effect.
            // Triangle wave 0..255..0
            breathe += breathe_dir * 8;
            if (breathe >= 255) {
                breathe = 255;
                breathe_dir = -1;
            } else if (breathe <= 0) {
                breathe = 0;
                breathe_dir = 1;
            }
            indicator_set_rgb(0, activity ? 80 : 0, (uint8_t)breathe);
            vTaskDelay(pdMS_TO_TICKS(25));
            continue;
        }

        vTaskDelay(pdMS_TO_TICKS(100));
    }
}

static void led_save_rgb(uint8_t r, uint8_t g, uint8_t b)
{
    nvs_handle_t handle;
    esp_err_t err = nvs_open("storage", NVS_READWRITE, &handle);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "nvs_open failed: %s", esp_err_to_name(err));
        return;
    }

    nvs_set_u8(handle, "r", r);
    nvs_set_u8(handle, "g", g);
    nvs_set_u8(handle, "b", b);
    nvs_commit(handle);
    nvs_close(handle);
}

static bool led_load_rgb(uint8_t *r, uint8_t *g, uint8_t *b)
{
    if (r == NULL || g == NULL || b == NULL) {
        return false;
    }

    nvs_handle_t handle;
    esp_err_t err = nvs_open("storage", NVS_READWRITE, &handle);
    if (err != ESP_OK) {
        return false;
    }

    err = nvs_get_u8(handle, "r", r);
    err |= nvs_get_u8(handle, "g", g);
    err |= nvs_get_u8(handle, "b", b);
    nvs_close(handle);

    return err == ESP_OK;
}

static bool parse_rgb_space_separated(const char *s, uint8_t *r, uint8_t *g, uint8_t *b)
{
    if (s == NULL || r == NULL || g == NULL || b == NULL) {
        return false;
    }

    long values[3] = {0};
    for (int i = 0; i < 3; i++) {
        while (isspace((unsigned char)*s)) {
            s++;
        }

        char *end = NULL;
        long v = strtol(s, &end, 10);
        if (end == s) {
            return false;
        }
        if (v < 0 || v > 255) {
            return false;
        }
        values[i] = v;
        s = end;

        while (isspace((unsigned char)*s)) {
            s++;
        }
        // Allow commas as separators too.
        if (*s == ',') {
            s++;
        }
    }

    while (isspace((unsigned char)*s)) {
        s++;
    }
    if (*s != 0) {
        return false;
    }

    *r = (uint8_t)values[0];
    *g = (uint8_t)values[1];
    *b = (uint8_t)values[2];
    return true;
}

// ADC streaming task for real-time debugging
static void adc_stream_task_fn(void *arg)
{
    (void)arg;
    
    if (!adc_inputs_init()) {
        ESP_LOGE(TAG, "ADC stream: init failed");
        s_adc_stream_enabled = false;
        vTaskDelete(NULL);
        return;
    }
    
    ESP_LOGI(TAG, "ADC stream started");
    
    while (s_adc_stream_enabled) {
        int v9 = 0, v10 = 0, v11 = 0, v12 = 0;
        
        // Read both encoder ADC pairs
        const bool ok1 = adc_inputs_read_gpio9_gpio10(&v9, &v10);
        const bool ok2 = adc_inputs_read_gpio11_gpio12(&v11, &v12);
        if (!ok1) { v9 = -1; v10 = -1; }
        if (!ok2) { v11 = -1; v12 = -1; }
        
        // Format: ADC <timestamp_us> <gpio9> <gpio10> <gpio11> <gpio12>
        int64_t ts = esp_timer_get_time();
        
        // Output directly to stdout/serial
        printf("ADC %lld %d %d %d %d\n", ts, v9, v10, v11, v12);
        
        // Bound UART bandwidth and let the encoder/control tasks run.
        // 100 Hz is sufficient for low-speed diagnostic sweeps at 115200 baud.
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    
    ESP_LOGI(TAG, "ADC stream stopped");
    s_adc_stream_task = NULL;
    vTaskDelete(NULL);
}

void commands_init(void)
{
    s_led_dance_task = NULL;
    s_led_dance_enabled = false;

    s_user_led_r = 0;
    s_user_led_g = 0;
    s_user_led_b = 0;

    s_led_indicator_task = NULL;
    s_indicator_state = COMMANDS_INDICATOR_OFF;
    s_led_mutex = xSemaphoreCreateMutex();
    s_indicator_activity_until = 0;

    s_ble_joinable = false;
    s_ble_connected = false;
    s_ble_notify_enabled = false;
    s_ble_conn_id = 0;

    s_motor_mutex = xSemaphoreCreateMutex();
    s_motor_stop_timer = NULL;

#ifdef CONFIG_AXIS_MOTION_ENABLED
    s_axis_motion.state = AXIS_IDLE;
    if (xTaskCreate(axis_motion_task, "axis_motion", 4096, NULL, 11,
                    &s_axis_motion_task) != pdPASS) {
        s_axis_motion_task = NULL;
        ESP_LOGE(TAG, "failed to start axis positioning task");
    }
#endif

    // LED strip
    led_strip_config_t strip_config = {
        .strip_gpio_num = GPIO_LED,
        .max_leds = LED_COUNT,
    };
    led_strip_rmt_config_t rmt_config = {
        .resolution_hz = 10 * 1000 * 1000, // 10MHz
        .flags.with_dma = false,
    };

    esp_err_t err = led_strip_new_rmt_device(&strip_config, &rmt_config, &s_led_strip);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "led_strip_new_rmt_device failed: %s", esp_err_to_name(err));
    } else {
        led_strip_clear(s_led_strip);

        // Boot indicator: flash green x2 very quickly.
        indicator_set_rgb(0, 255, 0);
        vTaskDelay(pdMS_TO_TICKS(60));
        indicator_set_rgb(0, 0, 0);
        vTaskDelay(pdMS_TO_TICKS(40));
        indicator_set_rgb(0, 255, 0);
        vTaskDelay(pdMS_TO_TICKS(60));
        indicator_set_rgb(0, 0, 0);

        uint8_t r = 0, g = 0, b = 0;
        if (led_load_rgb(&r, &g, &b)) {
            led_apply_rgb(r, g, b);
        }

        // Start indicator task (it will drive LED1 based on state).
        BaseType_t ok = xTaskCreate(led_indicator_task, "led_ind", 2048, NULL, 6, &s_led_indicator_task);
        if (ok != pdPASS) {
            s_led_indicator_task = NULL;
            ESP_LOGW(TAG, "failed to start indicator task");
        }
    }

    // H-bridge GPIO outputs
    gpio_config_t hb_conf = {
        .intr_type = GPIO_INTR_DISABLE,
        .mode = GPIO_MODE_OUTPUT,
        .pin_bit_mask = (1ULL << HBRIDGE_PIN_A) | (1ULL << HBRIDGE_PIN_B),
        .pull_down_en = 0,
        .pull_up_en = 0,
    };
    ESP_ERROR_CHECK(gpio_config(&hb_conf));

    ledc_timer_config_t timer_conf = {
        .speed_mode = MOTOR_LEDC_SPEED_MODE,
        .duty_resolution = MOTOR_PWM_RES,
        .timer_num = MOTOR_LEDC_TIMER,
        .freq_hz = MOTOR_PWM_FREQ_HZ,
        .clk_cfg = LEDC_AUTO_CLK,
    };
    ESP_ERROR_CHECK(ledc_timer_config(&timer_conf));

    ledc_channel_config_t ch_a = {
        .gpio_num = HBRIDGE_PIN_A,
        .speed_mode = MOTOR_LEDC_SPEED_MODE,
        .channel = MOTOR_LEDC_CH_A,
        .intr_type = LEDC_INTR_DISABLE,
        .timer_sel = MOTOR_LEDC_TIMER,
        .duty = 0,
        .hpoint = 0,
    };
    ledc_channel_config_t ch_b = {
        .gpio_num = HBRIDGE_PIN_B,
        .speed_mode = MOTOR_LEDC_SPEED_MODE,
        .channel = MOTOR_LEDC_CH_B,
        .intr_type = LEDC_INTR_DISABLE,
        .timer_sel = MOTOR_LEDC_TIMER,
        .duty = 0,
        .hpoint = 0,
    };
    ESP_ERROR_CHECK(ledc_channel_config(&ch_a));
    ESP_ERROR_CHECK(ledc_channel_config(&ch_b));

    {
        const esp_timer_create_args_t timer_args = {
            .callback = motor_stop_timer_cb,
            .arg = NULL,
            .dispatch_method = ESP_TIMER_TASK,
            .name = "motor_stop",
            .skip_unhandled_events = true,
        };
        esp_err_t t_err = esp_timer_create(&timer_args, &s_motor_stop_timer);
        if (t_err != ESP_OK) {
            s_motor_stop_timer = NULL;
            ESP_LOGW(TAG, "esp_timer_create(motor_stop) failed: %s", esp_err_to_name(t_err));
        }
    }

    motor_apply(MOTOR_STOP, 100);
}

void commands_indicator_set_state(commands_indicator_state_t state)
{
    s_indicator_state = state;
}

void commands_indicator_notify_activity(void)
{
    // Reset the overlay timer to 1 second from now.
    s_indicator_activity_until = xTaskGetTickCount() + pdMS_TO_TICKS(1000);
}

void commands_status_set_ble_adv_joinable(bool joinable)
{
    s_ble_joinable = joinable;
}

void commands_status_set_ble_connected(bool connected, uint16_t conn_id)
{
    s_ble_connected = connected;
    s_ble_conn_id = connected ? conn_id : 0;
    if (!connected) {
        s_ble_notify_enabled = false;
    }
}

void commands_status_set_ble_notify_enabled(bool enabled)
{
    s_ble_notify_enabled = enabled;
}

static void send_status(commands_reply_fn reply)
{
    char buf[640];

    const int64_t uptime_us = esp_timer_get_time();
    const uint32_t uptime_s = (uint32_t)(uptime_us / 1000000ULL);

    const uint32_t heap_free = esp_get_free_heap_size();
    const uint32_t heap_min = esp_get_minimum_free_heap_size();
    const esp_reset_reason_t reset_reason = esp_reset_reason();

    esp_chip_info_t chip = {0};
    esp_chip_info(&chip);

    uint8_t mac[6] = {0};
    esp_read_mac(mac, ESP_MAC_WIFI_STA);

    const TickType_t now = xTaskGetTickCount();
    const bool activity = (s_indicator_activity_until != 0) && (now < s_indicator_activity_until);
    uint32_t activity_ms_left = 0;
    if (activity) {
        activity_ms_left = (uint32_t)((s_indicator_activity_until - now) * portTICK_PERIOD_MS);
    }

    int n = snprintf(
        buf,
        sizeof(buf),
        "STATUS\n"
        "BLE: joinable=%d connected=%d notify=%d conn_id=%u\n"
        "MOTOR: dir=%s duty=%u%%\n"
#ifdef CONFIG_AXIS_MOTION_ENABLED
        "AXIS: state=%s target=%ld position=%ld duty=%d corrections=%d\n"
#endif
        "USER_LED: mode=%s rgb=%u %u %u\n"
        "ENCODER0: mode=ADC_SCHMITT gpio_a=%d gpio_b=%d pos=%ld\n"
        "ENCODER1: mode=ADC_DMA_SCHMITT gpio_a=%d gpio_b=%d pos=%ld\n"
        "INDICATOR: state=%s activity=%d (%ums)\n"
        "ESP: uptime=%us heap_free=%u heap_min=%u reset_reason=%d\n"
        "ESP: idf=%s chip_model=%d cores=%d rev=%d mac_sta=%02X:%02X:%02X:%02X:%02X:%02X\n",
        s_ble_joinable ? 1 : 0,
        s_ble_connected ? 1 : 0,
        s_ble_notify_enabled ? 1 : 0,
        (unsigned)s_ble_conn_id,
        motor_state_to_str(s_motor_state),
        (unsigned)s_motor_duty_percent,
#ifdef CONFIG_AXIS_MOTION_ENABLED
        axis_motion_state_name(s_axis_motion.state),
        (long)s_axis_motion.target,
        (long)quadrature_encoder_get_position_index(0),
        s_axis_motion.duty,
        s_axis_motion.corrections,
#endif
        s_led_dance_enabled ? "DANCE" : "SOLID",
        (unsigned)s_user_led_r,
        (unsigned)s_user_led_g,
        (unsigned)s_user_led_b,
        11,
        12,
        (long)quadrature_encoder_get_position_index(0),
        9,
        10,
        (long)quadrature_encoder_get_position_index(1),
        indicator_state_to_str(s_indicator_state),
        activity ? 1 : 0,
        (unsigned)activity_ms_left,
        (unsigned)uptime_s,
        (unsigned)heap_free,
        (unsigned)heap_min,
        (int)reset_reason,
        esp_get_idf_version(),
        (int)chip.model,
        (int)chip.cores,
        (int)chip.revision,
        mac[0],
        mac[1],
        mac[2],
        mac[3],
        mac[4],
        mac[5]);

    if (n <= 0) {
        reply("ERR status failed\n");
        return;
    }
    reply(buf);

    // CAN RPC node discovery (from PING ALL / PONG table)
    {
        char line[256];
        const uint8_t self_id = can_rpc_get_node_id();

        can_rpc_neighbor_info_t neigh[16];
        size_t n_neigh = can_rpc_get_neighbors(neigh, sizeof(neigh) / sizeof(neigh[0]));

        int m = snprintf(line, sizeof(line), "CAN_RPC: self_id=%u neighbors=%u\n", (unsigned)self_id, (unsigned)n_neigh);
        if (m > 0) {
            reply(line);
        }

        if (n_neigh == 0) {
            reply("CAN_RPC: neighbors: none (try PING ALL)\n");
        } else {
            // Print just IDs (easy to read over BLE)
            size_t pos = 0;
            pos += (size_t)snprintf(line + pos, sizeof(line) - pos, "CAN_RPC: neighbors:");
            for (size_t i = 0; i < n_neigh && pos < sizeof(line); i++) {
                pos += (size_t)snprintf(line + pos, sizeof(line) - pos, " %u", (unsigned)neigh[i].node_id);
            }
            if (pos < sizeof(line)) {
                pos += (size_t)snprintf(line + pos, sizeof(line) - pos, "\n");
            } else {
                line[sizeof(line) - 2] = '\n';
                line[sizeof(line) - 1] = 0;
            }
            reply(line);
        }
    }
}

void commands_handle_line(char *line, commands_reply_fn reply)
{
    if (line == NULL || reply == NULL) {
        return;
    }

    trim_right(line);
    if (line[0] == 0) {
        return;
    }

    // Any non-empty received line counts as "message activity".
    commands_indicator_notify_activity();

    ESP_LOGI(TAG, "CMD: %s", line);

    if (strcasecmp(line, "HELP") == 0) {
        reply(
            "Commands:\n"
            "  HELP\n"
            "  PING\n"
            "  PING ALL\n"
            "  STATUS\n"
            "  ECHO <text>\n"
            "  MOTOR F|B|S [duty_percent] [seconds]\n"
            "  M F|B|S [duty_percent] [seconds]\n"
            "  AXIS MOVE <signed_degrees> [max_duty]\n"
            "  AXIS STATUS|STOP\n"
            "  LED R G B\n"
            "  LED DANCE\n"
            "  ENCODER <0|1> [INVERT 0|1]\n"
            "  ENCODER STATS\n"
            "  ADC STREAM [START|STOP]\n"
            "  ENCODER CAPTURE\n"
            "  ID\n"
            "  ID SET <id>\n"
            "  ID RUN CMD <id> <command...>\n"
            "  ID RUN CMD * <command...>\n"
            "  ID RUN CMD ALL <command...>\n"
            "  RUN CMD <id> <command...>\n"
            "  RUN CMD * <command...>\n"
            "  <id> RUN CMD <command...>\n"
            "  * RUN CMD <command...>\n"
            "  CAN STATS\n"
            "  CAN TEST\n"
            "  CAN SEND <id> [b0..b7]\n");
        return;
    }

    if (strncasecmp(line, "AXIS", 4) == 0 &&
        (line[4] == 0 || isspace((unsigned char)line[4]))) {
#ifdef CONFIG_AXIS_MOTION_ENABLED
        const char *args = line + 4;
        while (isspace((unsigned char)*args)) args++;
        if (strcasecmp(args, "STATUS") == 0) {
            char buf[192];
            snprintf(buf, sizeof(buf),
                     "AXIS: state=%s start=%ld target=%ld position=%ld duty=%d corrections=%d\n",
                     axis_motion_state_name(s_axis_motion.state), (long)s_axis_motion.start,
                     (long)s_axis_motion.target,
                     (long)quadrature_encoder_get_position_index(0),
                     s_axis_motion.duty, s_axis_motion.corrections);
            reply(buf);
            return;
        }
        if (strcasecmp(args, "STOP") == 0) {
            axis_motion_cancel(AXIS_CANCELLED);
            if (s_motor_mutex) xSemaphoreTake(s_motor_mutex, portMAX_DELAY);
            motor_apply(MOTOR_STOP, 0);
            if (s_motor_mutex) xSemaphoreGive(s_motor_mutex);
            reply("OK AXIS STOP\n");
            return;
        }
        double degrees = 0.0;
        int max_duty = 80;
        char extra = 0;
        int parsed = sscanf(args + 4, " %lf %d %c", &degrees, &max_duty, &extra);
        if (strncasecmp(args, "MOVE", 4) != 0 || (parsed != 1 && parsed != 2)) {
            reply("ERR usage: AXIS MOVE <signed_degrees> [max_duty]\n");
            return;
        }
        uint32_t samples, errors, invalid;
        quadrature_encoder_axis_health(&samples, &errors, &invalid);
        if (!s_axis_motion_task || samples == 0 || !axis_motion_begin(
                &s_axis_motion, quadrature_encoder_get_position_index(0), degrees,
                strtod(CONFIG_AXIS_DEG_PER_TICK, NULL), max_duty, esp_timer_get_time())) {
            reply("ERR axis move rejected: use nonzero degrees within +/-30 and duty 35..100\n");
            return;
        }
        s_axis_start_errors = errors;
        s_axis_start_invalid = invalid;
        char buf[192];
        snprintf(buf, sizeof(buf), "OK AXIS MOVE target=%ld ticks=%ld max_duty=%d\n",
                 (long)s_axis_motion.target, (long)(s_axis_motion.target - s_axis_motion.start), max_duty);
        reply(buf);
#else
        reply("ERR axis positioning firmware is not enabled\n");
#endif
        return;
    }

    // Aliases for remote execution:
    //  - RUN CMD <id|*|ALL> <command...>
    //  - <id|*|ALL> RUN CMD <command...>
    {
        const char *p = line;
        while (isspace((unsigned char)*p)) {
            p++;
        }

        bool matched = false;
        bool broadcast = false;
        uint8_t dst_id = 0;
        const char *cmd = NULL;

        if (strncasecmp(p, "RUN CMD ", 8) == 0) {
            matched = true;
            p += 8;
        } else if (p[0] == '*') {
            const char *q = p + 1;
            while (isspace((unsigned char)*q)) {
                q++;
            }
            if (strncasecmp(q, "RUN CMD ", 8) == 0) {
                matched = true;
                broadcast = true;
                p = q + 8;
            }
        } else if (isdigit((unsigned char)p[0])) {
            char *end = NULL;
            unsigned long id_ul = strtoul(p, &end, 0);
            if (end != p) {
                const char *q = end;
                while (isspace((unsigned char)*q)) {
                    q++;
                }
                if (strncasecmp(q, "RUN CMD ", 8) == 0) {
                    matched = true;
                    if (id_ul == 0 || id_ul > 254UL) {
                        reply("ERR ID must be 1-254\n");
                        return;
                    }
                    dst_id = (uint8_t)id_ul;
                    p = q + 8;
                }
            }
        }

        if (matched) {
            while (isspace((unsigned char)*p)) {
                p++;
            }

            // Parse destination if we matched "RUN CMD ..." form.
            if (!broadcast && dst_id == 0) {
                if (toupper((unsigned char)p[0]) == 'A' && toupper((unsigned char)p[1]) == 'L' && toupper((unsigned char)p[2]) == 'L' &&
                    (p[3] == 0 || isspace((unsigned char)p[3]))) {
                    broadcast = true;
                    p += 3;
                } else if (p[0] == '*' && (p[1] == 0 || isspace((unsigned char)p[1]))) {
                    broadcast = true;
                    p += 1;
                } else {
                    char *end = NULL;
                    unsigned long id_ul = strtoul(p, &end, 0);
                    if (end == p) {
                        reply("ERR RUN CMD expects: RUN CMD <id|*|ALL> <command...>\n");
                        return;
                    }
                    if (id_ul == 0 || id_ul > 254UL) {
                        reply("ERR ID must be 1-254, '*' or ALL\n");
                        return;
                    }
                    dst_id = (uint8_t)id_ul;
                    p = end;
                }
            }

            while (isspace((unsigned char)*p)) {
                p++;
            }
            if (*p == 0) {
                reply("ERR missing command\n");
                return;
            }
            cmd = p;

            bool ok = broadcast ? can_rpc_send_cmd_broadcast(cmd) : can_rpc_send_cmd(dst_id, cmd);
            if (!ok) {
                reply("ERR CAN CMD send failed\n");
                return;
            }
            reply("OK\n");
            return;
        }
    }

    if (strcasecmp(line, "STATUS") == 0) {
        send_status(reply);
        return;
    }

    if (strcasecmp(line, "PING") == 0) {
        reply("PONG\n");
        return;
    }

    if (strcasecmp(line, "PING ALL") == 0) {
        can_rpc_ping_all();
        reply("OK\n");
        return;
    }

    if (strcasecmp(line, "ID") == 0) {
        char buf[64];
        int n = snprintf(buf, sizeof(buf), "ID=%u\n", (unsigned)can_rpc_get_node_id());
        if (n > 0) {
            reply(buf);
        } else {
            reply("ERR\n");
        }
        return;
    }

    if (strncasecmp(line, "ID SET ", 7) == 0) {
        const char *p = line + 7;
        while (isspace((unsigned char)*p)) {
            p++;
        }
        if (*p == 0) {
            reply("ERR ID SET expects: ID SET <id>\n");
            return;
        }

        char *end = NULL;
        unsigned long v = strtoul(p, &end, 0);
        if (end == p) {
            reply("ERR ID must be a number (0-254, 0xFF reserved)\n");
            return;
        }
        while (isspace((unsigned char)*end)) {
            end++;
        }
        if (*end != 0) {
            reply("ERR ID SET expects: ID SET <id>\n");
            return;
        }
        if (v > 254UL || v == 0UL) {
            reply("ERR ID must be 1-254 (0xFF reserved)\n");
            return;
        }
        if (!can_rpc_set_node_id((uint8_t)v)) {
            reply("ERR ID invalid\n");
            return;
        }
        reply("OK\n");
        return;
    }

    if (strncasecmp(line, "ID RUN CMD ", 11) == 0) {
        const char *p = line + 11;
        while (isspace((unsigned char)*p)) {
            p++;
        }
        if (*p == 0) {
            reply("ERR ID RUN CMD expects: ID RUN CMD <id> <command...>\n");
            return;
        }

        bool broadcast = false;
        uint8_t dst_id = 0;

        if (toupper((unsigned char)p[0]) == 'A' && toupper((unsigned char)p[1]) == 'L' && toupper((unsigned char)p[2]) == 'L' &&
            (p[3] == 0 || isspace((unsigned char)p[3]))) {
            broadcast = true;
            p += 3;
        } else if (p[0] == '*' && (p[1] == 0 || isspace((unsigned char)p[1]))) {
            broadcast = true;
            p += 1;
        } else {
            char *end = NULL;
            unsigned long id_ul = strtoul(p, &end, 0);
            if (end == p) {
                reply("ERR ID must be a number, '*' or ALL\n");
                return;
            }
            if (id_ul == 0 || id_ul > 254UL) {
                reply("ERR ID must be 1-254, '*' or ALL\n");
                return;
            }
            dst_id = (uint8_t)id_ul;
            p = end;
        }

        while (isspace((unsigned char)*p)) {
            p++;
        }
        if (*p == 0) {
            reply("ERR missing command after ID\n");
            return;
        }

        bool ok = false;
        if (broadcast) {
            ok = can_rpc_send_cmd_broadcast(p);
        } else {
            ok = can_rpc_send_cmd(dst_id, p);
        }

        if (!ok) {
            reply("ERR CAN CMD send failed\n");
            return;
        }
        reply("OK\n");
        return;
    }

    if (strncasecmp(line, "ECHO ", 5) == 0) {
        reply(line + 5);
        reply("\n");
        return;
    }

    // Motor control:
    //   MOTOR F|B|S [duty_percent] [seconds]
    //   M     F|B|S [duty_percent] [seconds]
    // Examples:
    //   MOTOR F 40 1.0   -> forward 40% for 1.0s
    //   M B 30 2.5       -> backward 30% for 2.5s
    const char *motor_p = NULL;
    if (strncasecmp(line, "MOTOR ", 6) == 0) {
        motor_p = line + 6;
    } else if (strncasecmp(line, "M ", 2) == 0) {
        motor_p = line + 2;
    }
    if (motor_p) {
        axis_motion_cancel(AXIS_CANCELLED);
        const char *p = motor_p;
        while (isspace((unsigned char)*p)) {
            p++;
        }

        char dir = (char)toupper((unsigned char)*p);
        if (dir == 0) {
            reply("ERR MOTOR expects F|B|S [duty_percent] [seconds]\n");
            return;
        }
        p++;

        if (*p != 0 && !isspace((unsigned char)*p)) {
            reply("ERR MOTOR expects F|B|S [duty_percent] [seconds]\n");
            return;
        }

        while (isspace((unsigned char)*p)) {
            p++;
        }

        long duty = 100;
        bool has_duration = false;
        double duration_s = 0.0;
        if (*p != 0) {
            char *end = NULL;
            duty = strtol(p, &end, 10);
            if (end == p) {
                reply("ERR duty must be 0-100\n");
                return;
            }
            if (duty < 0 || duty > 100) {
                reply("ERR duty must be 0-100\n");
                return;
            }

            p = end;
            while (isspace((unsigned char)*p)) {
                p++;
            }

            if (*p != 0) {
                errno = 0;
                char *end2 = NULL;
                duration_s = strtod(p, &end2);
                if (end2 == p || errno != 0) {
                    reply("ERR seconds must be a number (e.g. 1.0)\n");
                    return;
                }
                if (!(duration_s > 0.0) || duration_s > 3600.0) {
                    reply("ERR seconds must be > 0 and <= 3600\n");
                    return;
                }
                while (isspace((unsigned char)*end2)) {
                    end2++;
                }
                if (*end2 != 0) {
                    reply("ERR MOTOR expects F|B|S [duty_percent] [seconds]\n");
                    return;
                }
                has_duration = true;
            }
        }

        if (has_duration && dir != 'S' && !s_motor_stop_timer) {
            reply("ERR timed motor unavailable (timer init failed)\n");
            return;
        }

        if (s_motor_stop_timer) {
            // Cancel any pending timed run.
            (void)esp_timer_stop(s_motor_stop_timer);
        }

        if (s_motor_mutex) {
            xSemaphoreTake(s_motor_mutex, portMAX_DELAY);
        }

        switch (dir) {
        case 'F':
            motor_apply(MOTOR_FORWARD, (uint8_t)duty);
            break;
        case 'B':
            motor_apply(MOTOR_BACKWARD, (uint8_t)duty);
            break;
        case 'S':
            motor_apply(MOTOR_STOP, 0);
            // Stop ignores duration (and we already cancelled any timer).
            has_duration = false;
            break;
        default:
            if (s_motor_mutex) {
                xSemaphoreGive(s_motor_mutex);
            }
            reply("ERR MOTOR expects F|B|S [duty_percent]\n");
            return;
        }

        if (s_motor_mutex) {
            xSemaphoreGive(s_motor_mutex);
        }

        if (has_duration) {
            if (!s_motor_stop_timer) {
                reply("ERR timed motor unavailable (timer init failed)\n");
                return;
            }
            const double us_f = duration_s * 1000000.0;
            const uint64_t us = (uint64_t)(us_f + 0.5);
            esp_err_t t_err = esp_timer_start_once(s_motor_stop_timer, us);
            if (t_err != ESP_OK) {
                if (s_motor_mutex) xSemaphoreTake(s_motor_mutex, portMAX_DELAY);
                motor_apply(MOTOR_STOP, 0);
                if (s_motor_mutex) xSemaphoreGive(s_motor_mutex);
                reply("ERR failed to start timer\n");
                return;
            }
        }

        reply("OK\n");
        return;
    }

    if (strcasecmp(line, "ENCODER MOTORCAPTURE") == 0) {
        quadrature_encoder_motor_capture(reply);
        return;
    }

    if (strcasecmp(line, "ENCODER CAPTURE") == 0) {
        quadrature_encoder_capture(reply);
        return;
    }

    // ENCODER <0|1> [INVERT 0|1] - Get or set encoder inversion
    // ENCODER STATS - Get encoder performance statistics
    if (strncasecmp(line, "ENCODER ", 8) == 0) {
        const char *args = line + 8;
        
        // Check for STATS command first
        if (strcasecmp(args, "STATS") == 0) {
            uint32_t samples = 0;
            uint32_t overflows = 0;
            quadrature_encoder_get_stats(&samples, &overflows);
            quadrature_encoder_motor_stats(reply);
            
            char buf[256];
            snprintf(buf, sizeof(buf), 
                     "ENCODER STATS: axis_samples=%lu dma_overflows=%lu\n",
                     (unsigned long)samples,
                     (unsigned long)overflows);
            reply(buf);
            return;
        }
        
        int enc_idx = -1;
        char subcmd[32] = {0};
        int invert_val = -1;

        // Parse: ENCODER <0|1> or ENCODER <0|1> INVERT <0|1>
        int n = sscanf(args, "%d %31s %d", &enc_idx, subcmd, &invert_val);

        if (enc_idx < 0 || enc_idx > 1) {
            reply("ERR encoder index must be 0 or 1\n");
            return;
        }

        if (n == 1) {
            // Get current state
            bool inv = quadrature_encoder_get_invert((uint8_t)enc_idx);
            int32_t pos = quadrature_encoder_get_position_index((uint8_t)enc_idx);
            char buf[128];
            snprintf(buf, sizeof(buf), "ENCODER %d: pos=%ld invert=%d\n", enc_idx, (long)pos, inv ? 1 : 0);
            reply(buf);
        } else if (n >= 2 && strcasecmp(subcmd, "INVERT") == 0) {
            if (invert_val < 0 || invert_val > 1) {
                reply("ERR invert must be 0 or 1\n");
                return;
            }
            quadrature_encoder_set_invert((uint8_t)enc_idx, invert_val != 0);
            char buf[64];
            snprintf(buf, sizeof(buf), "OK encoder %d invert=%d\n", enc_idx, invert_val);
            reply(buf);
        } else {
            reply("ERR usage: ENCODER <0|1> [INVERT 0|1]\n");
        }
        return;
    }

    // ADC STREAM [START|STOP] - Stream raw ADC values for debugging
    if (strncasecmp(line, "ADC STREAM", 10) == 0) {
        const char *args = line + 10;
        while (isspace((unsigned char)*args)) args++;
        
        if (strcasecmp(args, "START") == 0) {
            if (s_adc_stream_enabled) {
                reply("ADC stream already running\n");
                return;
            }
            s_adc_stream_enabled = true;
            xTaskCreate(adc_stream_task_fn, "adc_stream", 4096, NULL, 18, &s_adc_stream_task);
            reply("OK ADC stream started\n");
        } else if (strcasecmp(args, "STOP") == 0) {
            if (!s_adc_stream_enabled) {
                reply("ADC stream not running\n");
                return;
            }
            s_adc_stream_enabled = false;
            if (s_adc_stream_task) {
                vTaskDelete(s_adc_stream_task);
                s_adc_stream_task = NULL;
            }
            reply("OK ADC stream stopped\n");
        } else if (strlen(args) == 0) {
            reply(s_adc_stream_enabled ? "ADC stream: RUNNING\n" : "ADC stream: STOPPED\n");
        } else {
            reply("ERR usage: ADC STREAM [START|STOP]\n");
        }
        return;
    }

    if (strcasecmp(line, "CAN STATS") == 0 || strcasecmp(line, "CAN STATUS") == 0) {
        twai_status_info_t st;
        if (!can_iface_get_status(&st)) {
            reply("ERR CAN status unavailable\n");
            return;
        }

        char buf[320];
        int n = snprintf(
            buf,
            sizeof(buf),
            "CAN: state=%d msgs_tx=%" PRIu32 " msgs_rx=%" PRIu32 " tx_err=%" PRIu32 " rx_err=%" PRIu32 " tx_fail=%" PRIu32 " rx_miss=%" PRIu32 " rx_ovr=%" PRIu32 " arb_lost=%" PRIu32 " bus_err=%" PRIu32 "\n",
            (int)st.state,
            (uint32_t)st.msgs_to_tx,
            (uint32_t)st.msgs_to_rx,
            (uint32_t)st.tx_error_counter,
            (uint32_t)st.rx_error_counter,
            (uint32_t)st.tx_failed_count,
            (uint32_t)st.rx_missed_count,
            (uint32_t)st.rx_overrun_count,
            (uint32_t)st.arb_lost_count,
            (uint32_t)st.bus_error_count);
        if (n <= 0) {
            reply("ERR CAN status failed\n");
            return;
        }
        reply(buf);

        // Also print discovered nodes as runnable commands (handy over BLE).
        can_rpc_neighbor_info_t neigh[16];
        size_t n_neigh = can_rpc_get_neighbors(neigh, sizeof(neigh) / sizeof(neigh[0]));
        if (n_neigh > 0) {
            reply("NODES (copy/paste):\n");
            for (size_t i = 0; i < n_neigh; i++) {
                char linebuf[64];
                int m = snprintf(linebuf, sizeof(linebuf), "ID RUN CMD %u STATUS\n", (unsigned)neigh[i].node_id);
                if (m > 0) {
                    reply(linebuf);
                }
            }
        } else {
            reply("NODES: none (try PING ALL)\n");
        }
        return;
    }

    if (strcasecmp(line, "CAN TEST") == 0) {
        static uint8_t s_can_test_counter;
        uint8_t payload[8] = {'T', 'E', 'S', 'T', s_can_test_counter++, 0xCA, 0xFE, 0x01};
        const uint32_t id = 0x123;
        if (!can_iface_send(id, false, payload, sizeof(payload))) {
            reply("ERR CAN TX failed\n");
            return;
        }
        reply("OK\n");
        return;
    }

    if (strncasecmp(line, "CAN SEND ", 9) == 0) {
        const char *p = line + 9;
        while (isspace((unsigned char)*p)) {
            p++;
        }
        if (*p == 0) {
            reply("ERR CAN SEND expects: CAN SEND <id> [b0..b7]\n");
            return;
        }

        char *end = NULL;
        unsigned long id_ul = strtoul(p, &end, 0);
        if (end == p) {
            reply("ERR CAN id must be a number (hex like 0x123 ok)\n");
            return;
        }
        uint32_t id = (uint32_t)id_ul;
        p = end;

        uint8_t data[8];
        size_t dlc = 0;
        while (true) {
            while (*p == ',' || isspace((unsigned char)*p)) {
                p++;
            }
            if (*p == 0) {
                break;
            }
            if (dlc >= 8) {
                reply("ERR CAN data too long (max 8 bytes)\n");
                return;
            }
            unsigned long b = strtoul(p, &end, 0);
            if (end == p) {
                reply("ERR CAN data byte must be 0-255 (hex like 0xAA ok)\n");
                return;
            }
            if (b > 255UL) {
                reply("ERR CAN data byte must be 0-255\n");
                return;
            }
            data[dlc++] = (uint8_t)b;
            p = end;
        }

        const bool ext = id > 0x7FF;
        if (!ext && id > 0x7FF) {
            reply("ERR CAN std id must be <= 0x7FF\n");
            return;
        }
        if (ext && id > 0x1FFFFFFF) {
            reply("ERR CAN ext id must be <= 0x1FFFFFFF\n");
            return;
        }

        if (!can_iface_send(id, ext, dlc ? data : NULL, dlc)) {
            reply("ERR CAN TX failed\n");
            return;
        }

        reply("OK\n");
        return;
    }

    if (strcasecmp(line, "LED DANCE") == 0) {
        if (s_led_strip == NULL) {
            reply("ERR LED strip not initialized\n");
            return;
        }

        if (s_led_dance_task == NULL) {
            BaseType_t ok = xTaskCreate(led_dance_task, "led_dance", 2048, NULL, 5, &s_led_dance_task);
            if (ok != pdPASS) {
                s_led_dance_task = NULL;
                reply("ERR failed to start LED task\n");
                return;
            }
        }

        s_led_dance_enabled = true;
        reply("OK\n");
        return;
    }

    if (strncasecmp(line, "LED ", 4) == 0) {
        uint8_t r = 0, g = 0, b = 0;
        if (!parse_rgb_space_separated(line + 4, &r, &g, &b)) {
            reply("ERR LED expects: LED R G B (0-255)\n");
            return;
        }

        s_led_dance_enabled = false;
        led_apply_rgb(r, g, b);
        led_save_rgb(r, g, b);
        reply("OK\n");
        return;
    }

    reply("ERR unknown command (type HELP)\n");
}
