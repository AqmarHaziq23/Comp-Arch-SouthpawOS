/*
  PunchTracker_Sound.ino   (PunchTracker + buzzer sounds + menu music)
  ------------------------------------------------------------------
  60-second punch counter for a punching bag.

  Hardware (TinyCircuits):
    - TinyScreen+  (SAMD21 + 96x64 OLED + 4 buttons)
    - BMA250 accelerometer: either
        * Accelerometer TinyShield (stacked)          -> USE_WIRELING 0
        * Accelerometer Wireling + Wireling Adapter   -> USE_WIRELING 1
    - Wireling Adapter TinyShield + Buzzer Wireling (AST1035) in port
      BUZZER_PORT (default 0). If the accelerometer is also a Wireling
      (USE_WIRELING 1) it must be in a DIFFERENT port.
    - Mount the TinyScreen+ firmly on the BACK of the bag (tape / strap).

  Sounds (all non-blocking: the buzzer never stops the punch detection):
    Menu song  plays on the start screen until the round begins
    CUE        each start hit, and each 3-2-1 countdown tick
    BELL       "GO!" (round start) and time up (round end)
    WARN       10 seconds left
    HIT        every punch that is counted
    MISS       error screens (no sensor, bag moving, ...)
    Sounds interrupt the song; the song carries on afterwards. The buzzer
    is silent during settle + calibration so it cannot disturb them.

  Libraries (Arduino Library Manager / TinyCircuits GitHub):
    - TinyScreen
    - TinyCircuits BMA250   (BMA250.h / BMA250.cpp)
    - FlashStorage          (by Arduino / cmaglie)  - saves the high score
    - Wireling              (always: it powers the buzzer port)

  Board: "TinyScreen+" (TinyCircuits SAMD Boards package)

  How it works:
    1. Hit the bag 5 times (or press a button) to start.
    2. Bag settles -> 2 s calibration -> 3-2-1 countdown -> 60 s round.
    3. Every punch flashes a fist, coloured by strength:
         green = JAB, orange = SOLID, red = POWER!
       Punches less than COMBO_GAP_MS apart build a COMBO counter.
    4. Results pages rotate every 4 s:
         Page 1 Summary   - punches, avg / max strength, best ever
         Page 2 Graph     - punches per 5 s, bars coloured by strength
         Page 3 Analysis  - fatigue (first 20 s vs last 20 s),
                            best combo, % of time the bag was moving
    5. High score (most punches, hardest hit, longest combo) is kept in
       flash and survives power-off. "NEW RECORD!" when beaten.

  Hub mode (BLE, protocol/PROTOCOL.md):
    The board advertises as P1D-BAG. When the Pi hub connects and sends a
    command, the bag switches to hub mode: the hub runs the game (M,WAIT
    shows "tap your card, then hit the bag 5x"), the screen shows the called combo, the hub's S,* sounds play, and every
    hit is sent as I,<millis at the peak>,<strength 0-100>. If the hub
    disconnects, the bag goes back to the standalone game below.
    BLE code: ble.cpp / ble.h (TinyCircuits STBLE UARTPassThrough example,
    adapted by Yue Ming for the wrists and by Haziq for the bag).

  Buttons:
    Start screen : any button = start
                   hold LOWER-LEFT 3 s = clear the high score
    Results      : UPPER-RIGHT = next page
                   LOWER-LEFT  = resend punch log over USB serial
                   other       = new round
                   (or hit the bag 5 times)

  Detection method:
    - Accel is read at ~100 Hz in g.
    - A slow moving average per axis tracks gravity + bag tilt.
    - "dyn" = magnitude of (accel - average) = how hard the bag is
      being shaken right now.
    - A punch = dyn rising above a threshold (auto-set from the
      calibration noise, minimum PUNCH_MIN_G). The highest dyn during
      that spike is stored as the punch strength. Hysteresis + a
      refractory period stop one punch from being counted twice.
*/

#include <Wire.h>
#include <SPI.h>
#include <TinyScreen.h>
#include <FlashStorage.h>
#include <Wireling.h>
#include "BMA250.h"
#include "ble.h"

// ------------------------- user settings ---------------------------
#define USE_WIRELING        0      // 1 = Accelerometer Wireling via adapter
#define WIRELING_PORT       0      // adapter port the accelerometer Wireling is in (only if USE_WIRELING 1)

// ---- sound settings ----
#define SOUND_ENABLED       1      // 0 = everything silent
#define BUZZER_PORT         0      // 0-3: Wireling Adapter port the Buzzer Wireling is in
#define MUSIC_STEP_MS       143    // length of one 16th note of the menu song; 143 = about 105 BPM, smaller = faster
#define MENU_MUSIC_ON_IDLE  1      // song on the start screen
#define MENU_MUSIC_ON_RESULTS 0    // 1 = song also plays on the results screens
#define HIT_SOUND_ON        1      // 0 = no beep for each counted punch
#define WARN_BEFORE_END_MS  10000  // WARN sound when this much time is left

#if USE_WIRELING && (WIRELING_PORT == BUZZER_PORT)
  #error "The accelerometer Wireling and the buzzer are set to the same adapter port"
#endif

#if defined(ARDUINO_ARCH_SAMD)
  #define SerialMonitorInterface SerialUSB
#else
  #define SerialMonitorInterface Serial
#endif

const uint32_t SESSION_MS         = 60000;  // recording length (1 minute)
const uint32_t COUNTDOWN_MS       = 3000;
const uint32_t CAL_MS             = 2000;   // still-bag calibration time
const uint32_t SAMPLE_INTERVAL_MS = 10;     // ~100 Hz
const uint32_t UI_INTERVAL_MS     = 100;    // screen refresh while recording

// Accelerometer range. 8 g gives headroom for hard punches.
// counts per g for the 10-bit BMA250: 2g=256, 4g=128, 8g=64, 16g=32
#define ACCEL_RANGE   BMA250_range_8g
const float COUNTS_PER_G = 64.0;

// Punch detection tuning  (raise PUNCH_MIN_G if bag swing gives false hits,
// lower it if light jabs are missed)
const float    PUNCH_MIN_G     = 1.00;  // minimum spike counted as a punch
const float    NOISE_SIGMA     = 6.0;   // threshold = max(PUNCH_MIN_G, 6 * noise)
const float    RELEASE_RATIO   = 0.50;  // must fall below 50% of threshold to re-arm
const uint32_t PEAK_MAX_MS     = 150;   // longest a single impact spike can last
const uint32_t REFRACTORY_MS   = 250;   // min gap between two punches
const float    ACTIVE_MIN_G    = 0.08;  // above this the bag counts as "moving"
const float    CAL_MAX_NOISE_G = 0.15;  // calibration rejected if bag moves more
const float    BASELINE_ALPHA  = 0.02;  // slow average (gravity / tilt tracking)
const float    VIB_ALPHA       = 0.10;  // smoothing for the live movement bar
const float    VIB_FULLSCALE_G = 1.50;  // movement bar is full at this level

// Punch rating, as multiples of the detection threshold.
// e.g. threshold 0.5 g -> SOLID from 1.0 g, POWER from 1.75 g
const float    SOLID_RATIO     = 4.0;
const float    POWER_RATIO     = 6.5;
const uint32_t COMBO_GAP_MS    = 600;   // punches closer than this = combo

// Hub mode
const float    HUB_FULL_SCALE_G = 8.0;    // impact strength 100 = this many g (the accelerometer range)
const uint32_t HUB_FLASH_MS     = 200;    // hit counter flashes this long

// Results
const uint32_t RESULT_PAGE_MS    = 4000;   // auto-rotate results pages
const uint8_t  NUM_RESULT_PAGES  = 3;
const uint32_t FATIGUE_WINDOW_MS = 20000;  // compare first vs last 20 s
const float    FATIGUE_WARN_PCT  = 15.0;   // drop bigger than this shows red
const uint8_t  GRAPH_BINS        = 12;     // 60 s / 12 = 5 s per bar

// Hit-to-start: hit the bag START_HITS times to start / restart a round
const uint8_t  START_HITS         = 5;
const uint32_t START_HIT_GAP_MS   = 3000;  // progress resets if you pause longer
const uint32_t RESULTS_LOCKOUT_MS = 3000;  // ignore hits right after a round ends
const float    SETTLE_G           = 0.10;  // bag counts as "settled" below this
const uint32_t SETTLE_MS          = 1000;  // must stay settled this long
const uint32_t SETTLE_MAX_MS      = 8000;  // give up waiting and calibrate anyway
const uint32_t HS_RESET_HOLD_MS   = 3000;  // hold lower-left this long to clear record

