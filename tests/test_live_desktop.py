"""Exercise the actual live controls with a fake runtime and no AC or audio."""

import os
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMessageBox
except ImportError:
    DESKTOP_AVAILABLE = False
else:
    DESKTOP_AVAILABLE = True
    from ac_racing_coach import desktop as ui
    from ac_racing_coach import live_panel


ROOT = Path(__file__).resolve().parents[1]
RECORDING = ROOT / "recordings/session_20260913_213429_162345.csv"


class FakeRuntime:
    def __init__(self):
        self.calls = []
        self.capture_stop = threading.Event()
        self.finished = False
        self.state = {
            "status": "Ready for synthetic approach", "capture": "Fake capture",
            "path": "fake.csv", "log_path": "fake.jsonl", "ready": True,
            "error": "", "calibrated": False, "duration_s": 4.5,
            "lead_time_s": 3.0,
        }

    def snapshot(self):
        return dict(self.state)

    def set_enabled(self, value):
        self.calls.append(("enable", value))
        if not value:
            self.capture_stop.set()

    def set_muted(self, value):
        self.calls.append(("mute", value))

    def test_voice(self):
        self.calls.append(("test",))

    def calibrate(self):
        self.calls.append(("calibrate",))

    def set_lead_time(self, seconds):
        self.calls.append(("lead", seconds))
        self.state["lead_time_s"] = seconds

    def close(self):
        self.calls.append(("close",))
        self.capture_stop.set()


