#include "quadrature_encoder.h"
#include "adc_inputs.h"
#include "encoder_schmitt.h"
#include "device_config.h"

#include <stdatomic.h>
#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_timer.h"

#define AXIS_SAMPLE_PERIOD_US 1000
#define CAPTURE_SAMPLES 1024
// Measured motor lows rise from ~1200 to ~2700 with speed; highs clip at 4095.
// Thresholds were validated against raw captures and bidirectional speed tests.
#define AXIS_SCHMITT_CENTER 2800
#define MOTOR_SCHMITT_CENTER (device_config_get()->motor_center)
#define MOTOR_SCHMITT_HYSTERESIS (device_config_get()->motor_hysteresis)
#define SCHMITT_HYSTERESIS 100

static const char *TAG = "quad_enc";
static TaskHandle_t s_axis_task;
static esp_timer_handle_t s_axis_timer;
static bool s_started;
static atomic_int_least32_t s_position[2];
// Match physical yaw direction: motor F increases both reported positions.
static atomic_bool s_invert[2] = {false, true};
static encoder_schmitt_t s_decoder[2]; // Each decoder has exactly one task owner.
static atomic_uint s_forward[2], s_reverse[2], s_invalid[2];
static atomic_uint s_axis_samples, s_axis_errors;
static atomic_bool s_motor_ready;
static atomic_uint s_invalid_time_us, s_invalid_before, s_invalid_after;

static struct { int64_t time_us; int16_t raw[4]; } s_capture[CAPTURE_SAMPLES];
static atomic_int s_capture_count = -1;
static struct { uint32_t index; uint16_t a, b; } s_motor_capture[CAPTURE_SAMPLES];
static atomic_int s_motor_capture_count = -1;

static void process_sample(unsigned index, int a, int b)
{
    int step = encoder_schmitt_step(&s_decoder[index], a, b,
                                  index == 1 ? MOTOR_SCHMITT_CENTER : AXIS_SCHMITT_CENTER,
                                  index == 1 ? MOTOR_SCHMITT_HYSTERESIS : SCHMITT_HYSTERESIS);
    if (step) {
        if (atomic_load(&s_invert[index])) step = -step;
        atomic_fetch_add(&s_position[index], step);
    }
}

static void publish_stats(unsigned index)
{
    atomic_store(&s_forward[index], s_decoder[index].forward);
    atomic_store(&s_reverse[index], s_decoder[index].reverse);
    atomic_store(&s_invalid[index], s_decoder[index].invalid);
}

static void __attribute__((unused)) motor_process_sample(int a, int b, uint32_t index)
{
    static unsigned previous_raw;
    unsigned before = s_decoder[1].invalid;
    process_sample(1, a, b);
    if (s_decoder[1].invalid != before) {
        atomic_store(&s_invalid_time_us, (uint32_t)esp_timer_get_time());
        atomic_store(&s_invalid_before, previous_raw);
        atomic_store(&s_invalid_after, (unsigned)a | ((unsigned)b << 12));
    }
    previous_raw = (unsigned)a | ((unsigned)b << 12);
    if ((index & 127) == 127) publish_stats(1);
    int n = atomic_load(&s_motor_capture_count);
    if (n >= 0 && n < CAPTURE_SAMPLES) {
        s_motor_capture[n].index = index;
        s_motor_capture[n].a = a;
        s_motor_capture[n].b = b;
        atomic_store(&s_motor_capture_count, n + 1);
    }
}

static void axis_timer_cb(void *arg)
{
    (void)arg;
    if (s_axis_task) xTaskNotifyGive(s_axis_task);
}

static void axis_adc_task(void *arg)
{
    (void)arg;
    for (;;) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        int a = -1, b = -1;
        if (adc_inputs_read_gpio11_gpio12(&a, &b)) {
            process_sample(0, a, b);
            atomic_fetch_add(&s_axis_samples, 1);
            publish_stats(0);
        } else {
            atomic_fetch_add(&s_axis_errors, 1);
        }
        int n = atomic_load(&s_capture_count);
        if (n >= 0 && n < CAPTURE_SAMPLES) {
            int motor_a = -1, motor_b = -1;
            adc_inputs_read_gpio9_gpio10(&motor_a, &motor_b);
            s_capture[n].time_us = esp_timer_get_time();
            s_capture[n].raw[0] = a; s_capture[n].raw[1] = b;
            s_capture[n].raw[2] = motor_a; s_capture[n].raw[3] = motor_b;
            atomic_store(&s_capture_count, n + 1);
        }
        // The 1 kHz timer leaves idle time naturally; motor DMA never pauses.
    }
}

