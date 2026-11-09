"""Command-line recorder for original Assetto Corsa on Windows."""
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
import struct
import sys
from ac_racing_coach.recording import (CaptureLease, MemoryPage, PHYSICS, GRAPHICS_SIZE, STATIC_SIZE, parse_metadata, record)


def main():
    if sys.platform != "win32":
        print("Run this script on the Windows PC running original Assetto Corsa.")
        return 1
    output_path = None
    try:
        with ExitStack() as stack:
            stack.enter_context(CaptureLease())
            physics = stack.enter_context(MemoryPage("acpmf_physics", PHYSICS.size))
            graphics = stack.enter_context(MemoryPage("acpmf_graphics", GRAPHICS_SIZE))
            static = stack.enter_context(MemoryPage("acpmf_static", STATIC_SIZE))
            metadata = parse_metadata(static.read())
            if not metadata["car_model"] or not metadata["track"]:
                raise ValueError("Car and track information is not ready. Enter the car first.")

            folder = Path(__file__).resolve().parent / "recordings"
            folder.mkdir(exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            candidate = folder / f"session_{stamp}.csv"
            output = stack.enter_context(candidate.open("x", newline="", encoding="utf-8"))
            output_path = candidate
            print(f"Car: {metadata['car_model']} | Track: {metadata['track']} "
                  f"{metadata['track_configuration']}")
            print(f"Recording to: {output_path}")
            print("Drive your out-lap, then two full laps. Press Ctrl+C here to stop.")
            print("Lap validity is UNKNOWN: shared memory does not verify clean laps.")
            print("After stopping, use analyze_session.py FILE --list-laps and mark each reviewed lap.")
            record(physics, graphics, metadata, output,
                   metadata_provider=lambda: parse_metadata(static.read()))
    except KeyboardInterrupt:
        print("\nStopped.")
    except (OSError, ValueError, struct.error) as error:
        print(f"Could not continue recording: {error}")
        print("Load original AC, enter a practice session, and run this script again.")
        return 1
    finally:
        if output_path is not None:
            print(f"CSV file: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

