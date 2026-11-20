# TinyCircuits MAX30101 Library

Used for the TinyCircuits **[Pulse Oximetry Sensor](https://tinycircuits.com/collections/wireling-sensors/products/pulse-oximetry-sensor-wireling)**.

*Support this library by buying products from **[TinyCircuits](https://tinycircuits.com/)***


## MAX30101 Class

* **int begin(...)** Initializes the sensor, returns 1 if the MAX30101 is not detected, returns 0 otherwise.
* **void setMode(Mode mode)** Sets *mode* to a mode:
	* *MAX30101_MODE_HR_ONLY* for heart rate only
	* *MAX30101_MODE_SPO2_HR* for blood oxygen and heart rate
	* *MAX30101_MODE_MULTI_LED* for blood oxygen and heart rate using both the red and infrared LEDs in the sensor
* **void setSamplingRateAndAveragingCount(SamplingRate rate, SampleAveragingCount count)** Sets the sampling rate for the device to *rate* and will average a total of *count* samples for each value returned.
* **void setLEDPulseWidth(LEDPulseWidth pw)** Sets the pulse width for the red and infrared LEDs.
* **void setLEDCurrents( byte redLedCurrent, byte IRLedCurrent )** Sets the current for the red and infrared LEDs.
* **void updateTemperature()** Updates the value of *currentTemperature*.
* **fifo_t readFIFO()** Reads the raw values in the FIFO queue for both the red and infrared LEDs.
* **void printRegisters()** Prints the values of the following registers to the Serial Monitor:
	* *MAX30101_INT_STATUS_1*
	* *MAX30101_INT_STATUS_2*
	* *MAX30101_INT_ENABLE_1*
	* *MAX30101_INT_ENABLE_2*
	* *MAX30101_FIFO_WRITE_PTR*
	* *MAX30101_FIFO_OVERFLOW_COUNTER*
	* *MAX30101_FIFO_READ_PTR*
	* *MAX30101_FIFO_CONFIG*
	* *MAX30101_MODE_CONF*
	* *MAX30101_SPO2_CONF*
	* *MAX30101_LED1_PA*
	* *MAX30101_LED2_PA*
	* *MAX30101_REV_ID*
	* *MAX30101_PART_ID*
[//]: # (* **void printAlgorithmVals()** This does nothing right now since it's all commented out.)
* **bool foundPeak(unsigned long timeOfValue)** Determines if a valid peak has occurred in the pulse graph at the given time stamp *timeOfValue*.

* **dcFilter_t dcRemoval(float x, float prev_w, float alpha)**
* **void lowPassButterworthFilter( float x, butterworthFilter_t \* filterResult )**
* **float meanDiff(float M, meanDiffFilter_t\* filterValues)**

* **bool update()** Updates raw values from the red and infrared LED readings, sample time, and temperature reading.
* **bool pulseValid()** Returns true if a valid pulse has been detected.
* **float BPM()** Returns the sensed BPM value if there is a valid pulse. Otherwise, returns 0.0.
* **float cardiogram()** Returns the normalized cardiogram output value.
* **float oxygen()** Returns the current blood oxygen percentage.
* **float temperature()** Returns the current temperature in degrees Celsius.
* **float temperatureF()** Returns the current temperature in degrees Fahrenheit.
    
* **float rawCardiogram()** Returns the filtered
* **float rawIRVal()** Returns the raw value from the infrared LED reading.
* **float rawRedVal()** Returns the raw value from the red LED reading.
* **float DCfilteredIRVal()** Returns the filtered value from the infrared LED reading.
* **float DCfilteredRedVal()** Returns the filtered value from the red LED reading.

### **private:**
* **bool detectPulse(float sensor_value, unsigned long timeOfValue)**
* **void balanceIntesities( float redLedDC, float IRLedDC )**
* **void writeRegister(byte address, byte val)**
* **uint8_t readRegister(uint8_t address)**
* **void readFrom(byte address, int num, byte _buff[])**