static void position_log_task(void *arg)
{
    (void)arg;
    int32_t previous[2] = {INT32_MAX, INT32_MAX};
    for (;;) {
        for (unsigned i = 0; i < 2; i++) {
            int32_t pos = atomic_load(&s_position[i]);
            if (pos != previous[i]) {
                ESP_LOGI(TAG, "enc=%u pos=%ld", i, (long)pos);
                previous[i] = pos;
            }
        }
        // Position is updated on every transition; logging is rate limited.
        vTaskDelay(pdMS_TO_TICKS(100));
    }
}

void quadrature_encoder_start(void)
{
    if (s_started) return;
    atomic_store(&s_invert[0], device_config_get()->axis_invert);
    atomic_store(&s_invert[1], device_config_get()->encoder_motor_invert);
    if (!adc_inputs_init()) {
        ESP_LOGE(TAG, "axis ADC initialization failed");
        return;
    }
    if (device_config_get()->motor_encoder_enabled) {
    atomic_store(&s_motor_ready, adc_inputs_start_motor_stream(motor_process_sample));
    if (!atomic_load(&s_motor_ready)) ESP_LOGE(TAG, "motor DMA initialization failed");
    }
    if (xTaskCreatePinnedToCore(axis_adc_task, "axis_adc", 4096, NULL, 10, &s_axis_task, 1) != pdPASS) {
        ESP_LOGE(TAG, "axis task creation failed");
        return;
    }
    const esp_timer_create_args_t args = {
        .callback = axis_timer_cb, .dispatch_method = ESP_TIMER_TASK,
        .name = "axis_adc", .skip_unhandled_events = true,
    };
    ESP_ERROR_CHECK(esp_timer_create(&args, &s_axis_timer));
    ESP_ERROR_CHECK(esp_timer_start_periodic(s_axis_timer, AXIS_SAMPLE_PERIOD_US));
    if (xTaskCreate(position_log_task, "enc_log", 3072, NULL, 3, NULL) != pdPASS) {
        ESP_LOGW(TAG, "encoder serial logging unavailable");
    }
    s_started = true;
    ESP_LOGI(TAG, "Schmitt decode: enc0 GPIO11/12 1000 pairs/s; enc1 GPIO9/10 DMA %lu pairs/s; axis_threshold=%d +/-%d motor_threshold=%d +/-%d",
             (unsigned long)adc_inputs_motor_pair_rate(), AXIS_SCHMITT_CENTER, SCHMITT_HYSTERESIS, MOTOR_SCHMITT_CENTER, MOTOR_SCHMITT_HYSTERESIS);
}

int32_t quadrature_encoder_get_position(void) { return quadrature_encoder_get_position_index(0); }
void quadrature_encoder_set_position(int32_t pos) { quadrature_encoder_set_position_index(0, pos); }
int32_t quadrature_encoder_get_position_index(uint8_t index)
{
    return index < 2 ? atomic_load(&s_position[index]) : 0;
}
void quadrature_encoder_set_position_index(uint8_t index, int32_t pos)
{
    if (index < 2) atomic_store(&s_position[index], pos);
}
bool quadrature_encoder_get_invert(uint8_t index)
{
    return index < 2 && atomic_load(&s_invert[index]);
}
void quadrature_encoder_set_invert(uint8_t index, bool invert)
{
    if (index < 2) atomic_store(&s_invert[index], invert);
}
void quadrature_encoder_get_stats(uint32_t *samples, uint32_t *overflows)
{
    uint32_t pairs, errors, bad_order, overflow;
    adc_inputs_motor_stats(&pairs, &overflow, &errors, &bad_order);
    if (samples) *samples = atomic_load(&s_axis_samples);
    if (overflows) *overflows = overflow;
}

