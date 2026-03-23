#include "quadrature_encoder.h"

#include <math.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

#include "esp_err.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_task_wdt.h"

#include "adc_inputs.h"

#define ENCODER_COUNT 2
#define ENCODER0_GPIO_A 11
#define ENCODER0_GPIO_B 12
#define ENCODER1_GPIO_A 9
#define ENCODER1_GPIO_B 10

// 4 counts per full electrical cycle (equivalent to x4 digital quadrature).
#define ENCODER_COUNTS_PER_CYCLE 4.0f

// Sampling period for ADC-based decode.
// 100us = 10kHz - excellent response while allowing IDLE task to run
// ESP32-S3 can easily handle this with dedicated core.
#define ENCODER_ADC_SAMPLE_PERIOD_US 100

// CPU core assignment: Pin encoder task to core 1 (PRO_CPU=0, APP_CPU=1)
// Core 0 handles WiFi/BT and general tasks, Core 1 is dedicated to encoder
#define ENCODER_CPU_CORE 1

// Watchdog feed interval: feed every N samples to prevent timeout
// At 10kHz, 200 samples = 20ms - yield frequently for IDLE task
#define ENCODER_WDT_FEED_INTERVAL 200

// Task priority: High but not maximum to allow IDLE task to run for watchdog
// FreeRTOS priorities: higher number = higher priority. Most tasks are 5-10.
// Priority 10 is high enough for encoder but allows IDLE (priority 0) to run periodically
#ifndef ENCODER_TASK_PRIORITY
#define ENCODER_TASK_PRIORITY 10
#endif

// EMA adaptation rate for mean/variance normalization.
// At 10kHz, alpha=0.001 -> faster adaptation (~1s time constant)
#define ENCODER_NORM_ALPHA 0.001f

// Low-pass filter on the mean-centered vector (helps reject jitter).
// Higher alpha = faster tracking of motion
#define ENCODER_VEC_ALPHA 0.20f

// Adaptive hysteresis derived from signal standard deviation.
// Very low values = maximum sensitivity
#define ENCODER_HYST_STD_MULT 0.2f
#define ENCODER_HYST_MIN_COUNTS 10.0f
#define ENCODER_HYST_MAX_COUNTS 150.0f

// Ignore samples where the vector magnitude is tiny (phase is unreliable).
// Very low threshold to catch all real motion
#define ENCODER_MIN_MAG_COUNTS 25.0f

// Guard against division blow-ups when signal variance is small.
#define ENCODER_MIN_STD 10.0f

// Ignore samples that are clipped to the rails (phase info often unreliable).
#define ENCODER_CLIP_MARGIN 2

// Require a new quadrant to persist for a few samples before counting a step.
// At 10kHz, 1 sample = 100us - minimal debounce for maximum sensitivity
#define ENCODER_QUAD_DEBOUNCE_SAMPLES 1

// Optional compile-time inversion (set to 1 to flip direction).
#ifndef ENCODER_ADC_INVERT
#define ENCODER_ADC_INVERT 0
#endif

static const char *TAG = "quad_enc";

typedef struct {
    uint8_t enc_index;
    uint16_t a;
    uint16_t b;
    int8_t dir;
    uint8_t flags;
    int32_t pos;
} encoder_evt_t;

enum {
    ENC_EVT_POS = 1 << 1,
    ENC_EVT_DIR_FLIP = 1 << 2,
};

static QueueHandle_t s_evt_queue = NULL;
static TaskHandle_t s_print_task = NULL;

static TaskHandle_t s_sample_task = NULL;
static esp_timer_handle_t s_sample_timer = NULL;

// Statistics for monitoring encoder performance
static uint32_t s_sample_count = 0;
static uint32_t s_queue_overflow_count = 0;
static uint32_t s_last_overflow_warning = 0;

