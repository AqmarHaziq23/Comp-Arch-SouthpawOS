/*
 * Modified by: Yue Ming (Team P1D) – threshold + cooldown punch detector (original file).
 * Mirrored in training/tools/segment_punches.py: keep the two identical.
 */
#include "config.h"
#include "detector.h"

enum DetState : uint8_t { DET_IDLE, DET_SEARCH, DET_POST };

static DetState state = DET_IDLE;
static bool prevAbove = false;
static uint32_t triggerIdx = 0;
static uint32_t peakIdx = 0;
static uint32_t peakMag2 = 0;
static uint32_t cooldownUntil = 0;
static uint32_t dropped = 0;

void detector_reset() {
  state = DET_IDLE;
  prevAbove = false;
  triggerIdx = peakIdx = peakMag2 = cooldownUntil = 0;
  dropped = 0;
}

bool detector_feed(uint32_t i, uint32_t mag2, DetectorEvent& ev) {
  bool above = mag2 >= DETECT_THRESHOLD_SQ;
  bool ready = false;

  if (state == DET_IDLE) {
    if (above && !prevAbove && (int32_t)(i - cooldownUntil) >= 0) {
      triggerIdx = i;
      peakIdx = i;
      peakMag2 = mag2;
      state = DET_SEARCH;
    }
  } else if (state == DET_SEARCH) {
    if (mag2 > peakMag2) {            // strictly greater: the first of equal (clipped) peaks wins
      peakIdx = i;
      peakMag2 = mag2;
    }
  }

  if (state == DET_SEARCH && i - triggerIdx + 1 >= PEAK_SEARCH_SAMPLES) {
    state = DET_POST;
    cooldownUntil = peakIdx + COOLDOWN_SAMPLES;
  }

  if (state == DET_POST && i == peakIdx + WINDOW_POST_SAMPLES) {
    state = DET_IDLE;
    if (peakIdx >= WINDOW_PRE_SAMPLES) {
      ev.peakIndex = peakIdx;
      ev.peakMag2 = peakMag2;
      ready = true;
    } else {
      dropped++;
    }
  }

  prevAbove = above;
  return ready;
}

uint32_t detector_dropped() { return dropped; }
