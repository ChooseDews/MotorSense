#pragma once
#include <stddef.h>
#include <stdint.h>
#include "commands.h"
bool ota_update_command(const char *, commands_reply_fn);
bool ota_update_active(void);
bool ota_update_binary_active(void);
void ota_update_binary_feed(const uint8_t *, size_t, commands_reply_fn);
