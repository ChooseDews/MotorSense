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

#include "adc_inputs.h"

#define ENCODER_GPIO_A 11
#define ENCODER_GPIO_B 12

// 4 counts per full electrical cycle (equivalent to x4 digital quadrature).
#define ENCODER_COUNTS_PER_CYCLE 4.0f

// Sampling period for ADC-based decode.
// 500us = 2kHz (good starting point; adjust as needed).
#define ENCODER_ADC_SAMPLE_PERIOD_US 500

// EMA adaptation rate for mean/variance normalization.
// At 2kHz, alpha=0.001 -> ~0.5s time constant.
#define ENCODER_NORM_ALPHA 0.001f

// Low-pass filter on the mean-centered vector (helps reject jitter).
// Larger = follows motion more quickly.
#define ENCODER_VEC_ALPHA 0.25f

// Adaptive hysteresis derived from signal standard deviation.
// Higher = less sensitive (more noise immunity).
#define ENCODER_HYST_STD_MULT 0.35f
#define ENCODER_HYST_MIN_COUNTS 25.0f
#define ENCODER_HYST_MAX_COUNTS 400.0f

// Ignore samples where the vector magnitude is tiny (phase is unreliable).
#define ENCODER_MIN_MAG_COUNTS 40.0f

// Guard against division blow-ups when signal variance is small.
#define ENCODER_MIN_STD 20.0f

// Ignore samples that are clipped to the rails (phase info often unreliable).
#define ENCODER_CLIP_MARGIN 2

// Optional compile-time inversion (set to 1 to flip direction).
#ifndef ENCODER_ADC_INVERT
#define ENCODER_ADC_INVERT 0
#endif

static const char *TAG = "quad_enc";

static volatile int32_t s_position = 0;
static volatile int8_t s_last_dir = 0;

