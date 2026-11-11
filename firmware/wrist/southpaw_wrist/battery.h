/*
 * Based on: TinyCircuits TinyScreen/ST BLE Smartwatch example (displayBattery() in display.ino),
 *   Ben Rose, TinyCircuits, from TinyScreen_Examples.zip on the ICT1011 course site (xSiTe)
 * Modified by: Yue Ming (Team P1D) – returns the reading instead of drawing it.
 */
#ifndef SOUTHPAW_BATTERY_H
#define SOUTHPAW_BATTERY_H

#include <stdint.h>

// Supply voltage in hundredths of a volt (330 = 3.30 V), measured against the
// SAMD21's internal 1.1 V reference. 0 if it can't be read.
// It's the chip's supply, so it only starts falling when the battery is nearly flat.
uint16_t battery_read_centivolts();

#endif
