import pytest

from southpaw import protocol as p


def test_parse_punch_letters_and_words():
    assert p.parse_board("P,L,H,48213,11") == p.Punch("L", "H", 48213, 11)
    assert p.parse_board("P,R,UPPERCUT,5,9\n") == p.Punch("R", "U", 5, 9)
    assert p.parse_board("P,L,X,48213,27") == p.Punch("L", "X", 48213, 27)
    assert p.parse_board("P,L,UNKNOWN,1,3") == p.Punch("L", "X", 1, 3)


def test_parse_other_board_messages():
    assert p.parse_board("I,48260,72") == p.Impact(48260, 72)
    assert p.parse_board("H,142,1") == p.HeartRate(142, True)
    assert p.parse_board("H,142,0") == p.HeartRate(142, False)
    assert p.parse_board("T,48100") == p.ClockReply(48100)
    assert p.parse_board("R,1,2,3") == p.RawWindow("1,2,3")


@pytest.mark.parametrize(
    "line",
    ["", "X,1", "P,Q,H,1,1", "P,L,KICK,1,1", "P,L,H,abc,1", "I,1,101", "H,142,2", "T", "P,L,H,1"],
)
def test_bad_messages_raise(line):
    with pytest.raises(p.ProtocolError):
        p.parse_board(line)


def test_commands_fit_one_ble_packet():
    assert p.combo_msg([1, 2, 3]) == "C,1-2-3\n"
    assert p.sound_msg("HIT") == "S,HIT\n"
    assert p.mode_msg("SUMMARY") == "M,SUMMARY\n"
    assert p.clock_ping_msg() == "T\n"
    longest = p.combo_msg([6] * 9)  # "C,6-6-6-6-6-6-6-6-6\n" is exactly 20 bytes
    assert len(longest) == p.MAX_BLE_PAYLOAD
    with pytest.raises(p.ProtocolError):
        p.combo_msg([6] * 10)
    with pytest.raises(p.ProtocolError):
        p.combo_msg([7])


def test_worst_case_board_messages_fit():
    # ~27 hours of uptime in millis is 8 digits; still fits.
    for msg in ("P,L,U,99999999,16\n", "P,R,X,99999999,27\n", "I,99999999,100\n", "T,99999999\n", "H,250,1\n"):
        assert len(msg) <= p.MAX_BLE_PAYLOAD, msg


def test_parse_combo_and_stance():
    assert p.parse_combo("C,1-2-6") == [1, 2, 6]
    assert p.expected_punch(1) == ("L", "J")
    assert p.expected_punch(2) == ("R", "C")
    assert p.expected_punch(1, "southpaw") == ("R", "J")
    assert p.expected_punch(4, "southpaw") == ("L", "H")


def test_line_assembler_handles_split_and_merged_packets():
    a = p.LineAssembler()
    assert a.feed(b"P,L,H,1") == []
    assert a.feed(b"0,5\nI,12,") == ["P,L,H,10,5"]
    assert a.feed(b"40\r\nT,7\n") == ["I,12,40", "T,7"]


def test_line_assembler_drops_nul_bytes():
    # The TinyCircuits UARTPassThrough example sends a NUL after each message.
    a = p.LineAssembler()
    assert a.feed(b"P,L,H,1,5\n\x00") == ["P,L,H,1,5"]
    assert a.feed(b"I,2,40\n\x00") == ["I,2,40"]