const uint32_t FIST_FLASH_MS   = 350;   // how long the fist image is shown
const uint16_t MAX_PUNCHES     = 300;   // punch records kept in RAM

// ------------------------- colours (8-bit BBBGGGRR) ----------------
const uint8_t C_BLACK   = 0x00;
const uint8_t C_WHITE   = 0xFF;
const uint8_t C_RED     = 0x03;
const uint8_t C_ORANGE  = 0x0F;
const uint8_t C_YELLOW  = 0x1F;
const uint8_t C_GREEN   = 0x1C;
const uint8_t C_DKGREEN = 0x0C;
const uint8_t C_CYAN    = 0xFC;
const uint8_t C_GRAY    = 0x6D;
const uint8_t C_SKIN    = 0x57;   // peach
const uint8_t C_OUTLINE = 0x05;   // dark brown

// ------------------------- fist bitmap (32 x 32) -------------------
// '.' background, '#' skin, '+' crease / outline. Edges get outlined
// automatically. Only the used area (cols 3-28, rows 2-30) is drawn,
// at 2x scale, on the right of the screen; text goes on the left.
const uint8_t FIST_W = 32, FIST_H = 32, FIST_SCALE = 2;
const uint8_t FIST_C0 = 3, FIST_CW = 26;   // columns drawn
const uint8_t FIST_R0 = 2, FIST_RH = 29;   // rows drawn
const uint8_t FIST_X = 96 - FIST_CW * FIST_SCALE - 2;     // 42
const uint8_t FIST_Y = (64 - FIST_RH * FIST_SCALE) / 2;   // 3
const char FIST[FIST_H][FIST_W + 1] = {
  "................................",
  "................................",
  ".....####..####..####..####.....",
  "....######+#####+#####+#####....",
  "....######+#####+#####+#####....",
  "....######+#####+#####+#####....",
  "....######+#####+#####+#####....",
  "....######+#####+#####+#####....",
  "....######+#####+#####+#####....",
  "....++++++++++++++++++++++++....",
  "....######+#####+#####+#####....",
  "....######+#####+#####+#####....",
  "....######+#####+#####+#####....",
  "....######+#####+#####+#####....",
  "...++++++++++++++++++++#####....",
  "...###################+#####....",
  "...###################+#####....",
  "...###################+#####....",
  "...###################+#####....",
  "...++++++++++++++++++++#####....",
  "....########################....",
  "....########################....",
  "....########################....",
  "....########################....",
  ".....######################.....",
  "......####################......",
  "........################........",
  "........################........",
  "........################........",
  "........################........",
  "........################........",
  "................................"
};

// ------------------------- high score (flash) ----------------------
const uint32_t HS_MAGIC = 0x50554E43;   // "PUNC" - marks valid saved data
struct HighScore {
  uint32_t magic;
  uint16_t bestPunches;
  uint16_t bestPeakMilliG;
  uint16_t bestCombo;
};
FlashStorage(hsStore, HighScore);
HighScore hs;

// ------------------------- objects & state -------------------------
TinyScreen display = TinyScreen(TinyScreenPlus);
BMA250 accel;

enum State { ST_IDLE, ST_SETTLE, ST_CALIBRATE, ST_COUNTDOWN, ST_RECORD, ST_RESULTS, ST_HUB };
State state = ST_IDLE;

struct PunchRecord {
  uint32_t tMs;        // time since round start
  uint16_t peakMilliG; // strength of the hit (milli-g)
};
PunchRecord punches[MAX_PUNCHES];
uint16_t punchCount = 0;           // total detected (may exceed MAX_PUNCHES)

// filter / detector state
float baseX, baseY, baseZ;         // slow average = gravity + tilt
float vibLevel = 0;                // smoothed movement level (g)
float noiseG = 0, threshold = PUNCH_MIN_G, activeLevel = ACTIVE_MIN_G;
bool  inPeak = false, armed = true;
float peakG = 0;
uint32_t peakStartMs = 0, lastPunchEndMs = 0;
uint32_t peakAtMs = 0;             // when the strongest sample of the current hit was read

// session statistics
uint32_t sessionStartMs = 0;
uint32_t sampleCount = 0, activeSamples = 0;
float    sumDyn = 0, maxDyn = 0, sumPeak = 0, maxPeak = 0;
uint16_t comboLen = 0, maxCombo = 0;
uint32_t lastPunchStartMs = 0;
uint16_t ratingCount[3] = {0, 0, 0};   // JAB / SOLID / POWER

// computed results
float    resAvgPeak = 0, resFirstAvg = 0, resLastAvg = 0;
bool     resFatigueValid = false;
int      resMovingPct = 0;
uint8_t  binCount[GRAPH_BINS];
float    binSum[GRAPH_BINS];
bool     newCountRecord = false, newPowerRecord = false, newComboRecord = false;
uint16_t prevBestPunches = 0;
uint8_t  resultsPage = 0;
uint32_t lastPageMs = 0;

// calibration (Welford mean / variance per axis)
uint32_t calN = 0;
float calMeanX, calMeanY, calMeanZ, calM2X, calM2Y, calM2Z;

// timing / UI
uint32_t stateStartMs = 0, lastSampleMs = 0, lastUiMs = 0, fistUntilMs = 0;
bool fistShowing = false, needRedraw = true;
int lastCountdownShown = -1;
uint8_t lastButtons = 0;
uint32_t llDownSinceMs = 0;
bool llHeld = false;

// hit-to-start
uint8_t  startHits = 0;
uint32_t lastStartHitMs = 0;
uint32_t settleOkSinceMs = 0;
bool     settleOk = false;
uint32_t resultsShownMs = 0;
bool     resultsWasLocked = true;

// ------------------------- sound: data -----------------------------
// Types stay above every function (the Arduino IDE builds prototypes at the first function).
struct Note      { uint16_t freq; uint16_t ms; };      // game sound note; freq 0 = silence
struct MusicNote { uint16_t freq; uint8_t steps; };    // menu song note; steps = 16th notes, freq 0 = rest
struct SfxDef    { const Note *notes; uint8_t count; uint8_t prio; };

uint8_t buzzerPin() {
  switch (BUZZER_PORT) {
    case 1:  return A1;
    case 2:  return A2;
    case 3:  return A3;
    default: return A0;
  }
}

// Game sounds. The buzzer is loudest near 2700 Hz.
const Note P_BELL[] = { {2700, 250}, {0, 70}, {2700, 250}, {0, 70}, {2700, 250} };
const Note P_WARN[] = { {2700, 90}, {0, 70}, {2700, 90}, {0, 70}, {2700, 90} };
const Note P_CUE[]  = { {2700, 60} };
const Note P_HIT[]  = { {2200, 60}, {0, 20}, {2700, 90} };
const Note P_MISS[] = { {1400, 140}, {0, 20}, {900, 220} };

enum SfxId { SFX_BELL, SFX_WARN, SFX_CUE, SFX_HIT, SFX_MISS };

// A new sound replaces the playing one only if its priority is the same or higher,
// so the round bell is never cut short by a punch beep.
const SfxDef SFX[] = {
  { P_BELL, sizeof(P_BELL) / sizeof(Note), 3 },
  { P_WARN, sizeof(P_WARN) / sizeof(Note), 2 },
  { P_CUE,  sizeof(P_CUE)  / sizeof(Note), 1 },
  { P_HIT,  sizeof(P_HIT)  / sizeof(Note), 1 },
  { P_MISS, sizeof(P_MISS) / sizeof(Note), 2 },
};

// Menu song (piano version, 10 bars, 160 steps). Pitches sit high (about 1000-2000 Hz)
// because this buzzer is far louder near its 2700 Hz resonance than at low notes.
#define N_B5      988
#define N_C6     1047
#define N_CS6    1109
#define N_D6     1175
#define N_DS6    1245
#define N_E6     1319
#define N_F6     1397
#define N_G6     1568
#define N_A6     1760
#define N_B6     1976

