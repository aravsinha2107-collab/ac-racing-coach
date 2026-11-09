"""Capture and live-observer integration without running Assetto Corsa.

The lease check uses the real Windows mutex on independent threads. All game
memory and capture clocks are deterministic fakes; no recordings are modified.
"""

import csv
from dataclasses import FrozenInstanceError, replace
import io
import os
import struct
import threading
import unittest
from unittest.mock import patch

from ac_racing_coach import recording as recording
from ac_racing_coach.live_runtime import LatestTelemetry
from ac_racing_coach.session import SessionTimeline, TelemetryFrame


METADATA = {
    "car_model": "test_car", "track": "monza", "track_configuration": "",
    "track_length_m": 5755.02, "ac_version": "1.16.4",
    "shared_memory_version": "1.7",
}


def pages(packet, *, status=2, position=.1, lap_time_ms=1000,
          completed_laps=0, in_pit=0, in_pit_lane=0, physics_packet=None):
    physics = recording.PHYSICS.pack(
        packet if physics_packet is None else physics_packet,
        .5, .1, 50.0, 3, 4000, 0.0, 100.0,
    )
    graphics = bytearray(recording.GRAPHICS_SIZE)
    for offset, value in ((0, packet), (4, status), (8, 0),
                          (132, completed_laps), (140, lap_time_ms),
                          (160, in_pit), (276, in_pit_lane)):
        struct.pack_into("<i", graphics, offset, value)
    struct.pack_into("<f", graphics, 248, position)
    return physics, bytes(graphics)


class FakePage:
    def __init__(self, values):
        self.values = iter(values)
        self.current = None

    def stable_read(self):
        self.current = next(self.values)
        return self.current

    def read(self):
        return self.current


class StopAfter:
    def __init__(self, iterations):
        self.remaining = iterations

    def wait(self, _interval):
        self.remaining -= 1
        return self.remaining < 0


def capture(sequence, *, times=None, observer=None, metadata_provider=None):
    """Each sequence item is one capture poll, after the initial clock read."""
    if times is None:
        times = [100 + (index + 1) * .02 for index in range(len(sequence))]
    output = io.StringIO()
    reports = []
    timeline = SessionTimeline("capture-test", 99)
    with patch.object(recording.time, "monotonic", side_effect=[100, *times]):
        count = recording.record(
            FakePage([p for p, _ in sequence]),
            FakePage([g for _, g in sequence]), dict(METADATA), output,
            timeline=timeline, observer=observer,
            metadata_provider=metadata_provider, stop_event=StopAfter(len(sequence)),
            report=lambda *args, **_kwargs: reports.append(" ".join(map(str, args))),
        )
    return count, list(csv.DictReader(io.StringIO(output.getvalue()))), reports


def frame(*, capture_time_s=1.0, status=2, fresh=True, segment=1,
          session_id="mailbox-test", metadata=None, sample=None):
    return TelemetryFrame(
        session_id, capture_time_s, status, fresh, metadata or METADATA,
        sample or {"in_pit": 0, "in_pit_lane": 0, "track_position": .1,
                   "speed_kmh": 100}, segment,
    )


