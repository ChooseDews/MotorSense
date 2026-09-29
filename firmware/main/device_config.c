#include "device_config.h"
#include "nvs.h"
#include "esp_app_desc.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <math.h>

static device_config_t current, pending;
const device_config_t *device_config_get(void) { return &current; }
static bool valid(const device_config_t *c) {
    return (!strcmp(c->role,"YAW") || !strcmp(c->role,"PITCH")) &&
        c->counts_rev >= 100 && c->counts_rev <= 10000000 &&
        isfinite(c->min_deg) && isfinite(c->max_deg) && isfinite(c->zero_deg) &&
        c->min_deg >= -360 && c->max_deg <= 360 && c->min_deg < c->max_deg &&
        c->zero_deg >= c->min_deg && c->zero_deg <= c->max_deg &&
        c->motor_center >= 101 && c->motor_center <= 3995 &&
        c->motor_hysteresis >= 1 && c->motor_hysteresis <= 100 &&
        c->motor_invert <= 1 && c->axis_invert <= 1 &&
        c->encoder_motor_invert <= 1 && c->motor_encoder_enabled <= 1;
}
static esp_err_t save(const device_config_t *c) {
    nvs_handle_t h;
    esp_err_t e = nvs_open("motorsense", NVS_READWRITE, &h);
    if(e != ESP_OK) return e;
    // One versioned blob commits the complete configuration atomically.
    e = nvs_set_blob(h,"config_v1",c,sizeof(*c));
    if(e == ESP_OK) e = nvs_commit(h);
    nvs_close(h); return e;
}
esp_err_t device_config_init(void) {
    current = (device_config_t){.role="YAW",.hardware="unknown",.counts_rev=11840,
        .min_deg=-180,.max_deg=180,.zero_deg=0,.encoder_motor_invert=1,
        .motor_center=3600,.motor_hysteresis=100};
    nvs_handle_t h;
    esp_err_t e = nvs_open("motorsense",NVS_READWRITE,&h);
    if(e != ESP_OK) return e;
    size_t n = sizeof(current);
    e = nvs_get_blob(h,"config_v1",&current,&n);
    nvs_close(h);
    if(e == ESP_ERR_NVS_NOT_FOUND) e = save(&current);
    if(e != ESP_OK) return e;
    if(n != sizeof(current) || !memchr(current.role,0,sizeof(current.role)) ||
       !memchr(current.hardware,0,sizeof(current.hardware)) || !valid(&current)) return ESP_ERR_INVALID_STATE;
    pending = current; return ESP_OK;
}
static void report(commands_reply_fn reply) {
    char b[256];
    snprintf(b,sizeof(b),"DEVICE role=%s firmware=%s hardware=%s project=%s\n",current.role,
        esp_app_get_description()->version,current.hardware,esp_app_get_description()->project_name); reply(b);
    snprintf(b,sizeof(b),"CALIBRATION counts_rev=%lu counts_degree=%.9f degrees_count=%.10f min=%.3f max=%.3f zero=%.3f\n",
        (unsigned long)current.counts_rev,current.counts_rev/360.0,360.0/current.counts_rev,current.min_deg,current.max_deg,current.zero_deg); reply(b);
    snprintf(b,sizeof(b),"CONFIG motor_invert=%u axis_invert=%u encoder_motor_invert=%u motor_encoder_enabled=%u motor_center=%u motor_hysteresis=%u pending_reboot=%d\n",
        current.motor_invert,current.axis_invert,current.encoder_motor_invert,current.motor_encoder_enabled,current.motor_center,current.motor_hysteresis,memcmp(&current,&pending,sizeof(current))!=0); reply(b);
}
bool device_config_command(const char *line, commands_reply_fn reply, bool idle) {
    if(!strcasecmp(line,"DEVICE INFO") || !strcasecmp(line,"DEVICE ROLE") ||
       !strcasecmp(line,"DEVICE HW") || !strcasecmp(line,"CALIBRATION") || !strcasecmp(line,"VERSION")) {report(reply);return true;}
    if(strncasecmp(line,"DEVICE ",7)) return false;
    if(!idle) {reply("ERR motor busy\n");return true;}
    device_config_t c = pending;
    char key[32], value[32], extra;
    if(sscanf(line,"DEVICE ROLE %31s %c",value,&extra)==1) {
        if(strcasecmp(value,"YAW") && strcasecmp(value,"PITCH")) goto bad;
        strcpy(c.role,!strcasecmp(value,"YAW")?"YAW":"PITCH");
        // Role does not silently overwrite measured per-device settings.
    } else if(sscanf(line,"DEVICE HW %31s %c",value,&extra)==1) {
        if(strlen(value)>=sizeof(c.hardware)) goto bad;
        strcpy(c.hardware,value);
    } else if(sscanf(line,"DEVICE SET %31s %31s %c",key,value,&extra)==2) {
        char *end; double v=strtod(value,&end);
        if(*end || end==value || !isfinite(v)) goto bad;
        if(!strcmp(key,"min_deg")) c.min_deg=v;
        else if(!strcmp(key,"max_deg")) c.max_deg=v;
        else if(!strcmp(key,"zero_deg")) c.zero_deg=v;
        else {
            if(v<0 || v>10000000 || floor(v)!=v) goto bad;
            if(!strcmp(key,"counts_rev")) c.counts_rev=(uint32_t)v;
            else if(!strcmp(key,"motor_center") && v<=3995) c.motor_center=v;
            else if(!strcmp(key,"motor_hysteresis") && v<=100) c.motor_hysteresis=v;
            else if(v<=1 && !strcmp(key,"motor_invert")) c.motor_invert=v;
            else if(v<=1 && !strcmp(key,"axis_invert")) c.axis_invert=v;
            else if(v<=1 && !strcmp(key,"encoder_motor_invert")) c.encoder_motor_invert=v;
            else if(v<=1 && !strcmp(key,"motor_encoder_enabled")) c.motor_encoder_enabled=v;
            else goto bad;
        }
    } else goto bad;
    if(!valid(&c)) goto bad;
    if(save(&c)!=ESP_OK) {reply("ERR NVS save\n");return true;}
    pending=c;reply("OK saved; reboot to apply\n");return true;
bad: reply("ERR invalid DEVICE configuration\n");return true;
}
