#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Initialize and start quadrature encoder readers on GPIO11/12 and GPIO9/10.
 * Prints the position whenever it changes.
 */
void quadrature_encoder_start(void);

/** Get current position (counts per edge transition). */
int32_t quadrature_encoder_get_position(void);

/** Get current position for a given encoder index (0 or 1). */
int32_t quadrature_encoder_get_position_index(uint8_t index);

/** Set current position. */
void quadrature_encoder_set_position(int32_t position);

/** Set current position for a given encoder index (0 or 1). */
void quadrature_encoder_set_position_index(uint8_t index, int32_t position);

/** Get A/B inversion state for a given encoder index (0 or 1). */
bool quadrature_encoder_get_invert(uint8_t index);

/** Set A/B inversion state for a given encoder index (0 or 1). */
void quadrature_encoder_set_invert(uint8_t index, bool invert);

/** Get encoder statistics (sample count and queue overflow count). */
void quadrature_encoder_get_stats(uint32_t *sample_count, uint32_t *overflow_count);

#ifdef __cplusplus
}
#endif
