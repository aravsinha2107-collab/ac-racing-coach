"""Record one original Assetto Corsa session on Windows, using standard Python.

Run: py record_session.py
Load a practice session before running; stop with Ctrl+C in the terminal.
Output: recordings/session_<timestamp>.csv beside this script.

This is a raw recorder, not a coach or a clean-lap validator. The first and last
laps may be partial. capture_segment marks observed pauses, gaps and resets;
never treat separate segments as one uninterrupted lap. Stop and run again
after changing the car, track, or session. Physics and graphics are independent
game updates, so even the best-effort snapshot below is not exactly frame-synced.

Shared-memory layout references for ORIGINAL AC (not ACC or EVO):
https://github.com/mdjarv/assettocorsasharedmemory/blob/master/Physics.cs
https://github.com/mdjarv/assettocorsasharedmemory/blob/master/Graphics.cs
https://github.com/mdjarv/assettocorsasharedmemory/blob/master/StaticInfo.cs
"""

import csv
import ctypes
from ctypes import wintypes
import math
import struct
import time
from .session import SessionTimeline, TelemetryFrame


SAMPLE_INTERVAL = 0.02  # Aim for up to 50 readings/second, not a guaranteed rate.
PHYSICS = struct.Struct("<ifffiiff")  # First 32 bytes of the physics page.
GRAPHICS_SIZE = 284
STATIC_SIZE = 554
FIELDS = [
    "recording_format_version", "session_id",
    "lap_validity", "ac_version", "shared_memory_version",
    "capture_time_s", "capture_segment", "car_model", "track",
    "track_configuration", "track_length_m", "session_type",
    "physics_packet", "graphics_packet", "completed_laps", "lap_time_s",
    "last_lap_time_s", "track_position", "speed_kmh", "throttle_pct",
    "brake_pct", "gear", "rpm", "steering_raw", "fuel_l",
    "in_pit", "in_pit_lane", "tyre_compound", "penalty_time_s",
]


def integer(data, offset):
    return struct.unpack_from("<i", data, offset)[0]


def floating(data, offset):
    return struct.unpack_from("<f", data, offset)[0]


def wide_text(data, offset, characters):
    # Windows wchar_t is 2 bytes. Decode explicitly even when inspecting on Linux.
    return data[offset:offset + characters * 2].decode("utf-16-le").split("\0", 1)[0]


class MemoryPage:
    """Open an existing game mapping with read-only access; never create one."""

    def __init__(self, name, size):
        self.size = size
        self.handle = None
        self.address = None
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        self.api.OpenFileMappingW.restype = wintypes.HANDLE
        self.api.MapViewOfFile.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
            wintypes.DWORD, ctypes.c_size_t,
        ]
        self.api.MapViewOfFile.restype = ctypes.c_void_p
        self.api.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
        self.api.UnmapViewOfFile.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL

        self.handle = self.api.OpenFileMappingW(0x0004, False, "Local\\" + name)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.address = self.api.MapViewOfFile(self.handle, 0x0004, 0, 0, size)
        if not self.address:
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)

    def read(self):
        return ctypes.string_at(self.address, self.size)

    def stable_read(self):
        # Reject observed counter changes during the copy. This is best effort,
        # not a lock or a guarantee against every concurrent game write.
        for _ in range(3):
            before = ctypes.string_at(self.address, 4)
            data = self.read()
            after = ctypes.string_at(self.address, 4)
            if before == data[:4] == after:
                return data
        return None

    def close(self):
        if self.address:
            self.api.UnmapViewOfFile(self.address)
            self.address = None
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class CaptureLease:
    """One producer across this desktop and the CLI in a Windows logon session.

    Acquire and release on the capture thread. Windows releases ownership after
    a crash; an abandoned mutex can safely be acquired by the next producer.
    """

    def __enter__(self):
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.api.CreateMutexW.restype = wintypes.HANDLE
        self.api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.api.WaitForSingleObject.restype = wintypes.DWORD
        self.api.ReleaseMutex.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.api.CreateMutexW(None, False, "Local\\ACRacingCoach.Capture.v1")
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        result = self.api.WaitForSingleObject(self.handle, 0)
        if result not in (0, 0x80):  # acquired or abandoned
            self.api.CloseHandle(self.handle)
            self.handle = None
            raise OSError("Another AC Racing Coach capture is running. Stop the CLI recorder or disable the other desktop coach first.")
        return self

    def __exit__(self, *_):
        if self.handle:
            self.api.ReleaseMutex(self.handle)
            self.api.CloseHandle(self.handle)
            self.handle = None


