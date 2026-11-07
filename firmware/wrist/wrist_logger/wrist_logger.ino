/*
 * P1D wrist board - P0 accelerometer logger
 *
 * Streams accelerometer readings over USB as CSV lines at a fixed rate:
 *     board_ms,x_raw,y_raw,z_raw
 * At the +/-16g range the BMA250 gives 32 counts per g (so 32 = 1 g).
 *
 * Based on: Accelerometer TinyShield example by TinyCircuits
 *   https://learn.tinycircuits.com/Sensors/Accelerometer_TinyShield_Tutorial/
 * Modified by: Yue Ming (Team P1D) - +/-16g range, fastest useful update rate,
 *   fixed-rate sampling with micros(), CSV output for the Python logger.
 *
 * Needs: TinyCircuits "BMA250" Arduino library (BMA250.zip from the tutorial page).
 * Board: TinyScreen+ with the Accelerometer TinyShield stacked on it.
 */

#include <Wire.h>
#include <BMA250.h>

#if defined(ARDUINO_ARCH_SAMD)
  #define SerialMonitorInterface SerialUSB   // TinyScreen+ prints over native USB
#else
  #define SerialMonitorInterface Serial
#endif

// The BMA250 register values for range and bandwidth (from the Bosch datasheet).
// The TinyCircuits library normally defines these names; these are fallbacks
// in case your copy of the library spells them differently.
#ifndef BMA250_range_16g
  #define BMA250_range_16g 0x0C          // +/-16 g  -> 32 counts per g
#endif
#ifndef BMA250_update_time_2ms
  #define BMA250_update_time_2ms 0x0D    // 250 Hz bandwidth, new data every 2 ms
#endif

const uint32_t SAMPLE_RATE_HZ   = 200;                        // 200 readings per second
const uint32_t SAMPLE_PERIOD_US = 1000000UL / SAMPLE_RATE_HZ;  // 5000 us

BMA250 accel_sensor;
uint32_t nextSampleUs;

void setup() {
  SerialMonitorInterface.begin(115200);
  Wire.begin();
  accel_sensor.begin(BMA250_range_16g, BMA250_update_time_2ms);
  nextSampleUs = micros();
}

void loop() {
  uint32_t now = micros();
  if ((int32_t)(now - nextSampleUs) < 0) return;   // not time yet

  // If we fell far behind (e.g. USB stalled), resync instead of bursting.
  if ((int32_t)(now - nextSampleUs) > (int32_t)(10 * SAMPLE_PERIOD_US)) nextSampleUs = now;
  nextSampleUs += SAMPLE_PERIOD_US;

  accel_sensor.read();
  int x = accel_sensor.X;
  int y = accel_sensor.Y;
  int z = accel_sensor.Z;

  // dtr(), not !SerialUSB: the bool check runs delay(10) on every call (capped us at ~80 Hz).
  if (!SerialMonitorInterface.dtr()) return;        // nothing listening on USB

  // The TinyCircuits example reports -1,-1,-1 when the shield isn't detected.
  if (x == -1 && y == -1 && z == -1) {
    SerialMonitorInterface.println("ERROR: no BMA250 detected - check the accelerometer shield is stacked");
    delay(1000);
    return;
  }

  SerialMonitorInterface.print(millis());
  SerialMonitorInterface.print(',');
  SerialMonitorInterface.print(x);
  SerialMonitorInterface.print(',');
  SerialMonitorInterface.print(y);
  SerialMonitorInterface.print(',');
  SerialMonitorInterface.println(z);
}
