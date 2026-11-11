/*
 * SouthpawOS wrist board: sample the accelerometer, detect punches, classify them
 * with the random forest from train_classifier.py, send them over BLE and show the status on the TinyScreen+.
 *
 * Based on: Accelerometer TinyShield example by Laverena Wienclaw for TinyCircuits,
 *   https://learn.tinycircuits.com/Sensors/Accelerometer_TinyShield_Tutorial/
 * Modified by: Yue Ming (Team P1D) – non-blocking sampler / detector / features /
 *   classifier / display pipeline. Settings are in config.h.
 *
 * USB output (only while a program has the port open):
 *   board_ms,x,y,z            raw samples, when USB_STREAM_RAW is 1 (log_accel.py records these)
 *   # ...                     debug lines; the Python tools ignore them
 */
#include <Wire.h>
#include "config.h"
#include "sampler.h"
#include "detector.h"
#include "features.h"
#include "classifier.h"
#include "battery.h"
#include "display.h"
#include "ble.h"

#if defined(ARDUINO_ARCH_SAMD)
  #define SerialMonitorInterface SerialUSB
#else
  #define SerialMonitorInterface Serial
  #error "This sketch is for the TinyScreen+ (SAMD21)."
#endif

static Sample window[WINDOW_LEN];
static uint32_t punchCount = 0;
static uint32_t windowsLost = 0;        // detector fired but the ring no longer had the samples
static uint32_t knocksIgnored = 0;      // bursts shorter than MIN_BURST_MS
static uint32_t notPunches = 0;         // windows the classifier called "not a punch"
static uint32_t lastStatsMs = 0;
static uint32_t lastBatteryMs = 0;
static uint32_t maxWorkUs = 0;          // longest loop pass that handled a sample
static uint32_t maxDisplayUs = 0;
static bool lastSensorOk = false;
static bool statsPrinted = false;       // this pass printed a stats line: don't count it in maxWorkUs
static uint8_t statsStage = 0;          // 1 = the "# ble" stats line is due on the next pass
static bool punchHandled = false;       // this pass handled a punch: leave the screen for the next pass
static uint16_t batteryCv = 0;
static bool bleOk = false;              // the BlueNRG answered in setup()
static bool usbWasOpen = false;         // to print the info line once each time a program opens the port
static uint8_t lastBleState = 0;        // 0 advertising, 1 link up but notifications off, 2 connected

// dtr() just reads a flag. Don't use `if (!SerialUSB)`: it runs delay(10) on every call.
static bool usbOpen() { return SerialMonitorInterface.dtr(); }

static char* putInt(char* p, int32_t v) {
  uint32_t u = (uint32_t)v;
  if (v < 0) { *p++ = '-'; u = (uint32_t)(-v); }
  char tmp[10];
  uint8_t n = 0;
  do { tmp[n++] = (char)('0' + u % 10); u /= 10; } while (u);
  while (n) *p++ = tmp[--n];
  return p;
}

// One write() per line: each print() call becomes its own USB packet, and the core
// waits for the previous packet to go, so 7 separate prints cost up to ~3 ms.
static void printSample(const Sample& s) {
  char line[40];
  char* p = line;
  p = putInt(p, (int32_t)s.ms);
  *p++ = ',';
  p = putInt(p, s.x);
  *p++ = ',';
  p = putInt(p, s.y);
  *p++ = ',';
  p = putInt(p, s.z);
  *p++ = '\r';
  *p++ = '\n';
  SerialMonitorInterface.write((const uint8_t*)line, (size_t)(p - line));
}

static char* putStr(char* p, const char* s) {
  while (*s) *p++ = *s++;
  return p;
}

static char* putXYZ(char* p, int16_t x, int16_t y, int16_t z) {
  p = putInt(p, x);
  *p++ = ',';
  p = putInt(p, y);
  *p++ = ',';
  return putInt(p, z);
}

// Built in one buffer and sent with one write(), for the same reason as printSample().
static void printPunch(const PunchFeatures& f, PunchType t) {
  static const char AXES[3] = {'x', 'y', 'z'};
  char line[256];
  char* p = line;
  p = putStr(p, "# punch n=");      p = putInt(p, (int32_t)punchCount);
  p = putStr(p, " peak_ms=");       p = putInt(p, (int32_t)f.peakMs);
  p = putStr(p, " peak_g=");        p = putInt(p, f.peakG);
  p = putStr(p, " peak_counts=");   p = putInt(p, f.peakMag);
  p = putStr(p, " dom=");           *p++ = f.domSign > 0 ? '+' : '-';  *p++ = AXES[f.domAxis];
  p = putStr(p, " burst_ms=");      p = putInt(p, f.burstMs);
  p = putStr(p, " tilt=");          p = putInt(p, f.tiltChange);
  p = putStr(p, " pre=");           p = putXYZ(p, f.preX, f.preY, f.preZ);
  p = putStr(p, " post=");          p = putXYZ(p, f.postX, f.postY, f.postZ);
  p = putStr(p, " max=");           p = putXYZ(p, f.maxX, f.maxY, f.maxZ);
  p = putStr(p, " min=");           p = putXYZ(p, f.minX, f.minY, f.minZ);
  p = putStr(p, " order=");         p = putXYZ(p, f.orderX, f.orderY, f.orderZ);
  p = putStr(p, " win_peak=");      p = putInt(p, f.winPeakMag);
  p = putStr(p, " type=");          *p++ = punch_code(t);
  *p++ = '\r';
  *p++ = '\n';
  SerialMonitorInterface.write((const uint8_t*)line, (size_t)(p - line));
}