// Every bar is 16 steps (a step is a 16th note). 10 bars = 160 steps.
const MusicNote MUSIC[] = {
  // Bar 1
  {N_C6, 2}, {N_C6, 1}, {N_C6, 1}, {N_C6, 2}, {N_C6, 1}, {N_C6, 1}, {N_E6, 2}, {N_E6, 1}, {N_E6, 1}, {N_C6, 1}, {N_E6, 1}, {N_E6, 2},
  // Bar 2
  {N_E6, 2}, {N_E6, 1}, {N_E6, 1}, {N_E6, 2}, {N_E6, 1}, {N_E6, 1}, {N_G6, 2}, {N_G6, 1}, {N_E6, 1}, {N_E6, 4},
  // Bar 3
  {N_E6, 2}, {N_E6, 1}, {N_E6, 1}, {N_E6, 1}, {N_E6, 1}, {N_E6, 1}, {N_E6, 2}, {N_E6, 1}, {N_E6, 1}, {N_E6, 1}, {N_F6, 1}, {N_F6, 1}, {N_B5, 2},
  // Bar 4
  {N_E6, 1}, {N_D6, 1}, {N_E6, 1}, {N_E6, 1}, {N_E6, 1}, {N_E6, 1}, {N_E6, 1}, {N_E6, 2}, {N_E6, 1}, {N_E6, 1}, {N_E6, 1}, {N_E6, 1}, {N_G6, 1}, {N_G6, 2},
  // Bar 5
  {N_A6, 1}, {N_F6, 1}, {N_A6, 1}, {N_C6, 1}, {N_D6, 1}, {N_D6, 2}, {N_C6, 2}, {N_CS6, 1}, {N_CS6, 1}, {N_CS6, 1}, {N_CS6, 1}, {N_B6, 1}, {N_D6, 1}, {N_D6, 1},
  // Bar 6
  {N_E6, 1}, {N_E6, 1}, {N_E6, 1}, {N_E6, 1}, {N_E6, 1}, {N_E6, 2}, {N_D6, 1}, {N_E6, 1}, {N_DS6, 1}, {N_DS6, 1}, {N_DS6, 1}, {N_DS6, 1}, {N_G6, 1}, {N_G6, 1}, {N_E6, 1},
  // Bar 7
  {N_E6, 1}, {N_F6, 1}, {N_A6, 1}, {N_A6, 1}, {N_D6, 1}, {N_D6, 2}, {N_C6, 2}, {N_CS6, 1}, {N_CS6, 1}, {N_CS6, 1}, {N_D6, 1}, {N_B6, 1}, {N_D6, 1}, {N_D6, 1},
  // Bar 8
  {N_E6, 1}, {N_E6, 1}, {N_E6, 2}, {N_E6, 1}, {N_E6, 1}, {N_E6, 1}, {N_D6, 1}, {N_E6, 1}, {N_DS6, 1}, {N_DS6, 1}, {N_DS6, 1}, {N_E6, 1}, {N_E6, 1}, {N_E6, 1}, {N_E6, 1},
  // Bar 9
  {N_D6, 1}, {N_D6, 1}, {N_D6, 1}, {N_C6, 1}, {N_D6, 2}, {N_D6, 1}, {N_D6, 1}, {N_E6, 1}, {N_E6, 1}, {N_E6, 1}, {N_B5, 1}, {N_G6, 1}, {N_E6, 1}, {N_E6, 1}, {N_E6, 1},
  // Bar 10
  {N_F6, 2}, {N_C6, 1}, {N_C6, 1}, {N_C6, 1}, {N_B5, 1}, {N_C6, 1}, {N_F6, 1}, {N_C6, 1}, {N_C6, 1}, {N_C6, 1}, {N_G6, 1}, {N_F6, 1}, {N_F6, 2}, {N_E6, 1},
};
const uint16_t MUSIC_LEN = sizeof(MUSIC) / sizeof(MusicNote);
const uint16_t STEP_MS   = MUSIC_STEP_MS;

// ------------------------- sound: player (non-blocking) ------------
// One voice. A game sound ALWAYS wins over the song; when the sound ends the
// song carries on from the note it was interrupted on.
bool     musicOn = false;       // the song is supposed to be playing
bool     soundOn = false;       // a song note is sounding right now
uint16_t musicIdx = 0;
uint32_t noteEndMs = 0;         // when the current song note's slot ends (next note starts here)
uint32_t soundEndMs = 0;        // when the sound inside the slot ends (small gap between repeated notes)

const SfxDef *sfxCur = nullptr; // game sound being played, nullptr = none
uint8_t  sfxIdx = 0;
uint32_t sfxNoteEndMs = 0;

void startNote(uint32_t at) {
  const MusicNote &n = MUSIC[musicIdx];
  uint32_t len = (uint32_t)n.steps * STEP_MS;
  if (n.freq) {
    tone(buzzerPin(), n.freq);
    soundOn = true;
    soundEndMs = at + len - len / 6;
  } else {
    noTone(buzzerPin());
    soundOn = false;
  }
  noteEndMs = at + len;
}

void musicStart() {
  if (!SOUND_ENABLED) return;
  musicIdx = 0;
  musicOn = true;
  if (!sfxCur) startNote(millis());   // else it starts when the game sound ends
}

void musicStop() {
  musicOn = false;
  soundOn = false;
  if (!sfxCur) noTone(buzzerPin());   // never cut a game sound short
}

// Every note is scheduled from the previous note's planned end, not from "now",
// so the song does not drift and the last note flows straight into the first.
void musicStep() {
  if (!musicOn) return;
  uint32_t now = millis();
  if (soundOn && (int32_t)(now - soundEndMs) >= 0) {
    noTone(buzzerPin());
    soundOn = false;
  }
  if ((int32_t)(now - noteEndMs) >= 0) {
    uint32_t at = noteEndMs;
    if ((int32_t)(now - at) > (int32_t)(4 * STEP_MS)) at = now;   // far behind: resync instead of rushing
    musicIdx = (musicIdx + 1) % MUSIC_LEN;
    startNote(at);
  }
}

void sfxStartNote(uint32_t at) {
  const Note &n = sfxCur->notes[sfxIdx];
  if (n.freq) tone(buzzerPin(), n.freq); else noTone(buzzerPin());
  sfxNoteEndMs = at + n.ms;
}

void playSfx(uint8_t id) {
  if (!SOUND_ENABLED) return;
  if (id == SFX_HIT && !HIT_SOUND_ON) return;
  const SfxDef *d = &SFX[id];
  if (sfxCur && d->prio < sfxCur->prio) return;   // a more important sound is playing
  sfxCur = d;
  sfxIdx = 0;
  soundOn = false;                                // the song is paused, not forgotten
  sfxStartNote(millis());
}

// Call this on every pass of loop() and inside every wait.
void audioStep() {
  if (sfxCur) {
    uint32_t now = millis();
    if ((int32_t)(now - sfxNoteEndMs) >= 0) {
      uint32_t at = sfxNoteEndMs;
      if (++sfxIdx >= sfxCur->count) {
        sfxCur = nullptr;
        noTone(buzzerPin());
        if (musicOn) startNote(now);              // song carries on
      } else {
        sfxStartNote(at);
      }
    }
    return;
  }
  musicStep();
}

// delay() that keeps the buzzer running
void waitMs(uint32_t ms) {
  uint32_t t0 = millis();
  while (millis() - t0 < ms) { audioStep(); ble_poll(); }
}

bool warnPlayed = false;        // WARN already sounded this round

// ------------------------- helpers ---------------------------------
bool readAccel(float &x, float &y, float &z) {
#if USE_WIRELING
  Wireling.selectPort(WIRELING_PORT);
#endif
  accel.read();
  if (accel.X == -1 && accel.Y == -1 && accel.Z == -1) return false; // not connected
  x = accel.X / COUNTS_PER_G;
  y = accel.Y / COUNTS_PER_G;
  z = accel.Z / COUNTS_PER_G;
  return true;
}

void printCentered(const char *s, int y, const FONT_INFO &font, uint8_t fg, uint8_t bg) {
  display.setFont(font);
  display.fontColor(fg, bg);
  int w = display.getPrintWidth((char *)s);
  display.setCursor((96 - w) / 2, y);
  display.print(s);
}

void printAt(const char *s, int x, int y, const FONT_INFO &font, uint8_t fg, uint8_t bg) {
  display.setFont(font);
  display.fontColor(fg, bg);
  display.setCursor(x, y);
  display.print(s);
}

// value in g -> "1.8g" without needing printf float support
void fmtG(char *buf, size_t n, float g) {
  int tenths = (int)(g * 10.0f + 0.5f);
  snprintf(buf, n, "%d.%dg", tenths / 10, tenths % 10);
}

// 0 = JAB, 1 = SOLID, 2 = POWER
uint8_t ratePunch(float g) {
  if (g >= threshold * POWER_RATIO) return 2;
  if (g >= threshold * SOLID_RATIO) return 1;
  return 0;
}

