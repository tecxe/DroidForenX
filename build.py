#!/usr/bin/env python
"""Build a standalone DroidForenX executable.

    python build.py           # build for the current platform
    python build.py --clean   # remove previous build output first
    python build.py --check   # report what would be bundled, build nothing

Emits dist/DroidForenX.exe on Windows, or dist/DroidForenX on Linux/macOS.
Run it on the platform you want the binary for; the two are not portable.

adb is bundled automatically when desktop/adb_bundle/ contains it, so the
finished executable needs neither Python nor the Android SDK on the target.
If that directory is missing, run `python setup_adb.py` first.
"""

import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "DroidForenX.spec"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
ADB_DIR = ROOT / "desktop" / "adb_bundle"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def preflight() -> bool:
    """Check the build can succeed before spending minutes on it."""
    ok = True

    if not SPEC.exists():
        print(f"[x] missing spec: {SPEC}")
        return False

    if sys.version_info < (3, 11):
        print(f"[x] Python 3.11+ required, found {sys.version.split()[0]}")
        ok = False

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("[x] PyInstaller not installed -> pip install -r requirements.txt")
        ok = False

    for pkg in ("PyQt6", "openpyxl", "fpdf", "PIL"):
        try:
            __import__(pkg)
        except ImportError:
            print(f"[x] required package missing: {pkg}")
            ok = False

    adb = ADB_DIR / ("adb.exe" if sys.platform == "win32" else "adb")
    if adb.exists():
        print(f"[+] bundling adb ({adb.stat().st_size / 1e6:.1f} MB): {adb}")
        if sys.platform != "win32":
            # PyInstaller preserves the mode bits it copies, and a non-executable
            # adb fails with "Permission denied" only once the app is running.
            import os

            mode = adb.stat().st_mode
            if not mode & 0o111:
                print(f"[!] {adb} is not executable -> chmod +x it")
    else:
        print(f"[!] no bundled adb at {adb}; the exe will fall back to PATH")

    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="Build a standalone DroidForenX executable.")
    ap.add_argument("--clean", action="store_true", help="delete build/ and dist/ first")
    ap.add_argument("--check", action="store_true", help="preflight only, build nothing")
    args = ap.parse_args()

    print(f"[*] platform : {sys.platform}")
    print(f"[*] python   : {sys.version.split()[0]}")
    if not preflight():
        print("\n[x] preflight failed")
        return 1
    if args.check:
        print("\n[+] preflight OK")
        return 0

    if args.clean:
        for d in (BUILD, DIST):
            if d.exists():
                print(f"[*] removing {d.relative_to(ROOT)}/")
                shutil.rmtree(d)

    cmd = [sys.executable, "-m", "PyInstaller", str(SPEC), "--noconfirm"]
    print(f"\n[*] {' '.join(cmd)}\n")
    rc = subprocess.call(cmd, cwd=str(ROOT))
    if rc != 0:
        print(f"\n[x] PyInstaller exited {rc}")
        return rc

    exe = DIST / ("DroidForenX.exe" if sys.platform == "win32" else "DroidForenX")
    if not exe.exists():
        print(f"\n[x] build reported success but {exe} is missing")
        return 1

    digest = sha256(exe)
    size_mb = exe.stat().st_size / 1e6
    print(f"\n[+] built   : {exe}")
    print(f"[+] size    : {size_mb:.1f} MB")
    print(f"[+] sha256  : {digest}")

    sidecar = exe.with_suffix(exe.suffix + ".sha256")
    sidecar.write_text(f"{digest}  {exe.name}\n", encoding="utf-8")
    print(f"[+] sidecar : {sidecar.name}")

    print(
        "\n[i] Verify after download with:\n"
        f"    Get-FileHash {exe.name} -Algorithm SHA256   # PowerShell\n"
        f"    sha256sum {exe.name}                          # Linux/macOS"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())