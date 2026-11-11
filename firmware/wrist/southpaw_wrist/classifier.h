/*
 * Modified by: Yue Ming (Team P1D) – punch classifier interface (original file).
 */
#ifndef SOUTHPAW_CLASSIFIER_H
#define SOUTHPAW_CLASSIFIER_H

#include <stdint.h>
#include "features.h"

enum PunchType : uint8_t {
  PUNCH_UNKNOWN = 0,
  PUNCH_JAB,
  PUNCH_CROSS,
  PUNCH_HOOK,
  PUNCH_UPPERCUT,
};

PunchType classify(const PunchFeatures& f);

// Protocol letter: J, C, H, U. Unknown is 'X', which is NOT in protocol v1.1:
// the hub rejects it until Haziq adds it (question 1 from the P1 protocol check).
char punch_code(PunchType t);
const char* punch_name(PunchType t);

#endif
