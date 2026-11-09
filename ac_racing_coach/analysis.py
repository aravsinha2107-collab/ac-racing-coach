"""Analyze CSV recordings from record_session.py for original Assetto Corsa.

Setup once: py -m pip install matplotlib
Analyze the newest CSV in recordings/: py analyze_session.py
Choose files: py analyze_session.py recordings/session_a.csv recordings/session_b.csv

The script prints coaching notes, saves a PNG and text notes under analysis/,
and opens the PNG on Windows.
It uses the recorded game lap timer, not computer wall time, for lap comparisons.
Review lap numbers count capture-complete laps, including unknown/invalid laps.

A full timed lap has start/end coverage, a confirmed game finish time, and no
recorded pit or penalty flags. A short sampling gap of at most 0.5 seconds can
remain part of a lap if the game clock continues normally and track progress is
plausible. Such gaps are marked and excluded from plotted interpolation and
coaching suggestions. Pauses and resets are not joined. This does NOT verify track limits,
collisions, game-invalidated laps, weather, assists, or setup equivalence. The
recorder does not capture all of those. Coaching requires explicit manual-valid
review; capture completeness and a zero penalty timer never establish validity.

The original game can update track position, timer, and completed-lap counter in
slightly different samples at the finish line. Small boundary fragments are
excluded, and the reported finish time is read from subsequent samples.
"""

from __future__ import annotations

from bisect import bisect_left
import csv
from dataclasses import dataclass
from itertools import groupby
import math
from pathlib import Path
from .session import LapCompleted
from .validity import apply_review, lap_validity


INTEGER_FIELDS = (
    "capture_segment", "completed_laps", "session_type", "gear", "rpm",
    "in_pit", "in_pit_lane",
)
FLOAT_FIELDS = (
    "capture_time_s", "lap_time_s", "last_lap_time_s", "track_position",
    "speed_kmh", "throttle_pct", "brake_pct", "steering_raw", "fuel_l",
    "penalty_time_s", "track_length_m",
)
TEXT_FIELDS = ("car_model", "track", "track_configuration", "tyre_compound")
MAX_GAP_S = 0.25
MAX_CAPTURE_GAP_S = 0.5
TRACE_GAP_S = 0.1
BOUNDARY_TIME_S = 0.5
BOUNDARY_POSITION = 0.01


@dataclass
class Lap:
    source: Path
    rows: list[dict]
    seconds: float
    first_row: int
    last_row: int
    completion: LapCompleted | None = None

    @property
    def validity(self):
        return lap_validity(self.rows)

    @property
    def coaching_eligible(self):
        return self.validity == "manual_valid"

    @property
    def top_speed(self):
        return max(r["speed_kmh"] for r in self.rows)

    @property
    def sample_rate(self):
        duration = self.rows[-1]["capture_time_s"] - self.rows[0]["capture_time_s"]
        return (len(self.rows) - 1) / duration

    @property
    def max_gap(self):
        return max(b["capture_time_s"] - a["capture_time_s"]
                   for a, b in zip(self.rows, self.rows[1:]))

    @property
    def gaps(self):
        """Observed gap intervals: start/end track fraction and elapsed seconds."""
        return [(a["track_position"], b["track_position"], b["capture_time_s"] - a["capture_time_s"])
                for a, b in zip(self.rows, self.rows[1:])
                if max(b["capture_time_s"] - a["capture_time_s"],
                       b["lap_time_s"] - a["lap_time_s"]) > TRACE_GAP_S]


def time_label(seconds):
    milliseconds = round(seconds * 1000)
    minutes, remainder = divmod(milliseconds, 60000)
    return f"{minutes}:{remainder // 1000:02d}.{remainder % 1000:03d}"


def identity(row):
    return tuple(row[k] for k in TEXT_FIELDS) + (row["track_length_m"],)


