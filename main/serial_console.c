#include "serial_console.h"

#include <stdio.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "driver/uart.h"
#include "driver/uart_vfs.h"
#include "esp_log.h"
#include "esp_vfs_dev.h"
#include "sdkconfig.h"

#include "commands.h"

#define TAG "SERIAL_CMD"

static void serial_reply(const char *s)
{
    if (s == NULL) {
        return;
    }
    fputs(s, stdout);
    fflush(stdout);
}

static void serial_console_task(void *arg)
{
    (void)arg;

    char line[256];
    while (1) {
        if (fgets(line, sizeof(line), stdin) == NULL) {
            vTaskDelay(pdMS_TO_TICKS(20));
            continue;
        }

        // fgets keeps the newline; commands_handle_line trims as needed.
        commands_handle_line(line, serial_reply);
    }
}

void serial_console_start(void)
{
#if defined(CONFIG_ESP_CONSOLE_UART)
    const int uart_num = CONFIG_ESP_CONSOLE_UART_NUM;
#else
    const int uart_num = 0;
#endif

    // Ensure the console UART has a driver and stdin/stdout are routed.
    // If the driver is already installed, uart_driver_install will return ESP_FAIL;
    // that's fine for our purposes.
    (void)uart_driver_install(uart_num, 256, 0, 0, NULL, 0);
    uart_vfs_dev_use_driver(uart_num);

    BaseType_t ok = xTaskCreate(serial_console_task, "serial_cmd", 4096, NULL, 5, NULL);
    if (ok != pdPASS) {
        ESP_LOGW(TAG, "failed to start serial console task");
        return;
    }

    ESP_LOGI(TAG, "USB serial command console started");
    serial_reply("READY\n");
}
