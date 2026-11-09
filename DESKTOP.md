# Windows recording review

The first desktop window imports one CSV at a time, loads its matching validity
sidecar, lets the driver review laps, and compares two explicitly selected,
manually approved laps. It uses the same lap measurements, gap exclusions,
graphs, and feedback as the command-line tools.

## Dependencies and launch

Tested dependency versions are pinned in `requirements-desktop.txt`:
PySide6 6.11.2 and Matplotlib 3.11.2, on Windows x64 with Python 3.14.3.
From the project folder, create/use a virtual environment and install them:

```powershell
& 'C:\Users\Arav.S\AppData\Local\Python\pythoncore-3.14-64\python.exe' -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -r requirements-desktop.txt
```

Launch:

```powershell
& '.\.venv\Scripts\python.exe' desktop.py
```

The development environment created for this change uses `--system-site-packages`
to reuse the existing Matplotlib installation. A new ordinary virtual environment
works with the requirements command above. The command-line tools do not import
PySide6 and keep their existing commands and output locations.

## Review and compare

1. Click **Open Recording** and choose a CSV. The matching `.validity.json` is
   loaded automatically. Missing sidecars mean unknown validity; malformed or
   stale sidecars block loading with an error.
2. Select a lap row to reveal review actions. Use **Approve lap**, **Mark invalid**, or
   **Set unknown** according to AC's result. Approval is the driver's assertion,
   never automatic detection. Each change persists immediately in the existing
   hash-bound sidecar; the CSV is never rewritten. Raw invalid telemetry cannot
   be overridden by driver approval.
3. Choose a **Reference** and a later **Comparison lap** above the results, then **Compare**.
   Both selectors offer only manually approved laps. They must be distinct and
   have matching car, track, configuration, tyres, track length, and session type.
   Review lap numbers refer to capture-complete laps in file order, not
   necessarily the simulator's displayed lap number.
4. View the **Graphs** and **Feedback** tabs. The graph fits the available width
   with a minimum readable width and scrolling in narrower panes. Resize the
   sidebar divider to give it more room. “Latest” in
   the reused reports means the selected later lap, not an automatic selection.

Only one operation runs at a time, in a background thread. The window keeps
painting, scrolling, and responding while loading, saving review, or generating
reports. Controls that would conflict with an operation are temporarily disabled.
Closing during an operation waits for it to finish without blocking the event
loop, so a review write or plotting worker is not abandoned.

Changing either comparison selection or any review clears the previous report.
CSV and sidecar fingerprints are checked again before reviews and comparisons,
and after graph generation. If an external tool changes the files, reopen the
recording. Displayed reports represent the review used when generated; there is
no continuous filesystem watcher. Desktop reports stay in memory and do not
overwrite existing analysis files.

## Windows verification checklist

- Open your clean/invalid/clean test recording. Check all times and review states
  against the command-line listing; invalid laps remain visible in the lap list.
- Use a copy of a recording to change review states. Reopen it and confirm those
  states persist. Unknown and invalid laps must never appear in either selector.
- Select two approved laps in chronological order. Compare, check both tabs, and
  verify that the invalid lap is absent from the reference and feedback.
- Set a selected approved lap to unknown or invalid. The report should clear and
  that lap should disappear from both selectors.
- Move/resize the window during import and comparison. Close during a comparison:
  the status should indicate it is finishing, then the window should exit.

Run all regression, review-controller, and Qt integration tests:

```powershell
& '.\.venv\Scripts\python.exe' -B -m unittest discover -s tests -v
```

The dashboard uses a restrained dark palette, a centered welcome panel, a
320-pixel resizable lap sidebar, status badges, and grouped comparison selectors.
Review actions only appear for the selected lap. Feedback adds presentation
hierarchy to the existing facts; it does not generate new measurements. Embedded
plots use a dark theme while CLI plots remain light by default.

Keyboard support: Ctrl+O opens a recording; Tab/Shift+Tab moves between controls,
arrow keys navigate laps and selectors, and Space activates the focused button.
The selected control scrolls into view on smaller workspaces. At 150%/200% scale,
the outer workspace and graph can scroll instead of shrinking controls or text.
The file chooser uses a Qt dialog so its dropdowns, views and scrollbars inherit
the application theme.

Qt tests skip if PySide6 is unavailable; use the project environment to include
them. Tests make temporary copies instead of reviewing original recordings.
Live recording controls, video capture, speech, and an installer are later work.

Visual checks are repeatable with disposable recording copies:

```powershell
& '.\.venv\Scripts\python.exe' -B tools/desktop_screenshots.py
```

Captures and metrics are saved under `.ui-qa/redesign`. Tests cover empty, loaded,
unreviewed, insufficient-approved, loading, error, comparison and feedback states.
Qt scale factors 1, 1.5 and 2 simulate a physical 1280×800 work area in separate
processes. This verifies layout/scrolling and keyboard access under Qt scaling,
not the actual Windows display setting or moving a native window between monitors.
Native Windows DPI changes, clean-PC installation, and large endurance-session
performance remain untested. The requested reference screenshot attachments were
not available to the coding agent; the written visual requirements guided this redesign.
