"""Command-line analysis of saved AC recordings."""
import argparse
import os
from pathlib import Path
import sys
from ac_racing_coach.analysis import (load_csv, find_complete_laps, choose_laps, time_label, coaching_notes)
from ac_racing_coach.plotting import plot_laps
from ac_racing_coach.validity import save_review


def main():
    parser = argparse.ArgumentParser(description="Find complete AC laps and graph recorded driving inputs.")
    parser.add_argument("files", nargs="*", type=Path, help="CSV file(s), in recording order")
    parser.add_argument("--output", type=Path, help="PNG destination")
    parser.add_argument("--no-open", action="store_true", help="Save the plot without opening it")
    parser.add_argument("--list-laps", action="store_true", help="List capture-complete laps and validity without coaching")
    parser.add_argument("--mark-invalid", nargs="+", type=int, default=[], metavar="LAP", help="Persist manual invalid markers (one explicit CSV required)")
    parser.add_argument("--mark-valid", nargs="+", type=int, default=[], metavar="LAP", help="Attest that AC showed these laps valid; this is manual review")
    parser.add_argument("--mark-unknown", nargs="+", type=int, default=[], metavar="LAP", help="Clear previous review for these laps")
    args = parser.parse_args()
    base = Path(__file__).resolve().parent
    files = args.files
    marking = args.mark_invalid or args.mark_valid or args.mark_unknown
    if marking and len(files) != 1:
        parser.error("Manual markers require exactly one explicit CSV file")
    marked = args.mark_invalid + args.mark_valid + args.mark_unknown
    if len(marked) != len(set(marked)):
        parser.error("A lap can have only one decision per command")
    if not files:
        available = list((base / "recordings").glob("*.csv"))
        if not available:
            print("No CSV files found in recordings/. Record a session first, then stop it with Ctrl+C.")
            return 1
        files = [max(available, key=lambda path: path.stat().st_mtime_ns)]

    all_laps = []
    seen = set()
    try:
        for path in files:
            path = path.resolve()
            if path in seen:
                continue
            seen.add(path)
            rows = load_csv(path)
            laps, excluded = find_complete_laps(rows, path)
            if marking:
                decisions = {n: "invalid" for n in args.mark_invalid}
                decisions.update({n: "manual_valid" for n in args.mark_valid})
                decisions.update({n: "unknown" for n in args.mark_unknown})
                save_review(path, laps, decisions)
                rows = load_csv(path)
                laps, excluded = find_complete_laps(rows, path)
            print(f"\n{path.name}: {len(rows):,} readings; {len(laps)} complete timed lap(s).")
            print(f"Skipped {len(excluded)} partial, interrupted, or otherwise unsuitable portions.")
            for local_number, lap in enumerate(laps, 1):
                all_laps.append(lap)
                print(f"  Review lap {local_number}: {lap.validity} | CSV rows {lap.first_row}-{lap.last_row}")
                counter = lap.rows[0]["completed_laps"]
                print(f"  Lap {len(all_laps)}: {time_label(lap.seconds)} | Top speed: {lap.top_speed:.1f} km/h | "
                      f"Samples: {len(lap.rows):,} | Rate: {lap.sample_rate:.1f}/s | "
                      f"Largest gap: {lap.max_gap * 1000:.1f} ms | Counter: {counter} -> {counter + 1}")
                if lap.gaps:
                    print(f"  Contains {len(lap.gaps)} marked data gap(s); missing values will not be filled.")
        if not all_laps:
            print("\nNo complete lap passed the capture checks. Drive across the finish line, complete a full lap,")
            print("then keep recording for a few more seconds. Pauses, resets, and missing finishes are excluded.")
            return 1

        if args.list_laps:
            return 0
        selected = choose_laps(all_laps)
        if not selected:
            print("No coaching-eligible laps. Validity is unknown or invalid. Use --list-laps, then --mark-valid/--mark-invalid based on AC's result.")
            return 0
        print(f"Excluded {len(all_laps) - sum(l.coaching_eligible for l in all_laps)} invalid or unreviewed complete laps from coaching/reference selection.")
        print("Latest in the report means the latest manually reviewed valid lap, not necessarily the last lap driven.")
        print("\nFull capture checks do not verify track limits, collisions, or setup equivalence.")
        if len(selected) == 1:
            print("Plotting one full lap. A second matching full lap is needed for a comparison.")
        else:
            print(f"Comparison: latest lap minus reference = {selected[1].seconds - selected[0].seconds:+.3f} seconds.")
        notes = coaching_notes(selected)
        print("\n" + notes)
        destination = args.output or base / "analysis" / f"{selected[-1].source.stem}_lap_analysis.png"
        destination = destination.resolve()
        if destination.suffix.lower() != ".png":
            raise ValueError("The output filename must end in .png")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            plot_laps(selected, destination)
        except ImportError:
            print("\nTo create the graph, install Matplotlib: py -m pip install matplotlib")
            return 1
        print(f"\nGraph saved: {destination}")
        notes_path = destination.with_name(destination.stem + "_feedback.txt")
        notes_path.write_text(notes + "\n", encoding="utf-8")
        print(f"Coaching notes saved: {notes_path}")
        if sys.platform == "win32" and not args.no_open:
            try:
                os.startfile(str(destination))
            except OSError:
                print("Open the PNG from the analysis folder to view it.")
        return 0
    except (OSError, ValueError) as error:
        print(f"\nCould not analyze the recording: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

