"""Repeatable offscreen desktop QA using disposable recording copies.

Run with the desktop environment's Python. Creates screenshots plus a JSON
manifest under .ui-qa/redesign. Each scale runs in a fresh process because Qt
reads QT_SCALE_FACTOR at application creation. This simulates Qt DPI scaling;
it does not change or verify the native Windows display scaling setting.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "recordings/session_20260913_213429_162345.csv"
OUTPUT = ROOT / ".ui-qa/redesign"


def source_hashes():
    return {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((ROOT / "recordings").iterdir())
        if path.is_file() and (path.suffix == ".csv" or path.name.endswith(".validity.json"))
    }


def capture_scale(scale):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_SCALE_FACTOR"] = str(scale)
    sys.path.insert(0, str(ROOT))
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QFileDialog, QScrollArea
    from ac_racing_coach import desktop as ui

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")
    font_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for filename in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf"):
        path = font_dir / filename
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))

    OUTPUT.mkdir(parents=True, exist_ok=True)
    suffix = "" if scale == 1 else f"-{round(scale * 100)}pct"
    files = []
    metrics = {}
    before = source_hashes()
    window = ui.CoachWindow()
    window.resize(round(1280 / scale), round(800 / scale))
    window.show()
    QTest.qWait(25)

    def idle():
        deadline = time.monotonic() + 60
        while window.task is not None and time.monotonic() < deadline:
            QTest.qWait(10)
            time.sleep(.005)
        assert window.task is None, "Timed out waiting for analysis"
        app.processEvents()

    def capture(name):
        app.processEvents()
        window.repaint()
        QTest.qWait(60)
        pixmap = window.grab()
        assert pixmap.width() <= 1281 and pixmap.height() <= 801, "Window exceeds the 1280x800 scaled workspace"
        assert abs(pixmap.devicePixelRatio() - scale) < .01, "Qt did not apply the requested scale factor"
        path = OUTPUT / f"{name}{suffix}.png"
        assert pixmap.save(str(path)), f"Could not save {path}"
        files.append(path.name)
        metrics[name] = {
            "logical_size": [window.width(), window.height()],
            "image_pixels": [pixmap.width(), pixmap.height()],
            "device_pixel_ratio": pixmap.devicePixelRatio(),
            "sidebar_width": window.splitter.sizes()[0],
            "scroll_ranges": [
                {"horizontal": area.horizontalScrollBar().maximum(),
                 "vertical": area.verticalScrollBar().maximum()}
                for area in window.findChildren(QScrollArea) if area.isVisible()
            ],
        }

    def select_review(number, button):
        window.table.selectRow(number - 1)
        app.processEvents()
        assert window.review_panel.isVisible()
        assert button.isEnabled()
        button.click()
        idle()
        assert not window.error_label.isVisible(), window.error_label.text()

    def focus_exposed(widget):
        widget.setFocus()
        app.processEvents()
        QTest.qWait(20)
        assert app.focusWidget() is widget
        for area in window.findChildren(QScrollArea):
            if area.isAncestorOf(widget):
                center = widget.mapTo(area.viewport(), widget.rect().center())
                assert area.viewport().rect().contains(center), "Keyboard-focused action is clipped"

    def scroll_to_top():
        window.workspace_scroll.horizontalScrollBar().setValue(0)
        window.workspace_scroll.verticalScrollBar().setValue(0)

    def capture_popup(name, widget):
        app.processEvents()
        widget.repaint()
        QTest.qWait(80)
        path = OUTPUT / f"{name}{suffix}.png"
        pixmap = widget.grab()
        assert pixmap.save(str(path))
        files.append(path.name)
        metrics[name] = {"logical_size": [widget.width(), widget.height()],
                         "image_pixels": [pixmap.width(), pixmap.height()],
                         "device_pixel_ratio": pixmap.devicePixelRatio()}

    def capture_file_dialog():
        failures = []

        def inspect_and_cancel():
            dialogs = [item for item in QApplication.topLevelWidgets()
                       if isinstance(item, QFileDialog) and item.isVisible()]
            try:
                assert len(dialogs) == 1, "The Qt file dialog did not open"
                dialog = dialogs[0]
                assert dialog.testOption(QFileDialog.Option.DontUseNativeDialog)
                dialog.resize(800, 540)
                capture_popup("open-dialog", dialog)
                QTest.keyClick(dialog, Qt.Key.Key_Escape)
                app.processEvents()
                assert not dialog.isVisible(), "Escape did not cancel the file dialog"
            except Exception as error:
                failures.append(str(error))
            finally:
                for dialog in dialogs:
                    dialog.reject()

        QTimer.singleShot(100, inspect_and_cancel)
        window.pick_recording()
        assert not failures, "; ".join(failures)
        assert window.snapshot is not None

    try:
        with tempfile.TemporaryDirectory(prefix="ac-coach-visual-qa-") as scratch:
            csv = Path(scratch) / "QA_review_copy.csv"
            shutil.copyfile(FIXTURE, csv)
            assert window.welcome_panel.isVisible()
            assert not window.table.isVisible()
            capture("empty")

            original_open = ui.open_recording

            def delayed_open(*args):
                time.sleep(.65)
                return original_open(*args)

            with patch.object(ui, "open_recording", side_effect=delayed_open):
                window.load_recording(csv)
                assert window.task is not None
                assert not window.open_button.isEnabled()
                capture("loading")
                idle()
            assert [lap.seconds for lap in window.snapshot.laps] == [105.749, 102.913]
            assert [lap.validity for lap in window.snapshot.laps] == ["unknown", "unknown"]
            assert not window.review_panel.isVisible()
            assert not window.compare_button.isEnabled()
            capture("loaded-unreviewed")

            window.table.setFocus()
            QTest.keyClick(window.table, Qt.Key.Key_Down)
            assert window.table.selectedItems(), "Keyboard could not select a lap"
            assert window.review_panel.isVisible()
            window.approve_button.setFocus()
            QTest.keyClick(window.approve_button, Qt.Key.Key_Tab)
            assert app.focusWidget() is not window.approve_button
            assert app.focusWidget().isEnabled()

            # These are explicitly synthetic QA decisions on a temporary copy.
            # They do not attest that the original driving was valid.
            select_review(1, window.approve_button)
            select_review(2, window.invalid_button)
            assert not window.compare_button.isEnabled()
            for combo in (window.reference_combo, window.current_combo):
                offered = [combo.itemData(i) for i in range(combo.count()) if combo.itemData(i) is not None]
                assert offered == [1], "Invalid lap was offered for comparison"
            capture("insufficient")

            select_review(2, window.approve_button)
            window.reference_combo.setCurrentIndex(window.reference_combo.findData(1))
            window.current_combo.setCurrentIndex(window.current_combo.findData(2))
            assert window.compare_button.isEnabled()
            window.compare_button.click()
            assert window.task is not None
            capture("analyzing")
            idle()
            assert not window.error_label.isVisible(), window.error_label.text()
            assert not window.graph.original.isNull()
            assert "0.267" in window.feedback.toPlainText()
            assert window.tabs.isVisible()
            scroll_to_top()
            capture("loaded")
            focus_exposed(window.compare_button)
            capture("comparison-controls-scrolled")
            focus_exposed(window.approve_button)
            capture("review-controls-scrolled")
            if scale == 1:
                window.reference_combo.showPopup()
                app.processEvents()
                assert window.reference_combo.view().isVisible()
                capture_popup("dropdown", window.reference_combo.view().window())
                QTest.keyClick(window.reference_combo.view(), Qt.Key.Key_Escape)
                assert not window.reference_combo.view().isVisible(), "Escape did not dismiss the dropdown"
            scroll_to_top()
            window.tabs.setCurrentIndex(1)
            capture("feedback")

            # Revoking a displayed lap clears both graphs and feedback immediately.
            select_review(2, window.invalid_button)
            assert window.graph.original.isNull()
            assert window.feedback.toPlainText() == ""
            assert not window.compare_button.isEnabled()

            broken = Path(scratch) / "unreadable_recording.csv"
            broken.write_text("not,a,recording\n1,2,3\n", encoding="utf-8")
            window.load_recording(broken)
            idle()
            assert window.snapshot is None
            assert window.error_label.isVisible()
            assert window.open_button.isEnabled()
            capture("error")
            window.load_recording(csv)
            idle()
            assert window.snapshot is not None
            assert not window.error_label.isVisible()
            if scale == 1:
                capture_file_dialog()

        assert before == source_hashes(), "An original recording or sidecar changed"
        result = {
            "scale_factor": scale,
            "target_physical_workspace": [1280, 800],
            "screenshots": files,
            "metrics": metrics,
            "verified": [
                "empty welcome", "responsive load", "unreviewed import",
                "keyboard lap selection and Tab", "invalid lap exclusion",
                "keyboard focus reveals scrolled review and comparison actions",
                "insufficient approved laps", "comparison", "feedback",
                "revoking review clears stale reports", "error recovery",
                "original CSV and sidecar hashes unchanged",
            ],
            "limitation": "Offscreen Qt scaling simulation; no native Windows display setting or file dialog interaction verified.",
            "fixture_note": "QA manual approval was applied only to a temporary copy; no original review decisions were changed.",
        }
        (OUTPUT / f"qa{suffix}.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"scale": scale, "screenshots": len(files), "status": "passed"}))
    finally:
        idle()
        window.close()
        window.deleteLater()
        app.processEvents()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scale", type=float, choices=(1, 1.5, 2), help="Capture one scale in this process")
    args = parser.parse_args()
    if args.scale is not None:
        capture_scale(args.scale)
    else:
        for scale in (1, 1.5, 2):
            subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "--scale", str(scale)], check=True, cwd=ROOT)
        print(f"Desktop screenshots and QA manifests: {OUTPUT}")


if __name__ == "__main__":
    main()
