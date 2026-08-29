"""Generic Android device adapter.

Used for manufacturers without a specialised adapter. It tries ADB-based
detection first and, when ADB is unavailable or a device cannot be reached,
returns an honest "unknown/not-accessible" result rather than guessing.

This is the fallback target for every device that no specialised adapter can
handle.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from .base_adapter import DeviceInfo, DeviceNotFoundError
from .adb_adapter import AdbAdapter

logger = logging.getLogger(__name__)


class GenericAndroidAdapter(AdbAdapter):
    """Catch-all adapter: ADB detection for any Android device."""

    manufacturer_code = "generic"
    display_name = "Generic Android Adapter"

    def describe(self) -> dict:
        meta = super().describe()
        meta["fallback"] = True
        return meta