typedef struct {
    int32_t position;
    int8_t last_dir;

    float mean_a;
    float mean_b;
    float var_a;
    float var_b;
    bool stats_inited;

    float fa;
    float fb;
    bool vec_inited;

    int8_t sign_a;
    int8_t sign_b;
    uint8_t quad;
    bool quad_inited;

    uint8_t pending_quad;
    uint8_t pending_count;

    bool invert;
} encoder_state_t;

typedef bool (*encoder_read_fn_t)(int *v_a, int *v_b);

typedef struct {
    int gpio_a;
    int gpio_b;
    encoder_read_fn_t read_fn;
    encoder_state_t state;
} encoder_ctx_t;

static encoder_ctx_t s_encoders[ENCODER_COUNT] = {
    {
        .gpio_a = ENCODER0_GPIO_A,
        .gpio_b = ENCODER0_GPIO_B,
        .read_fn = adc_inputs_read_gpio11_gpio12,
        .state = {0},
    },
    {
        .gpio_a = ENCODER1_GPIO_A,
        .gpio_b = ENCODER1_GPIO_B,
        .read_fn = adc_inputs_read_gpio9_gpio10,
        .state = {0},
    },
};

static inline int8_t sign_with_hyst(float v, int8_t last, float hyst)
{
    if (last >= 0) {
        if (v < -hyst) {
            return -1;
        }
        return +1;
    }
    // last < 0
    if (v > hyst) {
        return +1;
    }
    return -1;
}

static inline uint8_t quad_from_signs(int8_t sa, int8_t sb)
{
    // Map (cos, sin) sign pair to quadrants:
    // (+,+)=0, (-,+)=1, (-,-)=2, (+,-)=3
    if (sa >= 0 && sb >= 0) {
        return 0;
    }
    if (sa < 0 && sb >= 0) {
        return 1;
    }
    if (sa < 0 && sb < 0) {
        return 2;
    }
    return 3;
}

static void encoder_sample_timer_cb(void *arg)
{
    (void)arg;
    // Direct task notification for minimal latency
    if (s_sample_task != NULL) {
        xTaskNotifyGive(s_sample_task);
    }
}

