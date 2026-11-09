"""Live-service isolation checks without AC, native audio, or original recordings."""

from dataclasses import replace
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

from ac_racing_coach.live_runtime import LatestTelemetry, LiveRuntime, SpeechGate
from ac_racing_coach.session import TelemetryFrame
from ac_racing_coach.speech import CUE_TEXT


ROOT = Path(__file__).resolve().parents[1]


class FakeSpeech:
    duration_s = 4.0
    voice_name = "Silent test voice"

    def __init__(self, *, blocked=False, failure=False):
        self.prepare_entered = threading.Event()
        self.release_prepare = threading.Event()
        if not blocked:
            self.release_prepare.set()
        self.failure = failure
        self.ready = False
        self.error = "Injected voice failure" if failure else None
        self.spoken = []
        self.cancels = 0
        self.busy = False
        self.closed = threading.Event()

    def prepare(self):
        self.prepare_entered.set()
        if not self.release_prepare.wait(3):
            raise RuntimeError("Test preparation was not released")
        self.ready = not self.failure
        return self.ready

    def speak(self, text):
        if not self.ready or self.closed.is_set() or self.busy:
            return False
        self.spoken.append(text)
        self.busy = True
        return True

    def cancel(self):
        self.cancels += 1
        self.busy = False

    def close(self):
        self.cancel()
        self.closed.set()


class FakeCapture:
    def __init__(self):
        self.runtime = None
        self.started = threading.Event()
        self.end_session = threading.Event()
        self.finished = threading.Event()
        self.ticks = 0
        self.starts = 0

    def __call__(self):
        self.starts += 1
        self.started.set()
        try:
            while not self.runtime.capture_stop.wait(0.005):
                self.ticks += 1
                if self.end_session.is_set():
                    break
        finally:
            self.finished.set()


class LiveRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "profiles").mkdir()
        shutil.copyfile(ROOT / "profiles/monza_first_chicane.json",
                        self.root / "profiles/monza_first_chicane.json")

    def wait_for(self, condition, message="background condition", timeout=2):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(condition(), message)

    def runtime(self, speech=None):
        speech = speech or FakeSpeech()
        capture = FakeCapture()
        runtime = LiveRuntime(root=self.root, speech_factory=lambda: speech,
                              capture_runner=capture)
        capture.runtime = runtime
        def cleanup():
            runtime.close()
            speech.release_prepare.set()
            runtime.worker.join(3)
            if runtime.capture_thread:
                runtime.capture_thread.join(3)
            self.assertTrue(runtime.finished, "Test left a service worker running")
        self.addCleanup(cleanup)
        self.assertTrue(speech.prepare_entered.wait(2))
        return runtime, speech, capture

    def test_slow_voice_preparation_does_not_block_capture_or_controls(self):
        runtime, speech, capture = self.runtime(FakeSpeech(blocked=True))
        runtime.set_enabled(True)
        self.assertTrue(capture.started.wait(1))
        runtime.set_enabled(True)
        runtime.set_muted(True)
        self.wait_for(lambda: capture.ticks >= 3)
        self.assertFalse(speech.release_prepare.is_set())
        self.assertEqual(capture.starts, 1)
        self.assertFalse(runtime.snapshot()["ready"])
        self.assertTrue(runtime.gate.muted)
        speech.release_prepare.set()
        self.wait_for(lambda: runtime.snapshot()["ready"])
        self.assertEqual(speech.spoken, [])

    def test_voice_preparation_failure_does_not_stop_recording(self):
        runtime, speech, capture = self.runtime(FakeSpeech(blocked=True, failure=True))
        runtime.set_enabled(True)
        self.assertTrue(capture.started.wait(1))
        speech.release_prepare.set()
        self.wait_for(lambda: not runtime.worker.is_alive())
        self.assertIn("Injected voice failure", runtime.snapshot()["error"])
        self.assertFalse(runtime.capture_stop.is_set())
        ticks = capture.ticks
        self.wait_for(lambda: capture.ticks > ticks + 2)
        self.assertFalse(capture.finished.is_set())
        self.assertTrue(speech.closed.is_set())

    def test_pending_test_voice_is_discarded_when_muted_or_enabled(self):
        for control in ("mute", "enable"):
            with self.subTest(control=control):
                runtime, speech, capture = self.runtime(FakeSpeech(blocked=True))
                runtime.test_voice()
                self.assertTrue(runtime.test_requested)
                if control == "mute":
                    runtime.set_muted(True)
                else:
                    runtime.set_enabled(True)
                self.assertFalse(runtime.test_requested)
                speech.release_prepare.set()
                self.wait_for(lambda: runtime.snapshot()["ready"])
                self.wait_for(lambda: runtime.snapshot()["status"] in
                              ("Live Coach off", "Waiting for live telemetry"))
                self.assertEqual(speech.spoken, [])
                runtime.close()
                self.wait_for(lambda: runtime.finished)

    def test_close_returns_while_preparing_and_workers_finish_asynchronously(self):
        runtime, speech, capture = self.runtime(FakeSpeech(blocked=True))
        runtime.set_enabled(True)
        self.assertTrue(capture.started.wait(1))
        runtime.test_voice()
        runtime.close()
        self.assertTrue(runtime.stop_event.is_set())
        self.assertTrue(runtime.gate.closed)
        self.assertFalse(runtime.finished)  # preparation is still deliberately held
        self.assertFalse(speech.release_prepare.is_set())
        self.assertTrue(capture.finished.wait(1))
        speech.release_prepare.set()
        self.wait_for(lambda: runtime.finished)
        self.assertEqual(speech.spoken, [])
        self.assertTrue(speech.closed.is_set())

    def test_runtime_mute_and_close_cancel_current_output_immediately(self):
        runtime, speech, _ = self.runtime()
        self.wait_for(lambda: runtime.snapshot()["ready"])
        self.assertTrue(runtime.gate.test(CUE_TEXT))
        self.assertTrue(speech.busy)
        runtime.set_muted(True)
        self.assertFalse(speech.busy)
        self.assertFalse(runtime.gate.test(CUE_TEXT))
        runtime.set_muted(False)
        self.assertTrue(runtime.gate.test(CUE_TEXT))
        runtime.close()
        self.assertFalse(speech.busy)
        self.assertFalse(runtime.gate.test(CUE_TEXT))

    def test_session_end_closes_playback_gate_before_another_worker_can_speak(self):
        runtime, speech, capture = self.runtime(FakeSpeech(blocked=True))
        runtime.set_enabled(True)
        self.assertTrue(capture.started.wait(1))
        capture.end_session.set()
        self.assertTrue(runtime.capture_done.wait(1))
        speech.release_prepare.set()
        self.wait_for(lambda: runtime.snapshot()["ready"])
        # Models an already-in-flight engine update racing the producer's end.
        self.assertFalse(runtime.gate.speak(CUE_TEXT))
        self.assertEqual(speech.spoken, [])

    def test_coach_worker_failure_is_logged_and_capture_continues(self):
        class BrokenCoach:
            def __init__(self, *args, **kwargs):
                pass
            def set_enabled(self, enabled):
                raise RuntimeError("Injected coaching failure")
            def close(self):
                pass
        with patch("ac_racing_coach.live_coaching.LiveCoach", BrokenCoach):
            runtime, speech, capture = self.runtime(FakeSpeech(blocked=True))
            runtime.set_enabled(True)
            self.assertTrue(capture.started.wait(1))
            speech.release_prepare.set()
            self.wait_for(lambda: not runtime.worker.is_alive())
        self.assertIn("Injected coaching failure", runtime.snapshot()["error"])
        ticks = capture.ticks
        self.wait_for(lambda: capture.ticks > ticks + 2)
        self.assertFalse(runtime.capture_stop.is_set())
        self.assertTrue(speech.closed.is_set())
        events = [json.loads(line) for line in
                  Path(runtime.snapshot()["log_path"]).read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(event.get("event") == "coach_error" and
                            "Injected coaching failure" in event["reason"] for event in events))


