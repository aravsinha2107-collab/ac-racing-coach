import csv
from copy import deepcopy
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from ac_racing_coach import analysis as a
from ac_racing_coach.validity import save_review, marker_path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "recordings/session_20260913_213429_162345.csv"


class ValidityTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "session.csv"
        shutil.copyfile(FIXTURE, self.source)

    def laps(self):
        return a.find_complete_laps(a.load_csv(self.source), self.source)[0]

    def test_legacy_unknown_is_not_valid_even_with_zero_penalty(self):
        laps = self.laps()
        self.assertEqual([lap.seconds for lap in laps], [105.749, 102.913])
        self.assertTrue(all(r["penalty_time_s"] == 0 for lap in laps for r in lap.rows))
        self.assertTrue(all(lap.validity == "unknown" for lap in laps))
        self.assertEqual(a.choose_laps(laps), [])
        self.assertIn("No coaching:", a.coaching_notes(laps))

    def test_persistent_invalid_never_reference_or_coaching_target(self):
        original_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        save_review(self.source, self.laps(), {1: "manual_valid", 2: "invalid"})
        laps = self.laps()
        self.assertEqual([lap.validity for lap in laps], ["manual_valid", "invalid"])
        self.assertEqual(laps[1].completion.lap_validity, "invalid")
        self.assertEqual(a.choose_laps(laps), [laps[0]])
        self.assertEqual(a.choose_laps(list(reversed(laps))), [laps[0]])
        self.assertIn("No coaching:", a.coaching_notes(laps))
        self.assertEqual(a.section_comparison(*laps), [])
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), original_hash)

    def test_clean_invalid_clean_selects_only_clean_laps(self):
        # Synthetic third lap for selection regression, not a claimed real drive.
        first, invalid = deepcopy(self.laps())
        third = deepcopy(first)
        for lap, state in ((first, "manual_valid"), (invalid, "invalid"), (third, "manual_valid")):
            for row in lap.rows:
                row["lap_validity"] = state
        selected = a.choose_laps([first, invalid, third])
        self.assertIs(selected[0], first)
        self.assertIs(selected[1], third)
        self.assertNotIn("1:42.913", a.coaching_notes(selected))

    def test_single_invalid_sample_latches_for_whole_lap(self):
        laps = self.laps()
        for row in laps[0].rows:
            row["lap_validity"] = "manual_valid"
        laps[0].rows[-5]["lap_validity"] = "invalid"
        self.assertEqual(laps[0].validity, "invalid")
        self.assertEqual(a.choose_laps(laps), [])

    def test_invalid_raw_field_cannot_be_overridden(self):
        with self.source.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            fields, rows = reader.fieldnames, list(reader)
        with self.source.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields + ["lap_validity"])
            writer.writeheader()
            for row in rows:
                writer.writerow(dict(row, lap_validity="invalid"))
        save_review(self.source, self.laps(), {1: "manual_valid"})
        self.assertEqual(self.laps()[0].validity, "invalid")

    def test_changed_csv_or_malformed_review_fails_closed(self):
        save_review(self.source, self.laps(), {1: "manual_valid"})
        with self.source.open("a") as stream:
            stream.write("\n")
        with self.assertRaisesRegex(ValueError, "stale"):
            self.laps()
        marker_path(self.source).write_text("{bad json", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.laps()

    def test_clear_review_and_invalid_lap_number(self):
        save_review(self.source, self.laps(), {1: "manual_valid"})
        save_review(self.source, self.laps(), {1: "unknown"})
        self.assertEqual(self.laps()[0].validity, "unknown")
        with self.assertRaises(ValueError):
            save_review(self.source, self.laps(), {3: "invalid"})

    def test_invalid_marker_does_not_spread_to_reused_counter(self):
        save_review(self.source, self.laps(), {1: "invalid", 2: "manual_valid"})
        first, second = self.laps()
        # Selection and marker identity must not depend on an assumed unique counter.
        for row in second.rows:
            row["completed_laps"] = first.rows[0]["completed_laps"]
        self.assertEqual(a.choose_laps([first, second]), [second])

    def test_runtime_version_strings_use_original_ac_static_offsets(self):
        from ac_racing_coach.recording import parse_metadata, STATIC_SIZE
        data = bytearray(STATIC_SIZE)
        for offset, value in ((0, "1.7"), (30, "1.16.4"), (68, "test_car"), (134, "monza")):
            encoded = value.encode("utf-16-le")
            data[offset:offset + len(encoded)] = encoded
        metadata = parse_metadata(data)
        self.assertEqual(metadata["ac_version"], "1.16.4")
        self.assertEqual(metadata["shared_memory_version"], "1.7")

    def test_cli_review_and_analysis(self):
        def run(*args):
            return subprocess.run([sys.executable, "-B", str(ROOT / "analyze_session.py"), str(self.source), *args], cwd=ROOT, capture_output=True, text=True)
        result = run("--list-laps")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Review lap 2: unknown", result.stdout)
        result = run("--mark-invalid", "2", "--mark-valid", "1", "--list-laps")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Review lap 2: invalid", result.stdout)
        result = run("--no-open", "--output", str(Path(self.temp.name) / "plot.png"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        notes = (Path(self.temp.name) / "plot_feedback.txt").read_text(encoding="utf-8")
        self.assertNotIn("1:42.913", notes)
        self.assertNotIn("Next practice focus", notes)


if __name__ == "__main__":
    unittest.main()
