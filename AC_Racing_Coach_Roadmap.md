AC Racing Coach: Windows app and public release roadmap

Research checked September 14, 2026. This is a proposed development plan. Competitor observations come from public pages and firsthand user reports; I have not installed or benchmarked Trophi.ai's current Windows build.

The first release should help drivers of original Assetto Corsa understand their own laps, choose a practical improvement, and measure whether it worked. Start with Monza and the McLaren MP4/4 mod you already drive, then validate more combinations. Becoming better than Trophi.ai overall is a longer-term ambition that needs comparative testing.

**What the competitor research establishes**

| Finding | Evidence and current status | Decision for our app |
|---|---|---|
| Original Assetto Corsa is an opening. | Trophi.ai's published game list includes iRacing, ACC, the F1 series, and LMU. Original AC is not listed. This establishes an advertised coverage gap, not that our coaching is already superior. [Official pricing and supported games](https://www.trophi.ai/pricing-sim-racing) | Make original AC the initial supported simulator. Publish the car, track, and mod versions we have actually tested. |
| Numerical advice can be difficult to apply. | A user reported difficulty translating suggested braking-distance and pressure changes into a driving action. This is a historical report. Trophi.ai now advertises instructions using track landmarks. [Firsthand report](https://www.reddit.com/r/simracing/comments/1ki407k/i_tried_trophy_ai_and_i_have_couple_of_issues/), [current Mansell features](https://www.trophi.ai/sim-racing-features/mansell) | Pair every suggestion with the relevant trace and a verified corner or marker. Let the driver choose one change to practise. |
| Too much speech can dilute useful advice. | A trial user reduced the enabled advice because it felt excessive, while also reporting clearer understanding and some improvement. Trophi.ai currently advertises one correction at a time. This does not establish a universal current defect. [Trial diary](https://www.reddit.com/r/iRacing/comments/1p20e4q/started_the_7_day_trial_of_trophiai_detailing_my/), [current Mansell features](https://www.trophi.ai/sim-racing-features/mansell) | Begin with a short post-lap debrief. Later add a selected focus, adjustable speech frequency, mute, and feedback after the relevant corner. |
| Reference selection affects usefulness. | An older discussion complained about being compared with a much faster driver. A person identifying themselves as Trophi.ai's product team replied that community laps and condition filters had been added. Its current feature page confirms community references. [Discussion and reply](https://www.reddit.com/r/simracing/comments/1i12ljx/trophyai_in_january_2025_any_thoughts/), [current reference options](https://www.trophi.ai/sim-racing-features/rtsa) | Start with the driver's own earlier laps. Later allow permissioned reference imports, with clear car, track, version, conditions, and quality information. |
| Reliability deserves explicit testing. | The August 19, 2026 notes describe fixes for overlay activation, controls, missing pace-note windows, and loading stalls. August 26 notes fix pace notes with community references. These are documented fixes, not proof that the bugs remain. [Version 1.109.0](https://www.trophi.ai/updates/1-109-0), [version 1.110.1](https://www.trophi.ai/updates/1-110-1) | Test reconnects, opening and closing windows, multiple sessions, stale telemetry, and application exit. Make current recording state obvious. |

Trophi.ai already advertises ranked time-loss sections, linked charts, a track map, replay analysis, and corner-specific coaching. Those are part of the competitive baseline. Our differentiation must be demonstrated through AC coverage, trustworthy measurements, ease of use, and learning outcomes. [Current lap-analysis features](https://www.trophi.ai/sim-racing-features/lap-analysis)

The public reports do not establish how frequently a problem occurs today. Current pages establish advertised capabilities, not independently measured performance. We should retest relevant complaints on the current product before making public comparisons.

**What we have today**

The first PySide6 desktop interface is now implemented for importing one recording,
viewing lap times and validity, saving driver review, selecting two approved laps,
and displaying the existing graphs and feedback. Disk work and report generation
run off the UI thread. This completes the import/review/compare portion of the
desktop milestone; live recording buttons and bundled distribution remain pending.
See [desktop setup, launch, and verification](DESKTOP.md). The driver reports that
the preceding live manual-validity test passed; automated validity detection is
still not implemented.

Validity correction, September 14: a live corner-cut test showed that capture-complete laps can still be invalid in AC with no useful penalty-time signal. Standard original-AC shared memory has no reliable validity signal established for this recorder. The installed AC Python app API and CSP Lua SDK expose validity through separate in-game APIs; an automatic bridge needs its own implementation and live tests. See [installed-version evidence, signal research, and exact test commands](LAP_VALIDITY.md).

The current core separates capture completeness from validity. New CSVs explicitly record unknown validity; legacy CSVs also default to unknown. Invalid and unreviewed laps cannot be coaching references or targets. A command-line manual review workflow stores valid/invalid decisions in a hash-bound sidecar without modifying the CSV. Manual valid means driver-attested, not automatically verified. Zero penalty time is never proof of a valid lap.

Your recorder reads original AC telemetry and saves CSV files. The analyzer finds full timed laps, compares recorded positions, plots speed and pedal inputs, and produces measured feedback. It marks short data gaps and excludes affected sections from recommendations.

Your September 13 recording gave us two full timed laps, 1:45.749 and 1:42.913. It also exposed a 0.267-second dropout that our earlier strict segmentation rejected. That recording is now a useful regression example: future changes must preserve the recorded lap times and the visible gap.

Current limitations matter for the next build. Ten-percent sections are coarse and do not establish corner names. The saved data does not establish racing-line geometry, collision history, official lap validity, or equivalent setup and weather. A lower minimum speed can describe a difference without proving its cause. Our rules currently select observations; they are not a trained model. Two laps are enough to exercise the comparison, but not to establish consistent coaching benefit.

**Synchronized capture and automatic post-lap speech**

The intended app includes synchronized screen recording and automatic spoken feedback after each completed lap. Telemetry and video acquisition timestamps must map to one shared monotonic session timeline. Record frame timestamps and dropped-frame information so a lap or coaching observation can locate the corresponding footage. Starting two recorders together is not sufficient proof of synchronization. Measure alignment and drift over a long session, including pauses and capture interruptions, before claiming synchronization.

After a lap is completed, wait for confirmed finish evidence and established validity, then automatically produce a short spoken debrief from validated measurements. Unknown or invalid laps must not produce coaching. A first valid lap without a suitable reference can receive a lap-time summary; incomplete or unsupported data must not produce a comparison claim. The current manual review is a temporary limitation: trustworthy automatic validity requires a verified in-game API bridge before live coaching is released. Recording must continue during analysis and speech. Include mute, frequency controls, and cancellation of queued advice on session changes or stale data.

The first core refactor now provides the `ac_racing_coach` package, a shared `SessionTimeline`, and `LapCompleted` events derived from the existing lap-validation rules. The event includes the recorded lap time, source row boundaries, estimated host finish time, and the host time when confirmation evidence was available. Event extraction is currently a batch API; live dispatch, deduplication, video recording, and speech remain future work. Existing CSV files retain their elapsed capture-time interpretation. New recordings add a format version and session ID; legacy recordings use their source path as the fallback event session identity. Neither format establishes synchronization with independently recorded historical footage.

Regression tests read the existing recordings without modifying them. They preserve the confirmed 105.749- and 102.913-second laps, the raw 0.267346-second gap displayed as 0.267 seconds, and exclusion of affected sections from recommendations.

**Recommended development tools**

Keep VS Code and Python. Use PySide6 with Qt Widgets for the first desktop interface, reuse the existing calculations, and retain Matplotlib for saved reports. Qt supports both Widgets and Qt Quick; we can improve presentation without replacing the telemetry engine. Use a project virtual environment and pin a combination of dependencies that passes the Windows build test. [Qt for Python setup](https://doc.qt.io/qtforpython-6/gettingstarted.html)

Use SQLite for session and practice-history metadata while retaining original recordings for reproducible analysis. Add Git version control before the next substantial refactor so each working version can be restored. Keep the first prototype's analysis local.

Package the Windows application with PyInstaller and put it behind an installer when the beta is ready. Users can run a bundled application without installing Python. PyInstaller's Windows build must run on Windows, either your PC or a Windows build runner. Its documentation does not make a Linux-generated package a Windows release. [PyInstaller manual](https://pyinstaller.org/en/stable/)

Treat Windows 11 x64 as the proposed first release target. Confirm the actual test PCs before finalizing support. Add other Windows versions only after testing them.

**Build milestones, in order**

| Milestone | Work | Evidence that it is ready |
|---|---|---|
| 1. Reusable recording and analysis core | Separate telemetry reading, session detection, lap extraction, and comparison from command-line entry points and plotting. Preserve existing CSV imports. Add a recording-format version, shared session timeline, and confirmed lap-completion event contract. | Existing recordings produce the same confirmed lap times and visible gaps. Regression checks cover missing finish evidence, pauses, data gaps, and exclusion of affected coaching sections. Extend reset and reconnect coverage before live dispatch. |
| 2. First Windows desktop app | Add Open Recording, a lap list, reference selection, Compare, graphs, and feedback. Then add Start/Stop Recording with waiting, recording, paused, and disconnected states. | You can import the existing CSV and record another session from buttons. The interface stays responsive. A bundled build opens on a Windows test machine without Python installed. |
| 3. Useful corner analysis | Create a verified Monza corner profile. Calculate sustained braking onset, brake release, throttle return, minimum speed, and exit speed where data supports them. Add position and other telemetry fields only after verifying what the simulator supplies. | Reviewed traces support each reported event. Corner boundaries are checked against driving footage or the simulator. Missing data blocks affected conclusions. Unknown tracks retain general analysis. |
| 4. Practice history and reference quality | Save session history, comparable conditions, selected targets, and repeated attempts. Offer a suitable previous lap and later permissioned reference imports. | The driver can see the evidence and comparison behind a suggestion, then whether repeated attempts improved the chosen section. Unknown conditions remain visible. |
| 5. Synchronized screen capture and automatic post-lap coaching | Run telemetry, video acquisition, analysis, and speech independently from interface drawing. Map frame timestamps to the shared session timeline. Dispatch each confirmed lap once and automatically speak a short measured debrief. Add mute, frequency controls, and cancellation of outdated advice. | Telemetry and video continue during speech. Each eligible completed lap triggers one debrief. Measure video alignment and drift across pauses and dropped frames. Advice is suppressed when data is stale or the session changes. Outdated messages do not play later. |
| 6. Closed beta | Invite approximately 10-20 willing testers with different PCs and controls. Observe installation and first use, collect opt-in diagnostic reports, and test more car/track combinations. | Installation, reconnects, long sessions, exiting, and upgrades behave consistently. Novices can explain and use the advice. Coaching observations have been checked by experienced drivers. |
| 7. Public release | Prepare an installer, signing/distribution route, versioned downloads, update recovery, documentation, support contact, and clear data controls. Decide pricing after measuring costs. | Clean-machine installation and upgrade/uninstall checks pass, the supported combinations are published, and release claims match what we measured. |

Each release should carry one visible improvement and a short test you can perform. We can revise the order if a real defect blocks progress; an unfinished UI should not obscure incorrect lap analysis.

**How the coaching should reach a conclusion**

For every recommendation, store the source laps, observed section, calculated difference, data-quality state, and reason for selecting it. Show the user the evidence in the graph. Distinguish a measurement from a possible explanation and a proposed practice exercise.

For example, your previous result establishes that the later lap lost about 0.49 seconds in the 70-80% section. It does not by itself establish that braking later is the right fix. We need to examine the inputs and repeat the comparison before choosing that instruction.

A reference lap supplies a comparison, not a guarantee of the fastest possible technique. A personal-best reference can still contain weak corners. Select usable references with matching metadata and disclose missing condition information; do not label a lap clean merely because a recorded penalty flag is zero. Imported references need provenance and plausibility checks. Do not present a sum of independent best sections as a guaranteed achievable lap.

An optional language model can explain already-checked facts, answer questions about them, and suggest an exercise from reviewed coaching material. The local analysis must calculate lap times, detect events, and decide whether the evidence is sufficient. Avoid sending every telemetry sample to a model. The app should still record and provide basic feedback when cloud services are unavailable. Put any developer-owned cloud credentials on a server, not inside the distributed executable, and introduce usage limits before a public cloud feature.

Keep recordings on the driver's PC by default. Make any cloud upload, shared reference, or diagnostic submission explicit. Allow export and deletion. Use original app branding and only distribute code, reference telemetry, graphics, and voices for which the project has permission. Review dependency licensing before distribution; this plan does not determine the final licensing arrangement.

**How we will measure improvement**

The following are proposed engineering targets, not achievements:

- Data correctness: all labeled recordings in the test set retain the expected finish times, boundaries, and gap markers. Tests cover pauses, session changes, disconnects, malformed imports, and stale snapshots.
- Traceable feedback: every numeric claim links to its source measurements. Flagged gaps and unsupported channels cannot produce a fine-grained driving instruction.
- Usability: a new tester can install, capture a session, and locate one useful practice target without developer assistance. Record where they get stuck.
- Performance: measure game frame times with the app on and off under the same settings, across repeated runs. Use less than a 3% increase in median and 95th-percentile frame time as an initial target on designated test PCs, with no recurring stalls. Revise only with measured reasons.
- Reliability: complete a continuous 60-minute session, pause/resume and disconnect checks, and normal exit without losing the saved session or leaving an unintended worker running.
- Learning: compare equal practice blocks with and without feedback, rotate the order across drivers or comparable combinations, and track median usable lap time, consistency, invalid laps, and comprehension. Do not equate one personal best with a coaching effect. Document learning carryover and other confounders.

An AC-only release cannot provide a same-game head-to-head trial against a product that does not list AC support. First evaluate our app against unaided AC practice and relevant AC tools. A later direct Trophi.ai comparison needs a mutually supported simulator, comparable conditions, the same task, and access to its then-current build.

Public distribution also needs a deliberate signing route. Microsoft documents that signatures identify a publisher but do not guarantee immediate SmartScreen reputation for a new direct-download application. Plan for this when selecting the installer and distribution channel. [Microsoft's SmartScreen guidance](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)

**Planning range**

Assuming roughly 8-12 focused hours per week, with coding assistance and regular driving tests, allow about 4-8 weeks for a usable desktop prototype, 3-5 months for a small closed beta, and 6-12 months or longer for a dependable public release. These are scope estimates from the current prototype, not promised dates. Broad mod compatibility, voice reliability, reference data, and support can extend the schedule. Outperforming an established competitor across all features has no defensible deadline today.

**Your next test**

1. Keep the current car and Monza combination, setup, fuel choice, tyres, assists, and session conditions consistent during the test.
2. Start the recorder, drive an out-lap if needed, then one clean timed lap, one intentionally invalid timed lap, and one clean timed lap. Check AC's validity result for each; a late cut can also invalidate a following lap. If so, record another clean lap.
3. Keep recording for several seconds after the final finish, then stop with Ctrl+C. Use `--list-laps` to match times to game results; use `--mark-invalid` and `--mark-valid` to persist explicit review decisions. Follow the exact PowerShell procedure in [LAP_VALIDITY.md](LAP_VALIDITY.md).
4. Analyze again. The invalid lap must remain labeled in the list and must never become a reference or coaching target. Only the reviewed clean laps may be compared. Leave unknown laps unapproved when the game's result is uncertain. No UI is part of this validity fix.

You do not need to collect a large dataset before we build the interface. Later tests should have a specific purpose, such as confirming a corner boundary, checking reconnection, or evaluating one practice instruction.
