"""setup_adb.py — bundle the official Android platform-tools (ADB) for this platform.

Downloads and extracts the latest official Android platform-tools into
``tools/platform-tools`` so that DroidForenX can perform ADB-mediated logical
acquisition without requiring a pre-installed SDK.

The archive is downloaded from the Google-hosted repository for the current
operating system. No patches, repacking, or third-party mirrors are used; the
binaries remain the unmodified official build.

Usage:
    python setup_adb.py            # download if missing, report status
    python setup_adb.py --force    # re-download even if present
    python setup_adb.py --check    # only report availability
"""

from __future__ import annotations

import argparse
import io
import os
import shutil
import stat
import sys
import urllib.request
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
TOOLS_DIR = PROJECT_ROOT / "tools"
DEST_DIR = TOOLS_DIR / "platform-tools"

#: The platform-tools executables that must be runnable on POSIX hosts.
_POSIX_TOOLS = ("adb", "fastboot", "mke2fs")


def adb_binary() -> Path:
    """Path of the adb executable for this platform (adb.exe on Windows)."""
    return DEST_DIR / ("adb.exe" if os.name == "nt" else "adb")

# Official Google repository for Android platform-tools.
_URLS = {
    "win32": "https://dl.google.com/android/repository/platform-tools-latest-windows.zip",
    "darwin": ["https://dl.google.com/android/repository/platform-tools-latest-darwin.zip",
               "https://dl.google.com/android/repository/platform-tools-latest-darwin-arm64.zip"],
    "linux": "https://dl.google.com/android/repository/platform-tools-latest-linux.zip",
}


def platform_urls() -> list[str]:
    urls = _URLS.get(sys.platform)
    if urls is None:
        raise SystemExit(f"Unsupported platform: {sys.platform}")
    if isinstance(urls, str):
        return [urls]
    return list(urls)


def make_executable(directory: Path | None = None, posix: bool | None = None) -> None:
    """
    Restore the executable bit on POSIX hosts.

    Python's zipfile module does not apply the Unix permission bits stored in a
    zip entry, so a freshly extracted adb comes out non-executable and every
    later call fails with "Permission denied". Windows has no such bit.

    ``posix`` is injectable so this stays testable on a Windows host.
    """
    if posix is None:
        posix = os.name != "nt"
    if not posix:
        return
    directory = directory or DEST_DIR
    for name in _POSIX_TOOLS:
        target = directory / name
        if target.exists():
            mode = target.stat().st_mode
            target.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def is_installed(posix: bool | None = None) -> bool:
    """True when adb is present and, on POSIX, actually executable."""
    if posix is None:
        posix = os.name != "nt"
    adb = DEST_DIR / ("adb" if posix else "adb.exe")
    if not adb.exists():
        # Tolerate a bundle produced on the other OS rather than re-downloading.
        legacy = DEST_DIR / ("adb.exe" if posix else "adb")
        return legacy.exists()
    if not posix:
        return True
    return os.access(adb, os.X_OK)


def download_to_bytes(url: str) -> bytes:
    print(f"  downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as resp:
        return resp.read()


def extract(zip_bytes: bytes) -> None:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        zf.extractall(TOOLS_DIR)
    # The archive contains a top-level "platform-tools/" directory that
    # exactly matches DEST_DIR; nothing to move.
    make_executable()


def install(force: bool = False) -> None:
    if is_installed() and not force:
        print(f"platform-tools already present at {DEST_DIR} (use --force to re-download)")
        return

    os.makedirs(TOOLS_DIR, exist_ok=True)
    last_err: Exception | None = None
    for url in platform_urls():
        try:
            data = download_to_bytes(url)
            if DEST_DIR.exists() and force:
                shutil.rmtree(DEST_DIR)
            extract(data)
            if is_installed():
                print(f"installed platform-tools -> {DEST_DIR}")
                return
        except Exception as exc:  # noqa: BLE001 - report and try next mirror
            last_err = exc
            print(f"  failed ({url}): {exc}", file=sys.stderr)
    raise SystemExit(f"Could not install platform-tools: {last_err}")


def check() -> int:
    if is_installed():
        adb = adb_binary()
        print(f"OK: adb present at {adb}")
        if os.name != "nt" and not os.access(adb, os.X_OK):
            print("  note: not executable — run without --check to repair", file=sys.stderr)
            return 1
        return 0
    print("MISSING: adb not bundled (run without --check to install)")
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Bundle official Android platform-tools.")
    parser.add_argument("--force", action="store_true", help="re-download even if present")
    parser.add_argument("--check", action="store_true", help="only report availability")
    args = parser.parse_args()

    if args.check:
        raise SystemExit(check())

    install(force=args.force)
    if not is_installed():
        raise SystemExit("installation did not produce an adb binary")


if __name__ == "__main__":
    main()
