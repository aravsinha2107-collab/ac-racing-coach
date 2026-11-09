"""The desktop plot theme must preserve the report's evidence and CLI default."""

from copy import deepcopy
import io
from pathlib import Path
import unittest
from unittest.mock import patch

from ac_racing_coach.analysis import find_complete_laps, load_csv
from ac_racing_coach.plotting import plot_laps


ROOT = Path(__file__).resolve().parents[1]
RECORDING = ROOT / "recordings/session_20260913_213429_162345.csv"


class PlotThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.laps = find_complete_laps(load_csv(RECORDING), RECORDING)[0]
        for lap in cls.laps:
            for row in lap.rows:
                row["lap_validity"] = "manual_valid"

    def test_default_report_remains_explicit_light_and_theme_is_local(self):
        import matplotlib
        before = matplotlib.rcParams["figure.facecolor"]
        implicit, explicit = io.BytesIO(), io.BytesIO()
        plot_laps(self.laps, implicit)
        plot_laps(self.laps, explicit, theme="light")
        self.assertEqual(implicit.getvalue(), explicit.getvalue())
        from PIL import Image
        with Image.open(implicit) as image:
            self.assertEqual(image.getpixel((0, 0))[:3], (255, 255, 255))
        plot_laps(self.laps, io.BytesIO(), theme="dark")
        self.assertEqual(matplotlib.rcParams["figure.facecolor"], before)

    def test_dark_report_has_readable_palette_and_does_not_bridge_gap(self):
        import numpy as np
        from matplotlib.colors import to_rgba
        from matplotlib.figure import Figure
        from PIL import Image

        destination = io.BytesIO()
        figures = []
        original_save = Figure.savefig

        def capture_and_save(figure, *args, **kwargs):
            figures.append(figure)
            return original_save(figure, *args, **kwargs)

        with patch.object(Figure, "savefig", capture_and_save):
            plot_laps(self.laps, destination, theme="dark")
        figure = figures[0]
        self.assertEqual(figure.get_facecolor(), to_rgba("#1B2028"))
        for axis in figure.axes:
            self.assertEqual(axis.get_facecolor(), to_rgba("#1B2028"))
            self.assertEqual(axis.yaxis.label.get_color(), "#F4F6FA")
        speed = figure.axes[0]
        self.assertEqual(speed.lines[0].get_color(), "#AABEE4")
        self.assertEqual(speed.lines[0].get_linestyle(), "--")
        self.assertEqual(speed.lines[1].get_color(), "#3BCBC4")
        self.assertEqual(speed.lines[1].get_linestyle(), "-")
        self.assertTrue(all(to_rgba(text.get_color()) == to_rgba("#F4F6FA")
                            for text in speed.get_legend().get_texts()))
        self.assertTrue(np.isnan(speed.lines[0].get_ydata()).any())
        delta = figure.axes[-1].lines[-1]
        left, right, _ = self.laps[0].gaps[0]
        affected = (delta.get_xdata() > left * 100) & (delta.get_xdata() < right * 100)
        self.assertTrue(affected.any())
        self.assertTrue(np.isnan(delta.get_ydata()[affected]).all())
        notes = [text for text in figure.texts if "0.267" in text.get_text()]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].get_color(), "#EACB75")
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        canvas = FigureCanvasAgg(figure)
        canvas.draw()
        renderer = canvas.get_renderer()
        self.assertLess(notes[0].get_window_extent(renderer).y1,
                        figure.axes[-1].xaxis.label.get_window_extent(renderer).y0,
                        "The visible gap warning must not collide with the track-position label")
        with Image.open(destination) as image:
            self.assertEqual(image.getpixel((0, 0))[:3], (27, 32, 40))

    def test_dark_theme_cannot_render_unknown_or_invalid_comparison(self):
        for validity in ("unknown", "invalid"):
            laps = deepcopy(self.laps)
            for row in laps[1].rows:
                row["lap_validity"] = validity
            destination = io.BytesIO()
            with self.subTest(validity=validity), self.assertRaisesRegex(ValueError, "manually reviewed"):
                plot_laps(laps, destination, theme="dark")
            self.assertEqual(destination.getvalue(), b"")
        with self.assertRaisesRegex(ValueError, "Plot theme"):
            plot_laps(self.laps, io.BytesIO(), theme="unknown")


if __name__ == "__main__":
    unittest.main()
