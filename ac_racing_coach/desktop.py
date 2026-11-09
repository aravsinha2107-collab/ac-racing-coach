"""Import, driver review, and comparison. All disk/analysis work is serialized off the UI thread."""

from io import BytesIO
from pathlib import Path
import sys

from PySide6.QtCore import Qt, QThread, QTimer
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QLabel, QMainWindow, QScrollArea, QTableWidgetItem,
)

from .analysis import coaching_notes, identity, time_label
from .plotting import plot_laps
from .review import open_recording, review_laps, select_comparison, assert_current
from .desktop_theme import dark_palette, feedback_html
from .dashboard_layout import build_layout
from .live_panel import LivePanel


VALIDITY_LABELS = {"unknown": "Unknown", "manual_valid": "Manually approved", "invalid": "Invalid"}


class Task(QThread):
    """One operation at a time also prevents concurrent Matplotlib access."""

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.result = None
        self.error = None

    def run(self):
        try:
            self.result = self.operation()
        except Exception as error:
            self.error = str(error) or type(error).__name__


class GraphView(QScrollArea):
    def __init__(self):
        super().__init__()
        self.setObjectName("graphView")
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self.label.setWordWrap(True)
        self.setWidget(self.label)
        self.setWidgetResizable(True)
        self.original = QPixmap()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_image(self, data):
        self.original = QPixmap()
        if not self.original.loadFromData(data, "PNG"):
            raise ValueError("Could not display the generated graph")
        self.fit_image()
        self.verticalScrollBar().setValue(0)

    def clear(self):
        self.original = QPixmap()
        self.label.clear()
        self.label.setMinimumSize(0, 0)

    def fit_image(self):
        if not self.original.isNull():
            width = max(700, self.viewport().width() - 2)
            ratio = self.devicePixelRatioF()
            scaled = self.original.scaledToWidth(round(width * ratio), Qt.TransformationMode.SmoothTransformation)
            scaled.setDevicePixelRatio(ratio)
            self.label.setPixmap(scaled)
            self.label.setMinimumSize(width, round(scaled.height() / ratio))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_image()


def generate_comparison(snapshot, reference, current):
    laps = select_comparison(snapshot, reference, current)
    notes = coaching_notes(laps)
    image = BytesIO()
    plot_laps(laps, image, theme="dark")
    assert_current(snapshot)
    return image.getvalue(), notes, reference, current


class CoachWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.snapshot = None
        self.task = None
        self.task_kind = None
        self.closing = False
        self.has_report = False
        build_layout(self, GraphView)
        self.live_panel = LivePanel(self)
        self.centralWidget().layout().insertWidget(0, self.live_panel)
        self.close_poll = QTimer(self)
        self.close_poll.setInterval(50)
        self.close_poll.timeout.connect(self.finish_close)
        self.update_controls()
        self.welcome_open.setFocus()

    @property
    def open_button(self):
        return self.header_open if self.pages.currentWidget() is self.workspace_scroll else self.welcome_open

    def keep_focus_visible(self, _old, current):
        if current is not None and self.workspace.isAncestorOf(current):
            self.workspace_scroll.ensureWidgetVisible(current, 12, 12)

    def clear_result(self):
        self.has_report = False
        self.graph.clear()
        self.feedback.clear()
        self.result_label.setText("Lap comparison")
        self.results_stack.setCurrentWidget(self.result_state_panel)

    def selection_changed(self):
        self.clear_result()
        for combo in (self.reference_combo, self.current_combo):
            combo.setToolTip(combo.currentText())
        self.update_controls()

    def update_controls(self):
        idle = self.task is None
        loaded = self.snapshot is not None
        self.welcome_open.setEnabled(idle)
        self.header_open.setEnabled(idle)
        self.reload_button.setEnabled(idle and loaded)
        self.table.setEnabled(idle)
        selected = loaded and self.table.selectionModel().hasSelection()
        self.review_panel.setVisible(bool(selected))
        if selected:
            self.review_title.setText(f"Review lap {self.table.currentRow() + 1}")
        for button in (self.approve_button, self.invalid_button, self.unknown_button):
            button.setEnabled(idle and selected)
        for combo in (self.reference_combo, self.current_combo):
            combo.setEnabled(idle and loaded)
        reference, current = self.reference_combo.currentData(), self.current_combo.currentData()
        ready = bool(loaded and reference and current and reference < current)
        approved = sum(lap.coaching_eligible for lap in self.snapshot.laps) if loaded else 0
        hint = "Only manually approved laps are available."
        if ready:
            ref_row = self.snapshot.laps[reference - 1].rows[0]
            cur_row = self.snapshot.laps[current - 1].rows[0]
            ready = identity(ref_row) == identity(cur_row) and ref_row["session_type"] == cur_row["session_type"]
            hint = ("Comparison lap follows the reference." if ready else
                    "Select laps with matching car, track, tyres, and session type.")
        elif approved >= 2:
            hint = "Choose an earlier reference and a later comparison lap."
        self.compare_button.setEnabled(idle and ready)
        self.selection_hint.setText(hint)
        if not loaded:
            self.pages.setCurrentWidget(self.welcome_page)
            self.welcome_progress.setVisible(not idle)
            self.welcome_open.setVisible(idle)
            if not idle:
                self.welcome_title.setText("Opening recording")
                self.welcome_subtitle.setText("Reading telemetry and saved lap reviews…")
            elif not self.error_label.isHidden():
                self.welcome_title.setText("Could not open recording")
                self.welcome_subtitle.setText("Check the recording and its review, or choose another file.")
            else:
                self.welcome_title.setText("Review your driving")
                self.welcome_subtitle.setText("Open a recording to inspect laps and compare your inputs.")
        else:
            self.pages.setCurrentWidget(self.workspace_scroll)
            if not self.has_report:
                self.results_stack.setCurrentWidget(self.result_state_panel)
                if not idle:
                    self.state_title.setText("Comparing laps" if self.task_kind == "compare" else "Saving review")
                    self.state_description.setText("Calculating measured differences and drawing traces…" if self.task_kind == "compare" else "Updating the selected lap’s review…")
                elif not self.snapshot.laps:
                    self.state_title.setText("No complete laps found")
                    self.state_description.setText("This recording has no laps that pass the capture checks.")
                elif approved < 2:
                    self.state_title.setText("Review laps to compare" if approved == 0 else "One more approved lap needed")
                    self.state_description.setText("Select a lap in the sidebar to review its validity. Two approved laps are needed for a comparison.")
                else:
                    self.state_title.setText("Choose your comparison")
                    self.state_description.setText("Select a reference and a later lap above to inspect their speed, pedal inputs and time difference.")

    def pick_recording(self):
        if self.task:
            return
        start = self.snapshot.path.parent if self.snapshot else Path(__file__).resolve().parents[1] / "recordings"
        path, _ = QFileDialog.getOpenFileName(self, "Open AC recording", str(start), "Telemetry recordings (*.csv)",
                                             options=QFileDialog.Option.DontUseNativeDialog)
        if path:
            self.load_recording(path)

    def reload_recording(self):
        if self.snapshot:
            self.load_recording(self.snapshot.path)

    def load_recording(self, path):
        if self.task:
            return
        self.snapshot = None
        self.table.setRowCount(0)
        self.reference_combo.clear()
        self.current_combo.clear()
        self.start_task("load", lambda: open_recording(Path(path)), "Loading recording and validity review…")

    def review_selected(self, state):
        if self.snapshot is None or self.task or not self.table.selectionModel().hasSelection():
            return
        snapshot, number = self.snapshot, self.table.currentRow() + 1
        self.start_task("review", lambda: review_laps(snapshot, {number: state}), "Saving driver review…")

    def compare_selected(self):
        if self.snapshot is None or self.task:
            return
        snapshot = self.snapshot
        reference, current = self.reference_combo.currentData(), self.current_combo.currentData()
        self.start_task("compare", lambda: generate_comparison(snapshot, reference, current), "Analyzing laps and rendering graphs…")

    def start_task(self, kind, operation, message):
        self.clear_result()
        self.error_label.hide()
        self.task_kind = kind
        self.task = Task(operation, self)
        self.task.finished.connect(self.task_finished)
        self.progress.show()
        self.statusBar().showMessage(message)
        self.update_controls()
        self.task.start()

    def task_finished(self):
        task, kind = self.task, self.task_kind
        self.task = None
        self.progress.hide()
        if task.error:
            self.error_label.setText(task.error + " Reopen the recording to refresh its review.")
            self.error_label.show()
            self.snapshot = None
            self.table.setRowCount(0)
            self.reference_combo.clear()
            self.current_combo.clear()
            self.lap_summary.setText("Recording unavailable")
            self.statusBar().showMessage("Could not complete the operation")
        elif kind in ("load", "review"):
            self.snapshot = task.result
            self.show_recording(preserve_selection=kind == "review")
            self.statusBar().showMessage("Driver review saved" if kind == "review" else "Recording loaded with matching validity review")
        else:
            image, notes, reference, current = task.result
            self.graph.set_image(image)
            self.feedback.setHtml(feedback_html(notes))
            self.result_label.setText(f"Reference: lap {reference}  /  Comparison: lap {current}")
            self.has_report = True
            self.results_stack.setCurrentWidget(self.tabs)
            self.tabs.setCurrentIndex(0)
            self.statusBar().showMessage("Comparison ready · Graphs and feedback available")
        task.deleteLater()
        self.update_controls()
        if self.closing:
            self.close()

    def show_recording(self, *, preserve_selection=False):
        snapshot = self.snapshot
        previous_row = self.table.currentRow() if preserve_selection else -1
        self.file_label.set_full_text(snapshot.path.name)
        self.file_label.setToolTip(str(snapshot.path))
        if snapshot.laps:
            metadata = snapshot.laps[0].rows[0]
            car = {"yk_1988_mclaren_mp4-4": "McLaren MP4/4 (1988)"}.get(metadata["car_model"], metadata["car_model"].replace("_", " "))
            track = metadata["track"].replace("_", " ").title()
            if metadata["track_configuration"]:
                track += " / " + metadata["track_configuration"]
            self.car_track_label.set_full_text(f"{car}  /  {track}")
        else:
            self.car_track_label.set_full_text("Recording review")
        self.table.setRowCount(len(snapshot.laps))
        for row_index, lap in enumerate(snapshot.laps):
            values = (str(row_index + 1), time_label(lap.seconds), VALIDITY_LABELS[lap.validity])
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 2:
                    item.setData(Qt.ItemDataRole.UserRole, lap.validity)
                elif column == 1:
                    font = QFont("Segoe UI")
                    font.setPixelSize(15)
                    font.setWeight(QFont.Weight.DemiBold)
                    item.setFont(font)
                item.setToolTip(f"Lap {row_index + 1} · {len(lap.gaps)} marked data gap(s)")
                self.table.setItem(row_index, column, item)
        eligible = sum(lap.coaching_eligible for lap in snapshot.laps)
        self.lap_summary.setText(f"{len(snapshot.laps)} complete · {eligible} approved")
        self.lap_summary.setToolTip(f"{len(snapshot.excluded)} partial or unsuitable portions excluded by capture checks")
        for combo in (self.reference_combo, self.current_combo):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("Select lap…", None)
            for number, lap in enumerate(snapshot.laps, 1):
                if lap.coaching_eligible:
                    combo.addItem(f"Lap {number} · {time_label(lap.seconds)}", number)
            combo.blockSignals(False)
        self.table.clearSelection()
        if previous_row >= 0 and snapshot.laps:
            self.table.selectRow(min(previous_row, len(snapshot.laps) - 1))

    def closeEvent(self, event):
        self.closing = True
        self.live_panel.shutdown()
        if self.task or not self.live_panel.finished:
            self.statusBar().showMessage("Finishing the current operation before closing…")
            self.close_poll.start()
            event.ignore()
        else:
            self.close_poll.stop()
            event.accept()

    def finish_close(self):
        if self.task is None and self.live_panel.finished:
            self.close()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("AC Racing Coach")
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    window = CoachWindow()
    window.show()
    return app.exec()