def load_csv(path):
    """Read and validate every row; never silently discard malformed input."""
    rows = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = set(INTEGER_FIELDS + FLOAT_FIELDS + TEXT_FIELDS)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path.name}: missing columns: {', '.join(sorted(missing))}")
        for line_number, raw in enumerate(reader, 2):
            try:
                if None in raw or any(raw.get(key) is None for key in required):
                    raise ValueError("wrong number of columns")
                row = {key: raw[key] for key in TEXT_FIELDS}
                row["session_id"] = raw.get("session_id", "")
                row["lap_validity"] = raw.get("lap_validity") or "unknown"
                if row["lap_validity"] not in ("unknown", "invalid"):
                    raise ValueError("unsupported lap_validity; valid reviews belong in the sidecar")
                row.update({key: int(raw[key]) for key in INTEGER_FIELDS})
                row.update({key: float(raw[key]) for key in FLOAT_FIELDS})
                if not all(math.isfinite(row[key]) for key in FLOAT_FIELDS):
                    raise ValueError("a numeric value is missing or non-finite")
                if not 0 <= row["track_position"] <= 1:
                    raise ValueError("track position must be between 0 and 1")
                for key in ("throttle_pct", "brake_pct"):
                    if not -0.01 <= row[key] <= 100.01:
                        raise ValueError(f"{key} must be between 0 and 100")
                if rows and row["capture_time_s"] <= rows[-1]["capture_time_s"]:
                    raise ValueError("capture timestamps must increase")
            except (ValueError, TypeError) as error:
                raise ValueError(f"{path.name}, row {line_number}: {error}") from error
            rows.append(row)
    if not rows:
        raise ValueError(f"{path.name} contains no recorded samples")
    apply_review(rows, path)
    return rows


def find_complete_laps(rows, source, *, session_id=None):
    """Find full timed laps. Keep brief dropouts visible; do not join pauses."""
    source = Path(source)
    session_id = session_id or (rows[0].get("session_id") if rows else None) or str(source.resolve())
    laps = []
    excluded = []

    def same_lap_key(row):
        return row["completed_laps"], identity(row), row["session_type"]

    def brief_dropout(before, after):
        # A real pause advances host time without comparable game time. Only
        # link a short dropout when both clocks and position remain consistent.
        host_gap = after["capture_time_s"] - before["capture_time_s"]
        game_gap = after["lap_time_s"] - before["lap_time_s"]
        progress = after["track_position"] - before["track_position"]
        length = before["track_length_m"]
        if length <= 0:
            return False
        expected_progress = max(before["speed_kmh"], after["speed_kmh"], 10) / 3.6 * host_gap / length
        return (TRACE_GAP_S < host_gap <= MAX_CAPTURE_GAP_S
                and game_gap > 0 and abs(host_gap - game_gap) <= 0.03
                and 0 <= progress <= 2 * expected_progress + 0.0005)

    block_ids = [0]
    for before, after in zip(rows, rows[1:]):
        split = same_lap_key(before) != same_lap_key(after)
        if after["capture_segment"] != before["capture_segment"] and not brief_dropout(before, after):
            split = True
        if (after["lap_time_s"] < before["lap_time_s"] - 0.001
                or after["track_position"] < before["track_position"] - 0.00001):
            split = True
        block_ids.append(block_ids[-1] + int(split))

    for _, indices in groupby(range(len(rows)), key=lambda index: block_ids[index]):
        indices = list(indices)
        start, end = indices[0], indices[-1]
        part = rows[start:end + 1]
        first, last = part[0], part[-1]
        reason = None
        if len(part) < 100:
            reason = "short boundary or partial fragment"
        elif first["track_position"] > BOUNDARY_POSITION or first["lap_time_s"] > BOUNDARY_TIME_S:
            reason = "missing the beginning of a timed lap"
        elif last["track_position"] < 1 - BOUNDARY_POSITION:
            reason = "missing the end of a timed lap"
        elif any(r["in_pit"] or r["in_pit_lane"] or r["penalty_time_s"] > 0 for r in part):
            reason = "recorded pit or penalty flag"
        else:
            for before, after in zip(part, part[1:]):
                host_gap = after["capture_time_s"] - before["capture_time_s"]
                game_gap = after["lap_time_s"] - before["lap_time_s"]
                progress = after["track_position"] - before["track_position"]
                if (host_gap > MAX_CAPTURE_GAP_S or game_gap > MAX_CAPTURE_GAP_S
                        or abs(host_gap - game_gap) > 0.1):
                    reason = "recording gap or paused game time"
                    break
                if game_gap < -0.001 or progress < -0.00001 or progress > 0.02:
                    reason = "timer reset, backwards movement, or position jump"
                    break

        finish_seconds = None
        if reason is None:
            following = []
            for row in rows[end + 1:]:
                if row["capture_time_s"] - last["capture_time_s"] > BOUNDARY_TIME_S:
                    break
                if identity(row) != identity(first) or row["session_type"] != first["session_type"]:
                    break
                following.append(row)
            # The counter can increment one sample later than the timer reset.
            counter_sample = next((r for r in following
                if r["completed_laps"] == first["completed_laps"] + 1), None)
            counter_incremented = counter_sample is not None
            for row in following:
                reported = row["last_lap_time_s"]
                host_to_boundary = row["capture_time_s"] - last["capture_time_s"]
                game_to_boundary = reported - last["lap_time_s"] + row["lap_time_s"]
                if (counter_incremented and row["track_position"] <= BOUNDARY_POSITION
                        and 0 <= row["lap_time_s"] <= BOUNDARY_TIME_S
                        and host_to_boundary <= MAX_GAP_S
                        and abs(host_to_boundary - game_to_boundary) <= 0.1
                        and last["lap_time_s"] <= reported <= last["lap_time_s"] + MAX_GAP_S
                        and reported > first["lap_time_s"]):
                    finish_seconds = reported
                    break
            if finish_seconds is None:
                reason = "finish time not confirmed by subsequent samples"

        if reason:
            excluded.append((start + 2, end + 2, reason))
        else:
            completion = LapCompleted(
                session_id=session_id,
                completed_laps=first["completed_laps"] + 1,
                lap_time_s=finish_seconds,
                first_row=start + 2, last_row=end + 2,
                finish_capture_time_s=row["capture_time_s"] - row["lap_time_s"],
                confirmed_at_capture_time_s=max(row["capture_time_s"], counter_sample["capture_time_s"]),
                lap_validity=lap_validity(part),
            )
            laps.append(Lap(source, part, finish_seconds, start + 2, end + 2, completion))
    return laps, excluded


