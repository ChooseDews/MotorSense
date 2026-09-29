#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
#include "commands.h"
typedef struct {
    char role[8], hardware[24];
    uint32_t counts_rev;
    double min_deg, max_deg, zero_deg;
    uint8_t motor_invert, axis_invert, encoder_motor_invert, motor_encoder_enabled;
    uint16_t motor_center, motor_hysteresis;
} device_config_t;
esp_err_t device_config_init(void);
const device_config_t *device_config_get(void);
bool device_config_command(const char *, commands_reply_fn, bool idle);
