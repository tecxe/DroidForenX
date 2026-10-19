"""Reproducible-build checks for build.py.

Two builds of the same commit must produce the same SHA-256, otherwise the
hash printed on the download page cannot be used by anyone who compiles the
source themselves. These tests cover the two mechanisms that make that hold:

  * SOURCE_DATE_EPOCH is derived from the git commit, not the wall clock
  * requirements-lock.txt exists and matches the installed environment

Running the full double-build takes several minutes, so that case is opt-in
via DROIDFORENX_SLOW_TESTS=1. The cheap structural checks run by default.

    python -m desktop.tests.test_reproducible_build
    DROIDFORENX_SLOW_TESTS=1 python -m desktop.tests.test_reproducible_build
"""

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD_PY = ROOT / "build.py"
LOCK = ROOT / "requirements-lock.txt"
DIST = ROOT / "dist"


def run_build(*args, timeout=1800):
    return subprocess.run(
        [sys.executable, str(BUILD_PY), *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class EpochPinningTest(unittest.TestCase):
    def test_build_py_pins_source_date_epoch(self):
        """The archive timestamps must come from the commit, not from now."""
        src = BUILD_PY.read_text(encoding="utf-8")
        self.assertIn(
            "SOURCE_DATE_EPOCH",
            src,
            "build.py must export SOURCE_DATE_EPOCH for PyInstaller to honour",
        )

    def test_epoch_comes_from_git_commit(self):
        src = BUILD_PY.read_text(encoding="utf-8")
        self.assertIn(
            "commit_timestamp",
            src,
            "SOURCE_DATE_EPOCH must be derived from the git commit timestamp",
        )
        self.assertIn("--pretty=%ct", src)

    def test_epoch_is_not_the_wall_clock(self):
        """time.time() would reintroduce drift; reject it outright."""
        src = BUILD_PY.read_text(encoding="utf-8")
        for smell in ("time.time()", "datetime.now()", "datetime.utcnow()"):
            self.assertNotIn(
                smell,
                src,
                f"build.py must not derive the epoch from the wall clock ({smell})",
            )

    def test_non_git_checkout_still_deterministic(self):
        """A tarball checkout has no git; the fallback must be a constant."""
        src = BUILD_PY.read_text(encoding="utf-8")
        self.assertIn("FALLBACK_EPOCH", src)


class LockFileTest(unittest.TestCase):
    def test_lock_exists(self):
        self.assertTrue(LOCK.exists(), f"missing {LOCK.name}")

    def test_lock_uses_exact_pins(self):
        pins = [
            ln.split("#", 1)[0].strip()
            for ln in LOCK.read_text(encoding="utf-8").splitlines()
        ]
        pins = [p for p in pins if p]
        self.assertTrue(pins, "lock file has no entries")
        for p in pins:
            self.assertIn("==", p, f"{p!r} is not pinned with ==")

    def test_lock_covers_direct_dependencies(self):
        text = LOCK.read_text(encoding="utf-8").lower()
        for dep in ("pyqt6", "fpdf2", "openpyxl", "pillow", "pyinstaller"):
            self.assertIn(dep, text, f"{dep} is not pinned in the lock file")

    def test_installed_environment_matches_lock(self):
        """Drift here means the built binary will not match the published hash."""
        from importlib.metadata import PackageNotFoundError, version

        pins = {}
        for line in LOCK.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if "==" in line:
                name, want = line.split("==", 1)
                pins[name.strip().lower().replace("_", "-")] = want.strip()

        drift = []
        for name, want in pins.items():
            try:
                got = version(name)
            except PackageNotFoundError:
                drift.append(f"{name} not installed (want {want})")
                continue
            if got != want:
                drift.append(f"{name} {got} (want {want})")

        self.assertEqual(drift, [], "dependency drift:\n  " + "\n  ".join(drift))

    def test_requirements_txt_points_at_the_lock(self):
        req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn(
            "requirements-lock.txt",
            req,
            "requirements.txt should tell people the lock file exists",
        )


class PreflightTest(unittest.TestCase):
    def test_check_mode_passes(self):
        out = run_build("--check")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_check_reports_epoch_source(self):
        out = run_build("--check")
        self.assertIn("SOURCE_DATE_EPOCH", out.stdout)


@unittest.skipUnless(
    os.environ.get("DROIDFORENX_SLOW_TESTS") == "1",
    "set DROIDFORENX_SLOW_TESTS=1 to run the double-build check (minutes)",
)
class DoubleBuildTest(unittest.TestCase):
    """The real proof: two builds, one hash."""

    def test_two_builds_are_byte_identical(self):
        exe = DIST / ("DroidForenX.exe" if sys.platform == "win32" else "DroidForenX")
        if not exe.exists():
            self.skipTest("no existing build to compare against")

        first = out1 = None
        first = exe.read_bytes()

        out2 = run_build()
        self.assertEqual(out2.returncode, 0, out2.stdout + out2.stderr)
        second = exe.read_bytes()

        import hashlib

        h1 = hashlib.sha256(first).hexdigest()
        h2 = hashlib.sha256(second).hexdigest()
        self.assertEqual(
            h1,
            h2,
            f"builds differ:\n  before {h1}\n  after  {h2}\n"
            f"SOURCE_DATE_EPOCH or the lock file is not doing its job",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
