"""Deterministic, conservative one-corner timing coach; no recording or audio loop.

Call update() with the latest frame from the existing capture loop. All times
use that producer's SessionTimeline. The speech sink must be nonblocking and
latest-only (speak returns False when busy); it exposes measured duration_s and
cancel(). A generic cue does not depend on, or assert, lap validity.
"""

import math


TIMING_TEST_CUE = "First chicane ahead. Focus on a smooth brake release."


class LiveCoach:
    def __init__(self, profile, speech, log=None):
        self.profile = profile
        self.speech = speech
        self.log = log or (lambda event: None)
        self.enabled = False
        self.muted = False
        self.status = "Live coach disabled"
        self._reason = "disabled"
        self._previous = None
        self._identity = None
        self._continuous_m = 0.0
        self._armed = False
        self._consumed = False
        self._now_s = None

    def _emit(self, event, **fields):
        try:
            self.log({"event": event, "capture_time_s": self._now_s, **fields})
        except Exception:
            pass  # Diagnostics must never break telemetry or coaching.

    def _cancel(self):
        try:
            self.speech.cancel()
        except Exception as error:
            self._emit("speech_error", operation="cancel", error=str(error))

    def _reset_approach(self, *, consumed=None):
        self._previous = None
        self._continuous_m = 0.0
        self._armed = False
        if consumed is not None:
            self._consumed = consumed

    def _state(self, reason, text, *, suppress=False, **fields):
        changed = reason != self._reason
        if suppress:
            self._reset_approach()
            if changed:
                self._cancel()
        self.status = text
        self._reason = reason
        if changed:
            self._emit("suppressed" if suppress else "state", reason=reason, **fields)
        return self.status

    def set_enabled(self, enabled):
        enabled = bool(enabled)
        if self.enabled != enabled:
            self.enabled = enabled
            self._cancel()
            self._reset_approach(consumed=False)
            self._identity = None
            self._state("waiting" if enabled else "disabled",
                        "Waiting for live telemetry" if enabled else "Live coach disabled")

    def set_muted(self, muted):
        muted = bool(muted)
        if self.muted != muted:
            self.muted = muted
            self._cancel()
            self._reset_approach()
            self._state("muted" if muted else "waiting",
                        "Live coach muted" if muted else "Waiting for a full approach")

    def replace_profile(self, profile):
        self._cancel()
        self.profile = profile
        self._identity = None
        self._reset_approach(consumed=False)
        self._state("profile_changed", "Profile updated; waiting for a full approach")

    def close(self):
        self.enabled = False
        self._cancel()
        self._reset_approach(consumed=False)
        self._state("closed", "Live coach stopped")

    def update(self, frame, now_s):
        self._now_s = now_s
        if not self.enabled:
            return self._state("disabled", "Live coach disabled", suppress=True)
        if self.muted:
            return self._state("muted", "Live coach muted", suppress=True)
        if frame is None:
            return self._state("no_telemetry", "Waiting for live telemetry", suppress=True)
        if frame.status != 2:
            state = {0: "session ended", 1: "replay", 3: "paused"}.get(frame.status, "not live")
            reason = {0: "session_ended", 1: "replay", 3: "paused"}.get(frame.status, "not_live")
            return self._state(reason, f"Speech suppressed: {state}", suppress=True, ac_status=frame.status)
        if not self.profile.supports(frame.metadata):
            return self._state("unsupported_track", "Speech suppressed: unsupported track/layout", suppress=True)
        if not self.profile.calibrated:
            return self._state("uncalibrated", "Calibrate the first-chicane marker before enabling cues", suppress=True)
        try:
            age = now_s - frame.capture_time_s
            if not math.isfinite(age) or not 0 <= age <= self.profile.maximum_sample_age_s or not frame.fresh:
                return self._state("stale", "Speech suppressed: stale telemetry", suppress=True, age_s=age)
            sample = frame.sample
            if not sample:
                return self._state("missing_sample", "Speech suppressed: missing telemetry", suppress=True)
            if sample.get("in_pit") or sample.get("in_pit_lane"):
                return self._state("pits", "Speech suppressed: pits", suppress=True)
            position = float(sample["track_position"])
            speed = float(sample["speed_kmh"])
            laps = int(sample["completed_laps"])
            if not math.isfinite(position) or not 0 <= position <= 1 or not math.isfinite(speed) or not 0 <= speed <= 500:
                raise ValueError("Invalid position/speed")
        except (KeyError, TypeError, ValueError, OverflowError):
            return self._state("invalid_sample", "Speech suppressed: invalid telemetry", suppress=True)
        position %= 1.0
        identity = (frame.session_id, frame.segment)
        if self._identity != identity:
            previous_identity = self._identity
            self._identity = identity
            self._reset_approach(consumed=False)
            self._cancel()
            self._emit("session_reset", previous=previous_identity, current=identity)
        current = (frame.capture_time_s, position, speed, laps)
        previous = self._previous
        self._previous = current
        if previous is None:
            return self._state("waiting_approach", "Waiting for a full approach")
        dt = current[0] - previous[0]
        if dt == 0:
            return self.status  # GUI can poll the same frame repeatedly.
        if dt < 0 or dt > self.profile.maximum_sample_age_s:
            return self._state("capture_gap", "Speech suppressed: telemetry discontinuity", suppress=True, gap_s=dt)
        raw_delta = position - previous[1]
        lap_delta = laps - previous[3]
        wraps = raw_delta < -0.5
        if lap_delta < 0 or lap_delta > 1 or (wraps and not (previous[1] > 0.9 and position < 0.1 and lap_delta == 1)) or (lap_delta == 1 and not wraps):
            return self._state("counter_reset", "Speech suppressed: session reset or teleport", suppress=True)
        distance_m = (raw_delta % 1.0) * self.profile.track_length_m
        plausible_m = max(speed, previous[2]) / 3.6 * dt + 6.0
        if raw_delta < 0 and not wraps or distance_m > plausible_m:
            return self._state("teleport", "Speech suppressed: teleport or reverse motion", suppress=True,
                               movement_m=distance_m, plausible_m=plausible_m)
        remaining = ((self.profile.corner_position - position) % 1.0) * self.profile.track_length_m
        previous_remaining = ((self.profile.corner_position - previous[1]) % 1.0) * self.profile.track_length_m
        passed_marker = distance_m > 0 and previous_remaining <= distance_m + 0.001
        if passed_marker:
            self._consumed = False
            self._armed = False
            self._continuous_m = 0.0
            self._emit("marker_passed")
            return self._state("passed_marker", "Corner passed; waiting for the next approach")
        if self._consumed:
            return self._state("consumed", "Approach handled; waiting for the next approach")
        if speed < self.profile.minimum_speed_kmh:
            self._armed = False
            self._continuous_m = 0.0
            return self._state("slow", "Waiting for a moving approach")
        try:
            duration = float(self.speech.duration_s)
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError("Speech duration is unavailable")
        except (AttributeError, TypeError, ValueError):
            return self._state("voice_unavailable", "Speech unavailable: voice has not been prepared", suppress=True)
        # The latest-only consumer can run after the capture instant. Project
        # that bounded age forward rather than treating an old position as now.
        eta = max(0.0, remaining / (speed / 3.6) - age)
        target_eta = duration + self.profile.launch_margin_s + self.profile.lead_time_s
        minimum_eta = duration + self.profile.launch_margin_s + self.profile.minimum_finish_margin_s
        if remaining > self.profile.maximum_approach_distance_m:
            self._armed = False
            self._continuous_m = 0.0
            return self._state("outside_approach", "Waiting for the first-chicane approach")
        if eta > target_eta:
            self._continuous_m += distance_m
            if self._continuous_m >= self.profile.minimum_continuous_distance_m:
                self._armed = True
            return self._state("armed" if self._armed else "building_approach",
                               "Approach ready — generic timing-test cue" if self._armed else "Confirming a continuous approach")
        # Entering the window without an upstream run is never a genuine approach.
        self._consumed = True
        if not self._armed:
            return self._state("near_corner", "Cue skipped: approach started too near the corner", suppress=True,
                               distance_m=remaining, estimated_arrival_s=eta)
        self._armed = False
        if eta < minimum_eta:
            return self._state("too_late", "Cue skipped: too late to finish speaking", suppress=True,
                               distance_m=remaining, estimated_arrival_s=eta, required_s=minimum_eta)
        try:
            accepted = self.speech.speak(TIMING_TEST_CUE)
        except Exception as error:
            self._emit("speech_error", operation="speak", error=str(error))
            return self._state("speech_failed", "Cue skipped: local speech failed", suppress=True)
        if not accepted:
            return self._state("speech_busy", "Cue skipped: voice busy; no cue queued")
        self._emit("cue", generic_timing_test=True, session_id=frame.session_id, segment=frame.segment,
                   distance_m=remaining, speed_kmh=speed, estimated_arrival_s=eta,
                   telemetry_age_s=age,
                   speech_duration_s=duration, configured_finish_lead_s=self.profile.lead_time_s,
                   launch_margin_s=self.profile.launch_margin_s,
                   estimated_finish_margin_s=eta - duration - self.profile.launch_margin_s)
        return self._state("cue_started", "Speaking: generic first-chicane timing-test cue")
