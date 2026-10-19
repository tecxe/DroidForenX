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

Reproducibility
---------------
The same commit builds byte-identically as long as two things hold: the
dependency versions match requirements-lock.txt, and SOURCE_DATE_EPOCH is
pinned. This script does both. Without them PyInstaller stamps the current
time and the build path into the archive, so two people building the same
source get different SHA-256 values -- which makes the hash published on the
download page useless for verifying anyone else's build.

Install with `python -m pip install -r requirements-lock.txt` before building.
"""

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "DroidForenX.spec"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
ADB_DIR = ROOT / "desktop" / "adb_bundle"
LOCK = ROOT / "requirements-lock.txt"

# Used when there is no git checkout (source tarball, sdist).
FALLBACK_EPOCH = "1700000000"  # 2023-11-14, arbitrary but fixed


def commit_timestamp() -> str:
    """Epoch seconds of the HEAD commit, for SOURCE_DATE_EPOCH.

    Deriving it from git rather than from the wall clock is what makes two
    builds of the same commit agree. Falls back to a fixed value so a
    non-git checkout still builds deterministically.
    """
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--pretty=%ct"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=15,
        )
        stamp = out.stdout.strip()
        if out.returncode == 0 and stamp.isdigit():
            return stamp
    except (OSError, subprocess.SubprocessError):
        pass
    return FALLBACK_EPOCH


def parse_lock() -> dict:
    """Read exact pins out of requirements-lock.txt."""
    if not LOCK.exists():
        return {}
    pins = {}
    for line in LOCK.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        m = re.match(r"^([A-Za-z0-9._-]+)==(.+)$", line)
        if m:
            pins[m.group(1).lower().replace("_", "-")] = m.group(2)
    return pins


def check_lock(verbose: bool = True) -> bool:
    """Warn when the environment does not match requirements-lock.txt.

    A mismatch does not fail the build -- people legitimately develop against
    a newer PyQt6 -- but it does mean the resulting binary will not match the
    published hash, so it is worth saying out loud.
    """
    pins = parse_lock()
    if not pins:
        if verbose:
            print("[!] no requirements-lock.txt; build will not be reproducible")
        return False

    from importlib.metadata import PackageNotFoundError, version

    drift = []
    for name, want in sorted(pins.items()):
        try:
            got = version(name)
        except PackageNotFoundError:
            drift.append(f"{name}: not installed (want {want})")
            continue
        if got != want:
            drift.append(f"{name}: {got} (want {want})")

    if drift:
        if verbose:
            print("[!] dependency drift against requirements-lock.txt:")
            for d in drift:
                print(f"      {d}")
            print("      -> binary will not match the published SHA-256")
            print("      -> pip install -r requirements-lock.txt to reproduce")
        return False

    if verbose:
        print("[+] dependencies match requirements-lock.txt")
    return True


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

    check_lock()

    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="Build a standalone DroidForenX executable.")
    ap.add_argument("--clean", action="store_true", help="delete build/ and dist/ first")
    ap.add_argument("--check", action="store_true", help="preflight only, build nothing")
    args = ap.parse_args()

    print(f"[*] platform : {sys.platform}")
    print(f"[*] python   : {sys.version.split()[0]}")

    # Pin every timestamp PyInstaller would otherwise take from the wall
    # clock. This is what lets two builds of one commit produce one hash.
    epoch = commit_timestamp()
    print(f"[*] epoch    : SOURCE_DATE_EPOCH={epoch}")

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

    build_env = dict(os.environ, SOURCE_DATE_EPOCH=epoch, PYTHONHASHSEED="0")

    cmd = [sys.executable, "-m", "PyInstaller", str(SPEC), "--noconfirm"]
    print(f"\n[*] {' '.join(cmd)}\n")
    rc = subprocess.call(cmd, cwd=str(ROOT), env=build_env)
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
    print(
        "\n[i] Rebuilding this commit with requirements-lock.txt installed and\n"
        "    this same Python should reproduce the hash above exactly."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())