#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "esp_now.h"
#include "esp_random.h"
#include "driver/gpio.h"
#include "led_strip.h"
#include "esp_mac.h"

#define TAG "MESH_BLINKER"

#define GPIO_LED 39
#define GPIO_BUTTON 0
#define ESP_NOW_CHANNEL 1
#define LED_BRIGHTNESS_DIVISOR 10
// H-bridge pins (control two inputs of BL5617 / H-bridge)
#define HBRIDGE_PIN_A 6
#define HBRIDGE_PIN_B 7
#define HBRIDGE_CYCLE_MS 1000

static led_strip_handle_t led_strip;

typedef struct {
    uint8_t r;
    uint8_t g;
    uint8_t b;
} color_msg_t;

static void save_color(uint8_t r, uint8_t g, uint8_t b) {
    nvs_handle_t my_handle;
    esp_err_t err = nvs_open("storage", NVS_READWRITE, &my_handle);
    if (err == ESP_OK) {
        nvs_set_u8(my_handle, "r", r);
        nvs_set_u8(my_handle, "g", g);
        nvs_set_u8(my_handle, "b", b);
        nvs_commit(my_handle);
        nvs_close(my_handle);
    }
}

static void load_color(uint8_t *r, uint8_t *g, uint8_t *b) {
    nvs_handle_t my_handle;
    esp_err_t err = nvs_open("storage", NVS_READWRITE, &my_handle);
    if (err == ESP_OK) {
        nvs_get_u8(my_handle, "r", r);
        nvs_get_u8(my_handle, "g", g);
        nvs_get_u8(my_handle, "b", b);
        nvs_close(my_handle);
    }
}

static void update_led(uint8_t r, uint8_t g, uint8_t b) {
    if (led_strip) {
        // Scale down brightness
        led_strip_set_pixel(led_strip, 0, r / LED_BRIGHTNESS_DIVISOR, g / LED_BRIGHTNESS_DIVISOR, b / LED_BRIGHTNESS_DIVISOR);
        led_strip_set_pixel(led_strip, 1, r / LED_BRIGHTNESS_DIVISOR, g / LED_BRIGHTNESS_DIVISOR, b / LED_BRIGHTNESS_DIVISOR);
        led_strip_refresh(led_strip);
        ESP_LOGI(TAG, "LED Updated: R=%d G=%d B=%d", r, g, b);
    }
}

typedef enum {
    HBRIDGE_STOP = 0,
    HBRIDGE_FORWARD,
    HBRIDGE_REVERSE,
} hbridge_state_t;

static volatile hbridge_state_t hbridge_state = HBRIDGE_STOP;
static volatile int hbridge_advance_request = 0;

static void hbridge_set_state(hbridge_state_t s) {
    switch (s) {
        case HBRIDGE_STOP:
            gpio_set_level(HBRIDGE_PIN_A, 0);
            gpio_set_level(HBRIDGE_PIN_B, 0);
            break;
        case HBRIDGE_FORWARD:
            gpio_set_level(HBRIDGE_PIN_A, 1);
            gpio_set_level(HBRIDGE_PIN_B, 0);
            break;
        case HBRIDGE_REVERSE:
            gpio_set_level(HBRIDGE_PIN_A, 0);
            gpio_set_level(HBRIDGE_PIN_B, 1);
            break;
    }
}

static void hbridge_task(void *pvParameter) {
    while (1) {
        if (hbridge_advance_request) {
            hbridge_advance_request = 0;
            hbridge_state = (hbridge_state + 1) % 3;
            hbridge_set_state(hbridge_state);
            ESP_LOGI(TAG, "H-bridge advanced (LED change): %d", hbridge_state);
        } else {
            vTaskDelay(pdMS_TO_TICKS(HBRIDGE_CYCLE_MS));
            hbridge_state = (hbridge_state + 1) % 3;
            hbridge_set_state(hbridge_state);
            ESP_LOGI(TAG, "H-bridge cycled: %d", hbridge_state);
        }
    }
}

void notify_led_changed(void) {
    hbridge_advance_request = 1;
}

// Input a value 0 to 255 to get a color value.
// The colours are a transition r - g - b - back to r.
static void wheel(uint8_t wheel_pos, uint8_t *r, uint8_t *g, uint8_t *b) {
    wheel_pos = 255 - wheel_pos;
    if (wheel_pos < 85) {
        *r = 255 - wheel_pos * 3;
        *g = 0;
        *b = wheel_pos * 3;
    } else if (wheel_pos < 170) {
        wheel_pos -= 85;
        *r = 0;
        *g = wheel_pos * 3;
        *b = 255 - wheel_pos * 3;
    } else {
        wheel_pos -= 170;
        *r = wheel_pos * 3;
        *g = 255 - wheel_pos * 3;
        *b = 0;
    }
}

