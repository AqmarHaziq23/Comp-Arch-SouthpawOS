"""
test_guard.py - Southpaw OS, computer vision (Member D)

Tests the guard rules (guard.py) on made-up body poses, so no camera, MediaPipe or
OpenCV is needed. Run it after changing guard.py or the guard settings:

    python test_guard.py           (prints "ALL TESTS PASSED")
    python -m pytest test_guard.py (also works)

The made-up boxer: body length 200 px (shoulders at y=200, hips at y=400), mouth at
y=150, nose at x=320. Image y grows DOWNWARDS, so a SMALLER y is HIGHER up.
"""

import copy

from guard import GuardChecker, GuardTracker
from pose import Point
from vision_config import DEFAULTS


def make_pose(left_fist=(300, 150), right_fist=(340, 160), scale=1.0, hidden=()):
    """A boxer facing the camera. Fists are (x, y) in pixels; scale zooms the whole body
    (2.0 = standing twice as close). hidden = point names to mark as not visible."""
    points = {
        "NOSE": (320, 130), "L_EYE": (310, 120), "R_EYE": (330, 120),
        "MOUTH_L": (312, 150), "MOUTH_R": (328, 150),
        "L_SHOULDER": (270, 200), "R_SHOULDER": (370, 200),
        "L_ELBOW": (260, 260), "R_ELBOW": (380, 260),
        "L_HIP": (290, 400), "R_HIP": (350, 400),
    }
    for side, (x, y) in (("L", left_fist), ("R", right_fist)):
        points[f"{side}_WRIST"] = (x, y + 10)          # wrist a little below the knuckles
        points[f"{side}_INDEX"] = (x - 3, y - 5)
        points[f"{side}_PINKY"] = (x + 3, y - 5)
    return {name: Point(x * scale, y * scale, 0.1 if name in hidden else 0.99)
            for name, (x, y) in points.items()}


def config(stance="orthodox", lead=None):
    c = copy.deepcopy(DEFAULTS)
    c["player"]["stance"] = stance
    if lead is not None:                      # demand a higher lead hand than the default
        c["guard"]["lead_max_below_mouth"] = lead
    return c


def up(pose, stance="orthodox", lead=None):
    """(left up?, right up?) for one pose."""
    hands = GuardChecker(config(stance, lead)).check(pose)
    return hands["L"].up, hands["R"].up


def test_classic_guard_is_up():
    assert up(make_pose()) == (True, True)


def test_hand_at_chest_is_down():
    # Chest (y=240) is below the limit line: mouth 150 + 0.25 x 200 px = y 200
    assert up(make_pose(right_fist=(350, 240))) == (True, False)
    assert up(make_pose(left_fist=(300, 240))) == (False, True)


def test_hand_just_below_chin_still_counts():
    # Knuckles at y=185, wrist at 200: fist average 190, just above the limit of 200
    assert up(make_pose(right_fist=(340, 190)))[1] is True


def test_lead_hand_can_be_made_stricter():
    # With lead_max_below_mouth = 0.05 (limit y 160), a lead fist at y=175 is too low; the rear is still fine
    assert up(make_pose(left_fist=(300, 175), right_fist=(340, 175)), lead=0.05) == (False, True)


def test_hand_out_to_the_side_is_not_a_guard():
    # At head height but 1 body length (200 px) sideways from the nose
    assert up(make_pose(right_fist=(520, 140)))[1] is False


def test_same_pose_closer_to_the_camera_gives_the_same_answer():
    for fists in [dict(), dict(right_fist=(350, 240)), dict(left_fist=(300, 190))]:
        assert up(make_pose(**fists)) == up(make_pose(scale=2.0, **fists)), fists


def test_southpaw_swaps_lead_and_rear():
    # Left fist at y=175 with a strict lead hand: down for an orthodox lead, fine for a southpaw rear hand
    pose = make_pose(left_fist=(300, 175))
    assert up(pose, "orthodox", lead=0.05)[0] is False
    assert up(pose, "southpaw", lead=0.05)[0] is True


def test_first_real_body_length_replaces_the_shoulder_guess():
    # Hips hidden at first: scale is guessed from the shoulders; once the hips show, the real
    # body length (200 px) is used straight away instead of slowly drifting from the guess
    checker = GuardChecker(config())
    checker.check(make_pose(hidden=("L_HIP", "R_HIP")))
    assert checker.scale_is_guess
    checker.check(make_pose())
    assert not checker.scale_is_guess and abs(checker.scale - 200) < 1


def test_hidden_points_give_unknown_not_down():
    assert up(make_pose(hidden=("R_WRIST",)))[1] is None             # can't see the hand
    assert up(make_pose(hidden=("NOSE", "MOUTH_L", "MOUTH_R"))) == (None, None)   # can't see the face
    assert GuardChecker(config()).check(None)["L"].up is None         # nobody in view


def test_nose_alone_is_enough_for_the_face():
    assert up(make_pose(hidden=("MOUTH_L", "MOUTH_R"))) == (True, True)


def test_tracker_counts_time_and_drops():
    t = GuardTracker(drop_hold_s=0.4)
    frame = 1 / 10                                   # 10 frames a second
    for i in range(10):                              # 1 s: both up
        t.update(i * frame, {"L": True, "R": True})
    for i in range(10, 20):                          # 1 s: right hand down -> one drop after 0.4 s
        t.update(i * frame, {"L": True, "R": False})
    for i in range(20, 30):                          # 1 s: both up again
        t.update(i * frame, {"L": True, "R": True})
    assert t.hands["R"].drops == 1 and t.hands["L"].drops == 0
    assert round(t.pct("L")) == 100
    assert round(t.pct("R")) == 66                   # down 1.0 of the 2.9 s counted (last frame has no time yet)
    assert t.summary()["guard_drops"] == 1


def test_short_dip_is_not_a_drop_and_drop_fires_once():
    t = GuardTracker(drop_hold_s=0.4)
    events = []
    states = [True, False, False, True] + [False] * 8   # 0.2 s dip, then a long drop
    for i, s in enumerate(states):
        events += t.update(i * 0.1, {"L": s, "R": True})
    assert events == ["L"] and t.hands["L"].drops == 1
    assert t.is_dropped("L")


def test_ignored_frames_dont_count_and_end_the_down_stretch():
    # While the hub says the right hand is punching, its frames are left out
    t = GuardTracker(drop_hold_s=0.4)
    for i in range(10):
        t.update(i * 0.1, {"L": True, "R": False}, ignore=("R",))
    assert t.hands["R"].drops == 0 and t.pct("R") is None
    assert t.pct("L") == 100.0


def test_unknown_frames_dont_count_and_camera_gaps_are_capped():
    t = GuardTracker(drop_hold_s=0.4, max_gap_s=0.5)
    t.update(0.0, {"L": True, "R": None})
    t.update(10.0, {"L": True, "R": None})           # 10 s gap counts as 0.5 s
    assert t.hands["L"].known_s == 0.5 and t.hands["R"].known_s == 0.0


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_") and callable(test):
            test()
    print("ALL TESTS PASSED")
