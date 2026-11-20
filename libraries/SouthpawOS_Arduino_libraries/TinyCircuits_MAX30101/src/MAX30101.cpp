/*
  MIT License

  Updated to MAX30101 by Ben Rose for TinyCircuits, https://www.tinycircuits.com

  Copyright (c) 2017 Raivis Strogonovs (https://morf.lv)

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

#include "MAX30101.h"

#if defined (ARDUINO_ARCH_AVR)
#define MAX30101SerialMonitorInterface Serial
#elif defined(ARDUINO_ARCH_SAMD)
#define MAX30101SerialMonitorInterface SerialUSB
#endif

MAX30101::MAX30101(void) {
};

int MAX30101::begin(Mode modeInit, SamplingRate samplingRateInit, SampleAveragingCount averagingCountInit, LEDPulseWidth pulseWidthInit, LEDCurrent IrLedCurrentInit, bool debugInit)
{
  debug = debugInit;
  if(readRegister(MAX30101_PART_ID) != MAX30101_PART_ID_VALUE){
    return 1;
  }
  setMode(modeInit);
  setSamplingRateAndAveragingCount(samplingRateInit, averagingCountInit);
  setLEDPulseWidth(pulseWidthInit);
  redLEDCurrent = (uint8_t)STARTING_RED_LED_CURRENT;
  lastREDLedCurrentCheck = 0;

  IrLedCurrent = IrLedCurrentInit;
  setLEDCurrents(redLEDCurrent, IrLedCurrentInit);

  dcFilterIR.w = 0;
  dcFilterIR.result = 0;

  dcFilterRed.w = 0;
  dcFilterRed.result = 0;

  lpbFilterIR.v[0] = 0;
  lpbFilterIR.v[1] = 0;
  lpbFilterIR.result = 0;

  meanDiffIR.index = 0;
  meanDiffIR.sum = 0;
  meanDiffIR.count = 0;

  valuesBPM[0] = 0;
  valuesBPMSum = 0;
  valuesBPMCount = 0;
  bpmIndex = 0;

  irACValueSqSum = 0;
  redACValueSqSum = 0;
  samplesRecorded = 0;
  pulsesDetected = 0;
  currentSaO2Value = 0;

  lastSampleReadTime = 0;
  currentPulseDetectorState = PULSE_IDLE;
  lastBeatThreshold = 0;
  _pulseUpdate = 0;
  return 0;
}

// Writes val to address register on device
void MAX30101::writeRegister(byte address, byte val)
{
  Wire.beginTransmission(MAX30101_DEVICE_ADDR); // start transmission to device
  Wire.write(address); // send register address
  Wire.write(val); // send value to write
  Wire.endTransmission(); // end transmission
}

uint8_t MAX30101::readRegister(uint8_t address)
{
  Wire.beginTransmission(MAX30101_DEVICE_ADDR);
  Wire.write(address);
  Wire.endTransmission(false);
  Wire.requestFrom(MAX30101_DEVICE_ADDR, 1);

  return Wire.read();
}

// Reads num bytes starting from address register on device in to _buff array
void MAX30101::readFrom(byte address, int num, byte _buff[])
{
  Wire.beginTransmission(MAX30101_DEVICE_ADDR); // start transmission to device
  Wire.write(address); // sends address to read from
  Wire.endTransmission(false); // end transmission

  Wire.requestFrom(MAX30101_DEVICE_ADDR, num); // request 6 bytes from device Registers: DATAX0, DATAX1, DATAY0, DATAY1, DATAZ0, DATAZ1

  int i = 0;
  while (Wire.available()) // device may send less than requested (abnormal)
  {
    _buff[i++] = Wire.read(); // receive a byte
  }

  Wire.endTransmission(); // end transmission
}

void MAX30101::setMode(Mode mode)
{
  byte currentModeReg = readRegister( MAX30101_MODE_CONF );
  writeRegister( MAX30101_MODE_CONF, (currentModeReg & 0xF8) | mode );
}

void MAX30101::setSamplingRateAndAveragingCount(SamplingRate rate, SampleAveragingCount count)
{
  byte currentSpO2Reg = readRegister( MAX30101_SPO2_CONF );
  writeRegister( MAX30101_SPO2_CONF, (1 << 6) | (1 << 5) | ( currentSpO2Reg & 0xE3 ) | (rate << 2) );

  byte currentFIFOConfig = readRegister( MAX30101_FIFO_CONFIG );
  writeRegister( MAX30101_FIFO_CONFIG, ( currentFIFOConfig & 0x1F ) | ((count & 0x07) << 5) );

  samplePeriodMicros = sampleRateToSamplePeriodMicros[rate] * sampleAveragingValToCount[count];
}

void MAX30101::setLEDPulseWidth(LEDPulseWidth pw)
{
  byte currentSpO2Reg = readRegister( MAX30101_SPO2_CONF );
  writeRegister( MAX30101_SPO2_CONF, ( currentSpO2Reg & 0xFC ) | pw );
}

void MAX30101::setLEDCurrents( byte redLedCurrent, byte IRLedCurrent )
{
  writeRegister( MAX30101_LED1_PA, redLedCurrent);
  writeRegister( MAX30101_LED2_PA, IRLedCurrent);
}

void MAX30101::updateTemperature()
{
  //Reading MAX30101_INT_STATUS_2 to check for new conversion data does not seem to work
  //so we fall back to starting a new temp conversion after reading the old data.
  //Conversion takes <1ms, so reading at the optical sensor rate(Default 10ms) will enough
  //time. Temperature readings being 10ms old should be acceptable.
  int8_t temp = (int8_t)readRegister( MAX30101_TEMP_INT );
  float tempFraction = (float)readRegister( MAX30101_TEMP_FRACTION ) * 0.0625;
  currentTemperature = (float)temp + tempFraction;
  writeRegister( MAX30101_TEMP_CONFIG, 1 ); //start new conversion
}

fifo_t MAX30101::readFIFO()
{
  fifo_t result;

  byte buffer[6];
  readFrom( MAX30101_FIFO_DATA, 6, buffer );
  uint32_t temp = (buffer[0] << 16) | (buffer[1] << 8) | buffer[2];
  temp = temp >> 0;
  result.rawRed = temp & 0x0003FFFF;
  temp = (buffer[3] << 16) | (buffer[4] << 8) | buffer[5];
  temp = temp >> 0;
  result.rawIR = temp & 0x0003FFFF;
  //MAX30101SerialMonitorInterface.println(temp,BIN);
  //MAX30101SerialMonitorInterface.print((temp&0xFFFC0000)>>18,BIN);
  //MAX30101SerialMonitorInterface.print('\t');
  //MAX30101SerialMonitorInterface.println(temp&0x0003FFFF);

  return result;
}


void MAX30101::printRegisters()
{
  MAX30101SerialMonitorInterface.print(readRegister(MAX30101_INT_STATUS_1),  HEX); MAX30101SerialMonitorInterface.print('\t');
  MAX30101SerialMonitorInterface.print(readRegister(MAX30101_INT_STATUS_2),  HEX); MAX30101SerialMonitorInterface.print('\t');
  MAX30101SerialMonitorInterface.print(readRegister(MAX30101_INT_ENABLE_1),  HEX); MAX30101SerialMonitorInterface.print('\t');
  MAX30101SerialMonitorInterface.print(readRegister(MAX30101_INT_ENABLE_2),  HEX); MAX30101SerialMonitorInterface.print('\t');
  MAX30101SerialMonitorInterface.print(readRegister(MAX30101_FIFO_WRITE_PTR), HEX); MAX30101SerialMonitorInterface.print('\t');
  MAX30101SerialMonitorInterface.print(readRegister(MAX30101_FIFO_OVERFLOW_COUNTER), HEX); MAX30101SerialMonitorInterface.print('\t');
  MAX30101SerialMonitorInterface.print(readRegister(MAX30101_FIFO_READ_PTR), HEX); MAX30101SerialMonitorInterface.print('\t');
  MAX30101SerialMonitorInterface.print(readRegister(MAX30101_FIFO_CONFIG),   HEX); MAX30101SerialMonitorInterface.print('\t');
  MAX30101SerialMonitorInterface.print(readRegister(MAX30101_MODE_CONF), HEX); MAX30101SerialMonitorInterface.print('\t');
  MAX30101SerialMonitorInterface.print(readRegister(MAX30101_SPO2_CONF), HEX); MAX30101SerialMonitorInterface.print('\t');
  MAX30101SerialMonitorInterface.print(readRegister(MAX30101_LED1_PA), HEX); MAX30101SerialMonitorInterface.print('\t');
  MAX30101SerialMonitorInterface.print(readRegister(MAX30101_LED2_PA), HEX); MAX30101SerialMonitorInterface.print('\t');
  MAX30101SerialMonitorInterface.print(readRegister(MAX30101_REV_ID),      HEX); MAX30101SerialMonitorInterface.print('\t');
  MAX30101SerialMonitorInterface.print(readRegister(MAX30101_PART_ID),     HEX); MAX30101SerialMonitorInterface.print('\t');
}


void MAX30101::printAlgorithmVals()
{
  //MAX30101SerialMonitorInterface.print(_pulseValid);
  //MAX30101SerialMonitorInterface.print("\t");
  //MAX30101SerialMonitorInterface.print(currentBPM);
  //MAX30101SerialMonitorInterface.print("\t");
  //MAX30101SerialMonitorInterface.print(constrain(dcFilterIR.result,-1000,1000));
  //MAX30101SerialMonitorInterface.print("\t");
  //MAX30101SerialMonitorInterface.print(constrain(lpbFilterIR.result,-1000,1000));
  //MAX30101SerialMonitorInterface.print("\t");
  //MAX30101SerialMonitorInterface.print(constrain(testAverage.getAverage(),-1000,1000));
  //MAX30101SerialMonitorInterface.print("\t");
  //MAX30101SerialMonitorInterface.print(currentTemperature);
  //MAX30101SerialMonitorInterface.print("\t");
  //MAX30101SerialMonitorInterface.print(currentSaO2Value);
  //MAX30101SerialMonitorInterface.print("\t");
  //MAX30101SerialMonitorInterface.print(dcFilterIR.w/100000.0);
  //MAX30101SerialMonitorInterface.print(dcFilterIR.w);
  //MAX30101SerialMonitorInterface.print("\t");
  //MAX30101SerialMonitorInterface.print(dcFilterRed.w);
  //MAX30101SerialMonitorInterface.print("\t");
  //MAX30101SerialMonitorInterface.print(-dcFilterIR.result);
  //MAX30101SerialMonitorInterface.print("\t");
  //MAX30101SerialMonitorInterface.print(dcFilterRed.result);
  //MAX30101SerialMonitorInterface.print("\t");
  //MAX30101SerialMonitorInterface.println("");
}

bool MAX30101::pulseValid() {

  if (_pulseValid)
    return true;
  else
    return false;
}

float MAX30101::BPM() {
  if (_pulseValid)
    return currentBPM;
  else
    return 0.0;
}

float MAX30101::cardiogram() {
  return normalizedCardiogramOutputVal;
}

float MAX30101::rawCardiogram() {
  return lpbFilterIR.result;
}

float MAX30101::temperature() {
  return currentTemperature;
}

float MAX30101::temperatureF() {
  return currentTemperature*9.0/5.0 + 32.0;
}

float MAX30101::oxygen() {
  if (_pulseValid)
    return currentSaO2Value;
  else
    return 0.0;
}

float MAX30101::rawIRVal() {
  return dcFilterIR.w;
}

float MAX30101::rawRedVal() {
  return dcFilterRed.w;
}

float MAX30101::DCfilteredIRVal() {
  return dcFilterIR.result;
}

float MAX30101::DCfilteredRedVal() {
  return dcFilterRed.result;
}

bool MAX30101::update()
{
  unsigned long sampleTime;
  uint8_t numSamples;
  if (false) {
    //This code block polls the sensor until a new sample is available without using the interrupt line.
    //Polling at a high rate seems to add a lot of noise to the sensor output, and polling at a low rate
    //is less beneficial, so skip all this and just assume a time.
    unsigned long timeoutStart = micros();
    uint8_t writePos = readRegister(MAX30101_FIFO_WRITE_PTR);
    while (writePos == readRegister(MAX30101_FIFO_WRITE_PTR) && micros() - timeoutStart > samplePeriodMicros * 2ul); //wait for new sample, timeout after two sample periods
    sampleTime = micros();
    if (sampleTime - timeoutStart >= samplePeriodMicros * 2ul) {
      //need to reset, not implemented.................................
      return false;
    }
    writePos = (writePos + 1) & MAX30101_FIFO_PTR_MAX; //correct write position that incremented during while loop
    uint8_t readPos = readRegister(MAX30101_FIFO_READ_PTR);
    numSamples = (writePos - readPos) & MAX30101_FIFO_PTR_MAX;
  }else{
    //This code block assumes the sample time is 'now'
    uint8_t writePos = readRegister(MAX30101_FIFO_WRITE_PTR);
    uint8_t readPos = readRegister(MAX30101_FIFO_READ_PTR);
    numSamples = (writePos - readPos) & MAX30101_FIFO_PTR_MAX;
    if (!numSamples) {
      return false;
    }
    sampleTime = micros(); 
  }

  updateTemperature();

  //the datasheet says: "While the device output data is relatively insensitive to the wavelength
  //of the IR LED, the red LED’s wavelength is critical to correct interpretation of the data"
  //Looking at the photodiode efficiency table, there seems to be only a
  //small difference over the typical operating temperature.
  //float redLEDTemp = redLEDCurrent+0.5; //With default settings, temperature rise of the red LED vs die temp is under 1C according to datasheet Table 13
  //float redLEDWavelength = 655 + 0.15 * redLEDTemp; // Change is about 0.15nm per degree C according to datasheet graph

  fifo_t rawData;
  for (unsigned long i = 0; i < numSamples; i++) {
    rawData = readFIFO();
    dcFilterIR = dcRemoval( (float)rawData.rawIR, dcFilterIR.w, ALPHA );
    dcFilterRed = dcRemoval( (float)rawData.rawRed, dcFilterRed.w, ALPHA );

    irACValueSqSum += dcFilterIR.result * dcFilterIR.result;
    redACValueSqSum += dcFilterRed.result * dcFilterRed.result;

    samplesRecorded++;

    unsigned long calculatedSampleTime = sampleTime - ((unsigned long)numSamples - 1ul - i) * samplePeriodMicros;

    printAlgorithmVals();

    if (detectPulse(dcFilterIR.result, calculatedSampleTime)) {
      if (_pulseValid) {
        float ratioRMS = (log( sqrt(redACValueSqSum / samplesRecorded) ) * 660.0) / (log( sqrt(irACValueSqSum / samplesRecorded) ) * 880.0);
        currentSaO2Value = 110.0 - 25.0 * ratioRMS;
      }
      irACValueSqSum = 0;
      redACValueSqSum = 0;
      samplesRecorded = 0;
    }
    balanceIntesities( dcFilterRed.w, dcFilterIR.w );

  }
  lastSampleReadTime = sampleTime;
  return true;
}


void MAX30101::balanceIntesities( float redLedDC, float IRLedDC )
{
  if ( millis() - lastREDLedCurrentCheck >= RED_LED_CURRENT_ADJUSTMENT_MS) {
    if ( IRLedDC - redLedDC > MAGIC_ACCEPTABLE_INTENSITY_DIFF && redLEDCurrent < MAX30101_LED_CURRENT_50MA) {
      redLEDCurrent++;
      setLEDCurrents( redLEDCurrent, IrLedCurrent );
      if (debug == true) {
        MAX30101SerialMonitorInterface.print("RED LED Current + ");
        MAX30101SerialMonitorInterface.println(redLEDCurrent);
      }
    }
    else if (redLedDC - IRLedDC > MAGIC_ACCEPTABLE_INTENSITY_DIFF && redLEDCurrent > 0) {
      redLEDCurrent--;
      setLEDCurrents( redLEDCurrent, IrLedCurrent );
      if (debug == true) {
        MAX30101SerialMonitorInterface.print("RED LED Current - ");
        MAX30101SerialMonitorInterface.println(redLEDCurrent);
      }
    }
    lastREDLedCurrentCheck = millis();
  }
}


dcFilter_t MAX30101::dcRemoval(float x, float prev_w, float alpha)
{
  dcFilter_t filtered;
  filtered.w = x + alpha * prev_w;
  filtered.result = filtered.w - prev_w;

  return filtered;
}

void MAX30101::lowPassButterworthFilter( float x, butterworthFilter_t * filterResult )
{
  filterResult->v[0] = filterResult->v[1];

  //Fs = 100Hz and Fc = 10Hz
  //filterResult->v[1] = (2.452372752527856026e-1 * x) + (0.50952544949442879485 * filterResult->v[0]);

  //Fs = 100Hz and Fc = 4Hz
  filterResult->v[1] = (1.367287359973195227e-1 * x) + (0.72654252800536101020 * filterResult->v[0]); //Very precise butterworth filter

  filterResult->result = filterResult->v[0] + filterResult->v[1];
}

float MAX30101::meanDiff(float M, meanDiffFilter_t* filterValues)
{
  float avg = 0;

  filterValues->sum -= filterValues->values[filterValues->index];
  filterValues->values[filterValues->index] = M;
  filterValues->sum += filterValues->values[filterValues->index];

  filterValues->index++;
  filterValues->index = filterValues->index % MEAN_FILTER_SIZE;

  if (filterValues->count < MEAN_FILTER_SIZE)
    filterValues->count++;

  avg = filterValues->sum / filterValues->count;
  return avg - M;
}

bool debugPulseGraph = false;
bool debugNormalizedPulseGraph = false;
bool debugBeatDetect = false;

bool MAX30101::detectPulse(float dc_filtered_sensor_value, unsigned long timeOfValue) {
  float meanDiffResIR = meanDiff( dc_filtered_sensor_value, &meanDiffIR); //MAX30101SerialMonitorInterface.print(constrain(-meanDiffResIR,-1000,1000));MAX30101SerialMonitorInterface.print("\t");
  lowPassButterworthFilter( meanDiffResIR, &lpbFilterIR );                 //MAX30101SerialMonitorInterface.print(constrain(lpbFilterIR.result,-1200,1200));MAX30101SerialMonitorInterface.print("\t");

  float sensor_value = constrain(lpbFilterIR.result, -pulseMaxInput, pulseMaxInput);

  bool valueProbablyInvalid = false;
  if (abs(sensor_value) >= pulseMaxInput)
    valueProbablyInvalid = true;

  if (sensor_value > 10)
    positiveAverage.addValue(sensor_value);

  pulseThreshold = positiveAverage.getAverage();

  if (sensor_value < 10)
    smoothPositiveAverage.addValue(pulseThreshold);

  pulseThreshold = smoothPositiveAverage.getAverage();

  float newCalculatedCardiogramVal = sensor_value / (smoothPositiveAverage.getAverage() * 3.5) + cardiogramBaseline;



  if (valueProbablyInvalid) {
    sensorValidDataCount = 0;
    lastUsedCardiogramVal -= lastUsedCardiogramVal / 10;
    normalizedCardiogramOutputVal = lastUsedCardiogramVal + cardiogramBaseline;
  } else {
    if (sensorValidDataCount > VALID_DATA_MINIMUM_COUNT) {
      normalizedCardiogramOutputVal = newCalculatedCardiogramVal;
      lastUsedCardiogramVal = newCalculatedCardiogramVal;
    } else {
      if (sensorValidDataCount <= VALID_DATA_MINIMUM_COUNT) {
        sensorValidDataCount++;
        lastUsedCardiogramVal -= lastUsedCardiogramVal / 10;
        normalizedCardiogramOutputVal = lastUsedCardiogramVal + cardiogramBaseline;
      } else {
        if (lastCalculatedCardiogramVal < 0 && newCalculatedCardiogramVal > 0) {
          sensorValidDataCount++;
        }
      }
    }
  }
  lastCalculatedCardiogramVal = newCalculatedCardiogramVal;



  if (normalizedCardiogramOutputVal > 0)
    normalizedCardiogramOutputVal = 1.0 - 1.0 / (normalizedCardiogramOutputVal + 1.0); //this will 'pass' values <0.5 and exponentially dampen values above
  normalizedCardiogramOutputVal = constrain(normalizedCardiogramOutputVal, -1.0, 1.0);
  if (debugNormalizedPulseGraph)MAX30101SerialMonitorInterface.print("-1\t1\t");
  if (debugNormalizedPulseGraph)MAX30101SerialMonitorInterface.println(normalizedCardiogramOutputVal);

  if (debugPulseGraph) {
    MAX30101SerialMonitorInterface.print(sensor_value);
    MAX30101SerialMonitorInterface.print('\t');
    MAX30101SerialMonitorInterface.print(positiveAverage.getAverage());
    MAX30101SerialMonitorInterface.print('\t');
    MAX30101SerialMonitorInterface.print(pulseThreshold);
    MAX30101SerialMonitorInterface.print('\t');
  }

  uint32_t longestBeatDuration = 60000000.0 / 20.0;
  if ((micros() - lastBeatMicros) > longestBeatDuration) {
    if (_pulseValid) {
      if (debugBeatDetect == true) MAX30101SerialMonitorInterface.println("pulse valid clear");
      _pulseValid = 0;
      _pulseUpdate = true;
    }
  }
  if (valueProbablyInvalid)
  {
    if (debug == true) MAX30101SerialMonitorInterface.println("pulse detect reset");
    currentPulseDetectorState = PULSE_IDLE;
    prev_sensor_value = 0;
    lastLastBeatMicros = 0;
    lastBeatMicros = 0;
    currentBeatMicros = 0;
    pulseThreshold = 100;
    if (debugPulseGraph)MAX30101SerialMonitorInterface.println(0);
    return false;
  }

  switch (currentPulseDetectorState) {
    case PULSE_IDLE:
      if (sensor_value >= pulseThreshold) {
        currentPulseDetectorState = PULSE_TRACE_UP;
      }
      break;
    case PULSE_TRACE_UP:
      if (sensor_value < prev_sensor_value) {
        if (debugPulseGraph)MAX30101SerialMonitorInterface.println(sensor_value);
        currentPulseDetectorState = PULSE_TRACE_DOWN;
        prev_sensor_value = sensor_value;
        return foundPeak(timeOfValue);
      }
      break;
    case PULSE_TRACE_DOWN:
      if (sensor_value < pulseThreshold) {
        currentPulseDetectorState = PULSE_IDLE;
      }
      break;
  }
  prev_sensor_value = sensor_value;
  if (debugPulseGraph)MAX30101SerialMonitorInterface.println(0);
  return false;
}


bool MAX30101::foundPeak(unsigned long timeOfValue) {
  if (debugBeatDetect == true) {
    MAX30101SerialMonitorInterface.print("Peak Found: ");
    MAX30101SerialMonitorInterface.print(timeOfValue);
    MAX30101SerialMonitorInterface.print('\t');
  }
  uint32_t beatDuration = timeOfValue - lastBeatMicros;
  float rawBPM = 60000000.0 / (float)beatDuration;

  if (debugBeatDetect == true) {
    MAX30101SerialMonitorInterface.print(beatDuration);
    MAX30101SerialMonitorInterface.print('\t');
    MAX30101SerialMonitorInterface.print(rawBPM);
    MAX30101SerialMonitorInterface.print('\t');
  }

  if (_pulseValid == 0 && rawBPM < 40) {
    if (debugBeatDetect == true)MAX30101SerialMonitorInterface.println("Skipping peak!\t");
    lastBeatMicros = timeOfValue;
    return false;
  }
  if (rawBPM > 220) {
    if (debugBeatDetect == true)MAX30101SerialMonitorInterface.println("Ignoring peak!\t");
    return false;
  }

  if (_pulseValid) {
    if (abs(rawBPM * 2.0 - currentBPM) < 10.0) { //missed a peak while there was a valid BPM. Double the latest value and keep going.
      if (debugBeatDetect == true)MAX30101SerialMonitorInterface.print("Missed peak!\t");
      rawBPM *= 2.0;
    }
  }

  lastBeatMicros = timeOfValue;


  valuesBPM[bpmIndex] = rawBPM;
  valuesBPMSum = 0;
  for (int i = 0; i < PULSE_BPM_SAMPLE_SIZE; i++)
  {
    valuesBPMSum += valuesBPM[i];
  }

  bpmIndex++;
  bpmIndex = bpmIndex % PULSE_BPM_SAMPLE_SIZE;
  if (valuesBPMCount < PULSE_BPM_SAMPLE_SIZE)
    valuesBPMCount++;
  currentBPM = valuesBPMSum / valuesBPMCount;

  int diff = 0;
  for (int i = 0; i < PULSE_BPM_SAMPLE_SIZE; i++)
  {
    diff += abs(currentBPM - valuesBPM[i]);
  }
  if (diff < 15) {
    if (_pulseValid == 0) {
      _pulseUpdate = true;
    }
    _pulseValid++;
  } else {
    if (_pulseValid) {
      _pulseValid = false;
      _pulseUpdate = true;
    }
  }

  if (debugBeatDetect == true)MAX30101SerialMonitorInterface.println();
  return true;
}
