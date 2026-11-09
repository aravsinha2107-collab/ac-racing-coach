"""Driver-calibrated location and generic timing settings for one AC corner.

The shipped profile deliberately has no corner coordinate. A location is only
stored after the driver identifies the first chicane in the running game. It is
an approach marker, never a braking target or a claim about lap validity.
"""

from dataclasses import asdict, dataclass, replace
import json
import math
from pathlib import Path
import tempfile


DEFAULT_PROFILE_PATH = Path(__file__).resolve().parent.parent / "profiles" / "monza_first_chicane.json"


@dataclass(frozen=True)
class CornerProfile:
    profile_version: int = 1
    track: str = "monza"
    track_configuration: str = ""
    track_length_m: float = 5755.02
    corner_name: str = "First chicane"
    corner_position: float | None = None
    calibration_source: str = "uncalibrated"
    calibration_min_position: float = 0.123
    calibration_max_position: float = 0.178
    location_evidence: str = "Installed original Monza data/sections.ini: starting straight ends 0.123; Prima Variante 0.151–0.178"
    lead_time_s: float = 3.0
    launch_margin_s: float = 0.35
    minimum_finish_margin_s: float = 1.0
    maximum_approach_distance_m: float = 1600.0
    minimum_continuous_distance_m: float = 100.0
    minimum_speed_kmh: float = 30.0
    maximum_sample_age_s: float = 0.25

    def __post_init__(self):
        if self.profile_version != 1:
            raise ValueError("Unsupported corner profile version")
        if self.track != "monza" or self.track_configuration != "":
            raise ValueError("This prototype supports only the original Monza GP layout")
        if not math.isfinite(self.track_length_m) or abs(self.track_length_m - 5755.02) > 2:
            raise ValueError("Unexpected Monza GP track length")
        if self.corner_position is not None:
            if not math.isfinite(self.corner_position) or not 0 <= self.corner_position < 1:
                raise ValueError("Corner position must be a normalized track position")
            if self.calibration_source != "driver-attested":
                raise ValueError("A corner coordinate must come from driver calibration")
            if not self.calibration_min_position <= self.corner_position <= self.calibration_max_position:
                raise ValueError("Corner position is outside the first-chicane calibration area")
        if (self.calibration_min_position != 0.123 or self.calibration_max_position != 0.178):
            raise ValueError("Calibration bounds must match the installed Monza section evidence")
        for name in ("lead_time_s", "launch_margin_s", "minimum_finish_margin_s",
                     "maximum_approach_distance_m", "minimum_continuous_distance_m",
                     "minimum_speed_kmh", "maximum_sample_age_s"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        if not 1 <= self.lead_time_s <= 15:
            raise ValueError("Lead time must be between 1 and 15 seconds")
        if self.minimum_finish_margin_s > self.lead_time_s:
            raise ValueError("Minimum finish margin must not exceed lead time")
        if self.maximum_approach_distance_m >= self.track_length_m / 2:
            raise ValueError("Approach distance must be less than half a lap")
        if self.minimum_continuous_distance_m >= self.maximum_approach_distance_m:
            raise ValueError("Continuous approach distance exceeds the approach zone")

    @property
    def calibrated(self):
        return self.corner_position is not None and self.calibration_source == "driver-attested"

    def supports(self, metadata):
        try:
            length = float(metadata.get("track_length_m", 0))
        except (TypeError, ValueError):
            return False
        return (metadata.get("track") == self.track
                and metadata.get("track_configuration") == self.track_configuration
                and math.isfinite(length)
                and abs(length - self.track_length_m) <= 2.0)


def load_profile(path=DEFAULT_PROFILE_PATH):
    with Path(path).open(encoding="utf-8") as source:
        return CornerProfile(**json.load(source))


def save_profile(profile, path):
    """Atomically replace this profile; recordings and validity files are untouched."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=destination.parent,
                                         prefix=destination.name + ".", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            json.dump(asdict(profile), output, indent=2)
            output.write("\n")
        temporary.replace(destination)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def calibrate_profile(profile, frame, now_s):
    """Accept a driver-marked position only while stopped, live, fresh and out of pits.

    Software cannot identify the chicane here. The driver must physically stop
    at the agreed marker and confirm its identity before invoking this function.
    """
    if not profile.supports(frame.metadata):
        raise ValueError("Calibration requires the original Monza GP layout")
    if (frame.status != 2 or not frame.fresh or not math.isfinite(now_s)
            or not 0 <= now_s - frame.capture_time_s <= profile.maximum_sample_age_s):
        raise ValueError("Calibration requires fresh live telemetry")
    sample = frame.sample
    if not sample or sample.get("in_pit") or sample.get("in_pit_lane"):
        raise ValueError("Calibration must take place outside the pits")
    try:
        speed = float(sample["speed_kmh"])
        position = float(sample["track_position"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Calibration telemetry is incomplete") from error
    if not math.isfinite(speed) or not 0 <= speed <= 3:
        raise ValueError("Stop the car at the first-chicane marker before calibrating")
    if not math.isfinite(position) or not 0 <= position < 1:
        raise ValueError("Calibration track position is invalid")
    if not profile.calibration_min_position <= position <= profile.calibration_max_position:
        raise ValueError("Stop at the first-chicane entry: this position is outside the verified Monza section")
    return replace(profile, corner_position=position, calibration_source="driver-attested")