void quadrature_encoder_motor_stats(void (*reply)(const char *))
{
    uint32_t pairs, overflows, errors, bad_order;
    adc_inputs_motor_stats(&pairs, &overflows, &errors, &bad_order);
    char line[256];
    snprintf(line, sizeof(line), "MOTOR_STATS pairs=%lu rate=%lu overflows=%lu errors=%lu bad_order=%lu forward=%lu reverse=%lu invalid=%lu pos=%ld ready=%d\n",
             (unsigned long)pairs, (unsigned long)adc_inputs_motor_pair_rate(),
             (unsigned long)overflows, (unsigned long)errors, (unsigned long)bad_order,
             (unsigned long)atomic_load(&s_forward[1]), (unsigned long)atomic_load(&s_reverse[1]),
             (unsigned long)atomic_load(&s_invalid[1]), (long)atomic_load(&s_position[1]), atomic_load(&s_motor_ready));
    reply(line);
    unsigned prev = atomic_load(&s_invalid_before), next = atomic_load(&s_invalid_after);
    snprintf(line, sizeof(line), "MOTOR_LAST_INVALID time_us=%lu before=%u,%u after=%u,%u\n",
             (unsigned long)atomic_load(&s_invalid_time_us), prev & 4095, (prev >> 12) & 4095,
             next & 4095, (next >> 12) & 4095);
    reply(line);
    snprintf(line, sizeof(line), "AXIS_STATS samples=%lu errors=%lu forward=%lu reverse=%lu invalid=%lu pos=%ld\n",
             (unsigned long)atomic_load(&s_axis_samples), (unsigned long)atomic_load(&s_axis_errors),
             (unsigned long)atomic_load(&s_forward[0]), (unsigned long)atomic_load(&s_reverse[0]),
             (unsigned long)atomic_load(&s_invalid[0]), (long)atomic_load(&s_position[0]));
    reply(line);
}

static bool await_capture(atomic_int *count, void (*reply)(const char *))
{
    int expected = -1;
    if (!atomic_compare_exchange_strong(count, &expected, 0)) {
        reply("ERR capture busy\n"); return false;
    }
    int64_t deadline = esp_timer_get_time() + 3000000;
    while (atomic_load(count) < CAPTURE_SAMPLES) {
        if (esp_timer_get_time() > deadline) {
            // Reserve the buffer until reset: a late producer may still write.
            reply("ERR capture timeout; restart to retry\n"); return false;
        }
        vTaskDelay(1);
    }
    return true;
}

void quadrature_encoder_capture(void (*reply)(const char *))
{
    if (!await_capture(&s_capture_count, reply)) return;
    char line[128];
    reply("BURST BEGIN time_us gpio11 gpio12 gpio9_cached gpio10_cached\n");
    for (int i = 0; i < CAPTURE_SAMPLES; i++) {
        snprintf(line, sizeof(line), "BURST %lld %d %d %d %d\n", (long long)s_capture[i].time_us,
                 s_capture[i].raw[0], s_capture[i].raw[1], s_capture[i].raw[2], s_capture[i].raw[3]);
        reply(line);
    }
    reply("BURST END\n");
    atomic_store(&s_capture_count, -1);
}

void quadrature_encoder_motor_capture(void (*reply)(const char *))
{
    if (!atomic_load(&s_motor_ready)) { reply("ERR motor DMA unavailable\n"); return; }
    if (!await_capture(&s_motor_capture_count, reply)) return;
    char line[128];
    snprintf(line, sizeof(line), "MOTORADC BEGIN rate=%lu index gpio9 gpio10\n", (unsigned long)adc_inputs_motor_pair_rate());
    reply(line);
    for (int i = 0; i < CAPTURE_SAMPLES; i++) {
        snprintf(line, sizeof(line), "MOTORADC %lu %u %u\n", (unsigned long)s_motor_capture[i].index,
                 s_motor_capture[i].a, s_motor_capture[i].b);
        reply(line);
    }
    reply("MOTORADC END\n");
    atomic_store(&s_motor_capture_count, -1);
}

void quadrature_encoder_axis_health(uint32_t *samples, uint32_t *errors, uint32_t *invalid)
{
    *samples=atomic_load(&s_axis_samples);
    *errors=atomic_load(&s_axis_errors);
    *invalid=atomic_load(&s_invalid[0]);
}
