"""Synthetic timing/safety tests; no simulator, audio device or recording edits."""

from dataclasses import replace
import math
from pathlib import Path
import tempfile
import unittest

from ac_racing_coach.corner_profile import (
    CornerProfile, calibrate_profile, load_profile, save_profile,
)
from ac_racing_coach.live_coaching import LiveCoach, TIMING_TEST_CUE
from ac_racing_coach.session import TelemetryFrame


class FakeSpeech:
    duration_s = 3.0

    def __init__(self):
        self.active = False
        self.calls = []
        self.cancel_count = 0
        self.busy = False

    def speak(self, text):
        self.calls.append(text)
        if self.busy or self.active:
            return False
        self.active = True
        return True

    def cancel(self):
        self.cancel_count += 1
        self.active = False


class LiveCoachTests(unittest.TestCase):
    def setUp(self):
        # Synthetic coordinate inside the known section, never a shipped target.
        self.profile = CornerProfile(corner_position=0.155, calibration_source="driver-attested")
        self.speech = FakeSpeech()
        self.events = []
        self.coach = LiveCoach(self.profile, self.speech, self.events.append)
        self.coach.set_enabled(True)
        self.metadata = {"track": "monza", "track_configuration": "", "track_length_m": 5755.02}

    def frame(self, t, remaining_m, speed=180.0, *, laps=0, **changes):
        position = (self.profile.corner_position - remaining_m / self.profile.track_length_m) % 1
        frame = TelemetryFrame("session-a", t, 2, True, self.metadata,
                               {"track_position": position, "speed_kmh": speed,
                                "completed_laps": laps, "in_pit": 0, "in_pit_lane": 0})
        return replace(frame, **changes)

    def drive(self, start=750, end=200, *, speed=180.0, t=0.0, session_id="session-a", segment=0):
        step = speed / 3.6 * 0.1
        origin = self.profile.corner_position * self.profile.track_length_m - start
        initial_lap = math.floor(origin / self.profile.track_length_m)
        frames = []
        count = math.ceil((start - end) / step) + 1
        for index in range(count):
            remaining = start - index * step
            lap = math.floor((origin + index * step) / self.profile.track_length_m) - initial_lap
            frame = self.frame(t + index * 0.1, remaining, speed, laps=lap,
                               session_id=session_id, segment=segment)
            self.coach.update(frame, frame.capture_time_s)
            frames.append(frame)
        return frames[-1]

    def cue_events(self):
        return [event for event in self.events if event["event"] == "cue"]

    def test_normal_approach_uses_measured_speech_duration_and_lead(self):
        self.drive()
        self.assertEqual(self.speech.calls, [TIMING_TEST_CUE])
        event = self.cue_events()[0]
        self.assertTrue(event["generic_timing_test"])
        self.assertAlmostEqual(event["speech_duration_s"], 3)
        self.assertLessEqual(event["estimated_arrival_s"], 6.35)
        self.assertGreaterEqual(event["estimated_finish_margin_s"], 2.8)

    def test_no_duplicate_for_same_frames_or_continued_approach(self):
        last = self.drive(end=10)
        for _ in range(20):
            self.coach.update(last, last.capture_time_s)
        self.assertEqual(len(self.speech.calls), 1)

    def test_start_or_restart_near_corner_never_speaks(self):
        self.drive(start=250, end=20)
        self.assertFalse(self.speech.calls)
        self.assertTrue(any(event.get("reason") == "near_corner" for event in self.events))
        self.drive(start=200, end=10, t=10, session_id="restart")
        self.assertFalse(self.speech.calls)

    def test_late_speed_change_skips_without_backlog(self):
        last = self.drive(start=550, end=350, speed=90)
        late = self.frame(last.capture_time_s + 0.1, 343, speed=300)
        self.coach.update(late, late.capture_time_s)
        self.assertIn("too late", self.coach.status)
        self.assertFalse(self.speech.calls)
        self.assertTrue(any(event.get("reason") == "too_late" for event in self.events))

    def test_pause_discards_armed_approach_and_resume_near_corner_skips(self):
        last = self.drive(end=400)
        paused = replace(last, status=3, fresh=False, capture_time_s=last.capture_time_s + 0.1)
        self.coach.update(paused, paused.capture_time_s)
        self.assertIn("paused", self.coach.status)
        self.drive(start=250, end=10, t=paused.capture_time_s + 1)
        self.assertFalse(self.speech.calls)

    def test_guard_states_cancel_active_speech(self):
        for guard in ("pause", "replay", "ended", "pits", "pit_lane", "stale", "old", "unsupported", "layout"):
            with self.subTest(guard=guard):
                self.setUp()
                last = self.drive()
                self.assertTrue(self.speech.active)
                sample = dict(last.sample)
                changes = {"capture_time_s": last.capture_time_s + 0.1}
                now = changes["capture_time_s"]
                if guard in ("pause", "replay", "ended"):
                    changes.update(status={"pause": 3, "replay": 1, "ended": 0}[guard], fresh=False)
                elif guard in ("pits", "pit_lane"):
                    sample["in_pit" if guard == "pits" else "in_pit_lane"] = 1
                    changes["sample"] = sample
                elif guard == "stale":
                    changes["fresh"] = False
                elif guard == "old":
                    now += 0.3
                else:
                    metadata = dict(last.metadata)
                    metadata["track" if guard == "unsupported" else "track_configuration"] = "other"
                    changes["metadata"] = metadata
                self.coach.update(replace(last, **changes), now)
                self.assertFalse(self.speech.active)
                self.assertEqual(len(self.speech.calls), 1)

    def test_stale_repeated_gui_poll_cancels_and_logs_once(self):
        last = self.drive()
        self.coach.update(last, last.capture_time_s + 0.3)
        cancels = self.speech.cancel_count
        self.coach.update(last, last.capture_time_s + 0.4)
        self.assertFalse(self.speech.active)
        self.assertEqual(self.speech.cancel_count, cancels)
        self.assertEqual(sum(event.get("reason") == "stale" for event in self.events), 1)

    def test_finish_wrap_requires_lap_counter_and_preserves_approach(self):
        self.drive(start=1200, end=100)
        self.assertEqual(len(self.speech.calls), 1)
        self.assertFalse(any(event.get("reason") in ("teleport", "counter_reset") for event in self.events))

    def test_finish_wrap_without_counter_is_a_discontinuity(self):
        self.coach.update(self.frame(0, 895), 0)
        self.coach.update(self.frame(0.1, 890), 0.1)
        self.assertIn("reset or teleport", self.coach.status)
        self.assertFalse(self.speech.calls)

    def test_teleport_forward_and_reverse_cancel_and_require_new_approach(self):
        for remaining in (100, 600):
            with self.subTest(remaining=remaining):
                self.setUp()
                last = self.drive()
                self.coach.update(self.frame(last.capture_time_s + 0.1, remaining), last.capture_time_s + 0.1)
                self.assertFalse(self.speech.active)
                self.assertIn("teleport", self.coach.status)
                self.assertEqual(len(self.speech.calls), 1)

    def test_segment_and_session_reset_cancel_and_prevent_restart_duplicate(self):
        for changes in ({"segment": 1}, {"session_id": "new-session"}):
            with self.subTest(changes=changes):
                self.setUp()
                last = self.drive()
                reset = replace(last, capture_time_s=last.capture_time_s + 0.1, **changes)
                self.coach.update(reset, reset.capture_time_s)
                self.assertFalse(self.speech.active)
                self.coach.update(replace(reset, capture_time_s=reset.capture_time_s + 0.1), reset.capture_time_s + 0.1)
                self.assertEqual(len(self.speech.calls), 1)

    def test_mute_disable_and_close_cancel_active_and_pending_output(self):
        for action in ("mute", "disable", "close"):
            with self.subTest(action=action):
                self.setUp()
                last = self.drive()
                if action == "mute":
                    self.coach.set_muted(True)
                elif action == "disable":
                    self.coach.set_enabled(False)
                else:
                    self.coach.close()
                self.assertFalse(self.speech.active)
                self.coach.update(last, last.capture_time_s)
                self.assertEqual(len(self.speech.calls), 1)

    def test_unmute_near_corner_does_not_replay_cue(self):
        last = self.drive(end=400)
        self.coach.set_muted(True)
        self.coach.set_muted(False)
        self.drive(start=250, end=10, t=last.capture_time_s + 1)
        self.assertFalse(self.speech.calls)

    def test_busy_speech_is_skipped_and_never_queued(self):
        self.speech.busy = True
        self.drive(end=10)
        self.speech.busy = False
        self.assertEqual(len(self.speech.calls), 1)
        self.assertFalse(self.cue_events())
        self.assertTrue(any(event.get("reason") == "speech_busy" for event in self.events))

    def test_second_genuine_lap_can_cue_again(self):
        last = self.drive(end=-20)
        self.speech.active = False
        self.drive(start=self.profile.track_length_m - 25, end=100, t=last.capture_time_s + 0.1)
        self.assertEqual(len(self.speech.calls), 2)

    def test_failures_in_speech_and_logging_stay_isolated(self):
        def fail(*args):
            raise RuntimeError("failed sink")
        self.speech.speak = fail
        self.coach.log = fail
        self.drive()
        self.assertFalse(self.cue_events())

    def test_missing_voice_duration_and_uncalibrated_profile_are_safe(self):
        self.speech.duration_s = math.nan
        self.drive()
        self.assertFalse(self.speech.calls)
        self.coach.replace_profile(CornerProfile())
        self.coach.update(self.frame(20, 100), 20)
        self.assertIn("Calibrate", self.coach.status)

    def test_configurable_lead_changes_cue_onset(self):
        self.coach.replace_profile(replace(self.profile, lead_time_s=5))
        self.drive()
        self.assertAlmostEqual(self.cue_events()[0]["configured_finish_lead_s"], 5)
        self.assertGreater(self.cue_events()[0]["distance_m"], 400)

    def test_telemetry_delivery_age_is_subtracted_from_arrival_estimate(self):
        last = self.drive(end=325)
        frame = self.frame(last.capture_time_s + .1, 320)
        self.coach.update(frame, frame.capture_time_s + .1)
        self.assertEqual(len(self.speech.calls), 1)
        self.assertAlmostEqual(self.cue_events()[0]["estimated_arrival_s"], 6.3)
        self.assertAlmostEqual(self.cue_events()[0]["telemetry_age_s"], .1)

    def test_slow_or_stationary_updates_cannot_establish_an_approach(self):
        self.drive(start=600, end=400, speed=20)
        self.assertFalse(self.speech.calls)
        self.assertIn("moving approach", self.coach.status)

    def test_lap_counter_reset_cancels_active_speech(self):
        last = self.drive(start=1200, end=200)
        self.assertEqual(last.sample["completed_laps"], 1)
        frame = self.frame(last.capture_time_s + .1, 195, laps=0)
        self.coach.update(frame, frame.capture_time_s)
        self.assertFalse(self.speech.active)
        self.assertIn("reset or teleport", self.coach.status)


