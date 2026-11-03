"""Regression tests for the responsiveness fixes.

An acquisition and the artifact viewer both drive adb. Running them at once
made the window crawl, and a synchronous device scan on every page show added
a stall of its own. These tests pin the two contracts that keep the GUI free:

* a fresh device cache is applied without spawning a worker;
* the artifact viewer defers its crawl while an acquisition is running and
  resumes it exactly once when the acquisition finishes;
* a cancelled extraction is not persisted into the case.

Run with:  python -m unittest desktop.tests.test_performance_safety -v
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication

    from desktop.ui.pages.artifacts_page import ArtifactsPage
    from desktop.ui.widgets import device_status_bar as dsb
    from desktop.ui.widgets.device_status_bar import DeviceStatusBar

    _QT_IMPORT = True
except Exception:  # pragma: no cover - depends on the environment
    _QT_IMPORT = False


def _make_qapp():
    if not _QT_IMPORT:
        return None
    app = QApplication.instance() or QApplication([])
    app.setOrganizationName("DroidForenXTests")
    app.setApplicationName("DroidForenXTests")
    return app


@unittest.skipUnless(_QT_IMPORT, "PyQt6 is not available")
class FreshCacheScanTest(unittest.TestCase):
    """A hot cache must paint without touching adb on the GUI thread."""

    def setUp(self):
        self._app = _make_qapp()
        dsb._cache["devices"] = [{"serial": "CACHED", "model": "TECNO"}]
        dsb._cache["ts"] = __import__("time").monotonic()

    def test_a_fresh_cache_is_emitted_synchronously(self):
        bar = DeviceStatusBar()
        seen = []
        bar.device_connected.connect(seen.append)
        bar.scan()
        self.assertEqual(seen and seen[0].get("serial"), "CACHED")
        self.assertIsNone(bar._scan_worker)


@unittest.skipUnless(_QT_IMPORT, "PyQt6 is not available")
class AcquisitionDeferralTest(unittest.TestCase):
    """The viewer must stand down while an acquisition owns the device."""

    def setUp(self):
        self._app = _make_qapp()
        self.page = ArtifactsPage()
        self.page.device_bar._devices = [{"serial": "SERIAL"}]
        self.starts = []

        def fake_start(serial, category):
            self.starts.append((serial, category))

        self.page._start_load = fake_start

    def tearDown(self):
        self.page.deleteLater()
        self._app.processEvents()

    def test_the_crawl_waits_for_the_acquisition_to_finish(self):
        self.page.on_acquisition_started()
        self.page._on_device_connected({"serial": "SERIAL"})
        self.assertEqual(self.starts, [], "must not start while acquiring")
        self.assertTrue(self.page._load_deferred)

        self.page.on_acquisition_finished()
        self.assertEqual(self.starts, [("SERIAL", "All")])
        self.assertTrue(self.page._data_loaded)
        self.assertFalse(self.page._load_deferred)

    def test_a_cancelled_run_is_not_persisted(self):
        persisted = []
        self.page._persist_artifacts = lambda results: persisted.append(results)
        self.page._worker = type("W", (), {"_cancel": True})()

        self.page._on_finished([])

        self.assertEqual(persisted, [], "a cancelled run must not write the case")


if __name__ == "__main__":
    unittest.main()
