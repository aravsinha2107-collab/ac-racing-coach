# Reusable core

- `recording.py`: Windows shared-memory parsing and CSV capture. `record()` accepts a writable text stream, optional shared `SessionTimeline`, and a print-compatible `report` callback. It remains a blocking capture loop; worker scheduling is future work.
- `analysis.py`: CSV validation, complete-lap extraction, position-aligned comparison, and deterministic feedback. No plotting dependency is imported.
- `plotting.py`: Saved Matplotlib reports; plotting dependencies load only when a report is requested.
- `session.py`: Shared monotonic capture clock and immutable `LapCompleted` event contract.

The root scripts retain their original output locations and command-line options. `Telemetry.py` remains the standalone diagnostic.

New CSVs use `recording_format_version=2` with `session_id`, `lap_validity=unknown`, `ac_version`, and `shared_memory_version`; all prior columns remain. Unversioned and format-1 CSVs remain readable, with unknown validity. Capture time includes pauses; AC's game lap timer is used for performance comparisons. Session origins are never reset at lap boundaries. Future frame producers must share that origin and timestamp acquisition, with measured synchronization error.

`validity.py` stores manual lap reviews in a hash-bound JSON sidecar. `find_complete_laps` returns capture-complete laps, including invalid and unknown laps for review. `choose_laps`, `section_comparison`, `coaching_notes`, and `plot_laps` enforce manual-valid eligibility. Completion events carry `lap_validity`; completion alone must never authorize spoken coaching. See [research and clean/invalid/clean test procedure](../LAP_VALIDITY.md). No penalty-time or tyres-out heuristic certifies a valid lap.

`find_complete_laps(rows, source)` returns laps with a `completion` event. `confirmed_lap_events(rows, source, session_id=...)` returns those same events directly. These are batch functions, not live notifications. Future dispatchers must wait until `confirmed_at_capture_time_s` and deduplicate by `(session_id, first_row, last_row)`. A lap counter alone is insufficient because it can reset. `finish_capture_time_s` is an estimate, not a frame-exact finish timestamp. Capture-qualified laps are not certified clean laps.

Run read-only fixture tests from the project root:

```powershell
& 'C:\Users\Arav.S\AppData\Local\Python\pythoncore-3.14-64\python.exe' -B -m unittest discover -s tests -v
```

The PySide6 import/review/compare window is launched with `python desktop.py`
from the project root. `review.py` provides immutable recording snapshots and
explicit eligible-lap selection; `desktop.py` handles background operations and
display. See [desktop setup and checks](../DESKTOP.md). Video capture, speech,
live recording controls, and a live event dispatcher remain future work.

`dashboard_layout.py` and `desktop_theme.py` keep dashboard presentation separate
from controller decisions. `plot_laps(..., theme="dark")` is used only by the
desktop; the default light theme and CLI behavior remain unchanged.