def parse_metadata(data):
    return {
        "ac_version": wide_text(data, 30, 15),
        "shared_memory_version": wide_text(data, 0, 15),
        "car_model": wide_text(data, 68, 33),
        "track": wide_text(data, 134, 33),
        "track_configuration": wide_text(data, 524, 15),
        "track_length_m": round(floating(data, 520), 3),
    }


def parse_sample(physics, graphics):
    packet, gas, brake, fuel, gear, rpm, steering, speed = PHYSICS.unpack(physics)
    position = floating(graphics, 248)  # Fraction around the track spline: 0..1.
    if not all(math.isfinite(x) for x in (gas, brake, fuel, steering, speed, position)):
        raise ValueError("Non-finite telemetry value received.")
    if not (0 <= position <= 1 and -0.01 <= gas <= 1.01 and -0.01 <= brake <= 1.01):
        raise ValueError("Telemetry values are outside the expected AC ranges.")
    return {
        "session_type": integer(graphics, 8),
        "physics_packet": packet,
        "graphics_packet": integer(graphics, 0),
        "completed_laps": integer(graphics, 132),
        "lap_time_s": integer(graphics, 140) / 1000,
        "last_lap_time_s": integer(graphics, 144) / 1000,
        "track_position": round(position, 7),
        "speed_kmh": round(speed, 3),
        "throttle_pct": round(gas * 100, 3),
        "brake_pct": round(brake * 100, 3),
        "gear": gear - 1,  # CSV: -1 = reverse, 0 = neutral, 1 = first gear.
        "rpm": rpm,
        "steering_raw": round(steering, 6),
        "fuel_l": round(fuel, 4),
        "in_pit": integer(graphics, 160),
        "in_pit_lane": integer(graphics, 276),
        "tyre_compound": wide_text(graphics, 176, 33),
        "penalty_time_s": round(floating(graphics, 264), 3),
    }


def reset_observed(previous, current):
    if current["session_type"] != previous["session_type"]:
        return True
    if current["completed_laps"] < previous["completed_laps"]:
        return True
    if (current["completed_laps"] == previous["completed_laps"]
            and current["lap_time_s"] < previous["lap_time_s"] - 0.1):
        return True
    # A normal finish-line crossing increments the lap counter. A large jump
    # without that increment can be a teleport or a boundary-timing mismatch.
    if (current["completed_laps"] == previous["completed_laps"]
            and abs(current["track_position"] - previous["track_position"]) > 0.2):
        return True
    return any(current[key] < previous[key] for key in ("physics_packet", "graphics_packet"))


