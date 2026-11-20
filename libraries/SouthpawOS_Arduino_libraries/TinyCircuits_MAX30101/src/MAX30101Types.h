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

#ifndef MAX30101Types_H
#define MAX30101Types_H

#include <Arduino.h>
#include <Wire.h>
#include <math.h>

typedef enum PulseStateMachine {
  PULSE_IDLE,
  PULSE_TRACE_UP,
  PULSE_TRACE_DOWN
} PulseStateMachine;

struct fifo_t {
  uint32_t rawIR;
  uint32_t rawRed;
};

struct dcFilter_t {
  float w;
  float result;
};

struct butterworthFilter_t
{
  float v[2];
  float result;
};

struct meanDiffFilter_t
{
  float values[MEAN_FILTER_SIZE];
  byte index;
  float sum;
  byte count;
};

// Simple templated averaging class based on Running Average by Rob Tillaart: http://arduino.cc/playground/Main/RunningAverage
#ifndef RunningAverage
template <const unsigned int N>
class RunningAverage
{
  public:
    void addValue(int16_t val) {
      _ar[_index] = val;
      _index++;
      if (_index == N) _index = 0;
    };
    void fillValue(int16_t val) {
      for (unsigned int i = 0; i < N; i++)_ar[i] = val;
    };
    int16_t getAverage() {
      int32_t sum = 0;
      for (unsigned int i = 0; i < N; i++)sum += (int32_t)_ar[i];
      return sum / (int32_t)N;
    };
  protected:
    int _index = 0;
    int16_t _ar[N];
};

#endif
#endif
