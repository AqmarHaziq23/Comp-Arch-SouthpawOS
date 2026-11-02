"""Map each board's millis() onto the Pi's clock (time.monotonic seconds)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ClockSync:
    """Offset estimate for one board, from T pings.

    For each ping: offset = (t_send + t_recv) / 2 - board_ms / 1000.
    The sample with the shortest round trip wins, since it has the least jitter.
    """

    offset: float | None = None
    best_rtt: float = float("inf")
    samples: int = 0
    _pending_send: float | None = field(default=None, repr=False)

    def ping_sent(self, t_send: float) -> None:
        self._pending_send = t_send

    def reply_received(self, board_ms: int, t_recv: float) -> bool:
        """Record a T,<ms> reply. Returns True if it improved the estimate."""
        if self._pending_send is None:
            return False  # reply without a ping we know about
        t_send, self._pending_send = self._pending_send, None
        rtt = t_recv - t_send
        if rtt < 0:
            return False
        self.samples += 1
        if rtt < self.best_rtt:
            self.best_rtt = rtt
            self.offset = (t_send + t_recv) / 2 - board_ms / 1000
            return True
        return False

    @property
    def synced(self) -> bool:
        return self.offset is not None

    def to_pi_time(self, board_ms: int, fallback: float) -> float:
        """Board millis -> Pi time. Uses `fallback` (usually the receive time)
        until the board has been synced."""
        if self.offset is None:
            return fallback
        return board_ms / 1000 + self.offset