class CornerProfileTests(unittest.TestCase):
    def frame(self, **changes):
        profile = CornerProfile()
        metadata = {"track": "monza", "track_configuration": "", "track_length_m": profile.track_length_m}
        frame = TelemetryFrame("calibration", 5, 2, True, metadata,
                               {"track_position": 0.151, "speed_kmh": 0, "in_pit": 0, "in_pit_lane": 0})
        return replace(frame, **changes)

    def test_shipped_profile_has_no_invented_corner_coordinate(self):
        profile = load_profile()
        self.assertFalse(profile.calibrated)
        self.assertIsNone(profile.corner_position)

    def test_driver_calibration_roundtrip_and_generic_configuration(self):
        calibrated = calibrate_profile(CornerProfile(), self.frame(), 5.1)
        self.assertEqual(calibrated.corner_position, 0.151)
        self.assertEqual(calibrated.calibration_source, "driver-attested")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            save_profile(calibrated, path)
            self.assertEqual(load_profile(path), calibrated)

    def test_calibration_rejects_wrong_layout_motion_pits_stale_wrong_section(self):
        base = self.frame()
        cases = [replace(base, status=3), replace(base, fresh=False), replace(base, capture_time_s=4),
                 replace(base, metadata={**base.metadata, "track_configuration": "junior"}),
                 replace(base, metadata={**base.metadata, "track_length_m": 5700}),
                 replace(base, sample={**base.sample, "speed_kmh": 4}),
                 replace(base, sample={**base.sample, "in_pit_lane": 1}),
                 replace(base, sample={**base.sample, "track_position": 0.7}),
                 replace(base, sample={**base.sample, "track_position": math.nan})]
        for frame in cases:
            with self.subTest(frame=frame), self.assertRaises(ValueError):
                calibrate_profile(CornerProfile(), frame, 5)

    def test_profile_rejects_untrusted_coordinate_and_bad_config(self):
        for kwargs in ({"corner_position": .151}, {"lead_time_s": 0}, {"lead_time_s": math.inf},
                       {"track": "monza66"}, {"calibration_min_position": 0},
                       {"corner_position": .9, "calibration_source": "driver-attested"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                CornerProfile(**kwargs)


if __name__ == "__main__":
    unittest.main()