def confirmed_lap_events(rows, source, *, session_id=None):
    """Return capture-completion events, including their separate validity state.

    This is a batch API. A future live dispatcher must wait for confirmation and
    deduplicate by (session_id, first_row, last_row), not just the lap counter.
    """
    laps, _ = find_complete_laps(rows, source, session_id=session_id)
    return [lap.completion for lap in laps]


def choose_laps(laps):
    """Select the latest eligible lap and fastest eligible earlier matching lap."""
    laps = [lap for lap in laps if lap.coaching_eligible]
    if not laps:
        return []
    current = laps[-1]
    candidates = [lap for lap in laps[:-1] if identity(lap.rows[0]) == identity(current.rows[0])]
    if not candidates:
        return [current]
    reference = min(candidates, key=lambda lap: lap.seconds)
    return [reference, current]


def increasing_trace(lap, key):
    """Keep the first observation at each advancing track position for interpolation."""
    positions, values = [], []
    for row in lap.rows:
        if not positions or row["track_position"] > positions[-1]:
            positions.append(row["track_position"])
            values.append(row[key])
    return positions, values


def value_at(trace, gaps, position):
    """Interpolate between nearby real samples only; never fill a marked gap."""
    positions, values = trace
    if not positions or position < positions[0] or position > positions[-1]:
        return None
    if any(left < position < right for left, right, _ in gaps):
        return None
    index = bisect_left(positions, position)
    if index == 0 or positions[index] == position:
        return values[index]
    fraction = (position - positions[index - 1]) / (positions[index] - positions[index - 1])
    return values[index - 1] + fraction * (values[index] - values[index - 1])


