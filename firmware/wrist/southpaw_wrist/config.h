/*
 * Modified by: Yue Ming (Team P1D) – compile-time settings for the SouthpawOS wrist board (original file).
 *
 * training/tools/segment_punches.py reads the detector and feature constants
 * from this file, so the offline tool and the board can't drift apart.
 * Keep each of those as `constexpr <type> NAME = <integer>;` on one line.
 */
#ifndef SOUTHPAW_CONFIG_H
#define SOUTHPAW_CONFIG_H

#include <stdint.h>

// ---- Which wrist this board is strapped to: 'L' or 'R' ---------------------
#ifndef HAND
#define HAND 'L'
#endif

// BLE_PUBLIC_ADDR is written least-significant byte first (it reads 02:80:E1:50:31:4C for L).
// The board advertises with the BlueNRG chip's own stored random address (unique per
// chip); the public address is only made unique per hand so two boards never share one.
#if HAND == 'L'
  #define BLE_NAME "P1D-WRIST-L"
  #define BLE_PUBLIC_ADDR {0x4C, 0x31, 0x50, 0xE1, 0x80, 0x02}
#elif HAND == 'R'
  #define BLE_NAME "P1D-WRIST-R"
  #define BLE_PUBLIC_ADDR {0x52, 0x31, 0x50, 0xE1, 0x80, 0x02}
#else
  #error "HAND must be 'L' or 'R'"
#endif

// ---- Sampling ---------------------------------------------------------------
constexpr uint16_t SAMPLE_RATE_HZ = 200;
constexpr uint16_t COUNTS_PER_G = 32;              // BMA250 at +/-16 g

// ---- Punch detector (also used by segment_punches.py) -----------------------
// Total acceleration = sqrt(x^2 + y^2 + z^2) in counts, gravity included (1 g = 32).
constexpr uint16_t DETECT_THRESHOLD_COUNTS = 96;   // 3.0 g. Starting guess: tune with real punches.
constexpr uint16_t PEAK_SEARCH_MS = 150;           // after a trigger, the peak is the largest sample in this time.
                                                   // 100 missed the snap on 8/20 jabs: a slow 3-4 g wind-up set off the trigger ~110 ms before it.
constexpr uint16_t COOLDOWN_MS = 350;              // no new trigger until this long after the peak.
                                                   // Combo punches come 0.38-0.45 s after the previous one (14 Nov, in the case).
                                                   // 300 also caught the pull-back ~0.3 s after an uppercut; 400 left no margin.
constexpr uint16_t WINDOW_PRE_MS = 150;            // punch window starts this long before the peak
constexpr uint16_t WINDOW_POST_MS = 250;           // ... and ends this long after it

// ---- Features (also used by segment_punches.py) -----------------------------
constexpr uint16_t BURST_THRESHOLD_COUNTS = 48;    // 1.5 g: burst = the run above this around the peak
constexpr uint16_t GRAVITY_AVG_MS = 50;            // tilt = average of the first / last 50 ms of the window
constexpr uint16_t MIN_BURST_MS = 20;              // shorter bursts are knocks (e.g. putting the board down), not punches

// ---- Derived values (segment_punches.py computes these the same way) --------
constexpr uint32_t SAMPLE_PERIOD_US = 1000000UL / SAMPLE_RATE_HZ;
constexpr uint16_t SAMPLE_PERIOD_MS = 1000 / SAMPLE_RATE_HZ;
constexpr uint16_t PEAK_SEARCH_SAMPLES = PEAK_SEARCH_MS / SAMPLE_PERIOD_MS;
constexpr uint16_t COOLDOWN_SAMPLES = COOLDOWN_MS / SAMPLE_PERIOD_MS;
constexpr uint16_t WINDOW_PRE_SAMPLES = WINDOW_PRE_MS / SAMPLE_PERIOD_MS;
constexpr uint16_t WINDOW_POST_SAMPLES = WINDOW_POST_MS / SAMPLE_PERIOD_MS;
constexpr uint16_t WINDOW_LEN = WINDOW_PRE_SAMPLES + 1 + WINDOW_POST_SAMPLES;  // peak sits at index WINDOW_PRE_SAMPLES
constexpr uint16_t GRAVITY_AVG_SAMPLES = GRAVITY_AVG_MS / SAMPLE_PERIOD_MS;
constexpr uint32_t DETECT_THRESHOLD_SQ = (uint32_t)DETECT_THRESHOLD_COUNTS * DETECT_THRESHOLD_COUNTS;
constexpr uint32_t BURST_THRESHOLD_SQ = (uint32_t)BURST_THRESHOLD_COUNTS * BURST_THRESHOLD_COUNTS;

// ---- Ring buffer: the last ~1.3 s of samples ---------------------------------
constexpr uint16_t RING_SIZE = 256;                // power of two

static_assert(1000 % SAMPLE_RATE_HZ == 0, "sample period must be a whole number of ms");
static_assert(PEAK_SEARCH_SAMPLES >= 1, "peak search needs at least one sample");
static_assert(WINDOW_POST_MS > PEAK_SEARCH_MS, "the peak must be found before the window ends");
static_assert(COOLDOWN_MS > WINDOW_POST_MS, "one punch at a time: its window must finish before the cooldown ends");
static_assert(BURST_THRESHOLD_COUNTS <= DETECT_THRESHOLD_COUNTS, "the peak must be inside the burst");
static_assert(GRAVITY_AVG_SAMPLES >= 1 && 2 * GRAVITY_AVG_SAMPLES <= WINDOW_LEN, "gravity averages must fit in the window");
static_assert((RING_SIZE & (RING_SIZE - 1)) == 0, "RING_SIZE must be a power of two");
static_assert(RING_SIZE > WINDOW_LEN, "the ring must hold a whole punch window");

// ---- USB, display, housekeeping -----------------------------------------------
#define USB_STREAM_RAW 1                           // also print "board_ms,x,y,z" lines (log_accel.py can record them)
constexpr uint16_t STATS_INTERVAL_MS = 5000;       // "# stats ..." line
constexpr uint16_t BATTERY_INTERVAL_MS = 10000;
constexpr uint8_t DISPLAY_BRIGHTNESS = 8;          // 0-15
constexpr uint8_t SENSOR_LOST_SAMPLES = 20;        // this many -1,-1,-1 readings in a row = shield missing
constexpr uint16_t SENSOR_RETRY_MS = 1000;

#endif
