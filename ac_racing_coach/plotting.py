import math
from .analysis import TRACE_GAP_S, increasing_trace, value_at, time_label

def plot_laps(laps, destination, *, theme="light"):
    """Render reviewed telemetry, retaining the CLI's light report by default.

    The desktop's dark theme uses a compact heading and larger type so the same
    report remains readable inside a resizable, scrolling graph panel.
    """
    if not laps or any(not lap.coaching_eligible for lap in laps):
        raise ValueError("Comparison plots require manually reviewed valid laps")
    if theme not in ("light", "dark"):
        raise ValueError("Plot theme must be 'light' or 'dark'")
    import matplotlib
    matplotlib.use("Agg")  # Save a PNG without requiring an interactive GUI backend.
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MultipleLocator
    import numpy as np  # Installed as a Matplotlib dependency.

    dark = theme == "dark"
    background = "#1B2028" if dark else "white"
    foreground = "#F4F6FA" if dark else "#24364a"
    secondary = "#A7B0BE" if dark else "#526170"
    border = "#3B4452" if dark else "#c4ccd3"
    gap_colour = "#EACB75" if dark else "#e8c66c"
    comparison = len(laps) == 2
    row = laps[-1].rows[0]
    car = {"yk_1988_mclaren_mp4-4": "McLaren MP4/4 (1988)"}.get(row["car_model"], row["car_model"])
    track = row["track"].replace("_", " ").title()
    if row["track_configuration"]:
        track += f" ({row['track_configuration']})"
    colours = (["#AABEE4", "#3BCBC4"] if dark else ["#517b9d", "#bd4930"]) if comparison else ["#245fa5"]
    labels = ([f"Reference: {time_label(laps[0].seconds)}", f"Latest: {time_label(laps[1].seconds)}"]
              if comparison else [f"Recorded lap: {time_label(laps[0].seconds)}"])
    if dark and comparison:
        labels[1] = f"Comparison: {time_label(laps[1].seconds)}"
    plot_count = 4 if comparison else 3
    with plt.rc_context({
        "font.family": ["Segoe UI", "DejaVu Sans"] if dark else "DejaVu Sans",
        "font.size": 11.5 if dark else 10,
        "figure.facecolor": background, "axes.facecolor": background,
        "text.color": foreground, "axes.labelcolor": foreground,
        "xtick.color": secondary, "ytick.color": secondary, "axes.edgecolor": border,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.titleweight": "bold", "savefig.facecolor": background,
    }):
        size = (10, 8 if comparison else 7) if dark else (12, 10 if comparison else 8.6)
        fig, axes = plt.subplots(plot_count, 1, figsize=size, sharex=True)
        fig.subplots_adjust(left=.115 if dark else .1, right=.97, bottom=.17 if dark else .14,
                            top=.84 if dark else .79, hspace=.28 if dark else .24)
        if dark:
            fig.text(.115, .955, "Lap telemetry", fontsize=16, weight="bold", color=foreground)
        else:
            fig.text(.1, .955, f"{track} | {car}", fontsize=20, weight="bold", color="#172a3d")
            fig.text(.1, .915, "Lap telemetry by track position", fontsize=12, color="#536474")
        if comparison:
            gap = laps[1].seconds - laps[0].seconds
            description = f"Latest lap minus reference: {gap:+.3f} s. Positive means the latest lap is slower."
        else:
            lap = laps[0]
            description = (f"Lap time  {time_label(lap.seconds)}     "
                           f"Top speed  {lap.top_speed:.1f} km/h     "
                           f"Samples  {len(lap.rows):,}")
        if dark and comparison:
            description = f"Comparison minus reference: {gap:+.3f} s  ·  Positive = slower"
        fig.text(.115 if dark else .1, .923 if dark else .875, description,
                 fontsize=11, color=secondary if dark else foreground)
        panels = [("speed_kmh", "Speed (km/h)"), ("throttle_pct", "Throttle (%)"), ("brake_pct", "Brake (%)")]
        single_colours = ["#3BCBC4", "#3BCBC4", "#3BCBC4"] if dark else ["#245fa5", "#168679", "#c55040"]
        for panel_index, (key, ylabel) in enumerate(panels):
            ax = axes[panel_index]
            for index, lap in enumerate(laps):
                x, y = [], []
                for point, r in enumerate(lap.rows):
                    if point and max(r["capture_time_s"] - lap.rows[point - 1]["capture_time_s"],
                                     r["lap_time_s"] - lap.rows[point - 1]["lap_time_s"]) > TRACE_GAP_S:
                        x.append(float("nan"))
                        y.append(float("nan"))
                    x.append(r["track_position"] * 100)
                    y.append(r[key])
                colour = colours[index] if comparison else single_colours[panel_index]
                ax.plot(x, y, color=colour, linewidth=1.6 if dark else 1.4,
                        linestyle="--" if comparison and index == 0 else "-", label=labels[index])
            ax.set_ylabel(ylabel, labelpad=10)
            if key.endswith("_pct"):
                ax.set_ylim(-4, 104)
                ax.set_yticks([0, 50, 100])
            else:
                upper = max(lap.top_speed for lap in laps)
                ax.set_ylim(0, math.ceil(upper / 50) * 50)
                ax.yaxis.set_major_locator(MultipleLocator(100))
        axes[0].legend(loc="lower left", bbox_to_anchor=(0, 1.025 if dark else 1.05),
                       frameon=False, ncol=2, borderaxespad=0, labelcolor=foreground)
        if comparison:
            ref_x, ref_t = increasing_trace(laps[0], "lap_time_s")
            cur_x, cur_t = increasing_trace(laps[1], "lap_time_s")
            ref_gaps, cur_gaps = laps[0].gaps, laps[1].gaps
            # Align to the same track position; do not extrapolate beyond recorded coverage.
            x = np.linspace(max(ref_x[0], cur_x[0]), min(ref_x[-1], cur_x[-1]), 1200)
            def difference_at(position):
                ref_value = value_at((ref_x, ref_t), ref_gaps, position)
                cur_value = value_at((cur_x, cur_t), cur_gaps, position)
                return float("nan") if ref_value is None or cur_value is None else cur_value - ref_value
            delta = [difference_at(position) for position in x]
            axes[3].axhline(0, color="#697587" if dark else "#84909b", linewidth=.8)
            axes[3].plot(x * 100, delta, color="#3BCBC4" if dark else "#6e4a89", linewidth=1.5)
            axes[3].set_ylabel("Time delta (s)", labelpad=10)
        for ax in axes:
            for lap in laps:
                for start, end, _ in lap.gaps:
                    ax.axvspan(start * 100, end * 100, color=gap_colour, alpha=.28 if dark else .45, linewidth=0)
            ax.set_xlim(0, 100)
            ax.xaxis.set_major_locator(MultipleLocator(10))
            ax.grid(axis="both", color="#333D4B" if dark else "#e3e8ed", linewidth=.65)
            ax.set_axisbelow(True)
        axes[-1].set_xlabel("Position around the track (%)", labelpad=9)
        gap_messages = []
        for index, lap in enumerate(laps):
            label = ("Reference" if index == 0 else ("Comparison" if dark else "Latest")) if comparison else "Recorded lap"
            for start, _, seconds in lap.gaps:
                gap_messages.append(f"{label}: {seconds:.3f} s data gap near {start * 100:.1f}%, shaded in yellow.")
        if gap_messages:
            fig.text(.115 if dark else .1, .079 if dark else .84, "\n".join(gap_messages[:2]) if dark else " ".join(gap_messages[:2]),
                     fontsize=10 if dark else 9, color=gap_colour if dark else "#705512")
        fig.text(.115 if dark else .1, .044 if dark else .062,
                 "Validity manually reviewed by driver; not verified by telemetry. Setups may differ.",
                 fontsize=9, color=secondary if dark else "#667381")
        sources = list(dict.fromkeys(lap.source.name for lap in laps))
        fig.text(.115 if dark else .1, .021 if dark else .039, "Source: " + "; ".join(sources),
                 fontsize=8, color=secondary if dark else "#667381")
        fig.savefig(destination, dpi=160)
        plt.close(fig)



