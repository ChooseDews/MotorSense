#include "ota_update.h"
#ifdef ESP_PLATFORM
#include "sdkconfig.h"
#ifndef CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE
#error "MotorSense requires an OTA rollback-enabled bootloader; use sdkconfig.defaults"
#endif
#endif
#include "esp_ota_ops.h"
#include "esp_app_desc.h"
#include "esp_system.h"
#include "mbedtls/sha256.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdatomic.h>

static atomic_bool active;
static atomic_bool binary_mode;
static esp_ota_handle_t handle;
static const esp_partition_t *partition;
static size_t expected, received;
static unsigned char digest[32];
static mbedtls_sha256_context sha;
#define OTA_BIN_HEADER_SIZE 9
#define OTA_BIN_MAX_DATA 500
#define OTA_BIN_FRAME_SIZE (OTA_BIN_HEADER_SIZE + OTA_BIN_MAX_DATA)
static unsigned char binary_frame[OTA_BIN_FRAME_SIZE];
static size_t binary_frame_used, binary_frame_expected;
extern void commands_stop_for_ota(void);
bool ota_update_active(void) { return atomic_load(&active); }
bool ota_update_binary_active(void) { return atomic_load(&binary_mode); }
static int nibble(char c) {
    if(c>='0' && c<='9') return c-'0';
    if(c>='a' && c<='f') return c-'a'+10;
    if(c>='A' && c<='F') return c-'A'+10;
    return -1;
}
static bool hex(const char *s, unsigned char *out, size_t n) {
    for(size_t i=0;i<n;i++) { int a=nibble(s[2*i]), b=nibble(s[2*i+1]);
        if(a<0 || b<0) return false;
        out[i]=(a<<4)|b;
    }
    return true;
}
static void abort_update(void) {
    binary_mode=false;binary_frame_used=0;binary_frame_expected=0;
    if(active) {esp_ota_abort(handle);mbedtls_sha256_free(&sha);active=false;}
}

static bool begin_update(unsigned long size, const char *hash, bool binary, commands_reply_fn reply) {
    if(active || strlen(hash)!=64 || !hex(hash,digest,32)) return false;
    partition=esp_ota_get_next_update_partition(NULL);
    if(!partition || partition==esp_ota_get_running_partition() || size<sizeof(esp_app_desc_t)+32 || size>partition->size) return false;
    commands_stop_for_ota();
    if(esp_ota_begin(partition,size,&handle)!=ESP_OK) return false;
    expected=size;received=0;binary_frame_used=0;binary_frame_expected=0;
    mbedtls_sha256_init(&sha);
    if(mbedtls_sha256_starts(&sha,0)!=0) {esp_ota_abort(handle);mbedtls_sha256_free(&sha);return false;}
    active=true;binary_mode=binary;reply(binary ? "OK OTA BIN BEGIN\n" : "OK OTA BEGIN\n");return true;
}

static uint32_t read_u32_le(const unsigned char *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1]<<8) | ((uint32_t)p[2]<<16) | ((uint32_t)p[3]<<24);
}

static uint16_t read_u16_le(const unsigned char *p) {
    return (uint16_t)p[0] | ((uint16_t)p[1]<<8);
}

