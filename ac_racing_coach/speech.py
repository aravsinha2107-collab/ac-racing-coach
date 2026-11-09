"""Offline Windows speech, prepared once and played without a speech queue.

Call ``prepare`` on a worker: System.Speech renders the fixed timing-test cue to
an owned temporary WAV. Nothing is spoken while preparing. Its actual sample
duration is used by the live coach; driver/device latency still needs a driving
test. ``speak`` starts cached audio asynchronously or declines it immediately.

Windows playback uses PlaySound(None, 0) to stop pending/active audio; SND_PURGE
is deliberately avoided because it is unsupported on modern Windows:
https://docs.python.org/3/library/winsound.html
Local synthesis API:
https://learn.microsoft.com/en-us/dotnet/api/system.speech.synthesis.speechsynthesizer.setoutputtowavefile
"""

from pathlib import Path
import os
import subprocess
import sys
import tempfile
import threading
import time
import wave


CUE_TEXT = "First chicane ahead. Focus on a smooth brake release."


def _render_local_wav(path, text):
    """Use an installed Windows desktop voice; never contact a service."""
    if sys.platform != "win32":
        raise RuntimeError("Local speech requires Windows and an installed desktop voice.")
    if text != CUE_TEXT:
        raise ValueError("This prototype only prepares the fixed timing-test cue.")
    powershell = (Path(os.environ.get("SystemRoot", r"C:\Windows"))
                  / "System32/WindowsPowerShell/v1.0/powershell.exe")
    script = Path(__file__).with_name("render_speech.ps1")
    try:
        result = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
             "Bypass", "-File", str(script), "-OutputPath", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Local voice preparation timed out after 10 seconds.") from exc
    if result.returncode:
        detail = result.stderr.strip() or "Windows speech synthesis failed."
        raise RuntimeError(detail)
    return result.stdout.strip()


def _play_wav(path):
    import winsound

    if path is None:
        winsound.PlaySound(None, 0)
    else:
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC
                           | winsound.SND_NODEFAULT)


class WindowsSpeech:
    """Thread-safe single-cue output with no pending-message queue.

    Construction is cheap. Preparation may take up to 10 seconds and belongs on
    a worker, never on the UI or telemetry producer. Cancellation also works
    during preparation: preparation never schedules playback in the first place.
    ``close`` prevents any in-progress preparation from making output available.
    Injected renderer/player/clock allow tests without audio devices or Windows.
    """

    def __init__(self, *, renderer=None, player=None, clock=time.monotonic):
        self._renderer = renderer or _render_local_wav
        self._player = player or _play_wav
        self._clock = clock
        self._lock = threading.RLock()
        self._temporary = None
        self._path = None
        self._closed = False
        self._preparing = False
        self._active_until = 0.0
        self._has_playback = False
        self.duration_s = 0.0
        self.voice_name = ""
        self.error = None

    @property
    def ready(self):
        with self._lock:
            return self._path is not None and not self._closed and not self.error

    @property
    def busy(self):
        with self._lock:
            return self._has_playback and self._clock() < self._active_until

    @property
    def status(self):
        with self._lock:
            if self._closed:
                return "Voice closed"
            if self.error:
                return f"Voice unavailable: {self.error}"
            if self._preparing:
                return "Preparing local Windows voice"
            if not self.ready:
                return "Voice not prepared"
            if self.busy:
                return "Speaking generic timing-test cue"
            return f"Local voice ready ({self.duration_s:.2f} s cue)"

    def prepare(self):
        """Render and measure the fixed phrase; returns False on a contained error."""
        with self._lock:
            if self._closed or self._preparing:
                return False
            if self.ready:
                return True
            self._preparing = True
            self.error = None
        temporary = None
        try:
            temporary = tempfile.TemporaryDirectory(prefix="ac-coach-voice-")
            path = Path(temporary.name) / "first-chicane.wav"
            voice_name = self._renderer(path, CUE_TEXT)
            with wave.open(str(path), "rb") as audio:
                duration_s = audio.getnframes() / audio.getframerate()
                if audio.getcomptype() != "NONE" or not 0 < duration_s <= 30:
                    raise ValueError("The local voice produced an unusable audio file.")
            with self._lock:
                if self._closed:
                    return False
                self._temporary = temporary
                temporary = None
                self._path = path
                self.duration_s = duration_s
                self.voice_name = voice_name
                return True
        except Exception as exc:
            with self._lock:
                self.error = str(exc)
            return False
        finally:
            if temporary is not None:
                temporary.cleanup()
            with self._lock:
                self._preparing = False

    def speak(self, text=CUE_TEXT):
        """Accept only idle, ready output; rejected cues are never saved for later."""
        with self._lock:
            if text != CUE_TEXT or not self.ready or self.busy:
                return False
            try:
                self._player(self._path)
            except Exception as exc:
                self.error = str(exc)
                return False
            # A small guard prevents a repeated request overlapping a device's
            # startup tail. It is not an assertion of measured device latency.
            self._active_until = self._clock() + self.duration_s + 0.2
            self._has_playback = True
            return True

    def cancel(self):
        """Stop playback immediately; there is no deferred cue to drain."""
        with self._lock:
            if self._has_playback:
                try:
                    self._player(None)
                except Exception as exc:
                    self.error = f"Could not stop voice: {exc}"
            self._active_until = 0.0
            self._has_playback = False

    def close(self):
        """Cancel, disable future output and remove this instance's audio file."""
        with self._lock:
            self._closed = True
            self.cancel()
            temporary, self._temporary = self._temporary, None
            self._path = None
        if temporary is not None:
            temporary.cleanup()