const char *ratingName(uint8_t r) {
  return r == 2 ? "POWER!" : (r == 1 ? "SOLID" : "JAB");
}

uint8_t ratingColor(uint8_t r) {
  return r == 2 ? C_RED : (r == 1 ? C_ORANGE : C_DKGREEN);
}

// ------------------------- high score ------------------------------
void loadHighScore() {
  hs = hsStore.read();
  if (hs.magic != HS_MAGIC) {          // first boot / fresh upload
    hs.magic = HS_MAGIC;
    hs.bestPunches = 0;
    hs.bestPeakMilliG = 0;
    hs.bestCombo = 0;
  }
}

void clearHighScore() {
  hs.magic = HS_MAGIC;
  hs.bestPunches = 0;
  hs.bestPeakMilliG = 0;
  hs.bestCombo = 0;
  hsStore.write(hs);
  SerialMonitorInterface.println(F("High score cleared"));
}

// compares this round with the record; writes flash only if something improved
void updateHighScore() {
  prevBestPunches = hs.bestPunches;
  uint16_t peakMg = (uint16_t)min(maxPeak * 1000.0f, 65535.0f);

  newCountRecord = punchCount > 0 && punchCount > hs.bestPunches;
  newPowerRecord = peakMg > hs.bestPeakMilliG && punchCount > 0;
  newComboRecord = maxCombo >= 2 && maxCombo > hs.bestCombo;

  if (newCountRecord) hs.bestPunches = punchCount;
  if (newPowerRecord) hs.bestPeakMilliG = peakMg;
  if (newComboRecord) hs.bestCombo = maxCombo;
  if (newCountRecord || newPowerRecord || newComboRecord) hsStore.write(hs);
}

// ------------------------- fist image ------------------------------
bool fistFilled(int r, int c) {
  if (r < 0 || r >= FIST_H || c < 0 || c >= FIST_W) return false;
  return FIST[r][c] != '.';
}

uint8_t fistColor(int r, int c, uint8_t bg) {
  char ch = FIST[r][c];
  if (ch == '.') return bg;
  if (ch == '+') return C_OUTLINE;
  // auto outline: skin pixel touching background
  if (!fistFilled(r - 1, c) || !fistFilled(r + 1, c) ||
      !fistFilled(r, c - 1) || !fistFilled(r, c + 1)) return C_OUTLINE;
  return C_SKIN;
}

// Fist on the right, strength label / combo / punch number on the left.
void drawFist(uint16_t punchNumber, float strengthG, uint16_t combo) {
  uint8_t rating = ratePunch(strengthG);
  uint8_t bg = ratingColor(rating);
  const uint8_t w = FIST_CW * FIST_SCALE, h = FIST_RH * FIST_SCALE;

  display.drawRect(0, 0, 96, 64, TSRectangleFilled, bg);

  // stream the scaled bitmap straight into the OLED window
  uint8_t line[FIST_CW * FIST_SCALE];
  display.setX(FIST_X, FIST_X + w - 1);
  display.setY(FIST_Y, FIST_Y + h - 1);
  display.startData();
  for (int r = FIST_R0; r < FIST_R0 + FIST_RH; r++) {
    for (int c = 0; c < FIST_CW; c++) {
      uint8_t col = fistColor(r, FIST_C0 + c, bg);
      for (int s = 0; s < FIST_SCALE; s++) line[c * FIST_SCALE + s] = col;
    }
    for (int s = 0; s < FIST_SCALE; s++) display.writeBuffer(line, sizeof(line));
  }
  display.endTransfer();
  display.setX(0, 95);   // restore full-screen window
  display.setY(0, 63);

  char buf[10];
  printAt(ratingName(rating), 2, 3, liberationSans_8ptFontInfo, C_WHITE, bg);

  if (combo >= 2) {
    printAt("COMBO", 2, 20, liberationSans_8ptFontInfo, C_YELLOW, bg);
    snprintf(buf, sizeof(buf), "x%u", combo);
    printAt(buf, 4, 31, liberationSans_16ptFontInfo, C_YELLOW, bg);
  } else {
    for (int i = 0; i < 3; i++) {          // speed lines
      int y = 24 + i * 9;
      display.drawLine(6, y, 36, y, C_WHITE);
    }
  }

  snprintf(buf, sizeof(buf), "#%u", punchNumber);
  printAt(buf, 2, 53, liberationSans_8ptFontInfo, C_WHITE, bg);
}

// ------------------------- screens ---------------------------------
// row of START_HITS boxes; filled = hits landed so far
void drawStartProgress(int x0, int y, int size, bool locked) {
  for (uint8_t i = 0; i < START_HITS; i++) {
    int x = x0 + i * (size + 4);
    display.drawRect(x, y, size, size, TSRectangleFilled, C_BLACK);
    if (locked)              display.drawRect(x, y, size, size, TSRectangleNoFill, C_GRAY);
    else if (i < startHits)  display.drawRect(x, y, size, size, TSRectangleFilled, C_YELLOW);
    else                     display.drawRect(x, y, size, size, TSRectangleNoFill, C_WHITE);
  }
}

void showIdle() {
  char buf[24];
  display.clearScreen();
  printCentered("PUNCH TRACKER", 2, liberationSans_8ptFontInfo, C_YELLOW, C_BLACK);
  printCentered("Hit the bag 5x", 16, liberationSans_8ptFontInfo, C_WHITE, C_BLACK);
  printCentered("to start", 27, liberationSans_8ptFontInfo, C_WHITE, C_BLACK);
  drawStartProgress(15, 41, 10, false);
  if (hs.bestPunches > 0) {
    snprintf(buf, sizeof(buf), "Record: %u hits", hs.bestPunches);
    printCentered(buf, 54, liberationSans_8ptFontInfo, C_GRAY, C_BLACK);
  }
}

void showSettle() {
  display.clearScreen();
  printCentered("GET READY!", 14, liberationSans_8ptFontInfo, C_GREEN, C_BLACK);
  printCentered("let the bag settle", 32, liberationSans_8ptFontInfo, C_GRAY, C_BLACK);
}

void showError(const char *l1, const char *l2) {
  display.clearScreen();
  printCentered(l1, 18, liberationSans_8ptFontInfo, C_RED, C_BLACK);
  printCentered(l2, 34, liberationSans_8ptFontInfo, C_WHITE, C_BLACK);
}

void drawRecordStatic() {
  display.clearScreen();
  printAt("TIME", 4, 0, liberationSans_8ptFontInfo, C_GRAY, C_BLACK);
  printAt("HITS", 60, 0, liberationSans_8ptFontInfo, C_GRAY, C_BLACK);
  printAt("BAG", 2, 44, liberationSans_8ptFontInfo, C_GRAY, C_BLACK);
  display.drawRect(24, 44, 70, 10, TSRectangleNoFill, C_WHITE);
}

void drawRecordDynamic(uint32_t now) {
  uint32_t elapsed = now - sessionStartMs;
  uint32_t left = (elapsed >= SESSION_MS) ? 0 : (SESSION_MS - elapsed + 999) / 1000;
  char buf[12];

  // time left
  display.drawRect(0, 12, 48, 22, TSRectangleFilled, C_BLACK);
  snprintf(buf, sizeof(buf), "%lus", (unsigned long)left);
  printAt(buf, 4, 13, liberationSans_16ptFontInfo, left <= 10 ? C_RED : C_WHITE, C_BLACK);

  // hit count
  display.drawRect(48, 12, 48, 22, TSRectangleFilled, C_BLACK);
  snprintf(buf, sizeof(buf), "%u", punchCount);
  printAt(buf, 60, 13, liberationSans_16ptFontInfo, C_YELLOW, C_BLACK);

  // live movement bar
  float frac = vibLevel / VIB_FULLSCALE_G;
  if (frac > 1) frac = 1;
  if (frac < 0) frac = 0;
  int fill = (int)(frac * 68);
  uint8_t col = frac < 0.4f ? C_GREEN : (frac < 0.75f ? C_YELLOW : C_RED);
  if (fill > 0) display.drawRect(25, 45, fill, 8, TSRectangleFilled, col);
  if (fill < 68) display.drawRect(25 + fill, 45, 68 - fill, 8, TSRectangleFilled, C_BLACK);

  // strongest hit so far
  display.drawRect(0, 55, 96, 9, TSRectangleFilled, C_BLACK);
  char g[10];
  fmtG(g, sizeof(g), maxPeak);
  snprintf(buf, sizeof(buf), "Max %s", g);
  printAt(buf, 24, 55, liberationSans_8ptFontInfo, C_GRAY, C_BLACK);
}

