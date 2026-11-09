"""Exercise the real desktop widgets with temporary copies of a legacy CSV.

These tests also run without a display. CLI-only installations skip this module's
tests when the optional PySide6 dependency is absent.
"""

import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QScrollArea
except ImportError:
    DESKTOP_AVAILABLE = False
else:
    DESKTOP_AVAILABLE = True
    from ac_racing_coach import desktop as ui


ROOT = Path(__file__).resolve().parents[1]
RECORDING = ROOT / "recordings/session_20260913_213429_162345.csv"


@unittest.skipUnless(DESKTOP_AVAILABLE, "Optional PySide6 desktop dependency is not installed")
class DesktopIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setStyle("Fusion")
        # Windows' offscreen plugin does not discover system fonts. Register
        # the UI font for readable optional screenshots without native windows.
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for filename in ("segoeui.ttf", "segoeuib.ttf"):
            path = fonts / filename
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))

    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.csv = Path(self.scratch.name) / "recording.csv"
        shutil.copyfile(RECORDING, self.csv)
        self.csv_hash = hashlib.sha256(self.csv.read_bytes()).hexdigest()
        self.window = ui.CoachWindow()
        self.window.show()
        QTest.qWait(10)

    def tearDown(self):
        # Never destroy a QThread that is still running, even after an assertion.
        self.wait_until(lambda: self.window.task is None)
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.assertEqual(hashlib.sha256(self.csv.read_bytes()).hexdigest(), self.csv_hash)
        self.scratch.cleanup()

    def wait_until(self, predicate, timeout=30):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(10)
            # PySide's qWait can hold the GIL. Explicitly yield so the Python
            # worker receives the same opportunity it gets in QApplication.exec.
            time.sleep(.005)
        self.assertTrue(predicate(), "Background UI operation did not finish in time")

    def wait_idle(self):
        self.wait_until(lambda: self.window.task is None)

    def click(self, button):
        self.assertTrue(button.isEnabled(), button.text())
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)

    def load(self, path=None):
        with patch.object(ui.QFileDialog, "getOpenFileName", return_value=(str(path or self.csv), "")):
            self.click(self.window.open_button)
        self.wait_idle()

    def review(self, number, button):
        self.window.table.selectRow(number - 1)
        self.click(button)
        self.wait_idle()
        self.assertFalse(self.window.error_label.isVisible(), self.window.error_label.text())

    def offered(self, combo):
        return [combo.itemData(index) for index in range(combo.count()) if combo.itemData(index) is not None]

    def select_pair(self, reference, current):
        self.window.reference_combo.setCurrentIndex(self.window.reference_combo.findData(reference))
        self.window.current_combo.setCurrentIndex(self.window.current_combo.findData(current))

    def approve_both(self):
        self.load()
        for number in (1, 2):
            self.review(number, self.window.approve_button)

    def test_empty_welcome_has_one_open_action_without_disabled_workspace(self):
        self.assertTrue(self.window.welcome_panel.isVisible())
        self.assertEqual(self.window.welcome_title.text(), "Review your driving")
        self.assertIn("Open a recording to inspect laps", self.window.welcome_subtitle.text())
        self.assertTrue(self.window.open_button.isVisible())
        self.assertTrue(self.window.open_button.isEnabled())
        self.assertFalse(self.window.workspace.isVisible())
        self.assertFalse(self.window.table.isVisible())
        self.assertFalse(self.window.review_panel.isVisible())
        self.assertFalse(self.window.tabs.isVisible())

    def test_open_shortcut_uses_themed_dialog_and_cancel_keeps_empty_state(self):
        with patch.object(ui.QFileDialog, "getOpenFileName", return_value=("", "")) as dialog:
            QTest.keyClick(self.window, Qt.Key.Key_O, Qt.KeyboardModifier.ControlModifier)
        dialog.assert_called_once()
        self.assertTrue(dialog.call_args.kwargs["options"] & ui.QFileDialog.Option.DontUseNativeDialog)
        self.assertIsNone(self.window.task)
        self.assertIsNone(self.window.snapshot)
        self.assertTrue(self.window.welcome_panel.isVisible())

    def test_loaded_laps_require_explicit_selection_to_show_review(self):
        self.load()
        self.assertTrue(self.window.workspace.isVisible())
        self.assertFalse(self.window.welcome_panel.isVisible())
        self.assertTrue(self.window.table.isVisible())
        self.assertFalse(self.window.review_panel.isVisible())
        self.assertFalse(self.window.approve_button.isVisible())
        self.assertTrue(self.window.result_state_panel.isVisible())
        self.assertFalse(self.window.tabs.isVisible())
        self.assertFalse(self.window.compare_button.isEnabled())
        self.window.table.selectRow(0)
        self.assertTrue(self.window.review_panel.isVisible())
        self.assertTrue(self.window.approve_button.isVisible())
        self.window.table.clearSelection()
        self.assertFalse(self.window.review_panel.isVisible())

    def test_review_and_selectors_are_keyboard_accessible(self):
        self.load()
        self.window.table.setFocus()
        QTest.keyClick(self.window.table, Qt.Key.Key_Down)
        self.assertTrue(self.window.table.selectedItems())
        self.assertTrue(self.window.review_panel.isVisible())
        self.window.approve_button.setFocus()
        self.assertIs(self.app.focusWidget(), self.window.approve_button)
        QTest.keyClick(self.window.approve_button, Qt.Key.Key_Space)
        self.wait_idle()
        self.assertEqual(sum(lap.coaching_eligible for lap in self.window.snapshot.laps), 1)
        # A real Tab event must leave the action and reach another control.
        self.window.approve_button.setFocus()
        QTest.keyClick(self.window.approve_button, Qt.Key.Key_Tab)
        self.assertIsNot(self.app.focusWidget(), self.window.approve_button)
        self.assertTrue(self.app.focusWidget().isEnabled())
        self.window.reference_combo.setFocus()
        QTest.keyClick(self.window.reference_combo, Qt.Key.Key_End)
        self.assertIsNotNone(self.window.reference_combo.currentData())
        self.assertFalse(self.window.compare_button.isEnabled())

    def test_resize_keeps_actions_accessible_and_sidebar_resizable(self):
        self.load()
        self.window.resize(1280, 800)
        self.app.processEvents()
        sizes = self.window.splitter.sizes()
        self.assertGreaterEqual(sizes[0], 260)
        self.assertLessEqual(sizes[0], 370)
        self.window.splitter.setSizes([370, 800])
        self.app.processEvents()
        self.assertNotEqual(sizes, self.window.splitter.sizes())
        # A small logical workspace represents a high-DPI screen. Overflow
        # should be reachable using scrolling instead of clipped controls.
        self.window.resize(640, 400)
        self.app.processEvents()
        self.assertLessEqual(self.window.width(), 640)
        self.assertLessEqual(self.window.height(), 400)
        scrolls = self.window.findChildren(QScrollArea)
        self.assertTrue(any(area.verticalScrollBar().maximum() > 0 for area in scrolls))
        self.window.table.selectRow(1)
        self.window.approve_button.setFocus()
        self.app.processEvents()
        for area in scrolls:
            if area.isAncestorOf(self.window.approve_button):
                center = self.window.approve_button.mapTo(area.viewport(), self.window.approve_button.rect().center())
                self.assertTrue(area.viewport().rect().contains(center), "Focused review action was clipped by a scroll area")
        self.window.resize(1280, 800)
        self.app.processEvents()
        self.assertTrue(self.window.open_button.isVisible())
        self.assertTrue(self.window.reference_combo.isVisible())

    def test_open_review_and_reload_use_matching_sidecar(self):
        self.load()
        self.assertEqual(self.window.table.rowCount(), 2)
        self.assertEqual([self.window.table.item(row, 1).text() for row in (0, 1)], ["1:45.749", "1:42.913"])
        self.assertEqual([self.window.table.item(row, 2).text() for row in (0, 1)], ["Unknown", "Unknown"])
        self.assertEqual(self.offered(self.window.reference_combo), [])
        self.assertFalse(self.window.compare_button.isEnabled())

        self.review(1, self.window.approve_button)
        self.review(2, self.window.invalid_button)
        self.assertEqual(self.window.table.item(0, 2).text(), "Manually approved")
        self.assertEqual(self.window.table.item(1, 2).text(), "Invalid")
        for combo in (self.window.reference_combo, self.window.current_combo):
            self.assertEqual(self.offered(combo), [1])
        self.assertTrue(self.csv.with_suffix(".validity.json").is_file())

        self.click(self.window.reload_button)
        self.wait_idle()
        self.assertEqual([lap.validity for lap in self.window.snapshot.laps], ["manual_valid", "invalid"])
        self.review(1, self.window.unknown_button)
        self.assertEqual(self.offered(self.window.reference_combo), [])
        self.assertFalse(self.window.compare_button.isEnabled())

    def test_exact_selection_graph_feedback_and_review_clear_stale_result(self):
        self.approve_both()
        self.assertEqual(self.offered(self.window.reference_combo), [1, 2])
        for pair in ((1, 1), (2, 2), (2, 1)):
            self.select_pair(*pair)
            self.assertFalse(self.window.compare_button.isEnabled())
        self.select_pair(1, 2)
        self.assertTrue(self.window.compare_button.isEnabled())

        ticks = []
        timer = QTimer(self.window)
        timer.setInterval(10)
        timer.timeout.connect(lambda: ticks.append(time.monotonic()))
        actual_generate = ui.generate_comparison

        def delayed_generate(*args):
            time.sleep(.2)
            return actual_generate(*args)

        timer.start()
        with patch.object(ui, "generate_comparison", side_effect=delayed_generate):
            self.click(self.window.compare_button)
            self.assertFalse(self.window.open_button.isEnabled())
            self.assertFalse(self.window.approve_button.isEnabled())
            self.assertFalse(self.window.reference_combo.isEnabled())
            self.assertTrue(self.window.result_state_panel.isVisible())
            self.assertFalse(self.window.tabs.isVisible())
            QTest.qWait(100)
            self.assertIsNotNone(self.window.task)
            self.assertGreaterEqual(len(ticks), 3, "UI timer stalled during analysis")
            self.wait_idle()
        timer.stop()
        self.assertFalse(self.window.error_label.isVisible(), self.window.error_label.text())
        self.assertFalse(self.window.graph.original.isNull())
        self.assertTrue(self.window.tabs.isVisible())
        self.assertFalse(self.window.result_state_panel.isVisible())
        feedback = self.window.feedback.toPlainText()
        self.assertIn("1:45.749", feedback)
        self.assertIn("1:42.913", feedback)
        self.assertIn("0.267", feedback)
        self.assertIn("Validity: manually marked valid by the driver", feedback)
        self.assertIn("Reference: lap 1", self.window.result_label.text())
        screenshot = os.environ.get("AC_COACH_QA_SCREENSHOT")
        if screenshot:
            Path(screenshot).parent.mkdir(parents=True, exist_ok=True)
            self.assertTrue(self.window.grab().save(screenshot))

        self.window.table.selectRow(1)
        self.click(self.window.invalid_button)
        self.assertTrue(self.window.graph.original.isNull())
        self.assertEqual(self.window.feedback.toPlainText(), "")
        self.wait_idle()
        self.assertEqual(self.offered(self.window.reference_combo), [1])
        self.assertEqual(self.offered(self.window.current_combo), [1])
        self.assertFalse(self.window.compare_button.isEnabled())

    def test_malformed_load_fails_closed_and_open_button_recovers(self):
        self.approve_both()
        broken = Path(self.scratch.name) / "broken.csv"
        broken.write_text("not,a,recording\n1,2,3\n", encoding="utf-8")
        self.load(broken)
        self.assertTrue(self.window.error_label.isVisible())
        self.assertIn("missing columns", self.window.error_label.text())
        self.assertTrue(self.window.welcome_panel.isVisible())
        self.assertFalse(self.window.workspace.isVisible())
        self.assertIsNone(self.window.snapshot)
        self.assertEqual(self.window.table.rowCount(), 0)
        self.assertFalse(self.window.compare_button.isEnabled())
        self.assertEqual(self.window.feedback.toPlainText(), "")
        self.assertTrue(self.window.graph.original.isNull())
        self.load()
        self.assertFalse(self.window.error_label.isVisible())
        self.assertEqual(self.offered(self.window.reference_combo), [1, 2])

    def test_loading_is_responsive_and_close_waits_without_blocking(self):
        actual_open = ui.open_recording
        ticks = []
        timer = QTimer(self.window)
        timer.setInterval(10)
        timer.timeout.connect(lambda: ticks.append(time.monotonic()))

        def delayed_open(*args):
            time.sleep(.25)
            return actual_open(*args)

        timer.start()
        with patch.object(ui, "open_recording", side_effect=delayed_open):
            self.window.load_recording(self.csv)
            self.assertIsNotNone(self.window.task)
            self.assertTrue(self.window.welcome_panel.isVisible())
            self.assertFalse(self.window.workspace.isVisible())
            self.assertEqual(self.window.welcome_title.text(), "Opening recording")
            self.window.close()
            self.assertTrue(self.window.closing)
            self.assertTrue(self.window.isVisible())
            QTest.qWait(100)
            self.assertGreaterEqual(len(ticks), 3, "UI event loop blocked while waiting to close")
            self.assertIsNotNone(self.window.task)
            self.wait_until(lambda: self.window.task is None and not self.window.isVisible())
        timer.stop()

    def test_sidecar_changed_during_analysis_cannot_publish_feedback(self):
        self.approve_both()
        self.select_pair(1, 2)
        sidecar = self.csv.with_suffix(".validity.json")

        def mutate_review(_laps, _image, **_kwargs):
            # A different process rewrites the review while rendering is in flight.
            sidecar.write_text(sidecar.read_text(encoding="utf-8") + "\n", encoding="utf-8")

        with patch.object(ui, "plot_laps", side_effect=mutate_review):
            self.click(self.window.compare_button)
            self.wait_idle()
        self.assertTrue(self.window.error_label.isVisible())
        self.assertIn("changed", self.window.error_label.text())
        self.assertIsNone(self.window.snapshot)
        self.assertTrue(self.window.graph.original.isNull())
        self.assertEqual(self.window.feedback.toPlainText(), "")
        self.assertFalse(self.window.compare_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
