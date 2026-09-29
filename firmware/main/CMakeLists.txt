idf_component_register(SRCS "bt_main.c" "commands.c" "serial_console.c" "device_config.c" "ota_update.c" "quadrature_encoder.c" "adc_inputs.c"
                    PRIV_REQUIRES app_update mbedtls esp_app_format esp_wifi nvs_flash esp_event esp_timer driver esp_adc led_strip bt vfs esp_vfs_console
                    INCLUDE_DIRS "include")
