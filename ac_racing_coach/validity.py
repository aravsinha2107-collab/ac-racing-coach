"""Explicit driver review, bound to exact CSV bytes; no inferred clean laps."""

import hashlib
import json
from pathlib import Path
from tempfile import NamedTemporaryFile


_NOT_CHECKED = object()


def marker_path(source):
    return Path(source).with_suffix(".validity.json")


def read_review(source):
    source = Path(source)
    path = marker_path(source)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if not path.exists():
        return {"version": 1, "csv_sha256": digest, "laps": {}}
    try:
        review = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(review, dict) or review.get("version") != 1
                or review.get("csv_sha256") != digest
                or not isinstance(review.get("laps"), dict)):
            raise ValueError("unsupported or stale review")
        for key, state in review["laps"].items():
            bounds = key.split(":")
            if (len(bounds) != 2 or not all(b.isdigit() for b in bounds)
                    or int(bounds[0]) < 2 or int(bounds[1]) < int(bounds[0])
                    or state not in ("manual_valid", "invalid", "unknown")):
                raise ValueError("invalid review entry")
        return review
    except (ValueError, TypeError) as error:
        raise ValueError(f"{path.name}: invalid or stale validity review; no coaching allowed") from error


def apply_review(rows, source):
    for key, state in read_review(source)["laps"].items():
        first, last = map(int, key.split(":"))
        if last > len(rows) + 1:
            raise ValueError("Validity review row range exceeds recording")
        for row in rows[first - 2:last - 1]:
            # A raw invalid observation cannot be overridden by manual approval.
            if row["lap_validity"] != "invalid":
                row["lap_validity"] = state


def save_review(source, laps, decisions, *, expected_csv_sha256=None,
                expected_sidecar_sha256=_NOT_CHECKED):
    """decisions maps displayed 1-based capture-lap numbers to manual states.

    Desktop callers can supply fingerprints of the evidence actually displayed.
    ``None`` for expected_sidecar_sha256 means no sidecar existed at load time.
    The original CLI API remains supported without these optional checks.
    """
    if any(n < 1 or n > len(laps) for n in decisions):
        raise ValueError("Unknown lap number; use --list-laps before marking")
    if any(state not in ("manual_valid", "invalid", "unknown") for state in decisions.values()):
        raise ValueError("Unsupported manual validity state")
    source = Path(source)
    path = marker_path(source)

    def check_expected():
        if (expected_csv_sha256 is not None
                and hashlib.sha256(source.read_bytes()).hexdigest() != expected_csv_sha256):
            raise ValueError("Recording changed since review. Open the recording again before continuing.")
        if expected_sidecar_sha256 is not _NOT_CHECKED:
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except FileNotFoundError:
                digest = None
            if digest != expected_sidecar_sha256:
                raise ValueError("Validity review changed. Open the recording again before continuing.")

    check_expected()
    review = read_review(source)
    check_expected()
    if expected_csv_sha256 is not None and review["csv_sha256"] != expected_csv_sha256:
        raise ValueError("Recording changed during review. Open the recording again before continuing.")
    for number, state in decisions.items():
        lap = laps[number - 1]
        review["laps"][f"{lap.first_row}:{lap.last_row}"] = state
    temporary = None
    try:
        # Each writer owns its temporary file; concurrent clients must not
        # replace or clean up one another's in-progress review contents.
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(review, indent=2) + "\n")
        check_expected()
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def lap_validity(rows):
    states = {row.get("lap_validity", "unknown") for row in rows}
    if "invalid" in states:
        return "invalid"
    return "manual_valid" if states == {"manual_valid"} else "unknown"