static void handlePunch(const DetectorEvent& ev) {
  if (!sampler_copy(ev.peakIndex - WINDOW_PRE_SAMPLES, WINDOW_LEN, window)) {
    windowsLost++;
    return;
  }
  PunchFeatures f;
  features_compute(window, f);
  if (f.burstMs < MIN_BURST_MS) {         // a knock, not a punch (segment_punches.py does the same)
    knocksIgnored++;
    if (usbOpen()) {
      char line[64];
      char* p = putStr(line, "# ignored knock peak_ms=");
      p = putInt(p, (int32_t)f.peakMs);
      p = putStr(p, " burst_ms=");
      p = putInt(p, f.burstMs);
      *p++ = '\r';
      *p++ = '\n';
      SerialMonitorInterface.write((const uint8_t*)line, (size_t)(p - line));
    }
    return;
  }
  PunchType t = classify(f);
  if (t == PUNCH_NONE) {                  // e.g. this arm moving while the other hand punches
    notPunches++;
    if (usbOpen()) printPunch(f, t);      // type=N; nothing is sent over BLE, the screen keeps the last punch
    punchHandled = true;
    return;
  }
  punchCount++;
  display_set_punch(t, punchCount, f.peakG);

  // Protocol punch message: P,<hand>,<type>,<board_ms>,<peak_g>\n (protocol/PROTOCOL.md).
  // board_ms is the time of the peak sample.
  char msg[BLE_MAX_MSG + 8];
  char* p = msg;
  *p++ = 'P'; *p++ = ','; *p++ = HAND; *p++ = ','; *p++ = punch_code(t); *p++ = ',';
  p = putInt(p, (int32_t)f.peakMs);
  *p++ = ',';
  p = putInt(p, f.peakG);
  *p++ = '\n';
  uint8_t len = (uint8_t)(p - msg);
  bool queued = len <= BLE_MAX_MSG && ble_queue_line(msg, len);

  if (usbOpen()) {
    printPunch(f, t);
    char line[48];
    char* q = putStr(line, queued ? "# ble tx " : "# ble not sent (not connected) ");
    memcpy(q, msg, len);                  // msg already ends with '\n'
    q += len;
    SerialMonitorInterface.write((const uint8_t*)line, (size_t)(q - line));
  }
  punchHandled = true;
}

static void printInfo() {
  char addr[18];
  ble_address(addr);
  char line[160];
  char* p = putStr(line, "# southpaw_wrist name=" BLE_NAME " hand=");
  *p++ = HAND;
  p = putStr(p, " ble=");
  p = putStr(p, bleOk ? addr : "NOT FOUND");
  p = putStr(p, " rate_hz=");      p = putInt(p, SAMPLE_RATE_HZ);
  p = putStr(p, " threshold=");    p = putInt(p, DETECT_THRESHOLD_COUNTS);
  p = putStr(p, " cooldown_ms=");  p = putInt(p, COOLDOWN_MS);
  p = putStr(p, " min_burst_ms="); p = putInt(p, MIN_BURST_MS);
  *p++ = '\r';
  *p++ = '\n';
  SerialMonitorInterface.write((const uint8_t*)line, (size_t)(p - line));
}