class LiveRecordingTests(unittest.TestCase):
    def test_csv_and_observer_share_timeline_and_capture_segment(self):
        observed = []
        count, rows, _ = capture([pages(1), pages(2), pages(3)], observer=observed.append)
        live = [item for item in observed if item.fresh]
        self.assertEqual(count, 2)
        self.assertEqual(len(live), len(rows))
        self.assertEqual(set(rows[0]), set(recording.FIELDS))
        for row, item in zip(rows, live):
            self.assertEqual(row["session_id"], item.session_id)
            self.assertAlmostEqual(float(row["capture_time_s"]), item.capture_time_s)
            self.assertEqual(int(row["capture_segment"]), item.segment)
            self.assertEqual(row["lap_validity"], "unknown")
            self.assertEqual(float(row["speed_kmh"]), item.sample["speed_kmh"])
        self.assertAlmostEqual(live[0].capture_time_s, 1.04)
        self.assertEqual(observed[-1].status, 0, "Stopping capture must cancel the live consumer")

    def test_observer_failure_detaches_without_stopping_csv(self):
        calls = []

        def broken_observer(item):
            calls.append(item)
            raise RuntimeError("simulated coach failure")

        count, rows, reports = capture([pages(1), pages(2), pages(3), pages(4)],
                                      observer=broken_observer)
        self.assertEqual(count, 3)
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(calls), 1, "A failed observer must stay detached")
        self.assertTrue(any("recording continues" in message for message in reports))
        self.assertEqual([int(row["physics_packet"]) for row in rows], [2, 3, 4])

    def test_pause_replay_pit_and_stale_are_published_without_csv_rows(self):
        observed = []
        sequence = [pages(1), pages(2), pages(2, status=3), pages(2, status=1),
                    pages(2, in_pit=1), pages(2, in_pit_lane=1), pages(2), pages(3)]
        count, rows, _ = capture(sequence, times=[100.02, 100.04, 100.06, 100.08,
                                                   100.10, 100.12, 100.5, 100.52],
                                 observer=observed.append)
        self.assertEqual(count, 2)
        self.assertEqual([int(row["physics_packet"]) for row in rows], [2, 3])
        self.assertEqual([int(row["capture_segment"]) for row in rows], [1, 2])
        self.assertTrue(any(item.status == 3 and not item.fresh for item in observed))
        self.assertTrue(any(item.status == 1 and not item.fresh for item in observed))
        for key in ("in_pit", "in_pit_lane"):
            self.assertTrue(any(item.sample and item.sample.get(key) and not item.fresh
                                for item in observed), key)
        self.assertTrue(any(item.status == 2 and not item.fresh and item.sample is None
                            and item.capture_time_s == 1.5 for item in observed))

    def test_unstable_pages_emit_stale_barrier_after_freshness_window(self):
        observed = []
        count, rows, _ = capture([pages(1), pages(2), (None, None)],
                                 times=[100.02, 100.04, 100.5], observer=observed.append)
        self.assertEqual(count, 1)
        self.assertEqual(len(rows), 1)
        self.assertTrue(any(item.status == 2 and not item.fresh and item.sample is None
                            and item.capture_time_s == 1.5 for item in observed))

    def test_metadata_change_ends_file_before_mixing_tracks(self):
        observed = []
        metadata = iter([METADATA, METADATA, dict(METADATA, track="imola"), METADATA])
        count, rows, reports = capture([pages(1), pages(2), pages(3), pages(4)],
                                      observer=observed.append,
                                      metadata_provider=lambda: next(metadata))
        self.assertEqual(count, 1)
        self.assertEqual([row["track"] for row in rows], ["monza"])
        changed = [item for item in observed if item.metadata["track"] == "imola"]
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0].status, 0)
        self.assertFalse(changed[0].fresh)
        self.assertEqual([item.sample["physics_packet"] for item in observed if item.fresh], [2])
        self.assertTrue(any("changed" in message for message in reports))

    def test_reset_segment_publishes_in_same_frame_as_new_csv_segment(self):
        observed = []
        count, rows, _ = capture([pages(1), pages(2, lap_time_ms=5000),
                                  pages(3, lap_time_ms=100)], observer=observed.append)
        self.assertEqual(count, 2)
        self.assertEqual([int(row["capture_segment"]) for row in rows], [1, 2])
        self.assertEqual([item.segment for item in observed if item.fresh], [1, 2])

    def test_frame_copies_mutable_sources_and_rejects_mutation(self):
        metadata, sample = dict(METADATA), {"in_pit": 0, "speed_kmh": 100.0}
        item = TelemetryFrame("session", 1, 2, True, metadata, sample, 1)
        metadata["track"] = "imola"
        sample["speed_kmh"] = 999
        self.assertEqual(item.metadata["track"], "monza")
        self.assertEqual(item.sample["speed_kmh"], 100)
        with self.assertRaises(TypeError):
            item.metadata["track"] = "changed"
        with self.assertRaises(TypeError):
            item.sample["speed_kmh"] = 1
        with self.assertRaises(FrozenInstanceError):
            item.capture_time_s = 10


class LatestTelemetryTests(unittest.TestCase):
    def test_fast_producer_keeps_only_latest_frame(self):
        mailbox = LatestTelemetry()
        for index in range(1000):
            mailbox.publish(frame(capture_time_s=index / 50))
        pending = mailbox.take()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].capture_time_s, 999 / 50)
        self.assertEqual(mailbox.take(), [])

    def test_safety_barrier_survives_newer_live_samples(self):
        cases = {
            "pause": (frame(status=3, fresh=False), frame()),
            "replay": (frame(status=1, fresh=False), frame()),
            "pit": (frame(sample={"in_pit": 1}), frame()),
            "pit_lane": (frame(sample={"in_pit_lane": 1}), frame()),
            "stale": (frame(fresh=False), frame()),
            "reset": (frame(segment=2), frame(segment=2)),
            "new_session": (frame(session_id="new"), frame(session_id="new")),
            "new_track": (frame(metadata=dict(METADATA, track="imola")),
                          frame(metadata=dict(METADATA, track="imola"))),
        }
        for name, (barrier, resumed) in cases.items():
            with self.subTest(reason=name):
                mailbox = LatestTelemetry()
                mailbox.publish(frame())
                mailbox.take()
                mailbox.publish(barrier)
                for index in range(100):
                    mailbox.publish(replace(resumed, capture_time_s=2 + index / 50))
                pending = mailbox.take()
                self.assertEqual(len(pending), 2)
                self.assertFalse(pending[0].fresh, "Safety observation must precede live data")
                self.assertTrue(pending[1].fresh)
                self.assertEqual(pending[1].capture_time_s, 3.98)
                self.assertEqual(mailbox.take(), [])

    def test_repeated_unsafe_observations_do_not_accumulate(self):
        mailbox = LatestTelemetry()
        for index in range(1000):
            mailbox.publish(frame(capture_time_s=index / 50, status=3, fresh=False))
        pending = mailbox.take()
        self.assertLessEqual(len(pending), 2)
        self.assertTrue(all(item.status == 3 and not item.fresh for item in pending))
        self.assertEqual(pending[-1].capture_time_s, 999 / 50)


@unittest.skipUnless(os.name == "nt", "CaptureLease uses the Windows kernel mutex")
class WindowsCaptureLeaseTests(unittest.TestCase):
    def test_other_thread_is_rejected_until_owner_releases(self):
        def acquire_on_thread():
            results = []

            def contender():
                try:
                    with recording.CaptureLease():
                        results.append("acquired")
                except OSError as error:
                    results.append(str(error))

            thread = threading.Thread(target=contender, daemon=True)
            thread.start()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive(), "Capture lease acquisition must not block")
            self.assertEqual(len(results), 1)
            return results[0]

        # A Windows mutex is recursive on its owning thread. The independent
        # contender is essential to actually exercise duplicate-capture blocking.
        with recording.CaptureLease():
            self.assertIn("Another AC Racing Coach capture", acquire_on_thread())
        self.assertEqual(acquire_on_thread(), "acquired")


if __name__ == "__main__":
    unittest.main()
