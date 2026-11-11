/*
 * Modified by: Yue Ming (Team P1D) – integer-only punch features (original file).
 * Mirrored in training/tools/segment_punches.py: keep the two identical.
 *
 * All values are integers. Averages divide with C rounding (towards zero).
 */
#ifndef SOUTHPAW_FEATURES_H
#define SOUTHPAW_FEATURES_H

#include <stdint.h>
#include "sampler.h"

struct PunchFeatures {
  uint32_t peakMs;      // board time of the peak sample (used as the punch time)
  uint16_t peakMag;     // total acceleration at the peak, counts (32 = 1 g)
  uint8_t peakG;        // peakMag rounded to whole g (the punch message's peak field)
  uint8_t domAxis;      // 0 = x, 1 = y, 2 = z: the axis with the largest |value| at the peak
  int8_t domSign;       // +1 or -1: the sign of that axis at the peak
  uint16_t burstMs;     // how long the total stayed >= BURST_THRESHOLD around the peak
  int16_t preX, preY, preZ;     // gravity before: average of the first GRAVITY_AVG_SAMPLES of the window
  int16_t postX, postY, postZ;  // gravity after: average of the last GRAVITY_AVG_SAMPLES
  uint16_t tiltChange;  // |after - before| in counts: 0 = same tilt, ~45 = 90 deg, 64 = 180 deg

  // Whole-window features: they don't depend on which sample the detector picked as the peak.
  // (On hooks and uppercuts it picks the swing on some punches and the snap on others.)
  int16_t maxX, maxY, maxZ;     // largest value of each axis in the window, counts
  int16_t minX, minY, minZ;     // smallest value of each axis in the window, counts
  int16_t orderX, orderY, orderZ;  // ms from that axis's max to its min: > 0 = max came first
  uint16_t winPeakMag;          // largest total acceleration in the window, counts
};

uint32_t isqrt32(uint32_t v);                 // floor(sqrt(v))
uint32_t mag2_of(const Sample& s);            // x^2 + y^2 + z^2
// w holds WINDOW_LEN samples with the peak at index WINDOW_PRE_SAMPLES.
void features_compute(const Sample* w, PunchFeatures& f);

#endif