class SpeechGateTests(unittest.TestCase):
    def test_mute_disable_and_close_cancel_and_reject_speech(self):
        for action in ({"muted": True}, {"enabled": False}, {"closed": True}):
            with self.subTest(action=action):
                speech = FakeSpeech()
                speech.prepare()
                gate = SpeechGate(speech)
                gate.configure(enabled=True)
                self.assertTrue(gate.speak(CUE_TEXT))
                gate.configure(**action)
                self.assertFalse(speech.busy)
                self.assertFalse(gate.speak(CUE_TEXT))
                self.assertEqual(speech.spoken, [CUE_TEXT])

    def test_test_voice_only_plays_when_coach_disabled_and_unmuted(self):
        speech = FakeSpeech()
        speech.prepare()
        gate = SpeechGate(speech)
        self.assertTrue(gate.test(CUE_TEXT))
        gate.cancel()
        gate.configure(enabled=True)
        self.assertFalse(gate.test(CUE_TEXT))
        gate.configure(enabled=False, muted=True)
        self.assertFalse(gate.test(CUE_TEXT))
        gate.configure(muted=False, closed=True)
        self.assertFalse(gate.test(CUE_TEXT))


class LatestTelemetryTests(unittest.TestCase):
    def frame(self, **values):
        return TelemetryFrame("session", 1.0, 2, True, {"track": "monza"},
                              {"in_pit": 0, "in_pit_lane": 0}, **values)

    def test_slow_consumer_keeps_only_safety_barrier_and_latest_frame(self):
        box = LatestTelemetry()
        normal = self.frame()
        box.publish(normal)
        box.publish(replace(normal, status=3))
        for index in range(500):
            box.publish(replace(normal, capture_time_s=2 + index * .02))
        frames = box.take()
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0].status, 3)
        self.assertFalse(frames[0].fresh)
        self.assertEqual(frames[1].capture_time_s, 2 + 499 * .02)
        self.assertEqual(box.take(), [])

    def test_segment_change_is_visible_even_if_consumer_misses_transition(self):
        box = LatestTelemetry()
        box.publish(self.frame())
        box.take()
        box.publish(self.frame(segment=1))
        box.publish(replace(self.frame(segment=1), capture_time_s=1.02))
        frames = box.take()
        self.assertEqual(len(frames), 2)
        self.assertFalse(frames[0].fresh)
        self.assertEqual(frames[0].segment, 1)
        self.assertTrue(frames[1].fresh)


if __name__ == "__main__":
    unittest.main()
