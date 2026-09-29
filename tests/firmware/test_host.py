"""Compile real firmware modules against fault-injecting host IDF stubs."""
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
STUB = r'''
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_ERR_NVS_NOT_FOUND 1
#define ESP_ERR_INVALID_STATE 2
#define NVS_READWRITE 0
typedef int nvs_handle_t;
int nvs_open(const char*, int, nvs_handle_t*);
int nvs_get_blob(int,const char*,void*,size_t*);
int nvs_set_blob(int,const char*,const void*,size_t);
int nvs_commit(int);
void nvs_close(int);
typedef struct {char version[32],project_name[32];} esp_app_desc_t;
const esp_app_desc_t *esp_app_get_description(void);
typedef int esp_ota_handle_t;
typedef struct {size_t size; const char *label;} esp_partition_t;
const esp_partition_t *esp_ota_get_running_partition(void);
const esp_partition_t *esp_ota_get_next_update_partition(void*);
int esp_ota_begin(const esp_partition_t*,size_t,int*);
int esp_ota_write(int,const void*,size_t);
int esp_ota_abort(int);
int esp_ota_end(int);
int esp_ota_get_partition_description(const esp_partition_t*,esp_app_desc_t*);
int esp_ota_set_boot_partition(const esp_partition_t*);
void esp_restart(void);
#define pdMS_TO_TICKS(x) (x)
void vTaskDelay(int);
typedef int mbedtls_sha256_context;
void mbedtls_sha256_init(int*);
void mbedtls_sha256_free(int*);
int mbedtls_sha256_starts(int*,int);
int mbedtls_sha256_update(int*,const void*,size_t);
int mbedtls_sha256_finish(int*,unsigned char*);
'''
HARNESS = r'''
#include "stub.h"
#include "device_config.h"
#include "ota_update.h"
#include "axis_motion.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
static unsigned char stored[512];
static size_t stored_size;
static int fail_save, fail_write, fail_end, wrong_project, activated, restarted, stopped, aborted;
static char response[2048];
static esp_app_desc_t app={.version="1.0.0",.project_name="motorsense"};
static esp_partition_t running={4096,"ota_0"},next={4096,"ota_1"};
int nvs_open(const char*a,int b,int*c){*c=1;return 0;}
int nvs_get_blob(int h,const char*k,void*p,size_t*n){if(!stored_size)return ESP_ERR_NVS_NOT_FOUND;memcpy(p,stored,stored_size);*n=stored_size;return 0;}
int nvs_set_blob(int h,const char*k,const void*p,size_t n){if(fail_save)return 3;memcpy(stored,p,n);stored_size=n;return 0;}
int nvs_commit(int h){return 0;} void nvs_close(int h){}
const esp_app_desc_t *esp_app_get_description(void){return &app;}
const esp_partition_t *esp_ota_get_running_partition(void){return &running;}
const esp_partition_t *esp_ota_get_next_update_partition(void*p){return &next;}
int esp_ota_begin(const esp_partition_t*p,size_t n,int*h){assert(p!=&running);*h=1;return 0;}
int esp_ota_write(int h,const void*p,size_t n){return fail_write;}
int esp_ota_abort(int h){aborted++;return 0;}
int esp_ota_end(int h){return fail_end;}
int esp_ota_get_partition_description(const esp_partition_t*p,esp_app_desc_t*d){*d=app;if(wrong_project)strcpy(d->project_name,"other");return 0;}
int esp_ota_set_boot_partition(const esp_partition_t*p){activated++;return 0;}
void esp_restart(void){restarted++;} void vTaskDelay(int t){}
void commands_stop_for_ota(void){stopped++;}
void mbedtls_sha256_init(int*p){} void mbedtls_sha256_free(int*p){}
int mbedtls_sha256_starts(int*p,int n){return 0;}
int mbedtls_sha256_update(int*p,const void*b,size_t n){return 0;}
int mbedtls_sha256_finish(int*p,unsigned char*b){memset(b,0,32);return 0;}
static void reply(const char*s){strncat(response,s,sizeof(response)-strlen(response)-1);}
static void ota(const char*s,bool ok){response[0]=0;assert(ota_update_command(s,reply));assert((strncmp(response,"OK",2)==0)==ok);}
static void cfg(const char*s,bool ok){response[0]=0;assert(device_config_command(s,reply,true));assert((strncmp(response,"OK",2)==0)==ok);}
static void begin_size(unsigned size){char b[128];sprintf(b,"OTA BEGIN %u 0000000000000000000000000000000000000000000000000000000000000000",size);ota(b,true);}
static void begin(void){begin_size(192);}
static void data_n(unsigned offset,unsigned nbytes){char b[512];int n=sprintf(b,"OTA DATA %u ",offset);memset(b+n,'0',nbytes*2);b[n+nbytes*2]=0;ota(b,true);}
static void data(unsigned offset){data_n(offset,96);}
static void binary_data(unsigned offset,unsigned length,unsigned flags){
 unsigned char frame[509]={0x4d,0x53};frame[2]=offset;frame[3]=offset>>8;frame[4]=offset>>16;frame[5]=offset>>24;
 frame[6]=length;frame[7]=length>>8;frame[8]=flags;
 response[0]=0;ota_update_binary_feed(frame,5,reply);assert(response[0]==0);
 ota_update_binary_feed(frame+5,4,reply);assert(response[0]==0);
 memset(frame+9,0,length);ota_update_binary_feed(frame+9,length,reply);
}
int main(void){
 assert(device_config_init()==0); assert(device_config_get()->counts_rev==11840);
 cfg("DEVICE ROLE PITCH",true); cfg("DEVICE SET motor_invert 1",true);
 assert(!strcmp(device_config_get()->role,"YAW"));
 assert(device_config_init()==0);assert(!strcmp(device_config_get()->role,"PITCH"));assert(device_config_get()->motor_invert);
 cfg("DEVICE ROLE INVALID",false);cfg("DEVICE SET counts_rev 0",false);
 cfg("DEVICE SET zero_deg nan",false);cfg("DEVICE SET axis_invert 257",false);
 fail_save=1;cfg("DEVICE ROLE YAW",false);fail_save=0;
 assert(device_config_init()==0);assert(!strcmp(device_config_get()->role,"PITCH"));
 ota("OTA DATA 0 aa",false);ota("OTA BEGIN 99999 00",false);
 begin();assert(stopped==1);ota("OTA DATA 1 aa",false);ota("OTA DATA 0 xx",false);ota("OTA END",false);
 data(0);ota("OTA DATA 0 aa",false);data(96);ota("OTA DATA 192 aa",false);
 ota("OTA END",true);assert(activated==1 && restarted==1 && !ota_update_active());
 ota("OTA BIN BEGIN 192 0000000000000000000000000000000000000000000000000000000000000000",true);
 assert(ota_update_binary_active());binary_data(0,96,0);assert(response[0]==0);
 binary_data(96,96,1);assert(!ota_update_binary_active() && !strcmp(response,"OK OTA BIN 192\n"));
 ota("OTA END",true);assert(activated==2 && restarted==2 && !ota_update_active());
 begin();fail_write=1;ota("OTA DATA 0 aa",false);assert(!ota_update_active());fail_write=0;
 begin();data(0);data(96);fail_end=1;ota("OTA END",false);fail_end=0;
 begin();data(0);data(96);wrong_project=1;ota("OTA END",false);wrong_project=0;
 ota("OTA BEGIN 192 1000000000000000000000000000000000000000000000000000000000000000",true);
 data(0);data(96);ota("OTA END",false);assert(activated==2 && restarted==2);
 begin();ota("OTA ABORT",true);assert(!ota_update_active() && aborted>=3);
 begin_size(480);data_n(0,240);data_n(240,240);ota("OTA END",true);assert(activated==3 && restarted==3);
 begin();{char b[512];int n=sprintf(b,"OTA DATA 0 ");memset(b+n,'0',482);b[n+482]=0;ota(b,false);}ota("OTA ABORT",true);assert(aborted>=4);
 assert(device_config_init()==0);assert(!strcmp(device_config_get()->role,"PITCH"));
 axis_motion_t motion={0};assert(axis_motion_begin(&motion,0,360.0/11840,360.0/11840,80,0));assert(motion.target==1);
 puts("configuration persistence and OTA failure-path tests passed");
}
'''

class FirmwareTests(unittest.TestCase):
    def test_firmware(self):
        with tempfile.TemporaryDirectory() as directory:
            d = pathlib.Path(directory)
            (d / 'stub.h').write_text(STUB)
            for name in ['esp_err.h', 'nvs.h', 'esp_app_desc.h', 'esp_ota_ops.h', 'esp_system.h',
                         'mbedtls/sha256.h', 'freertos/FreeRTOS.h', 'freertos/task.h']:
                p = d / name
                p.parent.mkdir(exist_ok=True)
                p.write_text('#include "stub.h"\n')
            (d / 'test.c').write_text(HARNESS)
            subprocess.run(['cc', '-std=c11', '-I'+str(d), '-I'+str(ROOT/'main/include'),
                            str(d/'test.c'), str(ROOT/'main/device_config.c'), str(ROOT/'main/ota_update.c'),
                            '-lm', '-o', str(d/'test')], check=True)
            subprocess.run([str(d/'test')], check=True)

if __name__ == '__main__':
    unittest.main()
