/*
 * Based on: TinyScreen library and TinyScreenBasicExample by Ben Rose for TinyCircuits,
 *   https://github.com/TinyCircuits/TinyCircuits-TinyScreen_Lib (LGPL)
 * Modified by: Yue Ming (Team P1D) – status screen that redraws one changed line at a time.
 */
#ifndef SOUTHPAW_DISPLAY_H
#define SOUTHPAW_DISPLAY_H

#include <stdint.h>
#include "classifier.h"

void display_begin();                        // setup() only: draws the whole screen
void display_set_punch(PunchType t, uint32_t count, uint8_t peakG);
void display_set_ble(bool connected);
void display_set_battery(uint16_t centivolts);   // 0 = unknown
void display_set_sensor_ok(bool ok);
// Draw at most one changed line. Call right after a sample so it can't delay the next one.
// Returns the microseconds spent drawing (0 if nothing changed).
uint32_t display_poll();

#endif
