"""Locate the ADB (Android Debug Bridge) binary used by the platform.

The platform can operate two ways:
  * Bundle the official Android platform-tools under ``tools/platform-tools``
    by running ``setup_adb.py`` (see Phase 5), or
  * Reuse an ADB already available on the system ``PATH``.

This module resolves the best available ``adb`` executable and reports its
availability so other layers can degrade gracefully when ADB is absent.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional


def project_root() -> Path:
    """Return the project root directory (two levels up from this file)."""
    return Path(__file__).resolve().parent.parent


def bundled_adb_path() -> Optional[Path]:
    """Path to a bundled adb executable if present, else None."""
    root = project_root()
    # Windows executable
    for exe in ("adb.exe", "adb"):
        candidate = root / "tools" / "platform-tools" / exe
        if candidate.exists():
            return candidate
    return None


def system_adb_path() -> Optional[Path]:
    """Path to an adb on the system PATH if present, else None."""
    found = shutil.which("adb")
    if found:
        return Path(found)
    return None


def resolve_adb(env_adb: Optional[str] = None) -> Optional[Path]:
    """Return the adb executable to use, or None if ADB is not available.

    Resolution order:
      1. explicit path from environment (ADB_PATH)
      2. bundled platform-tools
      3. system PATH
    """
    explicit = env_adb or os.getenv("ADB_PATH")
    if explicit:
        p = Path(explicit)
        if p.exists():
            return p
    for candidate in (bundled_adb_path(), system_adb_path()):
        if candidate is not None:
            return candidate
    return None


def adb_available() -> bool:
    """Whether an adb binary is currently resolvable."""
    return resolve_adb() is not None


def adb_binary() -> Optional[str]:
    """Return the adb executable path as a string, or None."""
    path = resolve_adb()
    return str(path) if path else None
