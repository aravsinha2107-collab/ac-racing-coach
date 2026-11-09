# Lap validity: research and manual review

Inspected September 14, 2026. This change does not claim automatic invalid-lap detection.

## Installed game evidence

The installation at `C:\Program Files (x86)\Steam\steamapps\common\assettocorsa` has:

- `changelog.txt` headed **1.16.4**. `acs.exe` has no populated FileVersion/ProductVersion property in the inspected file.
- Steam app 244210 manifest with **buildid 14923034**.
- `extension/config/data_manifest.ini`: **CSP 0.3.0-preview445**, build **3978**. This identifies installed files, not proof that CSP was active during a particular recording.
- `apps/python/system/acsys.py`, line 8: **CS.LapInvalidated** exists in the in-game Python app API.
- `extension/internal/lua-sdk/ac_apps/lib.lua`, lines 4995-4996: **isLapValid** (requires physics availability) and **isLastLapValid** exist in CSP's in-game Lua API.

These app APIs are different from the external `acpmf_physics`, `acpmf_graphics`, and `acpmf_static` mappings. Using them requires an in-game bridge with its own transport, lap association, availability checks, and live validation. No bridge or game installation changes were made.

## Shared-memory conclusion

The original-AC reader's published [graphics structure](https://github.com/mdjarv/assettocorsasharedmemory/blob/master/Graphics.cs) exposes lap counters/timers, penalty time, flag, pit state, and related fields, but no explicit current/last-lap validity field. Its [physics structure](https://github.com/mdjarv/assettocorsasharedmemory/blob/master/Physics.cs) includes `NumberOfTyresOut`, but no explicit lap-validity state. These are third-party implementation sources, not a live certification of every CSP extension.

No reliable, documented lap-validity signal was established for the standard shared-memory interface used by this recorder. No unknown offset is interpreted as a validity flag, and no ACC or EVO layout is substituted. `penalty_time_s == 0`, a flag returning to normal, and tyres returning on track do not establish a valid completed lap. Tyres-out sampling can also miss short excursions and cannot reproduce all game invalidation rules. Positive penalties remain a conservative suitability exclusion; they never establish validity.

New CSV format 2 records `lap_validity=unknown`, plus `ac_version` and `shared_memory_version` from the static mapping's existing version strings. The next live capture will provide those runtime versions; existing recordings cannot supply them retrospectively. A live corner-cut signal experiment has not been performed by the coding agent.

## Current behavior

Capture completeness and validity are separate. Invalid/unreviewed complete laps remain in `--list-laps` with their recorded times so they can be reviewed. Their completion events explicitly carry validity. Only `manual_valid` laps can become references, coaching targets, section comparisons, or comparison plots. Unknown is the default for both new and legacy CSVs. There is no "assume valid" bypass.

Review decisions are saved in `session_NAME.validity.json` beside the CSV; the CSV is not rewritten. The sidecar binds to the CSV's SHA-256 and exact lap row ranges, avoiding ambiguity when lap counters reset. Changed recordings or malformed sidecars cause analysis to fail closed. Repeated analysis reloads the decisions. To revoke a decision use `--mark-unknown LAP`; to correct an erroneous manual decision use another explicit mark. A raw `invalid` sample cannot be overridden by a manual valid decision.

Manual validity is a driver's attestation based on the game's result, not telemetry verification. Do not mark a lap valid merely because you did not notice a warning. If AC also invalidates the next lap after a late cut, mark that next lap invalid too. Missed or incorrect manual approvals can still admit an invalid lap. Until a verified automatic bridge exists, automatic post-lap coaching cannot treat unknown laps as valid.

## Clean / invalid / clean driving check

Run these commands in the project folder. Keep the same car, Monza configuration, setup, and conditions throughout.

```powershell
$python = 'C:\Users\Arav.S\AppData\Local\Python\pythoncore-3.14-64\python.exe'
& $python -B record_session.py
```

1. Complete an out-lap as needed, then a full timed **clean lap**. Note its time and confirm AC accepts it.
2. Complete a second timed lap with an intentional corner cut near the end of Monza. Confirm AC actually marks it **invalid**, and note the time. A cut alone is not evidence of the game's decision.
3. Complete another **clean lap** and check AC accepts it. If invalidation carries into this lap, mark it invalid and drive another clean lap instead.
4. Keep recording for several seconds after the final finish, then stop with Ctrl+C. Do not reset/change session while collecting this test.
5. Set `$csv` to the exact path printed by the recorder and list laps:

```powershell
$csv = '.\recordings\session_REPLACE_WITH_PRINTED_FILENAME.csv'
& $python -B analyze_session.py $csv --list-laps
```

Match **Review lap** numbers to the recorded times and game results. These are capture-complete lap numbers, not necessarily AC's displayed lap numbers; partial laps are skipped. Initially all should be `unknown`.

If the listing confirms your clean/invalid/clean laps are review laps 1/2/3:

```powershell
& $python -B analyze_session.py $csv --mark-valid 1 3 --mark-invalid 2 --list-laps
& $python -B analyze_session.py $csv --no-open
```

Expected: review lap 2 remains visible as `invalid`, but the comparison uses laps 1 and 3 only. Lap 2 cannot become the reference even if it is faster. The feedback states validity was manually reviewed. Run analysis again to confirm the decisions persist. Adjust numbers if your listing differs; never blindly approve an extra or carried-invalid lap.

Automated regression checks (already run by the coding agent):

```powershell
& $python -B -m unittest discover -s tests -v
```

Tests cover unknown legacy laps with zero penalties, persisted invalid markers, direct coaching guards, a synthetic clean/invalid/clean selection, a late invalid sample, stale reviews, CLI review/report generation, and the original lap-time/gap regressions. The synthetic third lap is not presented as a recorded driving result. The driving check above still needs to be performed locally.
