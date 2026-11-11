/*
 * Modified by: Yue Ming (Team P1D) – runs the decision tree exported by
 *   training/tools/train_classifier.py --export (original file).
 *
 * The tree itself is generated C in classifier_model.c (scikit-learn tree -> m2cgen).
 * This file builds its input from PunchFeatures, picks the most likely class and turns
 * the letter into a PunchType. Python's train_classifier.py predicts the same way:
 * the first largest output wins.
 */
#include "config.h"
#include "classifier.h"
#include "classifier_model.h"

#if HAND == 'L'
  #define CLASSIFIER_SCORE classifier_score_L
  #define CLASSIFIER_CLASSES CLASSIFIER_CLASSES_L
#else
  #define CLASSIFIER_SCORE classifier_score_R
  #define CLASSIFIER_CLASSES CLASSIFIER_CLASSES_R
#endif

static PunchType fromCode(char c) {
  switch (c) {
    case 'J': return PUNCH_JAB;
    case 'C': return PUNCH_CROSS;
    case 'H': return PUNCH_HOOK;
    case 'U': return PUNCH_UPPERCUT;
    case 'N': return PUNCH_NONE;
    default: return PUNCH_UNKNOWN;
  }
}

PunchType classify(const PunchFeatures& f) {
  double input[CLASSIFIER_N_INPUTS] = CLASSIFIER_INPUTS(f);
  double out[CLASSIFIER_N_CLASSES];
  CLASSIFIER_SCORE(input, out);
  uint8_t best = 0;
  for (uint8_t i = 1; i < CLASSIFIER_N_CLASSES; i++) {
    if (out[i] > out[best]) best = i;      // strictly larger: the first largest wins, as in scikit-learn
  }
  return fromCode(CLASSIFIER_CLASSES[best]);
}

char punch_code(PunchType t) {
  switch (t) {
    case PUNCH_JAB: return 'J';
    case PUNCH_CROSS: return 'C';
    case PUNCH_HOOK: return 'H';
    case PUNCH_UPPERCUT: return 'U';
    case PUNCH_NONE: return 'N';
    default: return 'X';
  }
}

const char* punch_name(PunchType t) {
  switch (t) {
    case PUNCH_JAB: return "JAB";
    case PUNCH_CROSS: return "CROSS";
    case PUNCH_HOOK: return "HOOK";
    case PUNCH_UPPERCUT: return "UPPERCUT";
    case PUNCH_NONE: return "NONE";
    default: return "UNKNOWN";
  }
}