// ------------------------- results ---------------------------------
void computeResults() {
  uint16_t stored = punchCount < MAX_PUNCHES ? punchCount : MAX_PUNCHES;
  resAvgPeak = punchCount ? sumPeak / punchCount : 0;
  resMovingPct = sampleCount ? (int)(100UL * activeSamples / sampleCount) : 0;

  for (uint8_t i = 0; i < GRAPH_BINS; i++) { binCount[i] = 0; binSum[i] = 0; }

  float firstSum = 0, lastSum = 0;
  uint16_t firstN = 0, lastN = 0;
  const uint32_t binMs = SESSION_MS / GRAPH_BINS;

  for (uint16_t i = 0; i < stored; i++) {
    uint32_t t = punches[i].tMs;
    float g = punches[i].peakMilliG / 1000.0f;

    uint8_t b = t / binMs;
    if (b >= GRAPH_BINS) b = GRAPH_BINS - 1;
    if (binCount[b] < 255) binCount[b]++;
    binSum[b] += g;

    if (t < FATIGUE_WINDOW_MS)              { firstSum += g; firstN++; }
    if (t >= SESSION_MS - FATIGUE_WINDOW_MS) { lastSum += g; lastN++; }
  }

  resFatigueValid = firstN >= 2 && lastN >= 2;
  resFirstAvg = firstN ? firstSum / firstN : 0;
  resLastAvg  = lastN ? lastSum / lastN : 0;
}

// change in average punch strength, last 20 s vs first 20 s (percent)
int fatiguePct() {
  if (!resFatigueValid || resFirstAvg <= 0) return 0;
  float p = (resLastAvg - resFirstAvg) / resFirstAvg * 100.0f;
  return (int)(p < 0 ? p - 0.5f : p + 0.5f);
}

// "Hit 5x" + progress boxes, shown at the bottom of every results page
void drawResultsFooter(bool locked) {
  display.drawRect(0, 53, 96, 11, TSRectangleFilled, C_BLACK);
  printAt("Hit 5x", 2, 54, liberationSans_8ptFontInfo, C_GRAY, C_BLACK);
  drawStartProgress(36, 54, 8, locked);
}

void drawSummaryPage() {
  char buf[24], g1[10], g2[10];
  display.clearScreen();

  if (newCountRecord) printCentered("NEW RECORD!", 0, liberationSans_8ptFontInfo, C_RED, C_BLACK);
  else                printCentered("TIME'S UP!", 0, liberationSans_8ptFontInfo, C_YELLOW, C_BLACK);

  if (punchCount < 100) snprintf(buf, sizeof(buf), "%u punches", punchCount);
  else                  snprintf(buf, sizeof(buf), "%u hits", punchCount);
  printCentered(buf, 10, liberationSans_16ptFontInfo, C_WHITE, C_BLACK);

  fmtG(g1, sizeof(g1), resAvgPeak);
  fmtG(g2, sizeof(g2), maxPeak);
  snprintf(buf, sizeof(buf), "Avg %s Max %s", g1, g2);
  printCentered(buf, 30, liberationSans_8ptFontInfo, newPowerRecord ? C_RED : C_CYAN, C_BLACK);

  if (newCountRecord && prevBestPunches > 0)
    snprintf(buf, sizeof(buf), "Prev best: %u", prevBestPunches);
  else
    snprintf(buf, sizeof(buf), "Best ever: %u", hs.bestPunches);
  printCentered(buf, 41, liberationSans_8ptFontInfo, C_GRAY, C_BLACK);
}

void drawGraphPage() {
  display.clearScreen();
  printCentered("PUNCHES / 5s", 0, liberationSans_8ptFontInfo, C_CYAN, C_BLACK);

  const int top = 12, bottom = 51, maxH = bottom - top;
  uint8_t maxBin = 0;
  for (uint8_t i = 0; i < GRAPH_BINS; i++) if (binCount[i] > maxBin) maxBin = binCount[i];

  display.drawLine(0, bottom, 95, bottom, C_GRAY);
  if (maxBin == 0) {
    printCentered("no punches", 26, liberationSans_8ptFontInfo, C_GRAY, C_BLACK);
    return;
  }

  // bars: height = punches in that 5 s, colour = average strength rating
  for (uint8_t i = 0; i < GRAPH_BINS; i++) {
    if (binCount[i] == 0) continue;
    int h = (int)binCount[i] * maxH / maxBin;
    if (h < 1) h = 1;
    float avg = binSum[i] / binCount[i];
    uint8_t col = ratePunch(avg) == 2 ? C_RED : (ratePunch(avg) == 1 ? C_ORANGE : C_GREEN);
    display.drawRect(1 + i * 8, bottom - h, 7, h, TSRectangleFilled, col);
  }

  // peak bin value in the top-left corner
  char buf[8];
  snprintf(buf, sizeof(buf), "%u", maxBin);
  printAt(buf, 0, top, liberationSans_8ptFontInfo, C_GRAY, C_BLACK);
}

void drawAnalysisPage() {
  char buf[28], g1[10], g2[10];
  display.clearScreen();
  printCentered("ANALYSIS", 0, liberationSans_8ptFontInfo, C_CYAN, C_BLACK);

  // fatigue: average strength in the last 20 s vs the first 20 s
  if (resFatigueValid) {
    int p = fatiguePct();
    uint8_t col;
    if (p < 0) {
      snprintf(buf, sizeof(buf), "Power drop %d%%", -p);
      col = (-p >= FATIGUE_WARN_PCT) ? C_RED : C_YELLOW;
    } else {
      snprintf(buf, sizeof(buf), "No power drop!");
      col = C_GREEN;
    }
    printCentered(buf, 11, liberationSans_8ptFontInfo, col, C_BLACK);

    fmtG(g1, sizeof(g1), resFirstAvg);
    fmtG(g2, sizeof(g2), resLastAvg);
    snprintf(buf, sizeof(buf), "%s -> %s", g1, g2);
    printCentered(buf, 22, liberationSans_8ptFontInfo, C_GRAY, C_BLACK);
  } else {
    printCentered("Fatigue: n/a", 11, liberationSans_8ptFontInfo, C_GRAY, C_BLACK);
    printCentered("(too few punches)", 22, liberationSans_8ptFontInfo, C_GRAY, C_BLACK);
  }

  snprintf(buf, sizeof(buf), "Best combo x%u", maxCombo);
  printCentered(buf, 33, liberationSans_8ptFontInfo, newComboRecord ? C_RED : C_YELLOW, C_BLACK);

  snprintf(buf, sizeof(buf), "Bag moving %d%%", resMovingPct);
  printCentered(buf, 44, liberationSans_8ptFontInfo, C_GREEN, C_BLACK);
}

void showResultsPage(uint32_t now) {
  if (resultsPage == 0)      drawSummaryPage();
  else if (resultsPage == 1) drawGraphPage();
  else                       drawAnalysisPage();
  drawResultsFooter((now - resultsShownMs) < RESULTS_LOCKOUT_MS);
}

