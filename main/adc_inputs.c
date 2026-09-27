#include "adc_inputs.h"

#include <stdatomic.h>
#include <sys/lock.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_adc/adc_continuous.h"
#include "esp_log.h"

#define TAG "adc_inputs"
// Aggregate conversions: alternating GPIO9/GPIO10 gives 40 ksample/s per input.
#define MOTOR_ADC_RATE 80000
#define MOTOR_FRAME_BYTES 1024
static adc_oneshot_unit_handle_t s_adc2;
static adc_continuous_handle_t s_motor_adc;
static _lock_t s_init_lock, s_read_lock;
static atomic_uint s_motor_latest; // bit 31 = valid; A in bits 0..11, B in 12..23
static atomic_uint s_pairs, s_overflows, s_errors, s_bad_order;
static adc_inputs_pair_callback_t s_callback;

bool adc_inputs_init(void)
{
    _lock_acquire(&s_init_lock);
    if (s_adc2) {
        _lock_release(&s_init_lock);
        return true;
    }
    adc_oneshot_unit_init_cfg_t config = { .unit_id = ADC_UNIT_2 };
    esp_err_t err = adc_oneshot_new_unit(&config, &s_adc2);
    adc_oneshot_chan_cfg_t channel = { .bitwidth = ADC_BITWIDTH_DEFAULT, .atten = ADC_ATTEN_DB_12 };
    if (err == ESP_OK) err = adc_oneshot_config_channel(s_adc2, ADC_CHANNEL_0, &channel);
    if (err == ESP_OK) err = adc_oneshot_config_channel(s_adc2, ADC_CHANNEL_1, &channel);
    if (err != ESP_OK) {
        if (s_adc2) adc_oneshot_del_unit(s_adc2);
        s_adc2 = NULL;
        ESP_LOGE(TAG, "ADC2 init: %s", esp_err_to_name(err));
    }
    _lock_release(&s_init_lock);
    return err == ESP_OK;
}

bool adc_inputs_read_gpio11_gpio12(int *v11, int *v12)
{
    if (!v11 || !v12 || !adc_inputs_init()) return false;
    _lock_acquire(&s_read_lock);
    esp_err_t a = adc_oneshot_read(s_adc2, ADC_CHANNEL_0, v11);
    esp_err_t b = adc_oneshot_read(s_adc2, ADC_CHANNEL_1, v12);
    _lock_release(&s_read_lock);
    return a == ESP_OK && b == ESP_OK;
}

bool adc_inputs_read_gpio9_gpio10(int *v9, int *v10)
{
    // Diagnostics consume a snapshot, never interrupt continuous conversion.
    unsigned pair = atomic_load(&s_motor_latest);
    if (!v9 || !v10 || !(pair & 0x80000000u)) return false;
    *v9 = pair & 4095;
    *v10 = (pair >> 12) & 4095;
    return true;
}

static bool motor_overflow(adc_continuous_handle_t handle,
                           const adc_continuous_evt_data_t *event, void *arg)
{
    (void)handle; (void)event; (void)arg;
    atomic_fetch_add(&s_overflows, 1);
    return false;
}

