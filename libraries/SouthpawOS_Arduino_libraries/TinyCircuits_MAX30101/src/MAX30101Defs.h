/*
  MIT License

  Updated to MAX30101 by Ben Rose for TinyCircuits, https://www.tinycircuits.com

  Original Copyright (c) 2017 Raivis Strogonovs (https://morf.lv)

  Permission is hereby granted, free of charge, to any person obtaining a copy
  of this software and associated documentation files (the "Software"), to deal
  in the Software without restriction, including without limitation the rights
  to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
  copies of the Software, and to permit persons to whom the Software is
  furnished to do so, subject to the following conditions:

  The above copyright notice and this permission notice shall be included in all
  copies or substantial portions of the Software.

  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
  IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
  FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
  AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
  LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
  OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
  SOFTWARE.


*/

#ifndef MAX30101Defs_H
#define MAX30101Defs_H

/* MAX30101 register and bit defines */

#define MAX30101_DEVICE_ADDR              0x57

//Part ID Registers
#define MAX30101_REV_ID                   0xFE
#define MAX30101_PART_ID                  0xFF
#define MAX30101_PART_ID_VALUE            0x15

//status registers
#define MAX30101_INT_STATUS_1             0x00
#define MAX30101_INT_STATUS_2             0x01
#define MAX30101_INT_ENABLE_1             0x02
#define MAX30101_INT_ENABLE_2             0x03

//Fifo registers
#define MAX30101_FIFO_WRITE_PTR           0x04
#define MAX30101_FIFO_OVERFLOW_COUNTER    0x05
#define MAX30101_FIFO_READ_PTR            0x06
#define MAX30101_FIFO_DATA                0x07
#define MAX30101_FIFO_CONFIG              0x08

//Config registers
#define MAX30101_MODE_CONF                0x09
#define MAX30101_SPO2_CONF                0x0A

//LED amplitude registers
#define MAX30101_LED1_PA                  0x0C
#define MAX30101_LED2_PA                  0x0D
#define MAX30101_LED3_PA                  0x0E
#define MAX30101_LED4_PA                  0x0F

//#define MAX30101_LED_CONF                 0x11

//Temperature registers
#define MAX30101_TEMP_INT                 0x1F
#define MAX30101_TEMP_FRACTION            0x20
#define MAX30101_TEMP_CONFIG              0x21

//Bit defines MODE Regsiter
#define MAX30101_MODE_SHDN                (1<<7)
#define MAX30101_MODE_RESET               (1<<6)


typedef enum Mode {
  MAX30101_MODE_HR_ONLY                 = 0x02,
  MAX30101_MODE_SPO2_HR                 = 0x03,
  MAX30101_MODE_MULTI_LED               = 0x07
} Mode;

//Bit defines SpO2 register
#define MAX30101_SPO2_HI_RES_EN           (1 << 6)
typedef enum SamplingRate {
  MAX30101_SAMPLING_RATE_50HZ           = 0x00,
  MAX30101_SAMPLING_RATE_100HZ          = 0x01,
  MAX30101_SAMPLING_RATE_200HZ          = 0x02,
  MAX30101_SAMPLING_RATE_400HZ          = 0x03,
  MAX30101_SAMPLING_RATE_800HZ          = 0x04,
  MAX30101_SAMPLING_RATE_1000HZ         = 0x05,
  MAX30101_SAMPLING_RATE_1600HZ         = 0x06,
  MAX30101_SAMPLING_RATE_3200HZ         = 0x07
} SamplingRate;

const uint32_t sampleRateToSamplePeriodMicros[] = { 20000, 10000, 5000, 2500, 1250, 1000, 625, 313 };

typedef enum SampleAveragingCount {
  MAX30101_SAMPLE_AVERAGE_COUNT_1          = 0x00,
  MAX30101_SAMPLE_AVERAGE_COUNT_2          = 0x01,
  MAX30101_SAMPLE_AVERAGE_COUNT_4          = 0x02,
  MAX30101_SAMPLE_AVERAGE_COUNT_8          = 0x03,
  MAX30101_SAMPLE_AVERAGE_COUNT_16         = 0x04,
  MAX30101_SAMPLE_AVERAGE_COUNT_32         = 0x05
} SampleAveragingCount;

const uint32_t sampleAveragingValToCount[] = { 1, 2, 4, 8, 16, 32};

typedef enum LEDPulseWidth {
  MAX30101_PULSE_WIDTH_69US_ADC_15      = 0x00,
  MAX30101_PULSE_WIDTH_118US_ADC_16     = 0x01,
  MAX30101_PULSE_WIDTH_215US_ADC_17     = 0x02,
  MAX30101_PULSE_WIDTH_411US_ADC_18     = 0x03,
} LEDPulseWidth;

typedef enum LEDCurrent {
  MAX30101_LED_CURRENT_0MA              = 0x00,
  MAX30101_LED_CURRENT_27_1MA           = 0x7F,
  MAX30101_LED_CURRENT_50MA             = 0xFF
} LEDCurrent;

#define MAX30101_FIFO_PTR_MAX 0x1F

#endif