// ------------------------- serial log ------------------------------
void dumpLog() {
  uint16_t stored = punchCount < MAX_PUNCHES ? punchCount : MAX_PUNCHES;
  float avgDyn = sampleCount ? sumDyn / sampleCount : 0;

  SerialMonitorInterface.println(F("# ---- Punch session ----"));
  SerialMonitorInterface.print(F("# threshold_g,")); SerialMonitorInterface.println(threshold, 3);
  SerialMonitorInterface.print(F("# noise_g,"));     SerialMonitorInterface.println(noiseG, 3);
  SerialMonitorInterface.println(F("punch,time_s,peak_g,rating"));
  for (uint16_t i = 0; i < stored; i++) {
    float g = punches[i].peakMilliG / 1000.0;
    SerialMonitorInterface.print(i + 1);
    SerialMonitorInterface.print(',');
    SerialMonitorInterface.print(punches[i].tMs / 1000.0, 2);
    SerialMonitorInterface.print(',');
    SerialMonitorInterface.print(g, 2);
    SerialMonitorInterface.print(',');
    SerialMonitorInterface.println(ratingName(ratePunch(g)));
  }
  SerialMonitorInterface.print(F("# total_punches,"));   SerialMonitorInterface.println(punchCount);
  SerialMonitorInterface.print(F("# jab_solid_power,"));
  SerialMonitorInterface.print(ratingCount[0]); SerialMonitorInterface.print('/');
  SerialMonitorInterface.print(ratingCount[1]); SerialMonitorInterface.print('/');
  SerialMonitorInterface.println(ratingCount[2]);
  SerialMonitorInterface.print(F("# avg_peak_g,"));      SerialMonitorInterface.println(resAvgPeak, 2);
  SerialMonitorInterface.print(F("# max_peak_g,"));      SerialMonitorInterface.println(maxPeak, 2);
  SerialMonitorInterface.print(F("# best_combo,"));      SerialMonitorInterface.println(maxCombo);
  if (resFatigueValid) {
    SerialMonitorInterface.print(F("# first20s_avg_g,")); SerialMonitorInterface.println(resFirstAvg, 2);
    SerialMonitorInterface.print(F("# last20s_avg_g,"));  SerialMonitorInterface.println(resLastAvg, 2);
    SerialMonitorInterface.print(F("# power_change_pct,")); SerialMonitorInterface.println(fatiguePct());
  }
  SerialMonitorInterface.print(F("# per_5s,"));
  for (uint8_t i = 0; i < GRAPH_BINS; i++) {
    SerialMonitorInterface.print(binCount[i]);
    SerialMonitorInterface.print(i < GRAPH_BINS - 1 ? ' ' : '\n');
  }
  SerialMonitorInterface.print(F("# avg_movement_g,"));  SerialMonitorInterface.println(avgDyn, 3);
  SerialMonitorInterface.print(F("# max_movement_g,"));  SerialMonitorInterface.println(maxDyn, 2);
  SerialMonitorInterface.print(F("# bag_moving_pct,"));  SerialMonitorInterface.println(resMovingPct);
  SerialMonitorInterface.print(F("# record_punches,"));  SerialMonitorInterface.println(hs.bestPunches);
  SerialMonitorInterface.print(F("# record_peak_g,"));   SerialMonitorInterface.println(hs.bestPeakMilliG / 1000.0, 2);
  SerialMonitorInterface.print(F("# record_combo,"));    SerialMonitorInterface.println(hs.bestCombo);
  if (newCountRecord || newPowerRecord || newComboRecord)
    SerialMonitorInterface.println(F("# NEW RECORD!"));
  if (punchCount > MAX_PUNCHES)
    SerialMonitorInterface.println(F("# (log truncated: MAX_PUNCHES reached)"));
}

// ------------------------- state transitions -----------------------
void goIdle() {
  state = ST_IDLE;
  startHits = 0;
  inPeak = false; armed = true;
  showIdle();
#if MENU_MUSIC_ON_IDLE
  musicStart();                   // menu song until the round starts
#endif
}

// after the 5th hit: wait for the bag to stop swinging before calibrating
void startSettle(uint32_t now) {
  state = ST_SETTLE;
  stateStartMs = now;
  settleOk = false;
  inPeak = false; armed = true;
  musicStop();                    // song off for settle + calibration
  showSettle();
}

void startCalibration(uint32_t now) {
  state = ST_CALIBRATE;
  stateStartMs = lastSampleMs = now;
  calN = 0;
  calMeanX = calMeanY = calMeanZ = calM2X = calM2Y = calM2Z = 0;
  display.clearScreen();
  printCentered("Calibrating...", 18, liberationSans_8ptFontInfo, C_WHITE, C_BLACK);
  printCentered("keep bag still", 34, liberationSans_8ptFontInfo, C_GRAY, C_BLACK);
}

void calAdd(float v, float &mean, float &m2) {
  float d = v - mean;
  mean += d / calN;
  m2 += d * (v - mean);
}

void finishCalibration(uint32_t now) {
  if (calN < 10) {
    showError("Sensor error", "check accel");
    playSfx(SFX_MISS);
    waitMs(1500);
    goIdle();
    return;
  }
  noiseG = sqrt((calM2X + calM2Y + calM2Z) / (calN - 1));
  if (noiseG > CAL_MAX_NOISE_G) {          // bag was swinging: try again
    showError("Bag moving!", "wait, retrying");
    playSfx(SFX_MISS);
    waitMs(1000);                          // MISS (0.4 s) is over before calibration restarts
    startCalibration(millis());
    return;
  }
  baseX = calMeanX; baseY = calMeanY; baseZ = calMeanZ;
  threshold   = max(PUNCH_MIN_G, NOISE_SIGMA * noiseG);
  activeLevel = max(ACTIVE_MIN_G, 3.0f * noiseG);

  state = ST_COUNTDOWN;
  stateStartMs = now;
  lastCountdownShown = -1;
}

void startRecording(uint32_t now) {
  state = ST_RECORD;
  sessionStartMs = stateStartMs = lastSampleMs = lastUiMs = now;
  punchCount = 0;
  sampleCount = activeSamples = 0;
  sumDyn = maxDyn = sumPeak = maxPeak = 0;
  comboLen = maxCombo = 0;
  lastPunchStartMs = 0;
  ratingCount[0] = ratingCount[1] = ratingCount[2] = 0;
  vibLevel = 0;
  inPeak = false; armed = true;
  lastPunchEndMs = 0;
  fistShowing = false;
  needRedraw = true;
  warnPlayed = false;
}

void finishRecording() {
  inPeak = false;                 // drop a punch still in progress at 60 s
  state = ST_RESULTS;
  fistShowing = false;
  startHits = 0;
  resultsShownMs = lastPageMs = millis();
  resultsWasLocked = true;
  resultsPage = 0;

  playSfx(SFX_BELL);              // time up
#if MENU_MUSIC_ON_RESULTS
  musicStart();                   // starts after the bell has finished
#endif

  computeResults();
  updateHighScore();
  showResultsPage(resultsShownMs);
  dumpLog();
}

// ------------------------- signal processing -----------------------
// returns dynamic acceleration (g) with gravity / tilt removed
float processSample(float x, float y, float z, bool trackBaseline) {
  float dx = x - baseX, dy = y - baseY, dz = z - baseZ;
  float dyn = sqrt(dx * dx + dy * dy + dz * dz);
  if (trackBaseline) {          // freeze during a hit so the hit isn't absorbed
    baseX += BASELINE_ALPHA * dx;
    baseY += BASELINE_ALPHA * dy;
    baseZ += BASELINE_ALPHA * dz;
  }
  vibLevel += VIB_ALPHA * (dyn - vibLevel);
  return dyn;
}

void registerPunch(uint32_t now, float strengthG, uint32_t hitStartMs) {
  uint32_t t = hitStartMs - sessionStartMs;

  // combo: punch started soon after the previous one
  if (punchCount > 0 && hitStartMs - lastPunchStartMs <= COMBO_GAP_MS) comboLen++;
  else comboLen = 1;
  if (comboLen > maxCombo) maxCombo = comboLen;
  lastPunchStartMs = hitStartMs;

  if (punchCount < MAX_PUNCHES) {
    punches[punchCount].tMs = t;
    float mg = strengthG * 1000.0f;
    punches[punchCount].peakMilliG = mg > 65535 ? 65535 : (uint16_t)mg;
  }
  punchCount++;
  sumPeak += strengthG;
  if (strengthG > maxPeak) maxPeak = strengthG;

  uint8_t rating = ratePunch(strengthG);
  ratingCount[rating]++;

  SerialMonitorInterface.print(F("PUNCH "));
  SerialMonitorInterface.print(punchCount);
  SerialMonitorInterface.print(F(" t="));
  SerialMonitorInterface.print(t / 1000.0, 2);
  SerialMonitorInterface.print(F("s peak="));
  SerialMonitorInterface.print(strengthG, 2);
  SerialMonitorInterface.print(F("g "));
  SerialMonitorInterface.print(ratingName(rating));
  if (comboLen >= 2) {
    SerialMonitorInterface.print(F(" combo x"));
    SerialMonitorInterface.print(comboLen);
  }
  SerialMonitorInterface.println();

  playSfx(SFX_HIT);               // before drawFist so the beep is not delayed by the screen
  drawFist(punchCount, strengthG, comboLen);
  fistShowing = true;
  fistUntilMs = now + FIST_FLASH_MS;
}

// Returns true once per hit (when the spike ends). peakG / peakStartMs then
// hold the strength and start time of that hit. Used both for counting
// punches during a round and for the 5-hit start trigger.
bool detectHit(uint32_t now, float dyn) {
  if (!inPeak) {
    if (!armed && dyn < threshold * RELEASE_RATIO) armed = true;   // hysteresis
    if (armed && dyn >= threshold && (now - lastPunchEndMs) >= REFRACTORY_MS) {
      inPeak = true;
      armed = false;
      peakStartMs = now;
      peakAtMs = now;
      peakG = dyn;
    }
  } else {
    if (dyn > peakG) { peakG = dyn; peakAtMs = now; }
    if (dyn < threshold * RELEASE_RATIO || (now - peakStartMs) >= PEAK_MAX_MS) {
      inPeak = false;
      lastPunchEndMs = now;
      if (dyn < threshold * RELEASE_RATIO) armed = true;
      return true;
    }
  }
  return false;
}

