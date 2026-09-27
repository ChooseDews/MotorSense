#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Initializes the shared ADC inputs subsystem.
 * Safe to call multiple times.
 */
bool adc_inputs_init(void);

/**
 * Reads the two analog inputs currently wired to GPIO11/GPIO12.
 *
 * On ESP32-S3 these are ADC2 CH0/CH1.
 * Returns true on success.
 */
bool adc_inputs_read_gpio11_gpio12(int *v11, int *v12);

/**
 * Returns the latest DMA A/B snapshot for GPIO9/GPIO10.
 * Does not perform oneshot reads or interrupt continuous acquisition.
 *
 * On ESP32-S3 these are ADC1 CH8/CH9.
 * Returns true on success.
 */
bool adc_inputs_read_gpio9_gpio10(int *v9, int *v10);

/** Called from the DMA consumer task, not an ISR, for each consecutive A/B pair. */
typedef void (*adc_inputs_pair_callback_t)(int a, int b, uint32_t sample_index);
bool adc_inputs_start_motor_stream(adc_inputs_pair_callback_t callback);
void adc_inputs_motor_stats(uint32_t *pairs, uint32_t *overflows, uint32_t *errors, uint32_t *bad_order);
uint32_t adc_inputs_motor_pair_rate(void);

#ifdef __cplusplus
}
#endif