static void motor_adc_task(void *arg)
{
    (void)arg;
    uint8_t buffer[MOTOR_FRAME_BYTES] __attribute__((aligned(4)));
    int pending_a = -1;
    uint32_t index = 0;
    for (;;) {
        uint32_t length = 0;
        esp_err_t err = adc_continuous_read(s_motor_adc, buffer, sizeof(buffer), &length, 100);
        if (err == ESP_ERR_TIMEOUT) {
            // A frame normally arrives every 3.2 ms; 100 ms without data is
            // an acquisition fault, even when the DMA pool has not overflowed.
            atomic_fetch_add(&s_errors, 1);
            pending_a = -1;
            continue;
        }
        if (err != ESP_OK) {
            atomic_fetch_add(&s_errors, 1);
            pending_a = -1;
            vTaskDelay(1);
            continue;
        }
        for (unsigned offset = 0; offset < length; offset += SOC_ADC_DIGI_RESULT_BYTES) {
            const adc_digi_output_data_t *sample = (const void *)(buffer + offset);
            unsigned channel = sample->type2.channel;
            if (sample->type2.unit != 0) { pending_a = -1; atomic_fetch_add(&s_bad_order, 1); continue; }
            if (channel == ADC_CHANNEL_8) {
                if (pending_a >= 0) atomic_fetch_add(&s_bad_order, 1);
                pending_a = sample->type2.data;
            } else if (channel == ADC_CHANNEL_9 && pending_a >= 0) {
                int b = sample->type2.data;
                atomic_store(&s_motor_latest, 0x80000000u | (unsigned)pending_a | ((unsigned)b << 12));
                s_callback(pending_a, b, index++);
                pending_a = -1;
            } else { pending_a = -1; atomic_fetch_add(&s_bad_order, 1); }
        }
        atomic_store(&s_pairs, index);
        // Next read blocks until a DMA frame arrives. DMA keeps sampling while
        // this task is blocked; no software timer or deliberate acquisition gap.
    }
}

bool adc_inputs_start_motor_stream(adc_inputs_pair_callback_t callback)
{
    if (!callback || s_motor_adc) return false; // Called once by encoder startup.
    adc_continuous_handle_cfg_t handle_config = {
        .max_store_buf_size = 32768,
        .conv_frame_size = MOTOR_FRAME_BYTES,
    };
    esp_err_t err = adc_continuous_new_handle(&handle_config, &s_motor_adc);
    adc_digi_pattern_config_t pattern[2] = {
        { .atten = ADC_ATTEN_DB_12, .channel = ADC_CHANNEL_8, .unit = ADC_UNIT_1, .bit_width = 12 },
        { .atten = ADC_ATTEN_DB_12, .channel = ADC_CHANNEL_9, .unit = ADC_UNIT_1, .bit_width = 12 },
    };
    adc_continuous_config_t config = {
        .sample_freq_hz = MOTOR_ADC_RATE,
        .conv_mode = ADC_CONV_SINGLE_UNIT_1,
        .format = ADC_DIGI_OUTPUT_FORMAT_TYPE2,
        .pattern_num = 2, .adc_pattern = pattern,
    };
    adc_continuous_evt_cbs_t callbacks = { .on_pool_ovf = motor_overflow };
    if (err == ESP_OK) err = adc_continuous_config(s_motor_adc, &config);
    if (err == ESP_OK) err = adc_continuous_register_event_callbacks(s_motor_adc, &callbacks, NULL);
    if (err == ESP_OK) err = adc_continuous_start(s_motor_adc);
    if (err != ESP_OK) {
        if (s_motor_adc) adc_continuous_deinit(s_motor_adc);
        s_motor_adc = NULL;
        ESP_LOGE(TAG, "motor DMA init: %s", esp_err_to_name(err));
        return false;
    }
    s_callback = callback;
    if (xTaskCreatePinnedToCore(motor_adc_task, "motor_adc_dma", 4096, NULL, 12, NULL, 1) != pdPASS) {
        adc_continuous_stop(s_motor_adc);
        adc_continuous_deinit(s_motor_adc);
        s_motor_adc = NULL;
        return false;
    }
    ESP_LOGI(TAG, "motor DMA: %d conversions/s, %d A/B pairs/s", MOTOR_ADC_RATE, MOTOR_ADC_RATE / 2);
    return true;
}

void adc_inputs_motor_stats(uint32_t *pairs, uint32_t *overflows, uint32_t *errors, uint32_t *bad_order)
{
    *pairs = atomic_load(&s_pairs);
    *overflows = atomic_load(&s_overflows);
    *errors = atomic_load(&s_errors);
    *bad_order = atomic_load(&s_bad_order);
}

uint32_t adc_inputs_motor_pair_rate(void) { return MOTOR_ADC_RATE / 2; }