def record(physics_page, graphics_page, metadata, output, *, timeline=None, report=print,
           observer=None, stop_event=None, metadata_provider=None):
    """Capture to a writable text stream; callers may share the timeline.

    report accepts print-style arguments; pass a no-op to silence terminal output.
    observer receives immutable frames; use a nonblocking latest-value mailbox.
    An observer error detaches it without interrupting the CSV. Analysis and
    speech must run outside this single capture loop. stop_event permits graceful
    desktop shutdown. A metadata change ends the file instead of mixing tracks.
    """
    writer = csv.DictWriter(output, fieldnames=FIELDS)
    writer.writeheader()
    output.flush()
    timeline = timeline or SessionTimeline()
    start = time.monotonic()
    last_physics = None
    last_graphics = None
    previous = None
    last_saved_at = start
    last_notice = ""
    last_print = start - 1
    segment = 0
    pending_segment = True
    count = 0

    def publish(now, status, fresh=False, sample=None, current_metadata=None):
        nonlocal observer
        if observer is not None:
            try:
                observer(TelemetryFrame(timeline.session_id, timeline.elapsed(now),
                                       status, fresh, current_metadata or metadata,
                                       sample, segment))
            except Exception as error:
                observer = None
                report(f"Live observer stopped; recording continues: {error}", flush=True)

    def notice(message):
        nonlocal last_notice
        if message != last_notice:
            report(message, flush=True)
            last_notice = message

    try:
        while True:
            if stop_event is not None:
                if stop_event.wait(SAMPLE_INTERVAL):
                    break
            else:
                time.sleep(SAMPLE_INTERVAL)
            now = time.monotonic()
            graphics = graphics_page.stable_read()
            physics = physics_page.stable_read()
            if graphics is None or physics is None:
                if now - last_saved_at > 0.25:
                    publish(now, 2)
                continue
            # Reject graphics updates observed while the physics copy was read.
            if graphics[:4] != graphics_page.read()[:4]:
                if now - last_saved_at > 0.25:
                    publish(now, 2)
                continue

            status = integer(graphics, 4)
            if metadata_provider is not None:
                current_metadata = metadata_provider()
                if current_metadata != metadata:
                    publish(now, 0, current_metadata=current_metadata)
                    report("Car, track, or runtime changed. Start a new recording.")
                    break
            if status != 2:  # AC_LIVE; 0=off, 1=replay, 3=paused.
                publish(now, status)
                pending_segment = True
                last_physics = integer(physics, 0)
                last_graphics = integer(graphics, 0)
                if status == 0 and count:
                    report("Session ended. Start a new recording for the next session.")
                    break
                notice({0: "Waiting for a driving session...",
                        1: "Replay detected. Recording is paused.",
                        3: "Game paused. Recording will resume with live data."
                        }.get(status, "Waiting for live data..."))
                continue

            # A brief pit transition must reach the coach even if physics has
            # not advanced. Keep the regular CSV acceptance checks below.
            if observer is not None and len(graphics) >= GRAPHICS_SIZE:
                if integer(graphics, 160) or integer(graphics, 276):
                    publish(now, status, sample={"in_pit": integer(graphics, 160),
                                                "in_pit_lane": integer(graphics, 276)})

            p_id, g_id = integer(physics, 0), integer(graphics, 0)
            if last_physics is None:
                # Do not accept a frozen first snapshot as a live sample.
                last_physics, last_graphics = p_id, g_id
                continue
            if p_id == last_physics or g_id == last_graphics:
                if now - last_saved_at > 0.25:
                    publish(now, status)
                if now - last_saved_at > 1:
                    pending_segment = True
                    notice("No fresh telemetry. Resume driving, or restart after reopening AC.")
                continue
            last_physics, last_graphics = p_id, g_id
            sample = parse_sample(physics, graphics)

            if (pending_segment or now - last_saved_at > 0.25
                    or (previous is not None and reset_observed(previous, sample))):
                segment += 1
                pending_segment = False

            writer.writerow({
                "recording_format_version": 2,
                "session_id": timeline.session_id,
                "lap_validity": "unknown",
                "capture_time_s": round(timeline.elapsed(now), 6),
                "capture_segment": segment,
                **metadata, **sample,
            })
            publish(now, status, True, sample)
            count += 1
            last_saved_at = now
            previous = sample
            last_notice = ""

            if now - last_print >= 1:
                label = { -1: "R", 0: "N" }.get(sample["gear"], str(sample["gear"]))
                report(
                    f"Saved {count} readings | Lap counter: {sample['completed_laps']} | "
                    f"Lap time: {sample['lap_time_s']:.2f}s | "
                    f"Track: {sample['track_position'] * 100:.1f}% | "
                    f"Speed: {sample['speed_kmh']:.1f} km/h | Gear: {label}",
                    flush=True,
                )
                output.flush()
                last_print = now
    except KeyboardInterrupt:
        report("\nRecording stopped.")
    finally:
        # Also cancel live consumers on Ctrl+C, exceptions, or a requested stop.
        publish(now if 'now' in locals() else start, 0)
        output.flush()
        report(f"Saved {count} readings in total.")
    return count



