#pragma once
#include <stdbool.h>
#include <stdint.h>

// A clipped analog high still has an unambiguous sign. Fixed hysteresis avoids
// moving the threshold toward a stationary signal, which can invent counts.
typedef struct {
    bool initialized;
    uint8_t state;
    uint32_t forward, reverse, invalid;
} encoder_schmitt_t;

static inline int encoder_schmitt_step(encoder_schmitt_t *s, int a, int b,
                                       int center, int hysteresis)
{
    unsigned old = s->state;
    unsigned sa = old & 1, sb = (old >> 1) & 1;
    if (!s->initialized) {
        s->state = (a >= center) | ((b >= center) << 1);
        s->initialized = true;
        return 0;
    }
    if (a > center + hysteresis) sa = 1;
    else if (a < center - hysteresis) sa = 0;
    if (b > center + hysteresis) sb = 1;
    else if (b < center - hysteresis) sb = 0;
    unsigned next = sa | (sb << 1);
    s->state = next;
    if ((old ^ next) == 3) { s->invalid++; return 0; }
    // Positive follows (+,+) -> (-,+) -> (-,-) -> (+,-).
    static const int8_t delta[16] = {0,1,-1,0, -1,0,0,1, 1,0,0,-1, 0,-1,1,0};
    int step = delta[old * 4 + next];
    if (step > 0) s->forward++;
    if (step < 0) s->reverse++;
    return step;
}
