import pytest

from southpaw.clock import ClockSync
from southpaw.fusion import Fuser, ImpactEvent, PunchEvent, VisionEvent


def test_clock_keeps_shortest_round_trip():
    c = ClockSync()
    # Board booted at Pi time 100.0, so board_ms = (t - 100) * 1000.
    c.ping_sent(200.000)
    c.reply_received(100_030, 200.080)  # slow, asymmetric round trip
    c.ping_sent(201.000)
    c.reply_received(101_010, 201.020)  # fast, symmetric
    assert c.samples == 2
    assert c.offset == pytest.approx(100.0, abs=0.001)
    assert c.to_pi_time(105_000, fallback=0) == pytest.approx(205.0, abs=0.001)


def test_clock_falls_back_until_synced():
    c = ClockSync()
    assert c.to_pi_time(1234, fallback=9.5) == 9.5
    assert c.reply_received(1, 1.0) is False  # no ping outstanding


def test_punch_lands_only_with_nearby_impact():
    f = Fuser(use_vision=False)
    f.add_punch(PunchEvent(10.00, "L", "J", 9))
    f.add_punch(PunchEvent(10.50, "R", "C", 12))
    f.add_impact(ImpactEvent(10.04, 60))  # backs the jab
    f.add_impact(ImpactEvent(10.90, 70))  # too late for the cross
    out = f.resolve(now=20.0)
    assert [p.landed for p in out] == [True, False]
    assert out[0].impact_dt == pytest.approx(0.04)
    assert f.unmatched_impacts == 1


def test_one_impact_backs_only_one_punch():
    f = Fuser(use_vision=False)
    f.add_punch(PunchEvent(5.00, "L", "J", 9))
    f.add_punch(PunchEvent(5.10, "R", "C", 9))
    f.add_impact(ImpactEvent(5.05, 50))
    out = f.resolve(now=10.0)
    assert sum(p.landed for p in out) == 1


def test_waits_for_window_then_resolves():
    f = Fuser(use_vision=False)
    f.add_punch(PunchEvent(1.00, "L", "J", 9))
    assert f.resolve(now=1.05) == []  # impact could still arrive
    f.add_impact(ImpactEvent(1.10, 40))
    assert f.resolve(now=1.5)[0].landed


def test_vision_is_recorded_but_never_decides_landed():
    f = Fuser(use_vision=True)
    f.add_punch(PunchEvent(3.0, "L", "H", 10))
    f.add_vision(VisionEvent(3.05, "L", "U", contact=True, conf=0.9))
    f.add_vision(VisionEvent(3.02, "R", "H", contact=True, conf=0.9))  # other hand, ignored
    (p,) = f.resolve(now=10.0)
    assert p.landed is False
    assert p.vision_type == "U" and p.vision_agrees is False


def test_vision_off_ignores_events():
    f = Fuser(use_vision=False)
    f.add_punch(PunchEvent(3.0, "L", "H", 10))
    f.add_vision(VisionEvent(3.0, "L", "H", contact=True, conf=0.9))
    (p,) = f.resolve(now=10.0)
    assert p.vision_type is None