static void on_data_recv(const esp_now_recv_info_t *recv_info, const uint8_t *data, int len) {
    if (len == sizeof(color_msg_t)) {
        color_msg_t *msg = (color_msg_t *)data;
        ESP_LOGI(TAG, "Received Color: R=%d G=%d B=%d from " MACSTR, msg->r, msg->g, msg->b, MAC2STR(recv_info->src_addr));
        update_led(msg->r, msg->g, msg->b);
        save_color(msg->r, msg->g, msg->b);
        // Notify H-bridge controller that LED changed (advance state)
        extern void notify_led_changed(void);
        notify_led_changed();
    }
}

static void on_data_sent(const esp_now_send_info_t *info, esp_now_send_status_t status) {
    ESP_LOGI(TAG, "Last Packet Send Status: %s", status == ESP_NOW_SEND_SUCCESS ? "Delivery Success" : "Delivery Fail");
}

static void wifi_init(void) {
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_channel(ESP_NOW_CHANNEL, WIFI_SECOND_CHAN_NONE));
}

static void esp_now_init_func(void) {
    ESP_ERROR_CHECK(esp_now_init());
    ESP_ERROR_CHECK(esp_now_register_recv_cb(on_data_recv));
    ESP_ERROR_CHECK(esp_now_register_send_cb(on_data_sent));

    // Add broadcast peer
    esp_now_peer_info_t peerInfo = {};
    memset(&peerInfo, 0, sizeof(peerInfo));
    memset(peerInfo.peer_addr, 0xFF, 6); // Broadcast address
    peerInfo.channel = ESP_NOW_CHANNEL;
    peerInfo.encrypt = false;

    if (esp_now_add_peer(&peerInfo) != ESP_OK) {
        ESP_LOGE(TAG, "Failed to add peer");
        return;
    }
}

static void configure_led(void) {
    led_strip_config_t strip_config = {
        .strip_gpio_num = GPIO_LED,
        .max_leds = 2,
    };
    led_strip_rmt_config_t rmt_config = {
        .resolution_hz = 10 * 1000 * 1000, // 10MHz
        .flags.with_dma = false,
    };
    ESP_ERROR_CHECK(led_strip_new_rmt_device(&strip_config, &rmt_config, &led_strip));
    led_strip_clear(led_strip);
}

static void button_task(void *pvParameter) {
    gpio_config_t io_conf = {};
    io_conf.intr_type = GPIO_INTR_DISABLE;
    io_conf.mode = GPIO_MODE_INPUT;
    io_conf.pin_bit_mask = (1ULL << GPIO_BUTTON);
    io_conf.pull_down_en = 0;
    io_conf.pull_up_en = 1;
    gpio_config(&io_conf);

    int last_state = 1; // Button is pulled up, so default is 1

    while (1) {
        int current_state = gpio_get_level(GPIO_BUTTON);
        if (last_state == 1 && current_state == 0) {
            // Button pressed
            ESP_LOGI(TAG, "Button Pressed!");
            
            // Generate distinct random color
            color_msg_t msg;
            uint8_t wheel_pos = esp_random() % 256;
            wheel(wheel_pos, &msg.r, &msg.g, &msg.b);

            // Update local
            update_led(msg.r, msg.g, msg.b);
            save_color(msg.r, msg.g, msg.b);
            // Notify H-bridge controller that a local button caused a color change
            extern void notify_led_changed(void);
            notify_led_changed();

            // Broadcast
            uint8_t broadcastAddress[] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
            esp_err_t result = esp_now_send(broadcastAddress, (uint8_t *) &msg, sizeof(msg));
            
            if (result == ESP_OK) {
                ESP_LOGI(TAG, "Sent with success");
            } else {
                ESP_LOGE(TAG, "Error sending the data");
            }

            vTaskDelay(pdMS_TO_TICKS(200)); // Debounce
        }
        last_state = current_state;
        vTaskDelay(pdMS_TO_TICKS(50));
    }
}

void app_main(void) {
    // Initialize NVS
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    configure_led();
    
    // Restore last color
    uint8_t r = 0, g = 0, b = 0;
    load_color(&r, &g, &b);
    if (r != 0 || g != 0 || b != 0) {
        update_led(r, g, b);
    }

    wifi_init();
    esp_now_init_func();

    // Configure H-bridge GPIO pins as outputs and set initial state
    gpio_config_t hb_conf = {};
    hb_conf.intr_type = GPIO_INTR_DISABLE;
    hb_conf.mode = GPIO_MODE_OUTPUT;
    hb_conf.pin_bit_mask = (1ULL << HBRIDGE_PIN_A) | (1ULL << HBRIDGE_PIN_B);
    hb_conf.pull_down_en = 0;
    hb_conf.pull_up_en = 0;
    gpio_config(&hb_conf);
    hbridge_set_state(hbridge_state);

    xTaskCreate(hbridge_task, "hbridge_task", 2048, NULL, 10, NULL);

    xTaskCreate(button_task, "button_task", 4096, NULL, 10, NULL);
}
