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
import sys
import urllib.request
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
TOOLS_DIR = PROJECT_ROOT / "tools"
DEST_DIR = TOOLS_DIR / "platform-tools"

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


def is_installed() -> bool:
    return (DEST_DIR / "adb.exe").exists() or (DEST_DIR / "adb").exists()


def download_to_bytes(url: str) -> bytes:
    print(f"  downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as resp:
        return resp.read()


def extract(zip_bytes: bytes) -> None:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        zf.extractall(TOOLS_DIR)
    # The archive contains a top-level "platform-tools/" directory that
    # exactly matches DEST_DIR; nothing to move.


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
        print(f"OK: adb present at {DEST_DIR}")
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
