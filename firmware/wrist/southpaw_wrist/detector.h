/*
 * Modified by: Yue Ming (Team P1D) – threshold + cooldown punch detector (original file).
 *
 * training/tools/segment_punches.py implements exactly the same steps in Python.
 * If you change the algorithm here, change it there too.
 *
 * For each sample (in order, index i, squared total acceleration mag2):
 *   1. IDLE:   trigger when mag2 rises to >= threshold^2 (previous sample was below)
 *              and i >= cooldownUntil.
 *   2. SEARCH: the peak is the first largest mag2 in the PEAK_SEARCH_SAMPLES samples
 *              starting at the trigger. When the search ends, cooldownUntil = peak + COOLDOWN_SAMPLES.
 *   3. POST:   at i == peak + WINDOW_POST_SAMPLES the window [peak - PRE, peak + POST] is
 *              complete: report it (or drop it if it would start before sample 0). Back to IDLE.
 */
#ifndef SOUTHPAW_DETECTOR_H
#define SOUTHPAW_DETECTOR_H

#include <stdint.h>

struct DetectorEvent {
  uint32_t peakIndex;   // sample index of the peak
  uint32_t peakMag2;    // squared total acceleration at the peak (counts^2)
};

void detector_reset();
// Feed one sample. Returns true (and fills ev) when a punch window is complete.
bool detector_feed(uint32_t index, uint32_t mag2, DetectorEvent& ev);
uint32_t detector_dropped();   // punches dropped because the window started before sample 0

#endif
