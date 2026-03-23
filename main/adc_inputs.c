#include "adc_inputs.h"

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

#include "esp_err.h"
#include "esp_log.h"

#if ESP_IDF_VERSION_MAJOR >= 5
#include "esp_adc/adc_oneshot.h"
#endif

#define TAG "adc_inputs"

// Mutex removed for high-speed quadrature sampling - ADC units are accessed independently
// ADC1 (GPIO9/10) and ADC2 (GPIO11/12) can be read concurrently without mutex

#if ESP_IDF_VERSION_MAJOR >= 5
static adc_oneshot_unit_handle_t s_adc1_handle;
static adc_oneshot_unit_handle_t s_adc2_handle;
static bool s_inited;

bool adc_inputs_init(void)
{
    if (s_inited) {
        return true;
    }

#if !CONFIG_IDF_TARGET_ESP32S3
    ESP_LOGE(TAG, "adc_inputs currently supports ESP32-S3 only");
    return false;
#else
    esp_err_t err;

    // GPIO9/GPIO10 map to ADC1 CH8/CH9 on ESP32-S3.
    const adc_unit_t adc_unit1 = ADC_UNIT_1;
    const adc_channel_t adc_ch_gpio9 = ADC_CHANNEL_8;
    const adc_channel_t adc_ch_gpio10 = ADC_CHANNEL_9;

    // GPIO11/GPIO12 map to ADC2 CH0/CH1 on ESP32-S3.
    const adc_unit_t adc_unit2 = ADC_UNIT_2;
    const adc_channel_t adc_ch_gpio11 = ADC_CHANNEL_0;
    const adc_channel_t adc_ch_gpio12 = ADC_CHANNEL_1;

    adc_oneshot_unit_init_cfg_t init_cfg = {
        .unit_id = adc_unit1,
        .ulp_mode = ADC_ULP_MODE_DISABLE,
    };

    err = adc_oneshot_new_unit(&init_cfg, &s_adc1_handle);
    if (err != ESP_OK || s_adc1_handle == NULL) {
        ESP_LOGE(TAG, "adc_oneshot_new_unit ADC1 failed: %s", esp_err_to_name(err));
        return false;
    }

    init_cfg.unit_id = adc_unit2;
    err = adc_oneshot_new_unit(&init_cfg, &s_adc2_handle);
    if (err != ESP_OK || s_adc2_handle == NULL) {
        ESP_LOGE(TAG, "adc_oneshot_new_unit ADC2 failed: %s", esp_err_to_name(err));
        return false;
    }

    adc_oneshot_chan_cfg_t chan_cfg = {
        .bitwidth = ADC_BITWIDTH_DEFAULT,
        .atten = ADC_ATTEN_DB_12,
    };

    err = adc_oneshot_config_channel(s_adc1_handle, adc_ch_gpio9, &chan_cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "adc config GPIO9 failed: %s", esp_err_to_name(err));
        return false;
    }

    err = adc_oneshot_config_channel(s_adc1_handle, adc_ch_gpio10, &chan_cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "adc config GPIO10 failed: %s", esp_err_to_name(err));
        return false;
    }

    err = adc_oneshot_config_channel(s_adc2_handle, adc_ch_gpio11, &chan_cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "adc config GPIO11 failed: %s", esp_err_to_name(err));
        return false;
    }

    err = adc_oneshot_config_channel(s_adc2_handle, adc_ch_gpio12, &chan_cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "adc config GPIO12 failed: %s", esp_err_to_name(err));
        return false;
    }

    s_inited = true;
    ESP_LOGI(TAG, "initialized oneshot ADC for GPIO9/10 and GPIO11/12");
    return true;
#endif
}

bool adc_inputs_read_gpio11_gpio12(int *v11, int *v12)
{
    if (!adc_inputs_init()) {
        return false;
    }

#if !CONFIG_IDF_TARGET_ESP32S3
    (void)v11;
    (void)v12;
    return false;
#else
    if (v11 == NULL || v12 == NULL) {
        return false;
    }

    const adc_channel_t adc_ch_gpio11 = ADC_CHANNEL_0;
    const adc_channel_t adc_ch_gpio12 = ADC_CHANNEL_1;

    int a = 0;
    int b = 0;
    // Direct ADC reads without mutex for minimum latency
    (void)adc_oneshot_read(s_adc2_handle, adc_ch_gpio11, &a);
    (void)adc_oneshot_read(s_adc2_handle, adc_ch_gpio12, &b);

    *v11 = a;
    *v12 = b;
    return true;
#endif
}

bool adc_inputs_read_gpio9_gpio10(int *v9, int *v10)
{
    if (!adc_inputs_init()) {
        return false;
    }

#if !CONFIG_IDF_TARGET_ESP32S3
    (void)v9;
    (void)v10;
    return false;
#else
    if (v9 == NULL || v10 == NULL) {
        return false;
    }

    const adc_channel_t adc_ch_gpio9 = ADC_CHANNEL_8;
    const adc_channel_t adc_ch_gpio10 = ADC_CHANNEL_9;

    int a = 0;
    int b = 0;
    // Direct ADC reads without mutex for minimum latency
    (void)adc_oneshot_read(s_adc1_handle, adc_ch_gpio9, &a);
    (void)adc_oneshot_read(s_adc1_handle, adc_ch_gpio10, &b);

    *v9 = a;
    *v10 = b;
    return true;
#endif
}

#else

bool adc_inputs_init(void)
{
    ESP_LOGE(TAG, "adc_inputs requires ESP-IDF v5+ (adc_oneshot)");
    return false;
}

bool adc_inputs_read_gpio11_gpio12(int *v11, int *v12)
{
    (void)v11;
    (void)v12;
    return false;
}

bool adc_inputs_read_gpio9_gpio10(int *v9, int *v10)
{
    (void)v9;
    (void)v10;
    return false;
}

#endif