static void housekeeping(uint32_t nowMs) {
  bool open = usbOpen();
  if (open && !usbWasOpen) printInfo();
  usbWasOpen = open;

  display_set_ble(ble_connected());
  uint8_t bleState = ble_connected() ? 2 : ble_link_up() ? 1 : 0;
  if (bleState != lastBleState) {
    lastBleState = bleState;
    if (open) {
      static const char* const NAMES[3] = {"# ble state advertising\r\n", "# ble state link-up-notify-off\r\n",
                                           "# ble state connected (notifications on)\r\n"};
      const char* s = NAMES[bleState];
      SerialMonitorInterface.write((const uint8_t*)s, strlen(s));
    }
  }

  bool ok = sampler_sensor_ok();
  if (ok != lastSensorOk) {
    lastSensorOk = ok;
    display_set_sensor_ok(ok);
    if (usbOpen()) SerialMonitorInterface.println(ok ? F("# accelerometer OK") : F("# ERROR: no BMA250 detected - check the accelerometer shield"));
  }

  if (nowMs - lastBatteryMs >= BATTERY_INTERVAL_MS) {
    lastBatteryMs = nowMs;
    batteryCv = battery_read_centivolts();
    display_set_battery(batteryCv);
  }

  // The two stats lines go out in separate passes, each as one write(): ~35 separate
  // print() calls made this pass overrun the 5 ms slot when the reader is slow.
  if (statsStage == 0 && nowMs - lastStatsMs >= STATS_INTERVAL_MS) {
    lastStatsMs = nowMs;
    SamplerStats st;
    sampler_take_stats(st);
    if (usbOpen()) {
      char line[200];
      char* p = putStr(line, "# stats samples=");  p = putInt(p, (int32_t)st.samples);
      p = putStr(p, " late_max_us=");      p = putInt(p, (int32_t)st.maxLateUs);
      p = putStr(p, " resyncs=");          p = putInt(p, (int32_t)st.resyncs);
      p = putStr(p, " work_max_us=");      p = putInt(p, (int32_t)maxWorkUs);
      p = putStr(p, " display_max_us=");   p = putInt(p, (int32_t)maxDisplayUs);
      p = putStr(p, " punches=");          p = putInt(p, (int32_t)punchCount);
      p = putStr(p, " dropped=");          p = putInt(p, (int32_t)(detector_dropped() + windowsLost));
      p = putStr(p, " knocks=");           p = putInt(p, (int32_t)knocksIgnored);
      p = putStr(p, " not_punch=");        p = putInt(p, (int32_t)notPunches);
      p = putStr(p, " batt_cv=");          p = putInt(p, batteryCv);
      p = putStr(p, lastSensorOk ? " sensor=ok\r\n" : " sensor=missing\r\n");
      SerialMonitorInterface.write((const uint8_t*)line, (size_t)(p - line));
    }
    maxWorkUs = 0;
    maxDisplayUs = 0;
    statsPrinted = true;
    statsStage = 1;
  } else if (statsStage == 1) {
    BleStats bs;
    ble_take_stats(bs);
    if (usbOpen()) {
      char line[200];
      char* p = putStr(line, "# ble ");
      p = putStr(p, !bleOk ? "NOT FOUND" : ble_connected() ? "connected" : ble_link_up() ? "link-up-notify-off" : "advertising");
      p = putStr(p, " sent=");             p = putInt(p, (int32_t)bs.sent);
      p = putStr(p, " pings=");            p = putInt(p, (int32_t)bs.pings);
      p = putStr(p, " failed=");           p = putInt(p, (int32_t)bs.failed);
      p = putStr(p, " dropped=");          p = putInt(p, (int32_t)bs.dropped);
      p = putStr(p, " ignored_rx=");       p = putInt(p, (int32_t)bs.ignoredRx);
      p = putStr(p, " poll_max_us=");      p = putInt(p, (int32_t)bs.maxPollUs);
      p = putStr(p, " send_max_us=");      p = putInt(p, (int32_t)bs.maxSendUs);
      p = putStr(p, "\r\n");
      SerialMonitorInterface.write((const uint8_t*)line, (size_t)(p - line));
    }
    statsPrinted = true;
    statsStage = 0;
  }
}

void setup() {
  SerialMonitorInterface.begin(115200);
  Wire.begin();
  display_begin();
  bleOk = ble_begin();
  sampler_begin();
  detector_reset();
  lastSensorOk = !sampler_sensor_ok();   // force the first housekeeping() to report the sensor state
  batteryCv = battery_read_centivolts();
  display_set_battery(batteryCv);
  lastStatsMs = lastBatteryMs = millis();
}

void loop() {
  ble_poll();                             // every pass, so a T ping is answered straight away
  uint32_t t0 = micros();
  if (!sampler_poll()) {
    // No sample due. Keep the display and housekeeping moving while the sensor is missing.
    if (!sampler_sensor_ok()) {
      housekeeping(millis());
      display_poll();
    }
    return;
  }

  const Sample& s = sampler_latest();
  if (USB_STREAM_RAW && usbOpen()) printSample(s);

  DetectorEvent ev;
  if (detector_feed(sampler_count() - 1, mag2_of(s), ev)) handlePunch(ev);

  // Slower work goes right after a sample, so it has most of the 5 ms before the next one.
  // A screen line takes ~3 ms, so never draw in the same pass as a punch or the stats print.
  housekeeping(millis());
  if (!statsPrinted && !punchHandled) {
    uint32_t d = display_poll();
    if (d > maxDisplayUs) maxDisplayUs = d;
  }

  uint32_t work = micros() - t0;
  if (!statsPrinted && work > maxWorkUs) maxWorkUs = work;
  statsPrinted = false;
  punchHandled = false;
}
