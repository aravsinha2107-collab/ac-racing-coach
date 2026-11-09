"""Single capture producer, bounded live observer, and isolated coaching worker.

No Qt dependency. The UI polls a small status snapshot; it never reads game
memory, renders speech, joins a worker, or waits for analysis on its own thread.
"""

from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
import threading

from .recording import (CaptureLease, MemoryPage, PHYSICS, GRAPHICS_SIZE,
                        STATIC_SIZE, parse_metadata, record)
from .session import SessionTimeline


ROOT = Path(__file__).resolve().parents[1]
PROFILE_TEMPLATE = ROOT / "profiles" / "monza_first_chicane.json"
PROFILE_LOCAL = ROOT / "profiles" / "monza_first_chicane.local.json"


class LatestTelemetry:
    """At most two frames: a safety barrier and the newest observation.

    A brief pause/pit/stale frame cannot disappear behind a newer live sample.
    Reset segments also remain visible even if the consumer misses many frames.
    No telemetry or speech backlog accumulates when a consumer runs slowly.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.latest = None
        self.barrier = None
        self.previous = None

    def publish(self, frame):
        with self.lock:
            unsafe = frame.status != 2 or not frame.fresh or not frame.sample
            if frame.sample:
                unsafe |= bool(frame.sample.get("in_pit") or frame.sample.get("in_pit_lane"))
            if self.previous:
                unsafe |= ((frame.session_id, frame.segment, dict(frame.metadata)) !=
                           (self.previous.session_id, self.previous.segment, dict(self.previous.metadata)))
            if unsafe:
                # A reset barrier is deliberately not eligible even if the
                # current sample is live. The subsequent latest frame can rearm
                # only by following the engine's continuous-approach checks.
                self.barrier = replace(frame, fresh=False)
            self.latest = frame
            self.previous = frame

    def take(self):
        with self.lock:
            frames = []
            if self.barrier is not None:
                frames.append(self.barrier)
            if self.latest is not None and self.latest is not self.barrier:
                frames.append(self.latest)
            self.barrier = self.latest = None
            return frames


class SpeechGate:
    """Atomic mute/disable versus playback; cancellation never waits for TTS."""

    def __init__(self, output):
        self.output = output
        self.lock = threading.Lock()
        self.enabled = False
        self.muted = False
        self.closed = False

    @property
    def duration_s(self):
        return self.output.duration_s

    def configure(self, *, enabled=None, muted=None, closed=None):
        with self.lock:
            for key, value in (("enabled", enabled), ("muted", muted), ("closed", closed)):
                if value is not None:
                    setattr(self, key, value)
            if not self.enabled or self.muted or self.closed:
                self.output.cancel()

    def speak(self, text):
        with self.lock:
            if not self.enabled or self.muted or self.closed:
                return False
            return self.output.speak(text)

    def test(self, text):
        with self.lock:
            if self.enabled or self.muted or self.closed:
                return False
            return self.output.speak(text)

    def cancel(self):
        with self.lock:
            self.output.cancel()


class LiveRuntime:
    """Lazily created by an explicit Enable or Test Voice action.

    Enabling saves a new CSV through record(), whose callback only publishes to
    LatestTelemetry. Coach exceptions stop speech, never that capture worker.
    A stopped/ended capture is not automatically reconnected or reused: the
    driver explicitly disables and enables for a new session and timeline.
    """

    def __init__(self, *, root=ROOT, speech_factory=None, capture_runner=None):
        self.root = Path(root)
        self.timeline = SessionTimeline()
        self.mailbox = LatestTelemetry()
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.capture_stop = threading.Event()
        self.capture_done = threading.Event()
        self.capture_thread = None
        self.gate = None
        self.enabled = False
        self.muted = False
        self.lead_time_s = None
        self.test_requested = False
        self.calibration_requested = False
        self._state = {"status": "Preparing local Windows voice…", "capture": "Not recording",
                       "path": "", "log_path": "", "ready": False, "error": "",
                       "calibrated": False, "duration_s": 0.0, "lead_time_s": 3.0}
        self.speech_factory = speech_factory
        self.capture_runner = capture_runner or self._capture
        self.worker = threading.Thread(target=self._run, name="AC live coach", daemon=True)
        self.worker.start()

    def snapshot(self):
        with self.lock:
            return dict(self._state)

    def _set(self, **state):
        with self.lock:
            self._state.update(state)

    def set_enabled(self, enabled):
        with self.lock:
            self.enabled = bool(enabled)
            self.test_requested = False
            gate = self.gate
        if gate:
            gate.configure(enabled=enabled and not self.capture_done.is_set()
                           and not self.capture_stop.is_set())
        if enabled and self.capture_thread is None:
            self._set(capture="Connecting to AC…")
            self.capture_thread = threading.Thread(target=self._capture_entry,
                                                   name="AC single capture", daemon=True)
            self.capture_thread.start()
        elif not enabled:
            self.capture_stop.set()

    def set_muted(self, muted):
        with self.lock:
            self.muted = bool(muted)
            self.test_requested = False
            gate = self.gate
        if gate:
            gate.configure(muted=muted)

    def test_voice(self):
        with self.lock:
            if not self.enabled and not self.muted:
                self.test_requested = True  # repeated clicks collapse to one

    def calibrate(self):
        with self.lock:
            self.calibration_requested = True

    def set_lead_time(self, seconds):
        with self.lock:
            self.lead_time_s = float(seconds)

    def close(self):
        self.stop_event.set()
        self.capture_stop.set()
        with self.lock:
            self.test_requested = False
            gate = self.gate
        if gate:
            gate.configure(closed=True)

    @property
    def finished(self):
        return not self.worker.is_alive() and (self.capture_thread is None or not self.capture_thread.is_alive())

    def _capture_entry(self):
        try:
            self.capture_runner()
        except Exception as error:
            self._set(capture=f"Capture stopped: {error}")
        finally:
            self.capture_done.set()
            if self.gate:
                self.gate.configure(enabled=False)

    def _capture(self):
        with ExitStack() as stack:
            stack.enter_context(CaptureLease())
            physics = stack.enter_context(MemoryPage("acpmf_physics", PHYSICS.size))
            graphics = stack.enter_context(MemoryPage("acpmf_graphics", GRAPHICS_SIZE))
            static = stack.enter_context(MemoryPage("acpmf_static", STATIC_SIZE))
            metadata = parse_metadata(static.read())
            if not metadata["track"] or not metadata["car_model"]:
                raise ValueError("Enter a driving session first, then disable and enable Live Coach.")
            folder = self.root / "recordings"
            folder.mkdir(exist_ok=True)
            path = folder / f"session_{datetime.now():%Y%m%d_%H%M%S_%f}_live.csv"
            output = stack.enter_context(path.open("x", newline="", encoding="utf-8"))
            self._set(path=str(path), capture="Recording telemetry · validity unknown")
            record(physics, graphics, metadata, output, timeline=self.timeline,
                   observer=self.mailbox.publish, stop_event=self.capture_stop,
                   metadata_provider=lambda: parse_metadata(static.read()),
                   report=lambda *args, **kwargs: None)
            self._set(capture="Recording saved · disable and re-enable for another session")

    def _run(self):
        # Imports and synthesis are kept off both the Qt thread and the recorder.
        from .corner_profile import load_profile, save_profile, calibrate_profile
        from .live_coaching import LiveCoach
        from .speech import WindowsSpeech, CUE_TEXT

        speech = None
        log_file = None
        engine = None
        last_frame = None
        try:
            folder = self.root / "logs"
            folder.mkdir(exist_ok=True)
            log_path = folder / f"live_{datetime.now():%Y%m%d_%H%M%S_%f}.jsonl"
            log_file = log_path.open("x", encoding="utf-8", buffering=1)
            self._set(log_path=str(log_path))

            def log(event):
                log_file.write(json.dumps({"timeline_session_id": self.timeline.session_id,
                                           "logged_at_capture_time_s": self.timeline.elapsed(),
                                           **event}, ensure_ascii=False) + "\n")

            local = self.root / "profiles" / PROFILE_LOCAL.name
            profile = load_profile(local if local.exists() else self.root / "profiles" / PROFILE_TEMPLATE.name)
            self._set(calibrated=profile.calibrated, lead_time_s=profile.lead_time_s)
            speech = (self.speech_factory or WindowsSpeech)()
            gate = SpeechGate(speech)
            with self.lock:
                self.gate = gate
                gate.configure(enabled=self.enabled and not self.capture_done.is_set(),
                               muted=self.muted, closed=self.stop_event.is_set())
            if not speech.prepare():
                raise RuntimeError(speech.error or "Local Windows speech is unavailable")
            log({"event": "voice_ready", "duration_s": speech.duration_s,
                 "voice": getattr(speech, "voice_name", ""), "generic_timing_test": True})
            engine = LiveCoach(profile, gate, log=log)
            self._set(ready=True, duration_s=speech.duration_s)
            while not self.stop_event.wait(0.02):
                with self.lock:
                    enabled, muted = self.enabled, self.muted
                    test, self.test_requested = self.test_requested, False
                    calibrate, self.calibration_requested = self.calibration_requested, False
                    lead, self.lead_time_s = self.lead_time_s, None
                engine.set_enabled(enabled and not self.capture_done.is_set())
                engine.set_muted(muted)
                frames = self.mailbox.take()
                if frames:
                    last_frame = frames[-1]
                if lead is not None or calibrate:
                    try:
                        candidate = replace(profile, lead_time_s=lead) if lead is not None else profile
                        if calibrate:
                            if not enabled or self.capture_done.is_set() or last_frame is None:
                                raise ValueError("Enable Live Coach in a live Monza session before calibrating.")
                            candidate = calibrate_profile(candidate, last_frame, self.timeline.elapsed())
                        save_profile(candidate, local)
                        profile = candidate
                        engine.replace_profile(profile)
                        self._set(calibrated=profile.calibrated, lead_time_s=profile.lead_time_s, error="")
                        log({"event": "profile_saved", "corner_position": profile.corner_position,
                             "lead_time_s": profile.lead_time_s, "driver_calibration": calibrate})
                    except (ValueError, OSError) as error:
                        self._set(error=str(error))
                        log({"event": "profile_rejected", "reason": str(error)})
                now = self.timeline.elapsed()
                for frame in frames:
                    engine.update(frame, now)
                if not frames and last_frame is not None:
                    engine.update(last_frame, now)  # watchdog even if producer died
                if test:
                    accepted = gate.test(CUE_TEXT)
                    log({"event": "voice_test", "accepted": accepted})
                if speech.error:
                    raise RuntimeError(speech.error)
                if self.capture_done.is_set() and enabled:
                    status = "Session stopped · disable and enable Live Coach to reconnect"
                elif not enabled:
                    status = "Test voice playing · generic timing-test cue" if speech.busy else "Live Coach off"
                else:
                    status = engine.status
                self._set(status=status)
        except Exception as error:
            self._set(status="Live coaching unavailable · recording remains independent",
                      error=str(error), ready=False)
            if log_file:
                try:
                    log_file.write(json.dumps({"event": "coach_error", "reason": str(error)}) + "\n")
                except OSError:
                    pass
        finally:
            if engine:
                try:
                    engine.close()
                except Exception:
                    pass
            if speech:
                speech.close()
            if log_file:
                log_file.close()