static void encoder_process_sample(uint8_t enc_index, encoder_ctx_t *enc, int raw_a, int raw_b)
{
    encoder_state_t *st = &enc->state;

    // Clipped samples (near rails) are less reliable; skip if either channel is clipped.
    const bool clip_a = (raw_a <= ENCODER_CLIP_MARGIN) || (raw_a >= (4095 - ENCODER_CLIP_MARGIN));
    const bool clip_b = (raw_b <= ENCODER_CLIP_MARGIN) || (raw_b >= (4095 - ENCODER_CLIP_MARGIN));
    if (clip_a || clip_b) {
        return;
    }

    if (!st->stats_inited) {
        st->mean_a = (float)raw_a;
        st->mean_b = (float)raw_b;
        st->var_a = 1000.0f;
        st->var_b = 1000.0f;
        st->stats_inited = true;
    } else {
        // Exponential moving mean/variance for normalization.
        // Only update mean if we have enough signal (prevents drift when stationary).
        const float ca_temp = (float)raw_a - st->mean_a;
        const float cb_temp = (float)raw_b - st->mean_b;
        const float mag2_temp = (ca_temp * ca_temp) + (cb_temp * cb_temp);
        const float min_mag2_for_adapt = (ENCODER_MIN_MAG_COUNTS * 0.5f) * (ENCODER_MIN_MAG_COUNTS * 0.5f);
        
        const float adapt_alpha = (mag2_temp > min_mag2_for_adapt) ? ENCODER_NORM_ALPHA : (ENCODER_NORM_ALPHA * 0.1f);
        
        const float da = (float)raw_a - st->mean_a;
        st->mean_a += adapt_alpha * da;
        const float ea = (float)raw_a - st->mean_a;
        st->var_a += ENCODER_NORM_ALPHA * ((ea * ea) - st->var_a);

        const float db = (float)raw_b - st->mean_b;
        st->mean_b += adapt_alpha * db;
        const float eb = (float)raw_b - st->mean_b;
        st->var_b += ENCODER_NORM_ALPHA * ((eb * eb) - st->var_b);
    }

    float std_a = sqrtf(fmaxf(st->var_a, 1.0f));
    float std_b = sqrtf(fmaxf(st->var_b, 1.0f));
    if (std_a < ENCODER_MIN_STD) {
        std_a = ENCODER_MIN_STD;
    }
    if (std_b < ENCODER_MIN_STD) {
        std_b = ENCODER_MIN_STD;
    }

    const float ca = (float)raw_a - st->mean_a;
    const float cb = (float)raw_b - st->mean_b;

    // Low-pass filter the mean-centered vector to reduce jitter.
    if (!st->vec_inited) {
        st->fa = ca;
        st->fb = cb;
        st->vec_inited = true;
    } else {
        st->fa += ENCODER_VEC_ALPHA * (ca - st->fa);
        st->fb += ENCODER_VEC_ALPHA * (cb - st->fb);
    }

    // If the vector is too small, phase is unreliable.
    const float mag2 = (st->fa * st->fa) + (st->fb * st->fb);
    const float min_mag2 = ENCODER_MIN_MAG_COUNTS * ENCODER_MIN_MAG_COUNTS;
    if (mag2 < min_mag2) {
        return;
    }

    // Adaptive hysteresis per channel.
    float hyst_a = std_a * ENCODER_HYST_STD_MULT;
    float hyst_b = std_b * ENCODER_HYST_STD_MULT;
    if (hyst_a < ENCODER_HYST_MIN_COUNTS) {
        hyst_a = ENCODER_HYST_MIN_COUNTS;
    } else if (hyst_a > ENCODER_HYST_MAX_COUNTS) {
        hyst_a = ENCODER_HYST_MAX_COUNTS;
    }
    if (hyst_b < ENCODER_HYST_MIN_COUNTS) {
        hyst_b = ENCODER_HYST_MIN_COUNTS;
    } else if (hyst_b > ENCODER_HYST_MAX_COUNTS) {
        hyst_b = ENCODER_HYST_MAX_COUNTS;
    }

    if (!st->quad_inited) {
        st->sign_a = (st->fa >= 0.0f) ? +1 : -1;
        st->sign_b = (st->fb >= 0.0f) ? +1 : -1;
        st->quad = quad_from_signs(st->sign_a, st->sign_b);
        st->quad_inited = true;
        return;
    }

    const int8_t new_sign_a = sign_with_hyst(st->fa, st->sign_a, hyst_a);
    const int8_t new_sign_b = sign_with_hyst(st->fb, st->sign_b, hyst_b);
    const uint8_t new_quad = quad_from_signs(new_sign_a, new_sign_b);

    if (new_quad == st->quad) {
        st->pending_count = 0;
        return;
    }

    if (new_quad != st->pending_quad) {
        st->pending_quad = new_quad;
        st->pending_count = 1;
        return;
    }

    if (st->pending_count < ENCODER_QUAD_DEBOUNCE_SAMPLES) {
        st->pending_count++;
        return;
    }

    int32_t step = 0;
    // Valid quadrature transitions are adjacent only.
    if (new_quad == (uint8_t)((st->quad + 1u) & 3u)) {
        step = +1;
    } else if (new_quad == (uint8_t)((st->quad + 3u) & 3u)) {
        step = -1;
    } else {
        // Glitch / jump: ignore and resync the signs/quadrant.
        st->sign_a = new_sign_a;
        st->sign_b = new_sign_b;
        st->quad = new_quad;
        st->pending_count = 0;
        return;
    }

    st->sign_a = new_sign_a;
    st->sign_b = new_sign_b;
    st->quad = new_quad;
    st->pending_count = 0;

#if ENCODER_ADC_INVERT
    step = -step;
#endif

    // Apply per-encoder inversion
    if (st->invert) {
        step = -step;
    }

    int8_t new_dir = (step > 0) ? +1 : -1;
    uint8_t flags = ENC_EVT_POS;
    if (st->last_dir != 0 && new_dir != st->last_dir) {
        flags |= ENC_EVT_DIR_FLIP;
    }
    st->last_dir = new_dir;

    const int32_t pos = (st->position += step);

    if (s_evt_queue != NULL) {
        encoder_evt_t evt = {
            .enc_index = enc_index,
            .a = (uint16_t)raw_a,
            .b = (uint16_t)raw_b,
            .dir = new_dir,
            .flags = flags,
            .pos = pos,
        };
        if (xQueueSend(s_evt_queue, &evt, 0) != pdTRUE) {
            // Queue full - event dropped! This means print task can't keep up.
            s_queue_overflow_count++;
            // Warn occasionally (not every time to avoid flooding logs)
            uint32_t now_sec = (uint32_t)(esp_timer_get_time() / 1000000ULL);
            if (now_sec > s_last_overflow_warning + 5) {
                s_last_overflow_warning = now_sec;
                ESP_LOGW(TAG, "Encoder queue overflow! Dropped %lu events. Print task can't keep up.", 
                         (unsigned long)s_queue_overflow_count);
            }
        }
    }
}

