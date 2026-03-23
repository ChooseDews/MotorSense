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
 * Reads the two analog inputs currently wired to GPIO9/GPIO10.
 *
 * On ESP32-S3 these are ADC1 CH8/CH9.
 * Returns true on success.
 */
bool adc_inputs_read_gpio9_gpio10(int *v9, int *v10);

#ifdef __cplusplus
}
#endif