// One hit towards the 5-hit start. Returns true when the 5th hit lands.
bool countStartHit(uint32_t now) {
  if (startHits > 0 && now - lastStartHitMs > START_HIT_GAP_MS) startHits = 0;
  startHits++;
  lastStartHitMs = now;
  playSfx(SFX_CUE);
  SerialMonitorInterface.print(F("Start hit "));
  SerialMonitorInterface.print(startHits);
  SerialMonitorInterface.print('/');
  SerialMonitorInterface.println(START_HITS);
  return startHits >= START_HITS;
}

// Progress resets if the player pauses too long between start hits.
bool startHitsTimedOut(uint32_t now) {
  if (startHits > 0 && now - lastStartHitMs > START_HIT_GAP_MS) {
    startHits = 0;
    return true;
  }
  return false;
}

// ------------------------- hub mode (BLE) --------------------------
bool     bleOk = false;              // the BLE TinyShield answered in setup()
bool     bleWasConnected = false;
char     hubCombo[BLE_MAX_MSG + 1] = "";   // e.g. "1-2-3"
char     hubMode[8] = "ROUND";       // WAIT, ROUND, REST or SUMMARY
uint16_t hubHits = 0;                // hits sent this hub session
uint32_t hubFlashUntilMs = 0;
bool     hubFlashing = false;

// Append an unsigned number as decimal digits. Returns the new end.
char *putUint(char *p, uint32_t v) {
  char d[10];
  uint8_t n = 0;
  do { d[n++] = (char)('0' + v % 10); v /= 10; } while (v);
  while (n) *p++ = d[--n];
  return p;
}

// I,<board ms at the peak>,<strength 0-100>
void sendImpact(uint32_t atMs, float g) {
  int strength = (int)(g / HUB_FULL_SCALE_G * 100.0f + 0.5f);
  if (strength < 0) strength = 0;
  if (strength > 100) strength = 100;
  char msg[BLE_MAX_MSG];
  char *p = msg;
  *p++ = 'I'; *p++ = ',';
  p = putUint(p, atMs);
  *p++ = ',';
  p = putUint(p, (uint32_t)strength);
  *p++ = '\n';
  ble_queue_line(msg, (uint8_t)(p - msg));
}

// H,<bpm>,<1|0>  -- call this from the pulse sensor code once it's added
void sendHeartRate(uint16_t bpm, bool good) {
  if (!ble_connected()) return;
  char msg[BLE_MAX_MSG];
  char *p = msg;
  *p++ = 'H'; *p++ = ',';
  p = putUint(p, bpm);
  *p++ = ',';
  *p++ = good ? '1' : '0';
  *p++ = '\n';
  ble_queue_line(msg, (uint8_t)(p - msg));
}

const char *punchName(char n) {
  switch (n) {
    case '1': return "JAB";
    case '2': return "CROSS";
    case '3': return "L HOOK";
    case '4': return "R HOOK";
    case '5': return "L UPPER";
    case '6': return "R UPPER";
  }
  return "";
}

void drawHubHits(bool flash) {
  char buf[12];
  snprintf(buf, sizeof(buf), "hits %u", hubHits);
  display.drawRect(0, 54, 96, 10, TSRectangleFilled, C_BLACK);
  printCentered(buf, 54, liberationSans_8ptFontInfo, flash ? C_YELLOW : C_GRAY, C_BLACK);
}

void showHub() {
  display.clearScreen();
  if (strcmp(hubMode, "WAIT") == 0) {
    printCentered("SOUTHPAW", 2, liberationSans_16ptFontInfo, C_YELLOW, C_BLACK);
    printCentered("tap your card,", 24, liberationSans_8ptFontInfo, C_WHITE, C_BLACK);
    printCentered("then hit the bag 5x", 35, liberationSans_8ptFontInfo, C_WHITE, C_BLACK);
    drawHubHits(false);
    return;
  }
  if (strcmp(hubMode, "REST") == 0) {
    printCentered("REST", 6, liberationSans_16ptFontInfo, C_CYAN, C_BLACK);
    printCentered("finger on the", 32, liberationSans_8ptFontInfo, C_WHITE, C_BLACK);
    printCentered("pulse sensor", 43, liberationSans_8ptFontInfo, C_WHITE, C_BLACK);
    return;
  }
  if (strcmp(hubMode, "SUMMARY") == 0) {
    printCentered("DONE!", 6, liberationSans_16ptFontInfo, C_GREEN, C_BLACK);
    printCentered("see the dashboard", 34, liberationSans_8ptFontInfo, C_WHITE, C_BLACK);
    drawHubHits(false);
    return;
  }
  // ROUND: the called combo, big, with the punch names under it
  if (hubCombo[0]) {
    printCentered(hubCombo, 2, liberationSans_16ptFontInfo, C_YELLOW, C_BLACK);
    char names[3][10] = {"", "", ""};
    uint8_t k = 0;
    for (const char *c = hubCombo; *c && k < 6; c++) {
      if (*c >= '1' && *c <= '6') {
        if (k < 3) strncpy(names[k], punchName(*c), sizeof(names[k]) - 1);
        k++;
      }
    }
    for (uint8_t i = 0; i < 3 && names[i][0]; i++)
      printCentered(names[i], 22 + i * 10, liberationSans_8ptFontInfo, C_WHITE, C_BLACK);
    if (k > 3) printCentered("...", 52, liberationSans_8ptFontInfo, C_GRAY, C_BLACK);
  } else {
    printCentered("ROUND", 6, liberationSans_16ptFontInfo, C_GREEN, C_BLACK);
    printCentered("wait for the combo", 34, liberationSans_8ptFontInfo, C_WHITE, C_BLACK);
  }
  drawHubHits(false);
}

void enterHub() {
  if (state == ST_HUB) return;
  musicStop();
  state = ST_HUB;
  hubHits = 0;
  hubCombo[0] = '\0';
  inPeak = false; armed = true;
  SerialMonitorInterface.println(F("Hub mode: the Pi is running the game"));
}

// One command line from the Pi (without '\n').
void handleHubLine(const char *line) {
  if (line[0] == 'C' && line[1] == ',') {
    enterHub();
    strncpy(hubCombo, line + 2, sizeof(hubCombo) - 1);
    hubCombo[sizeof(hubCombo) - 1] = '\0';
    strcpy(hubMode, "ROUND");
    playSfx(SFX_CUE);
    showHub();
  } else if (line[0] == 'S' && line[1] == ',') {
    const char *n = line + 2;
    if      (strcmp(n, "BELL") == 0) playSfx(SFX_BELL);
    else if (strcmp(n, "WARN") == 0) playSfx(SFX_WARN);
    else if (strcmp(n, "HIT")  == 0) playSfx(SFX_HIT);
    else if (strcmp(n, "MISS") == 0) playSfx(SFX_MISS);
  } else if (line[0] == 'M' && line[1] == ',') {
    const char *m = line + 2;
    if (strcmp(m, "WAIT") == 0 || strcmp(m, "ROUND") == 0 || strcmp(m, "REST") == 0 || strcmp(m, "SUMMARY") == 0) {
      enterHub();
      strncpy(hubMode, m, sizeof(hubMode) - 1);
      hubMode[sizeof(hubMode) - 1] = '\0';
      if (strcmp(m, "ROUND") == 0) hubCombo[0] = '\0';
      if (strcmp(m, "WAIT") == 0 || strcmp(m, "ROUND") == 0) hubHits = 0;
      showHub();
    }
  }
  SerialMonitorInterface.print(F("from hub: "));
  SerialMonitorInterface.println(line);
}

