"""Host capture time is shared by producers; game time measures lap performance."""

from dataclasses import dataclass, field
import time
from uuid import uuid4
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class SessionTimeline:
    """One origin per recording, never reset on pauses or lap boundaries.

    Future video producers must share this instance/origin and convert their
    acquisition timestamps with elapsed(). Encoder completion time is unsuitable.
    The origin is process-host monotonic time, not UTC or the game's lap timer.
    Legacy CSV capture_time_s already uses this elapsed-time convention, but
    cannot retrospectively establish synchronization with unrelated video.
    """

    session_id: str = field(default_factory=lambda: uuid4().hex)
    origin_monotonic_s: float = field(default_factory=time.monotonic)

    def elapsed(self, monotonic_s=None):
        if monotonic_s is None:
            monotonic_s = time.monotonic()
        return monotonic_s - self.origin_monotonic_s


@dataclass(frozen=True)
class LapCompleted:
    """A capture-qualified lap, not an assertion of official clean-lap validity.

    finish_capture_time_s is estimated from the confirming sample's game timer;
    confirmed_at_capture_time_s is when all required finish evidence was present.
    first/last row numbers identify CSV evidence and disambiguate counter resets.
    """

    session_id: str
    completed_laps: int
    lap_time_s: float
    first_row: int
    last_row: int
    finish_capture_time_s: float
    confirmed_at_capture_time_s: float
    lap_validity: str = "unknown"


@dataclass(frozen=True)
class TelemetryFrame:
    """Immutable observation on the recorder's timeline, including unsafe states.

    ``fresh`` means both game packet counters advanced. State-only notifications
    never become CSV rows. Consumers must also check age against capture_time_s.
    ``segment`` marks the same capture discontinuities as the CSV recorder.
    This carries no assertion of official lap validity.
    """

    session_id: str
    capture_time_s: float
    status: int
    fresh: bool
    metadata: Mapping
    sample: Mapping | None
    segment: int = 0

    def __post_init__(self):
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        if self.sample is not None:
            object.__setattr__(self, "sample", MappingProxyType(dict(self.sample)))
