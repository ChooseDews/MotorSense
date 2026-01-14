#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Initialize and start a quadrature encoder reader on GPIO11 (A) and GPIO12 (B).
 * Prints the position whenever it changes.
 */
void quadrature_encoder_start(void);

/** Get current position (counts per edge transition). */
int32_t quadrature_encoder_get_position(void);

/** Set current position. */
void quadrature_encoder_set_position(int32_t position);

#ifdef __cplusplus
}
#endif
