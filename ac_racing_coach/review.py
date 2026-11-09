"""Immutable recording review and explicit comparison selection for desktop clients.

Driver approval remains an assertion recorded in the existing hash-bound sidecar.
Nothing in this module infers clean laps from timing or penalty values.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .analysis import Lap, find_complete_laps, identity, load_csv
from .session import LapCompleted
from .validity import marker_path, save_review


class StaleRecordingError(ValueError):
    """The files on disk no longer match the recording the driver reviewed."""


@dataclass(frozen=True)
class ReviewedLap:
    """Read-only lap evidence, compatible with the existing analysis and plots."""

    number: int
    source: Path
    rows: tuple[Mapping[str, object], ...]
    seconds: float
    first_row: int
    last_row: int
    completion: LapCompleted | None

    # Reuse the core's calculations; freezing the rows prevents GUI consumers
    # from bypassing sidecar review by modifying the displayed evidence.
    validity = Lap.validity
    coaching_eligible = Lap.coaching_eligible
    top_speed = Lap.top_speed
    sample_rate = Lap.sample_rate
    max_gap = Lap.max_gap
    gaps = Lap.gaps


@dataclass(frozen=True)
class RecordingSnapshot:
    path: Path
    csv_sha256: str
    sidecar_sha256: str | None
    laps: tuple[ReviewedLap, ...]
    excluded: tuple[tuple[int, int, str], ...]


def _fingerprints(path):
    csv_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    sidecar = marker_path(path)
    try:
        sidecar_bytes = sidecar.read_bytes()
    except FileNotFoundError:
        sidecar_digest = None
    else:
        sidecar_digest = hashlib.sha256(sidecar_bytes).hexdigest()
    return csv_digest, sidecar_digest


def assert_current(snapshot):
    """Fail closed if a recording or review changed after it was displayed.

    Call this again before presenting a completed background analysis. An
    external editor can change a sidecar while a worker is generating a graph.
    """
    try:
        actual = _fingerprints(snapshot.path)
    except OSError as error:
        raise StaleRecordingError("Recording is no longer accessible. Open it again before continuing.") from error
    if actual != (snapshot.csv_sha256, snapshot.sidecar_sha256):
        raise StaleRecordingError("Recording or validity review changed. Open the recording again before continuing.")


def open_recording(path):
    """Load a CSV and its matching sidecar without changing either file."""
    path = Path(path).resolve()
    before = _fingerprints(path)
    rows = load_csv(path)
    laps, excluded = find_complete_laps(rows, path)
    frozen_laps = tuple(
        ReviewedLap(
            number=number, source=lap.source,
            rows=tuple(MappingProxyType(dict(row)) for row in lap.rows),
            seconds=lap.seconds, first_row=lap.first_row,
            last_row=lap.last_row, completion=lap.completion,
        )
        for number, lap in enumerate(laps, 1)
    )
    snapshot = RecordingSnapshot(path, *before, frozen_laps, tuple(excluded))
    assert_current(snapshot)
    return snapshot


def _lap(snapshot, number):
    if type(number) is not int or not 1 <= number <= len(snapshot.laps):
        raise ValueError("Choose a capture-complete lap from the displayed lap list.")
    return snapshot.laps[number - 1]


def review_laps(snapshot, decisions):
    """Persist explicit driver decisions and return a newly loaded snapshot.

    Keys are displayed 1-based capture-lap numbers. States are ``manual_valid``,
    ``invalid``, or ``unknown``. Raw invalid CSV observations remain invalid even
    if a driver attempts to approve them, as required by the core sidecar logic.
    """
    if not decisions:
        raise ValueError("Select at least one lap to review.")
    for number in decisions:
        _lap(snapshot, number)
    assert_current(snapshot)
    save_review(
        snapshot.path, snapshot.laps, decisions,
        expected_csv_sha256=snapshot.csv_sha256,
        expected_sidecar_sha256=snapshot.sidecar_sha256,
    )
    updated = open_recording(snapshot.path)
    if updated.csv_sha256 != snapshot.csv_sha256:
        raise StaleRecordingError("Recording changed during review. Open it again before continuing.")
    return updated


def select_comparison(snapshot, reference_number, current_number):
    """Return exactly the requested earlier reference and later comparison lap.

    Only manually approved, matching car/track/configuration/tyres/track-length
    and session-type laps qualify. Eligibility never selects another lap on the
    driver's behalf. Chronological order preserves the core's 'Latest' labels.
    """
    assert_current(snapshot)
    reference = _lap(snapshot, reference_number)
    current = _lap(snapshot, current_number)
    if reference_number == current_number:
        raise ValueError("Choose two different laps for comparison.")
    if reference_number > current_number:
        raise ValueError("The reference must be earlier than the comparison lap.")
    if not reference.coaching_eligible or not current.coaching_eligible:
        raise ValueError("Both laps must be manually approved by the driver. Unknown and invalid laps cannot be compared.")
    if (identity(reference.rows[0]) != identity(current.rows[0])
            or reference.rows[0]["session_type"] != current.rows[0]["session_type"]):
        raise ValueError("Choose laps with matching car, track, configuration, tyres, track length, and session type.")
    return reference, current
