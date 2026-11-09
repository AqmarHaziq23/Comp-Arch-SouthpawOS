/*
 * Modified by: Yue Ming (Team P1D) – punch classifier, a stub until there is labelled data (original file).
 */
#include "config.h"
#include "classifier.h"

PunchType classify(const PunchFeatures& f) {
  (void)f;

  // ===== Yue Ming's rules go here =========================================
  // Pick the thresholds from training/data/features.csv (written by
  // training/tools/segment_punches.py); its columns are the PunchFeatures
  // fields. The shape will be something like:
  //
  //   if (f.tiltChange > TILT_HOOK && f.burstMs > BURST_HOOK_MS) return PUNCH_HOOK;
  //   if (f.domAxis == UPPERCUT_AXIS && f.domSign == UPPERCUT_SIGN) return PUNCH_UPPERCUT;
  //   return straight punch: JAB or CROSS depending on HAND and stance
  //
  // Put the thresholds in config.h. Later the exported decision tree
  // (micromlgen or m2cgen) can replace the body of this function.
  // ========================================================================

  return PUNCH_UNKNOWN;
}

char punch_code(PunchType t) {
  switch (t) {
    case PUNCH_JAB: return 'J';
    case PUNCH_CROSS: return 'C';
    case PUNCH_HOOK: return 'H';
    case PUNCH_UPPERCUT: return 'U';
    default: return 'X';
  }
}

const char* punch_name(PunchType t) {
  switch (t) {
    case PUNCH_JAB: return "JAB";
    case PUNCH_CROSS: return "CROSS";
    case PUNCH_HOOK: return "HOOK";
    case PUNCH_UPPERCUT: return "UPPERCUT";
    default: return "UNKNOWN";
  }
}
