/*
 * Modified by: Yue Ming (Team P1D) – integer-only punch features (original file).
 * Mirrored in training/tools/segment_punches.py: keep the two identical.
 */
#include "config.h"
#include "features.h"

uint32_t isqrt32(uint32_t n) {
  uint32_t root = 0;
  uint32_t bit = 1UL << 30;
  while (bit > n) bit >>= 2;
  while (bit != 0) {
    if (n >= root + bit) {
      n -= root + bit;
      root = (root >> 1) + bit;
    } else {
      root >>= 1;
    }
    bit >>= 2;
  }
  return root;
}

uint32_t mag2_of(const Sample& s) {
  int32_t x = s.x, y = s.y, z = s.z;
  return (uint32_t)(x * x + y * y + z * z);
}

static void average(const Sample* w, uint16_t start, uint16_t n, int16_t& ax, int16_t& ay, int16_t& az) {
  int32_t sx = 0, sy = 0, sz = 0;
  for (uint16_t i = start; i < start + n; i++) {
    sx += w[i].x;
    sy += w[i].y;
    sz += w[i].z;
  }
  ax = (int16_t)(sx / (int32_t)n);   // C division rounds towards zero
  ay = (int16_t)(sy / (int32_t)n);
  az = (int16_t)(sz / (int32_t)n);
}

void features_compute(const Sample* w, PunchFeatures& f) {
  const Sample& p = w[WINDOW_PRE_SAMPLES];
  f.peakMs = p.ms;
  f.peakMag = (uint16_t)isqrt32(mag2_of(p));
  f.peakG = (uint8_t)((f.peakMag + COUNTS_PER_G / 2) / COUNTS_PER_G);

  // Dominant axis at the peak; ties go to the lower axis (x before y before z).
  int16_t v[3] = {p.x, p.y, p.z};
  uint8_t best = 0;
  for (uint8_t a = 1; a < 3; a++) {
    int16_t mag = v[a] < 0 ? -v[a] : v[a];
    int16_t bestMag = v[best] < 0 ? -v[best] : v[best];
    if (mag > bestMag) best = a;
  }
  f.domAxis = best;
  f.domSign = v[best] < 0 ? -1 : 1;

  // Burst: the unbroken run of samples >= BURST_THRESHOLD that contains the peak.
  uint16_t run = 1;
  for (int16_t j = (int16_t)WINDOW_PRE_SAMPLES - 1; j >= 0 && mag2_of(w[j]) >= BURST_THRESHOLD_SQ; j--) run++;
  for (uint16_t j = WINDOW_PRE_SAMPLES + 1; j < WINDOW_LEN && mag2_of(w[j]) >= BURST_THRESHOLD_SQ; j++) run++;
  f.burstMs = run * SAMPLE_PERIOD_MS;

  // Tilt: gravity direction before vs after the punch.
  average(w, 0, GRAVITY_AVG_SAMPLES, f.preX, f.preY, f.preZ);
  average(w, WINDOW_LEN - GRAVITY_AVG_SAMPLES, GRAVITY_AVG_SAMPLES, f.postX, f.postY, f.postZ);
  int32_t dx = f.postX - f.preX, dy = f.postY - f.preY, dz = f.postZ - f.preZ;
  f.tiltChange = (uint16_t)isqrt32((uint32_t)(dx * dx + dy * dy + dz * dz));

  // Whole window: per-axis max and min (the first one wins a tie), their order, the largest total.
  int16_t mx[3], mn[3];
  uint16_t imx[3] = {0, 0, 0}, imn[3] = {0, 0, 0};
  mx[0] = mn[0] = w[0].x;
  mx[1] = mn[1] = w[0].y;
  mx[2] = mn[2] = w[0].z;
  uint32_t maxMag2 = mag2_of(w[0]);
  for (uint16_t i = 1; i < WINDOW_LEN; i++) {
    const int16_t s[3] = {w[i].x, w[i].y, w[i].z};
    for (uint8_t a = 0; a < 3; a++) {
      if (s[a] > mx[a]) { mx[a] = s[a]; imx[a] = i; }
      if (s[a] < mn[a]) { mn[a] = s[a]; imn[a] = i; }
    }
    uint32_t m2 = mag2_of(w[i]);
    if (m2 > maxMag2) maxMag2 = m2;
  }
  f.maxX = mx[0]; f.maxY = mx[1]; f.maxZ = mx[2];
  f.minX = mn[0]; f.minY = mn[1]; f.minZ = mn[2];
  f.orderX = (int16_t)(((int16_t)imn[0] - (int16_t)imx[0]) * (int16_t)SAMPLE_PERIOD_MS);
  f.orderY = (int16_t)(((int16_t)imn[1] - (int16_t)imx[1]) * (int16_t)SAMPLE_PERIOD_MS);
  f.orderZ = (int16_t)(((int16_t)imn[2] - (int16_t)imx[2]) * (int16_t)SAMPLE_PERIOD_MS);
  f.winPeakMag = (uint16_t)isqrt32(maxMag2);
}
