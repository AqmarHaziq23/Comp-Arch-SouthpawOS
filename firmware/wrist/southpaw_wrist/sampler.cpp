/*
 * Based on: Accelerometer TinyShield example by Laverena Wienclaw for TinyCircuits,
 *   https://learn.tinycircuits.com/Sensors/Accelerometer_TinyShield_Tutorial/
 * Modified by: Yue Ming (Team P1D) – fixed-rate 200 Hz sampling at +/-16 g into a ring buffer,
 *   non-blocking re-detection of a missing shield.
 */
#include <Arduino.h>
#include <Wire.h>
#include <BMA250.h>
#include "config.h"
#include "sampler.h"

static BMA250 accel;
static Sample ring[RING_SIZE];
static uint32_t count = 0;
static uint32_t nextUs = 0;
static bool sensorOk = false;
static uint8_t missingRun = 0;
static uint32_t lastRetryMs = 0;
static SamplerStats stats = {0, 0, 0};

static bool startSensor() {
  // begin() returns -1 when no BMA250 answers on I2C.
  sensorOk = (accel.begin(BMA250_range_16g, BMA250_update_time_2ms) == 0);
  missingRun = 0;
  nextUs = micros();
  return sensorOk;
}

void sampler_begin() {
  startSensor();
  lastRetryMs = millis();
}

bool sampler_poll() {
  if (!sensorOk) {
    uint32_t nowMs = millis();
    if (nowMs - lastRetryMs >= SENSOR_RETRY_MS) {
      lastRetryMs = nowMs;
      startSensor();
    }
    return false;
  }

  uint32_t now = micros();
  int32_t late = (int32_t)(now - nextUs);
  if (late < 0) return false;                        // not time yet
  if (late > (int32_t)(10 * SAMPLE_PERIOD_US)) {     // fell far behind: restart the schedule
    nextUs = now;
    late = 0;
    stats.resyncs++;
  }
  nextUs += SAMPLE_PERIOD_US;
  if ((uint32_t)late > stats.maxLateUs) stats.maxLateUs = (uint32_t)late;

  accel.read();
  // One -1,-1,-1 can be a real reading; a run of them means the shield is gone.
  if (accel.X == -1 && accel.Y == -1 && accel.Z == -1) {
    if (++missingRun >= SENSOR_LOST_SAMPLES) {
      sensorOk = false;
      lastRetryMs = millis();
    }
  } else {
    missingRun = 0;
  }

  Sample& s = ring[count & (RING_SIZE - 1)];
  s.ms = millis();
  s.x = accel.X;
  s.y = accel.Y;
  s.z = accel.Z;
  count++;
  stats.samples++;
  return true;
}

bool sampler_sensor_ok() { return sensorOk; }

uint32_t sampler_count() { return count; }

const Sample& sampler_latest() { return ring[(count - 1) & (RING_SIZE - 1)]; }

bool sampler_copy(uint32_t first, uint16_t n, Sample* out) {
  if (first + n > count) return false;               // not sampled yet
  if (count - first > RING_SIZE) return false;       // already overwritten
  for (uint16_t i = 0; i < n; i++) out[i] = ring[(first + i) & (RING_SIZE - 1)];
  return true;
}

void sampler_take_stats(SamplerStats& out) {
  out = stats;
  stats.samples = 0;
  stats.maxLateUs = 0;
  stats.resyncs = 0;
}