def section_comparison(reference, current):
    """Compare roughly 10% sections at matching positions, with gap coverage."""
    if not reference.coaching_eligible or not current.coaching_eligible:
        return []
    ref_trace = increasing_trace(reference, "lap_time_s")
    cur_trace = increasing_trace(current, "lap_time_s")
    left = max(ref_trace[0][0], cur_trace[0][0])
    right = min(ref_trace[0][-1], cur_trace[0][-1])
    edges = [left] + [i / 10 for i in range(1, 10) if left < i / 10 < right] + [right]
    sections = []
    for start, end in zip(edges, edges[1:]):
        ref_a = value_at(ref_trace, reference.gaps, start)
        ref_b = value_at(ref_trace, reference.gaps, end)
        cur_a = value_at(cur_trace, current.gaps, start)
        cur_b = value_at(cur_trace, current.gaps, end)
        if None in (ref_a, ref_b, cur_a, cur_b):
            continue
        ref_samples = [r for r in reference.rows if start <= r["track_position"] <= end]
        cur_samples = [r for r in current.rows if start <= r["track_position"] <= end]
        sections.append({
            "start": start, "end": end,
            "label": f"{start * 100:.0f}-{end * 100:.0f}%",
            "delta_s": (cur_b - cur_a) - (ref_b - ref_a),
            "reference_min_kmh": min(r["speed_kmh"] for r in ref_samples),
            "latest_min_kmh": min(r["speed_kmh"] for r in cur_samples),
            "has_gap": any(a < end and b > start for a, b, _ in reference.gaps + current.gaps),
        })
    return sections


def coaching_notes(laps):
    """Deterministic coaching rules based on measurements, not an AI model."""
    if not laps or any(not lap.coaching_eligible for lap in laps):
        return ("No coaching: invalid or unreviewed laps are not eligible. "
                "Review the game's lap validity using --list-laps and manual markers.")
    lines = ["AC Racing Coach - measured lap feedback", ""]
    if len(laps) < 2:
        lines += [f"Recorded lap: {time_label(laps[0].seconds)}.",
                  "A second matching full timed lap is needed to compare gains and losses."]
    else:
        reference, current = laps
        difference = current.seconds - reference.seconds
        direction = "faster" if difference < 0 else "slower"
        lines += [f"Reference lap: {time_label(reference.seconds)}",
                  f"Latest lap: {time_label(current.seconds)}",
                  (f"Latest lap is {abs(difference):.3f} seconds {direction}." if abs(difference) >= .0005
                   else "Both laps have the same recorded time."), ""]
        sections = section_comparison(reference, current)
        usable = [s for s in sections if not s["has_gap"]]
        gains = sorted((s for s in usable if s["delta_s"] < -.05), key=lambda s: s["delta_s"])[:2]
        losses = sorted((s for s in usable if s["delta_s"] > .05), key=lambda s: -s["delta_s"])
        if gains:
            lines.append("Largest gains in sections with continuous data:")
            for s in gains:
                lines += [f"  {s['label']} of the track: {abs(s['delta_s']):.3f} seconds gained.",
                          f"  Minimum recorded speed: {s['latest_min_kmh']:.1f} km/h latest; "
                          f"{s['reference_min_kmh']:.1f} km/h reference."]
            lines.append("")
        if losses:
            s = losses[0]
            lines += [f"Next practice focus: {s['label']} of the track.",
                      f"The latest lap lost {s['delta_s']:.3f} seconds in this section.",
                      f"Minimum recorded speed: {s['latest_min_kmh']:.1f} km/h latest; "
                      f"{s['reference_min_kmh']:.1f} km/h reference.",
                      "Compare the throttle and brake traces here. Use the faster recorded",
                      "pass through this section as a practice target, with consistent setup and conditions.", ""]
        elif usable:
            lines += ["No section with continuous data lost more than 0.05 seconds.",
                      "Focus on repeating the improvement across another complete lap.", ""]
        else:
            lines += ["There is not enough continuous shared coverage for a practice recommendation.", ""]
    for index, lap in enumerate(laps):
        label = ("Reference" if index == 0 else "Latest") if len(laps) == 2 else "Recorded lap"
        for start, end, seconds in lap.gaps:
            lines.append(f"Data gap: {label}, {start * 100:.2f}-{end * 100:.2f}% of the track, {seconds:.3f} seconds.")
    if any(lap.gaps for lap in laps):
        lines += ["The graph breaks across this gap. Affected sections are excluded from coaching suggestions.", ""]
    lines += ["Section times are estimates at matching track positions, over shared recorded coverage.",
              "These measurements show differences; they do not establish the cause.",
              "Track limits, collisions, and setup equivalence are not verified.", "",
              "Sources: " + "; ".join(dict.fromkeys(lap.source.name for lap in laps))]
    lines += ["Validity: manually marked valid by the driver; not automatically verified by AC telemetry."]
    return "\n".join(lines)



