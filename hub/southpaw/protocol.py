"""SouthpawOS message protocol v1.1: parse board messages, build Pi commands.

The spec lives in protocol/PROTOCOL.md. Keep the two in sync.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Union

PROTOCOL_VERSION = "1.5"
MAX_BLE_PAYLOAD = 20  # bytes per BLE packet, including the trailing "\n"

# Nordic UART Service, as set up by the TinyCircuits STBLE UART example.
NUS_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # board -> Pi (notify)
NUS_RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # Pi -> board (write)

HANDS = ("L", "R")
PUNCH_CODES = {"J": "JAB", "C": "CROSS", "H": "HOOK", "U": "UPPERCUT", "X": "UNKNOWN"}
UNKNOWN = "X"  # punch detected, type not classified
PUNCH_NAMES = {name: code for code, name in PUNCH_CODES.items()}
SOUNDS = ("BELL", "WARN", "HIT", "MISS")
MODES = ("WAIT", "ROUND", "REST", "SUMMARY")

# Combo numbers -> (lead/rear, punch code). Boxing numbering, orthodox stance.
COMBO_NUMBERS = {
    1: ("lead", "J"),
    2: ("rear", "C"),
    3: ("lead", "H"),
    4: ("rear", "H"),
    5: ("lead", "U"),
    6: ("rear", "U"),
}


class ProtocolError(ValueError):
    """A message that doesn't follow the protocol."""


# ---------------------------------------------------------------- board -> Pi


@dataclass(frozen=True)
class Punch:
    hand: str  # "L" or "R"
    type: str  # punch code: J, C, H, U, or X if unknown
    board_ms: int
    peak_g: int


@dataclass(frozen=True)
class Impact:
    board_ms: int
    strength: int  # 0-100


@dataclass(frozen=True)
class HeartRate:
    bpm: int
    good: bool


@dataclass(frozen=True)
class ClockReply:
    board_ms: int


@dataclass(frozen=True)
class RawWindow:
    payload: str  # format owned by Member A; passed through untouched


BoardMessage = Union[Punch, Impact, HeartRate, ClockReply, RawWindow]


def _int(text: str, field: str, lo: int | None = None, hi: int | None = None) -> int:
    try:
        value = int(text)
    except ValueError:
        raise ProtocolError(f"{field} must be an integer, got {text!r}") from None
    if lo is not None and value < lo:
        raise ProtocolError(f"{field} must be >= {lo}, got {value}")
    if hi is not None and value > hi:
        raise ProtocolError(f"{field} must be <= {hi}, got {value}")
    return value


def _fields(parts: list[str], n: int, line: str) -> None:
    if len(parts) != n:
        raise ProtocolError(f"expected {n} fields, got {len(parts)}: {line!r}")


def parse_board(line: str) -> BoardMessage:
    """Parse one line received from a board. Raises ProtocolError if invalid."""
    line = line.strip()
    if not line:
        raise ProtocolError("empty message")
    parts = line.split(",")
    kind = parts[0]

    if kind == "P":
        _fields(parts, 5, line)
        hand = parts[1]
        if hand not in HANDS:
            raise ProtocolError(f"hand must be L or R, got {hand!r}")
        code = parts[2]
        if code in PUNCH_NAMES:  # accept full words while firmware catches up
            code = PUNCH_NAMES[code]
        if code not in PUNCH_CODES:
            raise ProtocolError(f"unknown punch type {parts[2]!r}")
        return Punch(hand, code, _int(parts[3], "board_ms", lo=0), _int(parts[4], "peak_g", lo=0))

    if kind == "I":
        _fields(parts, 3, line)
        return Impact(_int(parts[1], "board_ms", lo=0), _int(parts[2], "strength", 0, 100))

    if kind == "H":
        _fields(parts, 3, line)
        flag = parts[2]
        if flag not in ("0", "1"):
            raise ProtocolError(f"heart-rate quality flag must be 0 or 1, got {flag!r}")
        return HeartRate(_int(parts[1], "bpm", 20, 250), flag == "1")

    if kind == "T":
        _fields(parts, 2, line)
        return ClockReply(_int(parts[1], "board_ms", lo=0))

    if kind == "R":
        return RawWindow(line[2:] if len(line) > 1 else "")

    raise ProtocolError(f"unknown message type {kind!r}: {line!r}")


# ---------------------------------------------------------------- Pi -> board


def _checked(msg: str) -> str:
    """Add the terminator and make sure the message fits in one BLE packet."""
    msg += "\n"
    if len(msg.encode("ascii")) > MAX_BLE_PAYLOAD:
        raise ProtocolError(f"message is {len(msg)} bytes, max {MAX_BLE_PAYLOAD}: {msg!r}")
    return msg


def combo_msg(numbers: Iterable[int]) -> str:
    numbers = list(numbers)
    if not numbers:
        raise ProtocolError("combo must have at least one punch")
    for n in numbers:
        if n not in COMBO_NUMBERS:
            raise ProtocolError(f"combo numbers are 1-6, got {n}")
    return _checked("C," + "-".join(str(n) for n in numbers))


def sound_msg(sound: str) -> str:
    if sound not in SOUNDS:
        raise ProtocolError(f"sound must be one of {SOUNDS}, got {sound!r}")
    return _checked(f"S,{sound}")


def mode_msg(mode: str) -> str:
    if mode not in MODES:
        raise ProtocolError(f"mode must be one of {MODES}, got {mode!r}")
    return _checked(f"M,{mode}")


def clock_ping_msg() -> str:
    return _checked("T")


def parse_combo(line: str) -> list[int]:
    """Parse a Pi -> bag combo command (used by the simulator)."""
    line = line.strip()
    if not line.startswith("C,"):
        raise ProtocolError(f"not a combo message: {line!r}")
    try:
        numbers = [int(n) for n in line[2:].split("-")]
    except ValueError:
        raise ProtocolError(f"bad combo: {line!r}") from None
    for n in numbers:
        if n not in COMBO_NUMBERS:
            raise ProtocolError(f"combo numbers are 1-6, got {n}")
    return numbers


def expected_punch(number: int, stance: str = "orthodox") -> tuple[str, str]:
    """Combo number -> (hand, punch code) for the player's stance."""
    side, code = COMBO_NUMBERS[number]
    lead = "L" if stance == "orthodox" else "R"
    rear = "R" if stance == "orthodox" else "L"
    return (lead if side == "lead" else rear, code)


# ---------------------------------------------------------------- line reassembly


class LineAssembler:
    """Turns a stream of BLE notification chunks into complete lines.

    Messages end with "\\n". If a board forgets the terminator, a chunk with no
    newline is held until the next chunk arrives, so always send "\\n".
    """

    MAX_BUFFER = 256  # drop junk rather than grow forever

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, chunk: bytes | str) -> list[str]:
        if isinstance(chunk, bytes):
            chunk = chunk.decode("ascii", errors="replace")
        self._buf += chunk.replace("\r", "").replace("\x00", "")  # STBLE example sends a trailing NUL
        *lines, self._buf = self._buf.split("\n")
        if len(self._buf) > self.MAX_BUFFER:
            self._buf = ""
        return [ln for ln in (s.strip() for s in lines) if ln]
