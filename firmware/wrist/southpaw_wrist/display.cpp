/*
 * Based on: TinyScreen library and TinyScreenBasicExample by Ben Rose for TinyCircuits,
 *   https://github.com/TinyCircuits/TinyCircuits-TinyScreen_Lib (LGPL)
 * Modified by: Yue Ming (Team P1D) – status screen that redraws one changed line at a time.
 */
#include <Arduino.h>
#include <TinyScreen.h>
#include <string.h>
#include "config.h"
#include "display.h"

static TinyScreen screen = TinyScreen(TinyScreenPlus);

enum Line : uint8_t { LINE_NAME, LINE_LAST, LINE_COUNT, LINE_PEAK, LINE_BLE, LINE_BATT, LINE_N };
constexpr uint8_t LINE_CHARS = 22;     // more than fits; the padding wipes old text
constexpr uint8_t LINE_HEIGHT = 10;    // thinPixel7_10pt is 10 px high: 6 lines fit in 64

static char lines[LINE_N][LINE_CHARS + 1];
static uint8_t dirty = 0;              // bit n set = line n needs redrawing

// Tiny formatting helpers (no printf, no String, no heap).
static uint8_t putStr(char* buf, uint8_t pos, const char* s) {
  while (*s && pos < LINE_CHARS) buf[pos++] = *s++;
  buf[pos] = '\0';
  return pos;
}

static uint8_t putUint(char* buf, uint8_t pos, uint32_t v) {
  char tmp[11];
  uint8_t n = 0;
  do { tmp[n++] = (char)('0' + v % 10); v /= 10; } while (v && n < sizeof(tmp));
  while (n && pos < LINE_CHARS) buf[pos++] = tmp[--n];
  buf[pos] = '\0';
  return pos;
}

static void setLine(Line l, const char* text) {
  char padded[LINE_CHARS + 1];
  uint8_t pos = putStr(padded, 0, text);
  while (pos < LINE_CHARS) padded[pos++] = ' ';
  padded[pos] = '\0';
  if (strcmp(padded, lines[l]) != 0) {
    memcpy(lines[l], padded, sizeof(padded));
    dirty |= (uint8_t)(1u << l);
  }
}

static void drawLine(uint8_t l) {
  screen.setCursor(0, l * LINE_HEIGHT);
  screen.print(lines[l]);
}

void display_begin() {
  for (uint8_t l = 0; l < LINE_N; l++) lines[l][0] = '\0';
  screen.begin();
  screen.setBrightness(DISPLAY_BRIGHTNESS);
  screen.clearScreen();
  screen.setFont(thinPixel7_10ptFontInfo);
  screen.fontColor(TS_8b_White, TS_8b_Black);

  setLine(LINE_NAME, BLE_NAME);
  display_set_punch(PUNCH_UNKNOWN, 0, 0);
  display_set_ble(false);
  display_set_battery(0);
  for (uint8_t l = 0; l < LINE_N; l++) drawLine(l);   // setup() may take its time
  dirty = 0;
}

void display_set_punch(PunchType t, uint32_t count, uint8_t peakG) {
  char buf[LINE_CHARS + 1];
  uint8_t p = putStr(buf, 0, "Last: ");
  putStr(buf, p, count ? punch_name(t) : "-");
  setLine(LINE_LAST, buf);

  p = putStr(buf, 0, "Punches: ");
  putUint(buf, p, count);
  setLine(LINE_COUNT, buf);

  p = putStr(buf, 0, "Peak: ");
  if (count) {
    p = putUint(buf, p, peakG);
    putStr(buf, p, " g");
  } else {
    putStr(buf, p, "-");
  }
  setLine(LINE_PEAK, buf);
}

void display_set_ble(bool connected) {
  setLine(LINE_BLE, connected ? "BLE: connected" : "BLE: not connected");
}

void display_set_battery(uint16_t cv) {
  char buf[LINE_CHARS + 1];
  uint8_t p = putStr(buf, 0, "Batt: ");
  if (cv == 0) {
    putStr(buf, p, "?");
  } else {
    p = putUint(buf, p, cv / 100);
    p = putStr(buf, p, ".");
    if (cv % 100 < 10) p = putStr(buf, p, "0");
    p = putUint(buf, p, cv % 100);
    putStr(buf, p, " V");
  }
  setLine(LINE_BATT, buf);
}

void display_set_sensor_ok(bool ok) {
  setLine(LINE_NAME, ok ? BLE_NAME : "NO ACCEL SHIELD!");
}

uint32_t display_poll() {
  if (!dirty) return 0;
  uint8_t l = 0;
  while (!(dirty & (1u << l))) l++;
  uint32_t t0 = micros();
  drawLine(l);
  dirty &= (uint8_t)~(1u << l);
  return micros() - t0;
}
