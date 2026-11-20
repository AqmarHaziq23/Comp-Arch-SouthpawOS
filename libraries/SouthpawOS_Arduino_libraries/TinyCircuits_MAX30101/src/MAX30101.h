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

#ifndef MAX30101_H
#define MAX30101_H

#include <Arduino.h>
#include <Wire.h>
#include <math.h>

/*----------------------------------------------*/
/* Config defines, you can tailor to your needs */
/*----------------------------------------------*/

/* MAX30101 parameters */
#define DEFAULT_OPERATING_MODE            MAX30101_MODE_SPO2_HR

//If edited, note sample rate maximum per pulse time as shown in datasheet
//Note that the sampling rate / averaging count = data update rate
#define DEFAULT_SAMPLING_RATE             MAX30101_SAMPLING_RATE_400HZ
#define DEFAULT_AVERAGING_COUNT           MAX30101_SAMPLE_AVERAGE_COUNT_4
#define DEFAULT_LED_PULSE_WIDTH           MAX30101_PULSE_WIDTH_411US_ADC_18

//#define DEFAULT_IR_LED_CURRENT            MAX30101_LED_CURRENT_50MA
#define DEFAULT_IR_LED_CURRENT            MAX30101_LED_CURRENT_27_1MA
#define STARTING_RED_LED_CURRENT          MAX30101_LED_CURRENT_27_1MA

/* Adjust RED LED current balancing*/
#define MAGIC_ACCEPTABLE_INTENSITY_DIFF         65000
#define RED_LED_CURRENT_ADJUSTMENT_MS           50

/* Filter parameters */
#define ALPHA                         0.95  //dc filter alpha value
#define MEAN_FILTER_SIZE              20

/* Pulse detection parameters */
#define PULSE_BPM_SAMPLE_SIZE       3 //Moving average size

#define VALID_DATA_MINIMUM_COUNT     40

#include <MAX30101Types.h>
#include <MAX30101Defs.h>

class MAX30101
{
  public:
    MAX30101(void);
    int begin(    Mode modeInit = DEFAULT_OPERATING_MODE,
                  SamplingRate samplingRateInit = DEFAULT_SAMPLING_RATE,
                  SampleAveragingCount averagingCountInit = DEFAULT_AVERAGING_COUNT,
                  LEDPulseWidth pulseWidthInit = DEFAULT_LED_PULSE_WIDTH,
                  LEDCurrent IrLedCurrentInit = DEFAULT_IR_LED_CURRENT,
                  bool debugInit = false
             );

    void setMode(Mode mode);
    void setSamplingRateAndAveragingCount(SamplingRate rate, SampleAveragingCount count);
    void setLEDPulseWidth(LEDPulseWidth pw);
    void setLEDCurrents( byte redLedCurrent, byte IRLedCurrent );
    float readTemperature();
    void updateTemperature();
    fifo_t readFIFO();
    void printRegisters();
    void printAlgorithmVals();
    bool foundPeak(unsigned long timeOfValue);

    dcFilter_t dcRemoval(float x, float prev_w, float alpha);
    void lowPassButterworthFilter( float x, butterworthFilter_t * filterResult );
    float meanDiff(float M, meanDiffFilter_t* filterValues);

    bool update();
    bool pulseValid();
    float BPM();
    float cardiogram();
    float oxygen();
    float temperature();
    float temperatureF();
    
    float rawCardiogram();
    float rawIRVal();
    float rawRedVal();
    float DCfilteredIRVal();
    float DCfilteredRedVal();

  private:
    bool detectPulse(float sensor_value, unsigned long timeOfValue);
    void balanceIntesities( float redLedDC, float IRLedDC );
    void writeRegister(byte address, byte val);
    uint8_t readRegister(uint8_t address);
    void readFrom(byte address, int num, byte _buff[]);

  private:
    bool debug;

    float currentTemperature;

    uint8_t IrLedCurrent;
    uint8_t redLEDCurrent;
    float lastREDLedCurrentCheck;

    uint32_t samplePeriodMicros;

    uint8_t currentPulseDetectorState;
    float currentBPM;
    float valuesBPM[PULSE_BPM_SAMPLE_SIZE];
    float valuesBPMSum;
    uint8_t valuesBPMCount;
    uint8_t bpmIndex;
    uint32_t lastBeatThreshold;
    int _pulseValid;

    unsigned long lastSampleReadTime;
    fifo_t prevFifo;

    dcFilter_t dcFilterIR;
    dcFilter_t dcFilterRed;
    butterworthFilter_t lpbFilterIR;
    meanDiffFilter_t meanDiffIR;

    float irACValueSqSum;
    float redACValueSqSum;
    uint16_t samplesRecorded;
    uint16_t pulsesDetected;
    float currentSaO2Value;

    int pulseMaxInput = 1500;
    int pulseThreshold = 100;

    int lastState = 0;

    float prev_sensor_value = 0;
    uint32_t currentBeatMicros = 0;
    uint32_t lastBeatMicros = 0;
    uint32_t lastLastBeatMicros = 0;
    bool _pulseUpdate;
    float normalizedCardiogramOutputVal;
    
    
    int sensorValidDataCount = 0;
    float lastUsedCardiogramVal = 0;
    float lastCalculatedCardiogramVal = 0;
    float cardiogramBaseline = -0.2;
    float positivePeakAverage = 0;
    int positivePeakCount = 0;


    RunningAverage<12> positiveAverage;
    RunningAverage<6> smoothPositiveAverage;
    RunningAverage<16> testAverage;
    RunningAverage<8> pra;
};

#endif
