"""Deterministic local-speech checks: no audible output or external processes."""

from pathlib import Path
import subprocess
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

from ac_racing_coach.speech import CUE_TEXT, WindowsSpeech, _play_wav, _render_local_wav


class SpeechTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        self.played = []
        self.rendered = []
        self.speech = WindowsSpeech(renderer=self.render, player=self.played.append,
                                    clock=lambda: self.now)
        self.addCleanup(self.speech.close)

    def render(self, path, text):
        self.rendered.append((path, text))
        with wave.open(str(path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(1000)
            audio.writeframes(b"\0\0" * 3500)
        return "Test local voice"

    def test_prepare_measures_audio_without_speaking_and_only_renders_once(self):
        self.assertFalse(self.speech.ready)
        self.assertFalse(self.speech.speak())
        self.assertTrue(self.speech.prepare())
        self.assertEqual(self.speech.duration_s, 3.5)
        self.assertEqual(self.speech.voice_name, "Test local voice")
        self.assertTrue(self.speech.ready)
        self.assertEqual(self.played, [])
        self.assertTrue(self.speech.prepare())
        self.assertEqual(len(self.rendered), 1)
        self.assertEqual(self.rendered[0][1], CUE_TEXT)

    def test_busy_rejects_cues_without_queueing_or_interrupting(self):
        self.speech.prepare()
        self.assertTrue(self.speech.speak(CUE_TEXT))
        self.assertTrue(self.speech.busy)
        self.assertFalse(self.speech.speak(CUE_TEXT))
        self.assertFalse(self.speech.speak("a different unmeasured phrase"))
        self.assertEqual(len(self.played), 1)
        self.now += 4.0
        self.assertFalse(self.speech.busy)
        self.assertEqual(len(self.played), 1)  # no backlog starts by itself
        self.assertTrue(self.speech.speak(CUE_TEXT))
        self.assertEqual(len(self.played), 2)

    def test_cancel_stops_active_output_and_leaves_no_pending_output(self):
        self.speech.prepare()
        self.speech.speak()
        self.speech.speak()
        self.speech.cancel()
        self.assertEqual(self.played[-1], None)
        self.assertFalse(self.speech.busy)
        self.now += 30
        self.assertEqual(len(self.played), 2)
        self.speech.cancel()  # idempotent and no unsolicited sounds
        self.assertEqual(len(self.played), 2)

    def test_close_removes_owned_file_and_disables_output(self):
        self.speech.prepare()
        path = self.rendered[0][0]
        self.speech.speak()
        self.speech.close()
        self.assertFalse(Path(path).exists())
        self.assertFalse(self.speech.ready)
        self.assertFalse(self.speech.speak())
        self.assertFalse(self.speech.prepare())
        self.assertEqual(self.played[-1], None)

    def test_render_failure_is_reported_without_playback(self):
        def fail(path, text):
            raise RuntimeError("No installed desktop voice")
        self.speech._renderer = fail
        self.assertFalse(self.speech.prepare())
        self.assertIn("No installed desktop voice", self.speech.error)
        self.assertFalse(self.speech.speak())
        self.assertEqual(self.played, [])

    def test_playback_and_cancellation_failure_are_contained(self):
        self.speech.prepare()
        def fail(path):
            raise RuntimeError("Audio device unavailable")
        self.speech._player = fail
        self.assertFalse(self.speech.speak())
        self.assertIn("Audio device unavailable", self.speech.error)
        self.speech.error = None
        self.speech._player = self.played.append
        self.speech.speak()
        self.speech._player = fail
        self.speech.cancel()
        self.assertIn("Could not stop voice", self.speech.error)
        self.assertFalse(self.speech.busy)

    def test_close_during_preparation_cannot_schedule_or_enable_output(self):
        entered, finish = threading.Event(), threading.Event()
        result = []
        def slow_render(path, text):
            entered.set()
            finish.wait(2)
            return self.render(path, text)
        self.speech._renderer = slow_render
        worker = threading.Thread(target=lambda: result.append(self.speech.prepare()))
        worker.start()
        self.assertTrue(entered.wait(2))
        self.speech.cancel()
        self.speech.close()
        finish.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [False])
        self.assertFalse(self.speech.ready)
        self.assertEqual(self.played, [])
        self.assertFalse(self.rendered[0][0].exists())

    def test_windows_playback_uses_async_file_and_supported_stop_operation(self):
        api = SimpleNamespace(PlaySound=Mock(), SND_FILENAME=1, SND_ASYNC=2,
                              SND_NODEFAULT=4)
        with patch.dict("sys.modules", {"winsound": api}):
            _play_wav(Path("cue.wav"))
            api.PlaySound.assert_called_with("cue.wav", 7)
            _play_wav(None)
            api.PlaySound.assert_called_with(None, 0)

    def test_preparation_process_is_hidden_and_bounded(self):
        with patch("ac_racing_coach.speech.sys.platform", "win32"), \
                patch("ac_racing_coach.speech.subprocess.CREATE_NO_WINDOW", 0x08000000,
                      create=True), \
                patch("ac_racing_coach.speech.subprocess.run") as run:
            run.return_value = SimpleNamespace(returncode=0, stdout="Desktop voice\n")
            self.assertEqual(_render_local_wav(Path("cue.wav"), CUE_TEXT), "Desktop voice")
            self.assertEqual(run.call_args.kwargs["timeout"], 10)
            self.assertEqual(run.call_args.kwargs["creationflags"], 0x08000000)
            self.assertNotIn("shell", run.call_args.kwargs)
            run.side_effect = subprocess.TimeoutExpired("powershell", 10)
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                _render_local_wav(Path("cue.wav"), CUE_TEXT)


if __name__ == "__main__":
    unittest.main()
