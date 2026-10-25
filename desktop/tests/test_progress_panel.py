"""Tests for the percentage shown while an image is being written.

Two things are pinned here, because both were wrong in a way nobody noticed
until someone watched a forty-minute image:

  1. ``sources.logical_size_estimate`` must ask the device the question that
     actually works. ``du -s -k /sdcard`` returns 4 on a TECNO KL6 and
     ``stat -f /sdcard`` describes the whole userdata filesystem; only
     ``du -s -k /sdcard/`` returns the real figure.
  2. ``ProgressPanel`` must show a number whenever a total exists, must never go
     backwards, must never claim 100% before the writer is done, and must say
     "size unknown" rather than inventing a denominator when none exists.

Run with:  python -m unittest desktop.tests.test_progress_panel -v
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication
    _QT_IMPORT = True
except Exception:  # pragma: no cover - depends on the environment
    _QT_IMPORT = False

from desktop.core.imaging import sources
from desktop.ui.widgets.progress_panel import (
    ProgressPanel, human_bytes, human_seconds, percent_of,
)


def _make_qapp():
    if not _QT_IMPORT:
        return None
    return QApplication.instance() or QApplication([])


#: Trimmed output of ``stat -f /sdcard`` on the TECNO KL6: the whole userdata
#: filesystem, 95 GiB in use. Kept as a fixture because it is the number the
#: old code believed.
STAT_F = """
  File: "/sdcard"
    ID: 0000000000000000 Namelen: 255    Type: 0x65735546
Block Size: 4096    Fundamental block size: 4096
Blocks: Total: 27592696\tFree: 4331984\tAvailable: 4197767
Inodes: Total: 13511677\tFree: 4197767
"""


class LogicalSizeEstimateTest(unittest.TestCase):
    """The measurement has to survive toybox's quirks, not assume GNU tools."""

    def test_du_is_asked_with_a_trailing_slash(self):
        seen = []

        def shell(serial, command, timeout=20):
            seen.append(command)
            return True, "36614766\t/sdcard/\n"

        total = sources.logical_size_estimate("SERIAL", shell=shell)
        self.assertEqual(total, 36614766 * 1024)
        self.assertIn("/sdcard/", seen[0])
        self.assertTrue(seen[0].startswith("du "),
                        "du is the measurement that works on this handset")

    def test_falls_back_to_stat_f_when_du_fails(self):
        def shell(serial, command, timeout=20):
            if command.startswith("du"):
                return False, ""
            return True, STAT_F

        total = sources.logical_size_estimate("SERIAL", shell=shell)
        self.assertEqual(total, (27592696 - 4331984) * 4096)

    def test_du_answering_with_noise_is_ignored(self):
        def shell(serial, command, timeout=20):
            if command.startswith("du"):
                return False, "du: permission denied\n"
            return True, STAT_F

        total = sources.logical_size_estimate("SERIAL", shell=shell)
        self.assertEqual(total, (27592696 - 4331984) * 4096)

    def test_a_nonzero_exit_still_yields_the_total(self):
        """The real device: one denied path makes du exit 1, output is fine."""
        def shell(serial, command, timeout=20):
            if command.startswith("du"):
                return False, "36614698\t/sdcard/\n"
            return True, STAT_F

        total = sources.logical_size_estimate("SERIAL", shell=shell)
        self.assertEqual(total, 36614698 * 1024)

    def test_nothing_measurable_returns_zero(self):
        def shell(serial, command, timeout=20):
            return False, ""

        self.assertEqual(sources.logical_size_estimate("SERIAL", shell=shell), 0)

    def test_a_raising_shell_is_not_fatal(self):
        def shell(serial, command, timeout=20):
            raise OSError("adb vanished")

        self.assertEqual(sources.logical_size_estimate("SERIAL", shell=shell), 0)

    def test_du_parser_takes_kib_and_ignores_warning_lines(self):
        output = ("du: /sdcard/Android/data/cache/license.local: Permission denied\n"
                  "36614766\t/sdcard/\n")
        self.assertEqual(sources._parse_du_bytes(output), 36614766 * 1024)

    def test_du_parser_handles_keyboard_interrupt_shaped_output(self):
        self.assertEqual(sources._parse_du_bytes(""), 0)
        self.assertEqual(sources._parse_du_bytes("nonsense\n"), 0)