void ota_update_binary_feed(const uint8_t *data, size_t len, commands_reply_fn reply) {
    if(!binary_mode || data==NULL || reply==NULL) return;
    for(size_t i=0;i<len;i++) {
        if(binary_frame_used>=sizeof(binary_frame)) goto malformed;
        binary_frame[binary_frame_used++]=data[i];
        if(binary_frame_used==1 && binary_frame[0]!=0x4d) goto malformed;
        if(binary_frame_used==2 && binary_frame[1]!=0x53) goto malformed;
        if(binary_frame_used==OTA_BIN_HEADER_SIZE) {
            size_t payload=read_u16_le(&binary_frame[6]);
            if(payload==0 || payload>OTA_BIN_MAX_DATA || (binary_frame[8]&~1u)) goto malformed;
            binary_frame_expected=OTA_BIN_HEADER_SIZE+payload;
        }
        if(binary_frame_expected && binary_frame_used==binary_frame_expected) {
            size_t payload=binary_frame_expected-OTA_BIN_HEADER_SIZE;
            size_t offset=read_u32_le(&binary_frame[2]);
            if(offset!=received || payload>expected-received ||
               esp_ota_write(handle,binary_frame+OTA_BIN_HEADER_SIZE,payload)!=ESP_OK ||
               mbedtls_sha256_update(&sha,binary_frame+OTA_BIN_HEADER_SIZE,payload)!=0) {
                abort_update();reply("ERR OTA binary offset or flash write\n");return;
            }
            received+=payload;
            bool acknowledge=(binary_frame[8]&1u)!=0;
            binary_frame_used=0;binary_frame_expected=0;
            if(received==expected) binary_mode=false;
            if(acknowledge) {
                char response[48];snprintf(response,sizeof(response),"OK OTA BIN %u\n",(unsigned)received);reply(response);
            }
        }
    }
    return;
malformed:
    abort_update();reply("ERR OTA malformed binary frame\n");
}

bool ota_update_command(const char *line, commands_reply_fn reply) {
    if(strncmp(line,"OTA ",4)) return false;
    char b[128], hash[65], extra;
    unsigned long size, offset;
    if(!strcmp(line,"OTA STATUS")) {
        snprintf(b,sizeof(b),"OTA active=%d received=%u size=%u running=%s\n",active,(unsigned)received,(unsigned)expected,esp_ota_get_running_partition()->label);
        reply(b);return true;
    }
    if(!strcmp(line,"OTA ABORT")) {abort_update();reply("OK OTA ABORT\n");return true;}
    if(sscanf(line,"OTA BIN BEGIN %lu %64s %c",&size,hash,&extra)==2) {
        if(!begin_update(size,hash,true,reply)) goto invalid;
        return true;
    }
    if(sscanf(line,"OTA BEGIN %lu %64s %c",&size,hash,&extra)==2) {
        if(!begin_update(size,hash,false,reply)) goto invalid;
        return true;
    }
    // Retain the ASCII data command for serial/manual recovery; BLE uses the
    // binary framed path above to avoid hex expansion and per-chunk ACKs.
    char data[481];
    if(sscanf(line,"OTA DATA %lu %480s %c",&offset,data,&extra)==2) {
        size_t n=strlen(data)/2; unsigned char bytes[240];
        if(!active || strlen(data)%2 || n==0 || offset!=received || n>expected-received || !hex(data,bytes,n)) goto invalid;
        if(esp_ota_write(handle,bytes,n)!=ESP_OK || mbedtls_sha256_update(&sha,bytes,n)!=0) {abort_update();goto invalid;}
        received+=n;snprintf(b,sizeof(b),"OK OTA DATA %u\n",(unsigned)received);reply(b);return true;
    }
    if(!strcmp(line,"OTA END")) {
        unsigned char actual[32];
        if(!active || received!=expected) goto invalid;
        if(mbedtls_sha256_finish(&sha,actual)!=0 || memcmp(actual,digest,32)) {abort_update();goto invalid;}
        mbedtls_sha256_free(&sha);
        esp_err_t e=esp_ota_end(handle);active=false;
        if(e!=ESP_OK) goto invalid;
        esp_app_desc_t desc;
        if(esp_ota_get_partition_description(partition,&desc)!=ESP_OK ||
           memcmp(desc.project_name,esp_app_get_description()->project_name,sizeof(desc.project_name))) goto invalid;
        if(esp_ota_set_boot_partition(partition)!=ESP_OK) goto invalid;
        reply("OK OTA REBOOT\n");vTaskDelay(pdMS_TO_TICKS(1000));esp_restart();return true;
    }
invalid: reply("ERR OTA state, offset, image or arguments; OTA STATUS / OTA ABORT\n");return true;
}
