idf_component_register(SRCS "bt_main.c" "commands.c" "serial_console.c" "can_iface.c" "can_rpc.c" "quadrature_encoder.c" "adc_inputs.c"
                    PRIV_REQUIRES esp_wifi nvs_flash esp_event esp_timer driver esp_adc led_strip bt vfs esp_vfs_console
                    INCLUDE_DIRS "include")