class HumanFormatTest(unittest.TestCase):
    def test_bytes(self):
        self.assertEqual(human_bytes(0), "0 B")
        self.assertEqual(human_bytes(900), "900 B")
        self.assertEqual(human_bytes(1536), "1.5 KB")
        self.assertEqual(human_bytes(62 * 1024 ** 3), "62.0 GB")

    def test_seconds(self):
        self.assertEqual(human_seconds(9), "9s")
        self.assertEqual(human_seconds(125), "2m 05s")
        self.assertEqual(human_seconds(3725), "1h 02m")

    def test_percent_never_reaches_one_hundred_early(self):
        self.assertIsNone(percent_of(10, 0))
        self.assertEqual(percent_of(999, 1000), 99)
        self.assertEqual(percent_of(1000, 1000), 100)
        self.assertEqual(percent_of(0, 1000), 0)


@unittest.skipUnless(_QT_IMPORT, "PyQt6 is not available")
class ProgressPanelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = _make_qapp()

    def setUp(self):
        self.panel = ProgressPanel()

    def test_shows_a_number_when_the_total_is_known(self):
        self.panel.set_progress(512, 1024, "packing DCIM")
        self.assertEqual(self.panel.bar.value(), 50)
        self.assertEqual(self.panel.stage_label.text(), "packing DCIM")
        self.assertIn("50%", self.panel.detail.text())
        self.assertIn("of 1.0 KB", self.panel.detail.text())

    def test_indeterminate_when_the_total_is_unknown_but_still_counts_bytes(self):
        self.panel.set_progress(4096, 0, "writing")
        self.assertEqual(self.panel.bar.maximum(), 0,
                         "an unknown total is a busy bar, not a fake percentage")
        self.assertIn("4.0 KB written", self.panel.detail.text())
        self.assertIn("size unknown", self.panel.detail.text())

    def test_never_goes_backwards(self):
        self.panel.set_progress(800, 1000)
        self.panel.set_progress(400, 1000)   # a retry reported less
        self.assertEqual(self.panel.bar.value(), 80)

    def test_total_may_grow_but_not_shrink(self):
        self.panel.set_progress(500, 1000)
        self.panel.set_progress(1500, 2000)   # tar outran its estimate
        self.assertEqual(self.panel.bar.value(), 75)
        self.panel.set_progress(1600, 1000)   # a smaller total must be ignored
        self.assertEqual(self.panel.bar.value(), 80)

    def test_completion_is_one_hundred_percent(self):
        self.panel.set_progress(1024, 1024)
        self.panel.finish()
        self.assertEqual(self.panel.bar.value(), 100)
        self.assertIn("100%", self.panel.detail.text())

    def test_a_failed_run_freezes_at_its_last_value(self):
        self.panel.set_progress(700, 1000)
        self.panel.stop_with("Failed")
        self.assertEqual(self.panel.bar.value(), 70)
        self.assertEqual(self.panel.stage_label.text(), "Failed")

    def test_late_signals_do_not_move_a_finished_bar(self):
        self.panel.set_progress(1000, 1000)
        self.panel.finish()
        self.panel.set_progress(10, 1000)
        self.assertEqual(self.panel.bar.value(), 100)

    def test_reset_clears_everything(self):
        self.panel.set_progress(500, 1000, "working")
        self.panel.finish()
        self.panel.reset()
        self.assertEqual(self.panel.bar.value(), 0)
        self.assertEqual(self.panel.stage_label.text(), "")
        self.assertEqual(self.panel.detail.text(), "")


if __name__ == "__main__":
    unittest.main()