@unittest.skipUnless(DESKTOP_AVAILABLE, "Optional PySide6 dependency is not installed")
class LiveDesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setStyle("Fusion")

    def setUp(self):
        self.instances = []
        self.release_task = threading.Event()
        self.window = ui.CoachWindow()
        self.panel = self.window.live_panel

        def factory():
            runtime = FakeRuntime()
            self.instances.append(runtime)
            return runtime

        self.factory = Mock(side_effect=factory)
        self.panel.runtime_factory = self.factory
        self.window.show()
        self.app.processEvents()

    def wait_until(self, predicate, timeout=10):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(10)
            time.sleep(.005)
        self.assertTrue(predicate(), "UI condition did not complete")

    def tearDown(self):
        self.release_task.set()
        for runtime in self.instances:
            runtime.finished = True
        self.window.close()
        self.wait_until(lambda: self.window.task is None and not self.window.isVisible())
        self.window.deleteLater()
        self.app.processEvents()

    def click(self, widget):
        self.assertTrue(widget.isEnabled(), widget.text())
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton)

    def test_construction_is_lazy_and_preserves_review_welcome(self):
        self.factory.assert_not_called()
        self.assertIsNone(self.panel.runtime)
        self.assertFalse(self.panel.poll.isActive())
        self.assertTrue(self.window.welcome_panel.isVisible())
        self.assertEqual(self.window.welcome_title.text(), "Review your driving")
        self.assertEqual(self.window.open_button.text(), "Open Recording")
        self.assertFalse(self.window.workspace.isVisible())
        self.assertTrue(self.panel.enable.isEnabled())
        self.assertTrue(self.panel.test.isEnabled())
        self.assertFalse(self.panel.calibrate.isEnabled())
        self.assertIn("not personalized advice", self.panel.caption.text())
        # Muting before first enable also must not initialize speech or capture.
        self.click(self.panel.mute)
        self.factory.assert_not_called()
        self.assertFalse(self.panel.test.isEnabled())

    def test_enable_mute_and_finish_lead_reach_same_runtime(self):
        self.click(self.panel.enable)
        self.factory.assert_called_once()
        runtime = self.instances[0]
        self.assertIn(("enable", True), runtime.calls)
        self.assertFalse(self.panel.test.isEnabled())
        self.assertTrue(self.panel.calibrate.isEnabled())
        self.click(self.panel.mute)
        self.assertIn(("mute", True), runtime.calls)
        self.assertIn("Muted", self.panel.status.text())
        self.assertTrue(self.panel.lead.isEnabled())
        self.panel.lead.setValue(5.5)
        self.assertIn(("lead", 5.5), runtime.calls)
        self.click(self.panel.enable)
        self.assertIn(("enable", False), runtime.calls)
        self.assertTrue(runtime.capture_stop.is_set())
        self.assertFalse(self.panel.calibrate.isEnabled())
        self.assertFalse(self.panel.test.isEnabled(), "Mute still applies after disabling")
        self.assertEqual(len(self.instances), 1)

    def test_voice_test_is_available_only_off_and_unmuted(self):
        self.click(self.panel.test)
        runtime = self.instances[0]
        self.assertEqual(runtime.calls.count(("test",)), 1)
        self.assertNotIn(("enable", True), runtime.calls)
        self.click(self.panel.enable)
        self.assertIs(self.panel.runtime, runtime)
        self.assertFalse(self.panel.test.isEnabled())
        QTest.mouseClick(self.panel.test, Qt.MouseButton.LeftButton)
        self.assertEqual(runtime.calls.count(("test",)), 1)
        self.click(self.panel.enable)
        self.assertTrue(self.panel.test.isEnabled())
        self.click(self.panel.mute)
        self.assertFalse(self.panel.test.isEnabled())
        QTest.mouseClick(self.panel.test, Qt.MouseButton.LeftButton)
        self.assertEqual(runtime.calls.count(("test",)), 1)

    def test_reenable_waits_for_old_runtime_before_creating_new_timeline(self):
        self.click(self.panel.enable)
        first = self.instances[0]
        self.click(self.panel.enable)
        self.click(self.panel.enable)
        self.assertTrue(self.panel.restarting)
        self.assertIn(("close",), first.calls)
        self.assertEqual(len(self.instances), 1)
        self.panel.refresh()
        self.assertEqual(len(self.instances), 1)
        first.finished = True
        self.panel.refresh()
        self.assertEqual(len(self.instances), 2)
        self.assertIs(self.panel.runtime, self.instances[1])
        self.assertIn(("enable", True), self.instances[1].calls)
        self.assertFalse(self.panel.restarting)

    def test_calibration_confirmation_delays_capture_and_disable_cancels(self):
        self.click(self.panel.enable)
        runtime = self.instances[0]

        class AcceptedDialog:
            ButtonRole = QMessageBox.ButtonRole
            StandardButton = QMessageBox.StandardButton

            def __init__(self, _parent):
                self.buttons = []

            def setWindowTitle(self, _text):
                pass

            def setText(self, _text):
                pass

            def setInformativeText(self, _text):
                pass

            def addButton(self, *_args):
                button = object()
                self.buttons.append(button)
                return button

            def exec(self):
                return None

            def clickedButton(self):
                return self.buttons[0]

        with patch.object(live_panel, "QMessageBox", AcceptedDialog):
            self.click(self.panel.calibrate)
        self.assertTrue(self.panel.calibration_timer.isActive())
        self.assertEqual(self.panel.calibration_timer.interval(), 5000)
        self.assertNotIn(("calibrate",), runtime.calls)
        self.panel.finish_calibration()
        self.assertEqual(runtime.calls.count(("calibrate",)), 1)
        self.click(self.panel.enable)
        self.assertFalse(self.panel.calibration_timer.isActive())
        self.panel.finish_calibration()
        self.assertEqual(runtime.calls.count(("calibrate",)), 1)

    def test_close_cancels_immediately_and_waits_without_blocking_review_task(self):
        self.click(self.panel.enable)
        runtime = self.instances[0]

        def delayed_open():
            self.release_task.wait(10)
            return ui.open_recording(RECORDING)

        self.window.start_task("load", delayed_open, "Synthetic slow load")
        ticks = []
        timer = QTimer(self.window)
        timer.setInterval(10)
        timer.timeout.connect(lambda: ticks.append(1))
        timer.start()
        began = time.monotonic()
        self.window.close()
        self.assertLess(time.monotonic() - began, .25)
        self.assertIn(("close",), runtime.calls)
        self.assertTrue(self.panel.shutting_down)
        self.assertFalse(self.panel.enable.isEnabled())
        self.assertTrue(self.window.isVisible())
        runtime.finished = True
        QTest.qWait(70)
        self.assertGreaterEqual(len(ticks), 3, "Shutdown must leave the Qt event loop responsive")
        self.assertTrue(self.window.isVisible(), "The current review task still owns the window")
        self.release_task.set()
        self.wait_until(lambda: self.window.task is None and not self.window.isVisible())
        timer.stop()

    def test_narrow_window_scrolls_live_controls_and_keyboard_reaches_them(self):
        self.window.resize(560, 400)
        self.app.processEvents()
        self.assertGreater(self.panel.horizontalScrollBar().maximum(), 0)
        self.panel.enable.setFocus()
        QTest.keyClick(self.panel.enable, Qt.Key.Key_Space)
        self.assertTrue(self.panel.enable.isChecked())
        self.assertEqual(len(self.instances), 1)
        self.panel.lead.setFocus()
        self.app.processEvents()
        self.panel.ensureWidgetVisible(self.panel.lead)
        self.app.processEvents()
        center = self.panel.lead.mapTo(self.panel.viewport(), self.panel.lead.rect().center())
        self.assertTrue(self.panel.viewport().rect().contains(center))
        self.assertEqual(self.window.welcome_title.text(), "Review your driving")
        self.assertLessEqual(self.window.width(), 560)


if __name__ == "__main__":
    unittest.main()