// ------------------------- setup / loop ----------------------------
void setup() {
  SerialMonitorInterface.begin(115200);
  Wire.begin();
  Wireling.begin();                   // powers the Wireling Adapter (buzzer, and accel if USE_WIRELING)
#if USE_WIRELING
  Wireling.selectPort(WIRELING_PORT);
#endif
  pinMode(buzzerPin(), OUTPUT);

  display.begin();
  display.setBrightness(10);          // 0-15
  display.setBitDepth(TSBitDepth8);

  loadHighScore();

  bleOk = ble_begin();
  if (bleOk) {
    char addr[18];
    ble_address(addr);
    SerialMonitorInterface.print(F("BLE ready as P1D-BAG, address "));
    SerialMonitorInterface.println(addr);
  } else {
    SerialMonitorInterface.println(F("BLE TinyShield not found: standalone game only"));
  }

  while (accel.begin(ACCEL_RANGE, BMA250_update_time_8ms) != 0) {
    SerialMonitorInterface.println(F("ERROR: no BMA250 detected"));
    showError("No accelerometer", "check connection");
    playSfx(SFX_MISS);
    waitMs(1000);
  }

  // initial gravity reference so start hits can be detected straight away
  float sx = 0, sy = 0, sz = 0, x, y, z;
  int n = 0;
  for (int i = 0; i < 30; i++) {
    if (readAccel(x, y, z)) { sx += x; sy += y; sz += z; n++; }
    delay(SAMPLE_INTERVAL_MS);
  }
  if (n > 0) { baseX = sx / n; baseY = sy / n; baseZ = sz / n; }
  else       { baseX = 0; baseY = 0; baseZ = 1; }

  goIdle();
}

void loop() {
  ble_poll();                         // every pass, so T pings are answered straight away
  audioStep();                        // keeps the song / game sounds going
  uint32_t now = millis();

  // commands from the hub (C, S, M)
  char line[BLE_MAX_MSG + 1];
  while (ble_take_line(line)) handleHubLine(line);

  // hub gone: back to the standalone game
  bool bleNow = ble_connected();
  if (bleNow != bleWasConnected) {
    bleWasConnected = bleNow;
    SerialMonitorInterface.println(bleNow ? F("Hub connected") : F("Hub disconnected"));
    if (!bleNow && state == ST_HUB) goIdle();
  }

  // button edge detection
  uint8_t btn = display.getButtons();
  uint8_t pressed = btn & ~lastButtons;
  lastButtons = btn;

  // sample the accelerometer at a fixed rate in every state
  bool sampleReady = false;
  float x = 0, y = 0, z = 0;
  if (now - lastSampleMs >= SAMPLE_INTERVAL_MS) {
    lastSampleMs = now;
    sampleReady = readAccel(x, y, z);
  }

  switch (state) {
    case ST_IDLE: {
      // hold lower-left for 3 s = clear high score
      if (btn & TSButtonLowerLeft) {
        if (!llHeld) { llHeld = true; llDownSinceMs = now; }
        else if (now - llDownSinceMs >= HS_RESET_HOLD_MS) {
          clearHighScore();
          showError("Record cleared", "");
          playSfx(SFX_CUE);
          waitMs(1200);
          llHeld = false;
          lastButtons = display.getButtons();   // don't re-trigger on release
          goIdle();
          break;
        }
      } else {
        llHeld = false;
      }

      if (pressed & ~TSButtonLowerLeft) { playSfx(SFX_CUE); startSettle(now); break; }   // other buttons start

      if (sampleReady) {
        float dyn = processSample(x, y, z, !inPeak);
        if (detectHit(now, dyn)) {
          if (countStartHit(now)) { startSettle(now); break; }
          drawStartProgress(15, 41, 10, false);
        }
      }
      if (startHitsTimedOut(now)) drawStartProgress(15, 41, 10, false);
      break;
    }

    case ST_SETTLE:
      // wait until the bag has been calm for SETTLE_MS (or SETTLE_MAX_MS passes)
      if (sampleReady) {
        processSample(x, y, z, true);
        if (vibLevel < SETTLE_G) {
          if (!settleOk) { settleOk = true; settleOkSinceMs = now; }
        } else {
          settleOk = false;
        }
      }
      if ((settleOk && now - settleOkSinceMs >= SETTLE_MS) ||
          now - stateStartMs >= SETTLE_MAX_MS) {
        startCalibration(now);
      }
      break;

    case ST_CALIBRATE:
      if (sampleReady) {
        calN++;
        calAdd(x, calMeanX, calM2X);
        calAdd(y, calMeanY, calM2Y);
        calAdd(z, calMeanZ, calM2Z);
      }
      if (now - stateStartMs >= CAL_MS) finishCalibration(now);
      break;

    case ST_COUNTDOWN: {
      if (sampleReady) processSample(x, y, z, true);   // keep baseline fresh
      uint32_t el = now - stateStartMs;
      int n = (el >= COUNTDOWN_MS) ? 0 : 3 - (int)(el / 1000);
      if (n != lastCountdownShown) {
        lastCountdownShown = n;
        playSfx(n > 0 ? SFX_CUE : SFX_BELL);        // 3, 2, 1 = tick; GO! = round bell
        display.clearScreen();
        char buf[6];
        if (n > 0) snprintf(buf, sizeof(buf), "%d", n);
        else       snprintf(buf, sizeof(buf), "GO!");
        printCentered(buf, 22, liberationSans_16ptFontInfo, n > 0 ? C_WHITE : C_GREEN, C_BLACK);
      }
      if (el >= COUNTDOWN_MS + 300) startRecording(now);
      break;
    }

    case ST_RECORD:
      if (sampleReady) {
        float dyn = processSample(x, y, z, !inPeak);
        sampleCount++;
        sumDyn += dyn;
        if (dyn > maxDyn) maxDyn = dyn;
        if (dyn > activeLevel) activeSamples++;
        if (detectHit(now, dyn)) registerPunch(now, peakG, peakStartMs);
      }

      if (!warnPlayed && now - sessionStartMs >= SESSION_MS - WARN_BEFORE_END_MS) {
        warnPlayed = true;
        playSfx(SFX_WARN);            // time is almost up
      }

      if (now - sessionStartMs >= SESSION_MS) {
        finishRecording();
        break;
      }

      if (fistShowing) {
        if ((int32_t)(now - fistUntilMs) >= 0) {
          fistShowing = false;
          needRedraw = true;
        }
      } else if (needRedraw) {
        drawRecordStatic();
        drawRecordDynamic(now);
        needRedraw = false;
        lastUiMs = now;
      } else if (now - lastUiMs >= UI_INTERVAL_MS) {
        drawRecordDynamic(now);
        lastUiMs = now;
      }
      break;

    case ST_HUB:
      // the hub runs the game: just report every hit
      if (sampleReady) {
        float dyn = processSample(x, y, z, !inPeak);
        if (detectHit(now, dyn)) {
          sendImpact(peakAtMs, peakG);
          hubHits++;
          SerialMonitorInterface.print(F("HIT sent, peak="));
          SerialMonitorInterface.print(peakG, 2);
          SerialMonitorInterface.println(F("g"));
          if (strcmp(hubMode, "ROUND") == 0 || strcmp(hubMode, "WAIT") == 0) {
            drawHubHits(true);
            hubFlashing = true;
            hubFlashUntilMs = now + HUB_FLASH_MS;
          }
        }
      }
      if (hubFlashing && (int32_t)(now - hubFlashUntilMs) >= 0) {
        hubFlashing = false;
        if (strcmp(hubMode, "ROUND") == 0 || strcmp(hubMode, "WAIT") == 0) drawHubHits(false);
      }
      break;

    case ST_RESULTS: {
      bool locked = (now - resultsShownMs) < RESULTS_LOCKOUT_MS;

      if (pressed & TSButtonLowerLeft) { dumpLog(); break; }
      if (pressed & TSButtonUpperRight) {            // manual next page
        resultsPage = (resultsPage + 1) % NUM_RESULT_PAGES;
        lastPageMs = now;
        showResultsPage(now);
        break;
      }
      if (pressed) { playSfx(SFX_CUE); startSettle(now); break; }

      if (sampleReady) {
        float dyn = processSample(x, y, z, !inPeak);
        bool hit = detectHit(now, dyn);
        if (hit && !locked) {                         // hits during lockout are ignored
          if (countStartHit(now)) { startSettle(now); break; }
          drawStartProgress(36, 54, 8, false);
        }
      }

      if (now - lastPageMs >= RESULT_PAGE_MS) {       // auto-rotate pages
        resultsPage = (resultsPage + 1) % NUM_RESULT_PAGES;
        lastPageMs = now;
        showResultsPage(now);
      }

      if (resultsWasLocked && !locked) drawStartProgress(36, 54, 8, false);  // boxes go white
      resultsWasLocked = locked;
      if (!locked && startHitsTimedOut(now)) drawStartProgress(36, 54, 8, false);
      break;
    }
  }
}
