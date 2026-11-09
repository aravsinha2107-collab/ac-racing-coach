"""Desktop review/selection checks using copies of the original recordings."""

from dataclasses import FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from types import MappingProxyType
import unittest
from unittest.mock import patch

from ac_racing_coach import analysis as a
from ac_racing_coach import review as r
from ac_racing_coach.validity import marker_path, save_review


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "recordings/session_20260913_213429_162345.csv"


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "session.csv"
        shutil.copyfile(FIXTURE, self.source)
        self.original_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.snapshot = r.open_recording(self.source)

    def approved(self):
        return r.review_laps(self.snapshot, {1: "manual_valid", 2: "manual_valid"})

    def test_legacy_open_lists_all_laps_without_approving_any(self):
        self.assertEqual([lap.number for lap in self.snapshot.laps], [1, 2])
        self.assertEqual([lap.seconds for lap in self.snapshot.laps], [105.749, 102.913])
        self.assertEqual([lap.validity for lap in self.snapshot.laps], ["unknown", "unknown"])
        self.assertTrue(self.snapshot.excluded)
        self.assertIsNone(self.snapshot.sidecar_sha256)
        self.assertFalse(marker_path(self.source).exists())
        with self.assertRaisesRegex(ValueError, "manually approved"):
            r.select_comparison(self.snapshot, 1, 2)

    def test_reviews_persist_and_never_modify_csv(self):
        updated = r.review_laps(self.snapshot, {1: "manual_valid", 2: "invalid"})
        reopened = r.open_recording(self.source)
        self.assertEqual([lap.validity for lap in reopened.laps], ["manual_valid", "invalid"])
        self.assertEqual(updated.sidecar_sha256, reopened.sidecar_sha256)
        data = json.loads(marker_path(self.source).read_text(encoding="utf-8"))
        self.assertEqual(data["csv_sha256"], self.original_hash)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.original_hash)
        # The evidence in an already displayed snapshot never changes in-place.
        self.assertEqual(self.snapshot.laps[0].validity, "unknown")

    def test_invalid_and_unknown_rejected_as_reference_and_target(self):
        for state in ("invalid", "unknown"):
            for affected in (1, 2):
                with self.subTest(state=state, affected=affected):
                    snapshot = r.open_recording(self.source)
                    decisions = {1: "manual_valid", 2: "manual_valid"}
                    decisions[affected] = state
                    snapshot = r.review_laps(snapshot, decisions)
                    with self.assertRaisesRegex(ValueError, "manually approved"):
                        r.select_comparison(snapshot, 1, 2)
                    self.assertIn("No coaching:", a.coaching_notes(snapshot.laps))
                    self.assertEqual(a.section_comparison(*snapshot.laps), [])

    def test_selected_approved_laps_keep_times_and_excluded_gap(self):
        snapshot = self.approved()
        selected = r.select_comparison(snapshot, 1, 2)
        self.assertIs(selected[0], snapshot.laps[0])
        self.assertIs(selected[1], snapshot.laps[1])
        self.assertEqual([lap.seconds for lap in selected], [105.749, 102.913])
        self.assertEqual(f"{selected[0].gaps[0][2]:.3f}", "0.267")
        self.assertIn("0.267 seconds", a.coaching_notes(selected))
        affected = [section for section in a.section_comparison(*selected) if section["has_gap"]]
        self.assertTrue(affected)
        for section in affected:
            section["delta_s"] = 999
        with patch.object(a, "section_comparison", return_value=affected):
            notes = a.coaching_notes(selected)
        self.assertNotIn("Next practice focus:", notes)
        self.assertIn("not enough continuous shared coverage", notes)

    def test_same_lap_reverse_order_and_bad_numbers_rejected(self):
        snapshot = self.approved()
        with self.assertRaisesRegex(ValueError, "different"):
            r.select_comparison(snapshot, 1, 1)
        with self.assertRaisesRegex(ValueError, "earlier"):
            r.select_comparison(snapshot, 2, 1)
        for pair in ((0, 2), (1, 3), (True, 2), (1, None)):
            with self.subTest(pair=pair), self.assertRaises(ValueError):
                r.select_comparison(snapshot, *pair)

    def test_mismatched_metadata_rejected(self):
        snapshot = self.approved()
        for key, value in (("car_model", "other_car"), ("track", "other_track"),
                           ("track_configuration", "other_configuration"),
                           ("tyre_compound", "other_tyres"), ("track_length_m", 1.0),
                           ("session_type", -1)):
            with self.subTest(key=key):
                target = snapshot.laps[1]
                rows = (MappingProxyType(dict(target.rows[0], **{key: value})),) + target.rows[1:]
                changed = replace(snapshot, laps=(snapshot.laps[0], replace(target, rows=rows)))
                with self.assertRaisesRegex(ValueError, "matching"):
                    r.select_comparison(changed, 1, 2)

    def test_clear_review_removes_eligibility(self):
        approved = self.approved()
        updated = r.review_laps(approved, {2: "unknown"})
        self.assertEqual(updated.laps[1].validity, "unknown")
        with self.assertRaises(ValueError):
            r.select_comparison(updated, 1, 2)

    def test_snapshot_and_lap_rows_are_immutable(self):
        with self.assertRaises(FrozenInstanceError):
            self.snapshot.csv_sha256 = "different"
        with self.assertRaises(FrozenInstanceError):
            self.snapshot.laps[0].seconds = 1
        with self.assertRaises(TypeError):
            self.snapshot.laps[0].rows[0]["lap_validity"] = "manual_valid"

    def test_external_csv_change_blocks_review_and_selection(self):
        snapshot = self.approved()
        sidecar_before = marker_path(self.source).read_bytes()
        with self.source.open("a", encoding="utf-8") as stream:
            stream.write("\n")
        with self.assertRaisesRegex(r.StaleRecordingError, "changed"):
            r.review_laps(snapshot, {2: "manual_valid"})
        with self.assertRaisesRegex(r.StaleRecordingError, "changed"):
            r.select_comparison(snapshot, 1, 2)
        self.assertEqual(marker_path(self.source).read_bytes(), sidecar_before)

    def test_external_sidecar_change_blocks_review_and_selection(self):
        snapshot = self.approved()
        save_review(self.source, snapshot.laps, {2: "invalid"})
        sidecar_before = marker_path(self.source).read_bytes()
        with self.assertRaisesRegex(r.StaleRecordingError, "changed"):
            r.select_comparison(snapshot, 1, 2)
        with self.assertRaisesRegex(r.StaleRecordingError, "changed"):
            r.review_laps(snapshot, {2: "manual_valid"})
        self.assertEqual(marker_path(self.source).read_bytes(), sidecar_before)

    def test_new_or_removed_sidecar_is_a_stale_snapshot(self):
        approved = self.approved()
        with self.assertRaises(r.StaleRecordingError):
            r.review_laps(self.snapshot, {1: "invalid"})
        marker_path(self.source).unlink()
        with self.assertRaises(r.StaleRecordingError):
            r.select_comparison(approved, 1, 2)

    def test_change_during_loading_is_rejected(self):
        original_find = r.find_complete_laps

        def change_after_parse(rows, source):
            result = original_find(rows, source)
            save_review(source, result[0], {1: "invalid"})
            return result

        with patch.object(r, "find_complete_laps", side_effect=change_after_parse):
            with self.assertRaisesRegex(r.StaleRecordingError, "changed"):
                r.open_recording(self.source)

    def test_change_just_before_saving_is_rejected(self):
        real_save = save_review

        def change_then_save(source, laps, decisions, **kwargs):
            real_save(source, laps, {1: "invalid"})
            return real_save(source, laps, decisions, **kwargs)

        with patch.object(r, "save_review", side_effect=change_then_save):
            with self.assertRaisesRegex(ValueError, "changed"):
                r.review_laps(self.snapshot, {1: "manual_valid"})
        self.assertEqual(r.open_recording(self.source).laps[0].validity, "invalid")

    def test_csv_change_just_before_saving_cannot_bind_old_rows_to_new_data(self):
        real_save = save_review

        def change_then_save(source, laps, decisions, **kwargs):
            with source.open("a", encoding="utf-8") as stream:
                stream.write("\n")
            return real_save(source, laps, decisions, **kwargs)

        with patch.object(r, "save_review", side_effect=change_then_save):
            with self.assertRaisesRegex(ValueError, "changed"):
                r.review_laps(self.snapshot, {1: "manual_valid"})
        self.assertFalse(marker_path(self.source).exists())

    def test_malformed_sidecar_and_invalid_review_requests_fail_closed(self):
        for decisions in ({}, {0: "manual_valid"}, {3: "invalid"}, {1: "automatic_valid"}):
            with self.subTest(decisions=decisions), self.assertRaises(ValueError):
                r.review_laps(self.snapshot, decisions)
        self.assertFalse(marker_path(self.source).exists())
        marker_path(self.source).write_text("{bad json", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "invalid or stale"):
            r.open_recording(self.source)


if __name__ == "__main__":
    unittest.main()