typedef struct {
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
    if (s_sample_task != NULL) {
        xTaskNotifyGive(s_sample_task);
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

    float mean_a = 0.0f;
    float mean_b = 0.0f;
    float var_a = 1000.0f;
    float var_b = 1000.0f;
    bool stats_inited = false;

    // Low-pass filtered mean-centered vector (ADC counts).
    float fa = 0.0f;
    float fb = 0.0f;
    bool vec_inited = false;

    // Quadrant state derived from signs (with hysteresis).
    int8_t sign_a = 0;
    int8_t sign_b = 0;
    uint8_t quad = 0;
    bool quad_inited = false;

    while (true) {
        // Wait for the periodic timer tick.
        (void)ulTaskNotifyTake(pdTRUE, portMAX_DELAY);

        int raw_a = 0;
        int raw_b = 0;
        if (!adc_inputs_read_gpio11_gpio12(&raw_a, &raw_b)) {
            continue;
        }

        // Clipped samples (near rails) are less reliable; skip only if both are clipped.
        const bool clip_a = (raw_a <= ENCODER_CLIP_MARGIN) || (raw_a >= (4095 - ENCODER_CLIP_MARGIN));
        const bool clip_b = (raw_b <= ENCODER_CLIP_MARGIN) || (raw_b >= (4095 - ENCODER_CLIP_MARGIN));
        if (clip_a && clip_b) {
            continue;
        }

        if (!stats_inited) {
            mean_a = (float)raw_a;
            mean_b = (float)raw_b;
            var_a = 1000.0f;
            var_b = 1000.0f;
            stats_inited = true;
        } else {
            // Exponential moving mean/variance for normalization.
            const float da = (float)raw_a - mean_a;
            mean_a += ENCODER_NORM_ALPHA * da;
            const float ea = (float)raw_a - mean_a;
            var_a += ENCODER_NORM_ALPHA * ((ea * ea) - var_a);

            const float db = (float)raw_b - mean_b;
            mean_b += ENCODER_NORM_ALPHA * db;
            const float eb = (float)raw_b - mean_b;
            var_b += ENCODER_NORM_ALPHA * ((eb * eb) - var_b);
        }

        float std_a = sqrtf(fmaxf(var_a, 1.0f));
        float std_b = sqrtf(fmaxf(var_b, 1.0f));
        if (std_a < ENCODER_MIN_STD) {
            std_a = ENCODER_MIN_STD;
        }
        if (std_b < ENCODER_MIN_STD) {
            std_b = ENCODER_MIN_STD;
        }

        const float ca = (float)raw_a - mean_a;
        const float cb = (float)raw_b - mean_b;

        // Low-pass filter the mean-centered vector to reduce jitter.
        if (!vec_inited) {
            fa = ca;
            fb = cb;
            vec_inited = true;
        } else {
            fa += ENCODER_VEC_ALPHA * (ca - fa);
            fb += ENCODER_VEC_ALPHA * (cb - fb);
        }

        // If the vector is too small, phase is unreliable.
        const float mag2 = (fa * fa) + (fb * fb);
        const float min_mag2 = ENCODER_MIN_MAG_COUNTS * ENCODER_MIN_MAG_COUNTS;
        if (mag2 < min_mag2) {
            continue;
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

        if (!quad_inited) {
            sign_a = (fa >= 0.0f) ? +1 : -1;
            sign_b = (fb >= 0.0f) ? +1 : -1;
            quad = quad_from_signs(sign_a, sign_b);
            quad_inited = true;
            continue;
        }

        const int8_t new_sign_a = sign_with_hyst(fa, sign_a, hyst_a);
        const int8_t new_sign_b = sign_with_hyst(fb, sign_b, hyst_b);
        const uint8_t new_quad = quad_from_signs(new_sign_a, new_sign_b);

        if (new_quad == quad) {
            continue;
        }

        int32_t step = 0;
        // Valid quadrature transitions are adjacent only.
        if (new_quad == (uint8_t)((quad + 1u) & 3u)) {
            step = +1;
        } else if (new_quad == (uint8_t)((quad + 3u) & 3u)) {
            step = -1;
        } else {
            // Glitch / jump: ignore and resync the signs/quadrant.
            sign_a = new_sign_a;
            sign_b = new_sign_b;
            quad = new_quad;
            continue;
        }

        sign_a = new_sign_a;
        sign_b = new_sign_b;
        quad = new_quad;

#if ENCODER_ADC_INVERT
        step = -step;
#endif

        int8_t new_dir = (step > 0) ? +1 : -1;
        uint8_t flags = ENC_EVT_POS;
        if (s_last_dir != 0 && new_dir != s_last_dir) {
            flags |= ENC_EVT_DIR_FLIP;
        }
        s_last_dir = new_dir;

        const int32_t pos = (s_position += step);

        if (s_evt_queue != NULL) {
            encoder_evt_t evt = {
                .a = (uint16_t)raw_a,
                .b = (uint16_t)raw_b,
                .dir = new_dir,
                .flags = flags,
                .pos = pos,
            };
            (void)xQueueSend(s_evt_queue, &evt, 0);
        }
    }
}

static void encoder_print_task(void *arg)
{
    (void)arg;

    int32_t last_printed_pos = 0x7fffffff;
    encoder_evt_t evt;

    while (true) {
        if (xQueueReceive(s_evt_queue, &evt, portMAX_DELAY) == pdTRUE) {
            // Emit a compact position-changed line.
            if ((evt.flags & ENC_EVT_POS) && evt.pos != last_printed_pos) {
                ESP_LOGI(TAG, "pos=%ld dir=%d A=%u B=%u%s", (long)evt.pos, (int)evt.dir, (unsigned)evt.a, (unsigned)evt.b,
                         (evt.flags & ENC_EVT_DIR_FLIP) ? " (DIR_FLIP)" : "");
                last_printed_pos = evt.pos;
            }
        }
    }
}

void quadrature_encoder_start(void)
{
    if (s_evt_queue != NULL) {
        return; // already started
    }

    s_evt_queue = xQueueCreate(32, sizeof(encoder_evt_t));
    if (s_evt_queue == NULL) {
        ESP_LOGE(TAG, "failed to create event queue");
        return;
    }

    s_last_dir = 0;

    s_sample_task = NULL;
    xTaskCreate(encoder_adc_task, "enc_adc", 4096, NULL, 8, &s_sample_task);

    if (s_sample_task == NULL) {
        ESP_LOGE(TAG, "failed to create encoder ADC task");
        return;
    }

    const esp_timer_create_args_t targs = {
        .callback = &encoder_sample_timer_cb,
        .arg = NULL,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "enc_adc",
        .skip_unhandled_events = true,
    };

    ESP_ERROR_CHECK(esp_timer_create(&targs, &s_sample_timer));
    ESP_ERROR_CHECK(esp_timer_start_periodic(s_sample_timer, ENCODER_ADC_SAMPLE_PERIOD_US));

    // Logging (ESP_LOG*) can be stack-hungry depending on config/newlib; give this task more.
    xTaskCreate(encoder_print_task, "enc_print", 4096, NULL, 5, &s_print_task);

    ESP_LOGI(TAG,
             "quadrature encoder started (ADC phase decode): A=GPIO%d B=GPIO%d sample_period_us=%d",
             (int)ENCODER_GPIO_A,
             (int)ENCODER_GPIO_B,
             (int)ENCODER_ADC_SAMPLE_PERIOD_US);
}

int32_t quadrature_encoder_get_position(void)
{
    return s_position;
}

void quadrature_encoder_set_position(int32_t position)
{
    s_position = position;
}
