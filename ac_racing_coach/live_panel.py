"""Small live prototype controls added to the existing review dashboard."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QDoubleSpinBox, QFrame, QHBoxLayout,
                               QLabel, QMessageBox, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from .live_runtime import LiveRuntime


class LivePanel(QScrollArea):
    def __init__(self, parent=None, *, runtime_factory=LiveRuntime):
        super().__init__(parent)
        self.runtime_factory = runtime_factory
        self.runtime = None
        self.shutting_down = False
        self.restarting = False
        self.setWidgetResizable(True)
        self.setFixedHeight(148)
        self.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setMinimumWidth(710)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(5)
        controls = QHBoxLayout()
        self.enable = QCheckBox("Enable Live Coach")
        self.mute = QCheckBox("Mute")
        self.test = QPushButton("Test Voice")
        self.calibrate = QPushButton("Calibrate corner…")
        self.lead = QDoubleSpinBox()
        self.lead.setRange(1, 15)
        self.lead.setValue(3)
        self.lead.setSingleStep(0.5)
        self.lead.setSuffix(" s")
        self.lead.setDecimals(1)
        self.lead.setEnabled(False)
        self.lead.setAccessibleName("Seconds between speech ending and the corner")
        self.lead.setToolTip("Desired finish lead: speech duration is added automatically. Applies at the current speed; validate while driving.")
        for widget in (self.enable, self.mute, self.test, self.calibrate):
            controls.addWidget(widget)
        controls.addStretch()
        controls.addWidget(QLabel("Finish lead"))
        controls.addWidget(self.lead)
        layout.addLayout(controls)
        self.caption = QLabel("Monza first chicane · Generic timing-test cue, not personalized advice. Enabling also saves a new CSV.")
        self.caption.setObjectName("muted")
        layout.addWidget(self.caption)
        self.status = QLabel("Live Coach off · Calibrate the corner before the first driving test")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setAccessibleName("Live coach status")
        layout.addWidget(self.status)
        self.details = QLabel("Local Windows voice · No automatic lap-validity detection")
        self.details.setTextFormat(Qt.TextFormat.PlainText)
        self.details.setObjectName("muted")
        self.details.setWordWrap(True)
        layout.addWidget(self.details)
        self.setWidget(body)
        self.enable.toggled.connect(self.toggle_enabled)
        self.mute.toggled.connect(self.toggle_muted)
        self.test.clicked.connect(self.test_voice)
        self.calibrate.clicked.connect(self.request_calibration)
        self.lead.valueChanged.connect(self.change_lead)
        self.calibrate.setEnabled(False)
        self.poll = QTimer(self)
        self.poll.setInterval(100)
        self.poll.timeout.connect(self.refresh)
        self.calibration_timer = QTimer(self)
        self.calibration_timer.setSingleShot(True)
        self.calibration_timer.timeout.connect(self.finish_calibration)

    def ensure_runtime(self):
        if self.runtime is None:
            self.runtime = self.runtime_factory()
            self.runtime.set_muted(self.mute.isChecked())
            self.poll.start()
        return self.runtime

    def toggle_enabled(self, enabled):
        if enabled:
            if self.runtime is not None and self.runtime.capture_stop.is_set():
                # A new session gets fresh workers and a fresh SessionTimeline.
                self.restarting = True
                self.runtime.close()
                self.status.setText("Finishing the previous capture before reconnecting…")
            else:
                self.ensure_runtime().set_enabled(True)
        elif self.runtime is not None:
            self.restarting = False
            self.calibration_timer.stop()
            self.runtime.set_enabled(False)
        self.refresh()

    def toggle_muted(self, muted):
        if self.runtime:
            self.runtime.set_muted(muted)
        self.refresh()

    def test_voice(self):
        self.ensure_runtime().test_voice()
        self.refresh()

    def change_lead(self, seconds):
        if self.runtime:
            self.runtime.set_lead_time(seconds)

    def request_calibration(self):
        box = QMessageBox(self)
        box.setWindowTitle("Driver calibration · first chicane")
        box.setText("Identify the first right-hand entry of Prima Variante yourself.")
        box.setInformativeText(
            "In a solo Monza practice session, stop with the front axle level with the start "
            "of the first chicane’s right-hand entry kerb. This marks the corner entry, not a braking target.\n\n"
            "Choose Save in 5 seconds, return to AC, leave the session unpaused, and stay stopped. "
            "The app checks live telemetry and the broad Monza section; only you confirm the exact location."
        )
        save = box.addButton("Save in 5 seconds", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is save and self.runtime and self.enable.isChecked():
            self.calibration_timer.start(5000)
            self.status.setText("Calibration in 5 seconds · return to AC and stay stopped, unpaused")

    def finish_calibration(self):
        if self.runtime and self.enable.isChecked() and not self.shutting_down:
            self.runtime.calibrate()

    def refresh(self):
        enabled = self.enable.isChecked()
        if self.restarting and self.runtime.finished:
            self.runtime = None
            self.restarting = False
            if enabled and not self.shutting_down:
                self.ensure_runtime().set_enabled(True)
        self.test.setEnabled(not enabled and not self.mute.isChecked() and not self.shutting_down)
        self.calibrate.setEnabled(enabled and not self.shutting_down and not self.restarting)
        if not self.runtime or self.restarting:
            return
        state = self.runtime.snapshot()
        self.lead.setEnabled(state["ready"] and not self.shutting_down)
        self.lead.blockSignals(True)
        self.lead.setValue(state["lead_time_s"])
        self.lead.blockSignals(False)
        calibration = "Driver-calibrated marker" if state["calibrated"] else "Calibration required"
        if not self.calibration_timer.isActive() and not self.shutting_down:
            prefix = "Muted · " if self.mute.isChecked() else ""
            self.status.setText(prefix + state["status"] + " · " + calibration)
        self.details.setText(state["error"] or state["capture"])
        self.details.setToolTip("\n".join(filter(None, (state["path"], state["log_path"],
                                                     f"Measured speech: {state['duration_s']:.3f} s"))))

    def shutdown(self):
        self.shutting_down = True
        self.restarting = False
        self.calibration_timer.stop()
        self.enable.setEnabled(False)
        self.mute.setEnabled(False)
        self.test.setEnabled(False)
        self.calibrate.setEnabled(False)
        if self.runtime:
            self.runtime.close()
            self.status.setText("Stopping live coaching and saving the recording…")

    @property
    def finished(self):
        return self.runtime is None or self.runtime.finished