static void encoder_adc_task(void *arg)
{
    (void)arg;

    if (!adc_inputs_init()) {
        ESP_LOGE(TAG, "adc_inputs_init failed; encoder task exiting");
        vTaskDelete(NULL);
        return;
    }

    ESP_LOGI(TAG, "Encoder ADC task started on CPU core %d with priority %d", 
             (int)xPortGetCoreID(), (int)uxTaskPriorityGet(NULL));

    uint32_t wdt_feed_counter = 0;

    while (true) {
        // Wait for the periodic timer tick.
        (void)ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        s_sample_count++;
        
        // Periodically delay to allow IDLE task to run and reset watchdog
        if (++wdt_feed_counter >= ENCODER_WDT_FEED_INTERVAL) {
            wdt_feed_counter = 0;
            // Block for 1 tick (~10ms) to give IDLE task guaranteed CPU time
            vTaskDelay(1);
        }
        
        for (uint8_t i = 0; i < ENCODER_COUNT; i++) {
            int raw_a = 0;
            int raw_b = 0;
            if (!s_encoders[i].read_fn(&raw_a, &raw_b)) {
                continue;
            }

            encoder_process_sample(i, &s_encoders[i], raw_a, raw_b);
        }
    }
}

static void encoder_print_task(void *arg)
{
    (void)arg;

    int32_t last_printed_pos[ENCODER_COUNT] = {0x7fffffff, 0x7fffffff};
    encoder_evt_t evt;

    while (true) {
        if (xQueueReceive(s_evt_queue, &evt, portMAX_DELAY) == pdTRUE) {
            // Emit a compact position-changed line.
            const uint8_t idx = evt.enc_index;
            if (idx < ENCODER_COUNT && (evt.flags & ENC_EVT_POS) && evt.pos != last_printed_pos[idx]) {
                ESP_LOGI(TAG, "enc=%u pos=%ld dir=%d A=%u B=%u%s", (unsigned)idx, (long)evt.pos, (int)evt.dir,
                         (unsigned)evt.a, (unsigned)evt.b, (evt.flags & ENC_EVT_DIR_FLIP) ? " (DIR_FLIP)" : "");
                last_printed_pos[idx] = evt.pos;
            }
        }
    }
}

