#include <assert.h>
#include "encoder_schmitt.h"
int main(void) {
 encoder_schmitt_t s={0};
 assert(encoder_schmitt_step(&s,4095,4095,2800,100)==0);
 assert(encoder_schmitt_step(&s,1500,4095,2800,100)==1);
 assert(encoder_schmitt_step(&s,1500,1500,2800,100)==1);
 assert(encoder_schmitt_step(&s,4095,1500,2800,100)==1);
 assert(encoder_schmitt_step(&s,4095,4095,2800,100)==1);
 assert(encoder_schmitt_step(&s,4095,1500,2800,100)==-1);
 assert(encoder_schmitt_step(&s,1500,1500,2800,100)==-1);
 assert(encoder_schmitt_step(&s,1500,4095,2800,100)==-1);
 assert(encoder_schmitt_step(&s,4095,4095,2800,100)==-1);
 for(int i=0;i<100;i++) assert(encoder_schmitt_step(&s,2750+i%100,2800,2800,100)==0);
 assert(encoder_schmitt_step(&s,1500,1500,2800,100)==0);
 assert(s.invalid==1 && s.forward==4 && s.reverse==4);
}
