"""Google Pixel Android device adapter.

Specialises ADB detection for Google Pixel / AOSP-style devices. Pixels run
a stock build with well-populated ``ro.*`` properties, so this adapter simply
applies the generic ADB mapping. If any Google-specific keys change in the
future they can be added here without affecting other manufacturers.

Uses the generic path when ADB is not available -- it never claims
Pixel-specific data it did not capture.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from .base_adapter import DeviceInfo, DeviceNotFoundError
from .adb_adapter import AdbAdapter

logger = logging.getLogger(__name__)


class GoogleAdapter(AdbAdapter):
    """Adapts Google Pixel devices (stock AOSP property layout)."""

    manufacturer_code = "google"
    display_name = "Google Adapter"

    def _apply_properties(self, info: DeviceInfo, props: Dict[str, str]):
        super()._apply_properties(info, props)
        if props:
            info.raw_properties["adapter"] = "google"
