"""Read-only regression checks against the driver's original recordings."""

import csv
import io
from pathlib import Path
import unittest

from ac_racing_coach import analysis as a
from ac_racing_coach.recording import FIELDS
from ac_racing_coach.session import SessionTimeline

ROOT = Path(__file__).resolve().parents[1]
RECORDING = ROOT / "recordings/session_20260913_213429_162345.csv"


class RecordingRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = a.load_csv(RECORDING)
        cls.laps, cls.excluded = a.find_complete_laps(cls.rows, RECORDING)

    def test_confirmed_times_and_gap(self):
        self.assertEqual([lap.seconds for lap in self.laps], [105.749, 102.913])
        self.assertEqual(len(self.laps[0].gaps), 1)
        self.assertAlmostEqual(self.laps[0].gaps[0][2], .267346, places=6)
        self.assertEqual(f"{self.laps[0].gaps[0][2]:.3f}", "0.267")
        self.assertEqual(self.laps[1].gaps, [])

    def test_feedback_matches_saved_report(self):
        from copy import deepcopy
        reviewed = deepcopy(self.laps)
        for lap in reviewed:
            for row in lap.rows:
                row["lap_validity"] = "manual_valid"
        expected = (ROOT / "analysis/session_20260913_213429_162345_lap_analysis_feedback.txt").read_text(encoding="utf-8")
        notes = a.coaching_notes(a.choose_laps(reviewed))
        self.assertEqual(notes.split("\nValidity:")[0].strip(), expected.strip())

    def test_gap_is_not_interpolated_or_recommended(self):
        from copy import deepcopy
        reviewed = deepcopy(self.laps)
        for lap in reviewed:
            for row in lap.rows:
                row["lap_validity"] = "manual_valid"
        reference, current = reviewed
        left, right, _ = reference.gaps[0]
        self.assertIsNone(a.value_at(a.increasing_trace(reference, "speed_kmh"), reference.gaps, (left + right) / 2))
        affected = [s for s in a.section_comparison(reference, current) if s["has_gap"]]
        self.assertTrue(affected)
        # Make the affected section an extreme apparent loss. It must still be
        # excluded, rather than passing only because it was not the largest loss.
        from unittest.mock import patch
        for section in affected:
            section["delta_s"] = 999
        with patch.object(a, "section_comparison", return_value=affected):
            notes = a.coaching_notes(reviewed)
        self.assertNotIn("Next practice focus:", notes)
        self.assertIn("not enough continuous shared coverage", notes)

    def test_confirmation_events_and_missing_finish(self):
        events = a.confirmed_lap_events(self.rows, RECORDING, session_id="test-session")
        self.assertEqual([e.lap_time_s for e in events], [105.749, 102.913])
        for lap, event in zip(self.laps, events):
            self.assertEqual(event.session_id, "test-session")
            self.assertEqual(event.first_row, lap.first_row)
            self.assertGreaterEqual(event.confirmed_at_capture_time_s, event.finish_capture_time_s)
            self.assertGreater(event.confirmed_at_capture_time_s, lap.rows[-1]["capture_time_s"])
            before_confirmation = [r for r in self.rows if r["capture_time_s"] < event.confirmed_at_capture_time_s]
            self.assertFalse(any(e.first_row == event.first_row for e in a.confirmed_lap_events(before_confirmation, RECORDING)))

    def test_pause_and_large_gap_reject_affected_lap(self):
        for offset in (.2, 1.0):
            rows = [dict(r) for r in self.rows]
            middle = (self.laps[0].first_row + self.laps[0].last_row) // 2 - 2
            for row in rows[middle:]:
                row["capture_time_s"] += offset
                row["capture_segment"] += 1
            laps, _ = a.find_complete_laps(rows, RECORDING)
            self.assertNotIn(105.749, [lap.seconds for lap in laps])
            self.assertIn(102.913, [lap.seconds for lap in laps])

    def test_all_existing_csvs_load(self):
        paths = list((ROOT / "recordings").glob("*.csv"))
        self.assertGreaterEqual(len(paths), 4)
        for path in paths:
            with self.subTest(path=path.name):
                a.find_complete_laps(a.load_csv(path), path)

    def test_shared_timeline_does_not_reset_for_laps_or_pauses(self):
        timeline = SessionTimeline(session_id="shared", origin_monotonic_s=100)
        self.assertEqual(timeline.elapsed(100.25), .25)
        self.assertEqual(timeline.elapsed(210), 110)
        self.assertEqual(timeline.session_id, "shared")

    def test_recorder_uses_supplied_timeline_and_keeps_csv_fields(self):
        import struct
        from unittest.mock import Mock, patch
        from ac_racing_coach import recording as r
        physics = Mock()
        graphics = Mock()
        physics.stable_read.side_effect = [struct.pack("<i", n) for n in (1, 2, 3)]
        pages = [struct.pack("<ii", n, status) for n, status in ((1, 2), (2, 2), (3, 0))]
        graphics.stable_read.side_effect = pages
        graphics.read.side_effect = pages
        sample = {k: v for k, v in self.rows[0].items() if k in FIELDS
                  and k not in ("capture_time_s", "capture_segment", "session_id")}
        sample.update(physics_packet=2, graphics_packet=2)
        output = io.StringIO()
        timeline = SessionTimeline(session_id="shared", origin_monotonic_s=99)
        with patch.object(r.time, "sleep"), patch.object(r.time, "monotonic", side_effect=[100, 100.02, 100.04, 100.06]), patch.object(r, "parse_sample", return_value=sample):
            count = r.record(physics, graphics, {}, output, timeline=timeline, report=lambda *args, **kwargs: None)
        saved = list(csv.DictReader(io.StringIO(output.getvalue())))
        self.assertEqual(count, 1)
        self.assertEqual(saved[0]["session_id"], "shared")
        self.assertEqual(saved[0]["recording_format_version"], "2")
        self.assertEqual(saved[0]["lap_validity"], "unknown")
        self.assertEqual(float(saved[0]["capture_time_s"]), 1.04)
        self.assertEqual(set(saved[0]), set(FIELDS))

    def test_versioned_csv_remains_readable(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            path = Path(directory) / "versioned.csv"
            with RECORDING.open(newline="", encoding="utf-8") as source, path.open("w", newline="", encoding="utf-8") as target:
                writer = csv.DictWriter(target, fieldnames=FIELDS)
                writer.writeheader()
                for row in csv.DictReader(source):
                    writer.writerow(dict(row, recording_format_version=1, session_id="new-session"))
            events = a.confirmed_lap_events(a.load_csv(path), path)
            self.assertEqual([e.lap_time_s for e in events], [105.749, 102.913])
            self.assertTrue(all(e.session_id == "new-session" for e in events))


if __name__ == "__main__":
    unittest.main()
