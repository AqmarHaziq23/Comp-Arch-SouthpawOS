/*
 * Based on: TinyCircuits TinyScreen/ST BLE Smartwatch example (displayBattery() in display.ino),
 *   Ben Rose, TinyCircuits, from TinyScreen_Examples.zip on the ICT1011 course site (xSiTe)
 * Modified by: Yue Ming (Team P1D) – returns the reading instead of drawing it.
 */
#include <Arduino.h>
#include "battery.h"

uint16_t battery_read_centivolts() {
#if defined(ARDUINO_ARCH_SAMD)
  // Measure the internal 1.1 V bandgap against the supply: supply = 1.1 V * 1024 / reading.
  SYSCTRL->VREF.reg |= SYSCTRL_VREF_BGOUTEN;
  while (ADC->STATUS.bit.SYNCBUSY == 1);
  ADC->SAMPCTRL.bit.SAMPLEN = 0x1;
  while (ADC->STATUS.bit.SYNCBUSY == 1);
  ADC->INPUTCTRL.bit.MUXPOS = 0x19;          // internal bandgap input
  while (ADC->STATUS.bit.SYNCBUSY == 1);
  ADC->CTRLA.bit.ENABLE = 0x01;
  while (ADC->STATUS.bit.SYNCBUSY == 1);
  ADC->SWTRIG.bit.START = 1;
  ADC->INTFLAG.bit.RESRDY = 1;               // discard the first conversion after a reference change
  while (ADC->STATUS.bit.SYNCBUSY == 1);
  ADC->SWTRIG.bit.START = 1;
  while (ADC->INTFLAG.bit.RESRDY == 0);      // a few microseconds
  uint32_t valueRead = ADC->RESULT.reg;
  while (ADC->STATUS.bit.SYNCBUSY == 1);
  ADC->CTRLA.bit.ENABLE = 0x00;
  while (ADC->STATUS.bit.SYNCBUSY == 1);
  SYSCTRL->VREF.reg &= ~SYSCTRL_VREF_BGOUTEN;
  if (valueRead == 0) return 0;
  return (uint16_t)((((1100UL * 1024UL) / valueRead) + 5UL) / 10UL);
#else
  return 0;
#endif
}
