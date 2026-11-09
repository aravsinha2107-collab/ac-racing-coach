import ctypes
from ctypes import wintypes
import struct
import time

# Read only the existing shared memory created by Assetto Corsa.
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
READ_ACCESS = 0x0004

kernel32.OpenFileMappingW.argtypes = [
    wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR
]
kernel32.OpenFileMappingW.restype = wintypes.HANDLE

kernel32.MapViewOfFile.argtypes = [
    wintypes.HANDLE, wintypes.DWORD,
    wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t
]
kernel32.MapViewOfFile.restype = ctypes.c_void_p

kernel32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
kernel32.UnmapViewOfFile.restype = wintypes.BOOL

kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

# First 32 bytes of original Assetto Corsa's physics data.
physics_format = struct.Struct("<ifffiiff")


def main():
    handle = kernel32.OpenFileMappingW(
        READ_ACCESS, False, "Local\\acpmf_physics"
    )

    if not handle:
        print("Cannot open AC telemetry.")
        print("Load a practice session, enter the car, then run this again.")
        return

    address = kernel32.MapViewOfFile(
        handle, READ_ACCESS, 0, 0, physics_format.size
    )

    if not address:
        error = ctypes.get_last_error()
        kernel32.CloseHandle(handle)
        raise ctypes.WinError(error)

    print("Telemetry opened. Press Ctrl+C in this terminal to stop.")

    last_packet = None
    last_update = time.monotonic()

    try:
        while True:
            data = ctypes.string_at(address, physics_format.size)

            packet, gas, brake, fuel, gear, rpm, steering, speed = (
                physics_format.unpack(data)
            )

            if packet != last_packet:
                last_packet = packet
                last_update = time.monotonic()

            if time.monotonic() - last_update > 2:
                message = "No new data. Resume driving, or restart this script."
            else:
                gear_label = "R" if gear == 0 else "N" if gear == 1 else str(gear - 1)
                message = (
                    f"Speed: {speed:6.1f} km/h | "
                    f"Throttle: {gas * 100:5.1f}% | "
                    f"Brake: {brake * 100:5.1f}% | "
                    f"Gear: {gear_label} | RPM: {rpm}"
                )

            print(message, flush=True)
            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\nTelemetry stopped.")
    finally:
        kernel32.UnmapViewOfFile(address)
        kernel32.CloseHandle(handle)


if __name__ == "__main__":
    main()