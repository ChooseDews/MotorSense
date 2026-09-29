#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <math.h>
#include <stdlib.h>

// Portable axis-only controller. Caller serializes access and supplies fresh
// encoder samples. Duty sign is in encoder coordinates, not H-bridge wiring.
typedef enum { AXIS_IDLE, AXIS_RUNNING, AXIS_SETTLING, AXIS_DONE,
               AXIS_CANCELLED, AXIS_TIMEOUT, AXIS_STALL, AXIS_SENSOR_FAULT,
               AXIS_WRONG_DIRECTION } axis_motion_state_t;
typedef struct {
    axis_motion_state_t state;
    int32_t start, target, last_position, pulse_start;
    int max_duty, duty, pulse_sign, corrections;
    double deg_per_tick, velocity;
    int64_t started_us, last_us, settle_us, progress_us, pulse_us, timeout_us;
    int32_t best_error;
} axis_motion_t;
static inline bool axis_motion_active(const axis_motion_t *m) {
    return m->state == AXIS_RUNNING || m->state == AXIS_SETTLING;
}
static inline void axis_motion_stop(axis_motion_t *m, axis_motion_state_t state) {
    m->state=state; m->duty=0;
}
static inline bool axis_motion_begin(axis_motion_t *m, int32_t position,
                                    double delta_deg, double scale, int max_duty, int64_t now) {
    if (!isfinite(delta_deg) || fabs(delta_deg)>180 || !isfinite(scale) || scale<=0 ||
        max_duty<35 || max_duty>100 || axis_motion_active(m)) return false;
    double ticks=round(delta_deg/scale), target=(double)position+ticks;
    if (ticks == 0) return false;
    if (target<INT32_MIN || target>INT32_MAX) return false;
    *m=(axis_motion_t){.state=AXIS_RUNNING,.start=position,.target=(int32_t)target,
        .last_position=position,.pulse_start=position,.max_duty=max_duty,
        .deg_per_tick=scale,.started_us=now,.last_us=now,.progress_us=now,
        .pulse_us=now,.best_error=(int32_t)fabs(ticks),
        // 30 s base for short moves plus 1 s per degree beyond 30 so a large
        // slew is not killed by the watchdog mid-travel; the 1.2 s stall check
        // still bounds genuinely stuck moves.
        .timeout_us=30000000+(int64_t)(fmax(0.,fabs(delta_deg)-30.)*1000000.)};
    return true;
}
static inline int axis_motion_step(axis_motion_t *m, int32_t pos, int64_t now, bool healthy) {
    if (!axis_motion_active(m)) return 0;
    if (!healthy) { axis_motion_stop(m,AXIS_SENSOR_FAULT); return 0; }
    if (now-m->started_us>m->timeout_us) { axis_motion_stop(m,AXIS_TIMEOUT); return 0; }
    double dt=(now-m->last_us)*1e-6;
    if (dt>0) m->velocity=.75*m->velocity+.25*((double)pos-m->last_position)*m->deg_per_tick/dt;
    m->last_position=pos; m->last_us=now;
    int64_t error=(int64_t)m->target-pos;
    double remaining=fabs((double)error)*m->deg_per_tick;
    int sign=error>0?1:-1;
    if (m->state==AXIS_SETTLING) {
        m->duty=0;
        if (now-m->settle_us<350000) return 0;
        if (llabs(error)<=2) { axis_motion_stop(m,AXIS_DONE); return 0; }
        if (++m->corrections>30) { axis_motion_stop(m,AXIS_STALL); return 0; }
        m->state=AXIS_RUNNING; m->pulse_start=pos; m->pulse_us=now;
        m->progress_us=now; m->best_error=(int32_t)llabs(error);m->pulse_sign=0;
    }
    // Detect sustained reversed feedback before it can drive away from target.
    if (m->pulse_sign && ((int64_t)pos-m->pulse_start)*m->pulse_sign < -5) {
        axis_motion_stop(m,AXIS_WRONG_DIRECTION);return 0;
    }
    if (llabs(error)<m->best_error) {m->best_error=(int32_t)llabs(error);m->progress_us=now;}
    if (now-m->progress_us>1200000) {axis_motion_stop(m,AXIS_STALL);return 0;}
    double speed_toward=m->velocity*sign;
    // Coast to rest before deciding whether a low-duty correction is needed.
    if (llabs(error)<=1 || (m->pulse_sign && sign!=m->pulse_sign) ||
        (speed_toward>0.2 && remaining<=speed_toward*.10+.025) ||
        (m->corrections>0 && remaining<.25 && now-m->pulse_us>=80000)) {
        m->state=AXIS_SETTLING; m->settle_us=now;m->duty=0;return 0;
    }
    double lead=fmax(0.,speed_toward)*.15;
    int duty=(int)lround(35+15*fmax(0.,remaining-lead-.25));
    if (duty>m->max_duty)duty=m->max_duty;
    // Ramp startup to limit abrupt acceleration; still reaches requested max.
    int ramp=35+(int)((now-m->pulse_us)/10000)*3;
    if(duty>ramp)duty=ramp;
    m->pulse_sign=sign;m->duty=sign*duty;return m->duty;
}
static inline const char *axis_motion_state_name(axis_motion_state_t s) {
    static const char *names[]={"IDLE","RUNNING","SETTLING","DONE","CANCELLED",
        "TIMEOUT","STALL","SENSOR_FAULT","WRONG_DIRECTION"};
    return s<=AXIS_WRONG_DIRECTION?names[s]:"UNKNOWN";
}