void quadrature_encoder_start(void)
{
    if (s_evt_queue != NULL) {
        return; // already started
    }

    // Larger queue to buffer events if print task is temporarily slow
    s_evt_queue = xQueueCreate(128, sizeof(encoder_evt_t));
    if (s_evt_queue == NULL) {
        ESP_LOGE(TAG, "failed to create event queue");
        return;
    }

    for (uint8_t i = 0; i < ENCODER_COUNT; i++) {
        s_encoders[i].state.position = 0;
        s_encoders[i].state.last_dir = 0;
        s_encoders[i].state.stats_inited = false;
        s_encoders[i].state.vec_inited = false;
        s_encoders[i].state.quad_inited = false;
        s_encoders[i].state.pending_quad = 0;
        s_encoders[i].state.pending_count = 0;
        // Encoder 0 works better inverted by default
        s_encoders[i].state.invert = (i == 0) ? true : false;
    }

    // Reset statistics
    s_sample_count = 0;
    s_queue_overflow_count = 0;
    s_last_overflow_warning = 0;

    s_sample_task = NULL;
    // CRITICAL: Pin to CPU core 1 (APP_CPU) for dedicated high-speed processing
    // Use highest priority to ensure we never miss encoder steps
    xTaskCreatePinnedToCore(encoder_adc_task, "enc_adc", 4096, NULL, 
                            ENCODER_TASK_PRIORITY, &s_sample_task, ENCODER_CPU_CORE);

    if (s_sample_task == NULL) {
        ESP_LOGE(TAG, "failed to create encoder ADC task");
        return;
    }

    // Use high-frequency timer with dedicated high-priority task dispatch
    // TASK mode on dedicated CPU core provides excellent performance
    const esp_timer_create_args_t targs = {
        .callback = &encoder_sample_timer_cb,
        .arg = NULL,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "enc_adc",
        .skip_unhandled_events = false,  // Don't skip - we want every sample
    };

    ESP_ERROR_CHECK(esp_timer_create(&targs, &s_sample_timer));
    ESP_ERROR_CHECK(esp_timer_start_periodic(s_sample_timer, ENCODER_ADC_SAMPLE_PERIOD_US));

    // Logging (ESP_LOG*) can be stack-hungry depending on config/newlib; give this task more.
    xTaskCreate(encoder_print_task, "enc_print", 4096, NULL, 5, &s_print_task);

    ESP_LOGI(TAG,
             "quadrature encoders started (ADC phase decode): enc0=GPIO%d/%d enc1=GPIO%d/%d sample_rate=%dHz priority=%d core=%d",
             (int)ENCODER0_GPIO_A,
             (int)ENCODER0_GPIO_B,
             (int)ENCODER1_GPIO_A,
             (int)ENCODER1_GPIO_B,
             (int)(1000000 / ENCODER_ADC_SAMPLE_PERIOD_US),
             (int)ENCODER_TASK_PRIORITY,
             (int)ENCODER_CPU_CORE);
}

int32_t quadrature_encoder_get_position(void)
{
    return quadrature_encoder_get_position_index(0);
}

void quadrature_encoder_set_position(int32_t position)
{
    quadrature_encoder_set_position_index(0, position);
}

int32_t quadrature_encoder_get_position_index(uint8_t index)
{
    if (index >= ENCODER_COUNT) {
        return 0;
    }
    return s_encoders[index].state.position;
}

void quadrature_encoder_set_position_index(uint8_t index, int32_t position)
{
    if (index >= ENCODER_COUNT) {
        return;
    }
    s_encoders[index].state.position = position;
}

bool quadrature_encoder_get_invert(uint8_t index)
{
    if (index >= ENCODER_COUNT) {
        return false;
    }
    return s_encoders[index].state.invert;
}

void quadrature_encoder_set_invert(uint8_t index, bool invert)
{
    if (index >= ENCODER_COUNT) {
        return;
    }
    s_encoders[index].state.invert = invert;
}

// Diagnostic function to get encoder statistics
void quadrature_encoder_get_stats(uint32_t *sample_count, uint32_t *overflow_count)
{
    if (sample_count) {
        *sample_count = s_sample_count;
    }
    if (overflow_count) {
        *overflow_count = s_queue_overflow_count;
    }
}
