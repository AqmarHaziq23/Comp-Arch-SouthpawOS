/*
 * Based on: Accelerometer TinyShield example by Laverena Wienclaw for TinyCircuits,
 *   https://learn.tinycircuits.com/Sensors/Accelerometer_TinyShield_Tutorial/
 * Modified by: Yue Ming (Team P1D) – fixed-rate 200 Hz sampling at +/-16 g into a ring buffer.
 */
#ifndef SOUTHPAW_SAMPLER_H
#define SOUTHPAW_SAMPLER_H

#include <stdint.h>

struct Sample {
  uint32_t ms;        // board millis() when read
  int16_t x, y, z;    // raw counts, 32 = 1 g
};

struct SamplerStats {
  uint32_t samples;   // samples taken since the last call
  uint32_t maxLateUs; // worst lateness of a sample against its schedule
  uint32_t resyncs;   // times the schedule was reset after falling far behind
};

void sampler_begin();                 // call after Wire.begin()
bool sampler_poll();                  // true when a new sample was stored
bool sampler_sensor_ok();
uint32_t sampler_count();             // samples stored since boot; the latest has index count-1
const Sample& sampler_latest();
// Copy samples [first, first+n) out of the ring. False if they're not there (yet / any more).
bool sampler_copy(uint32_t first, uint16_t n, Sample* out);
void sampler_take_stats(SamplerStats& out);  // read and reset

#endif
