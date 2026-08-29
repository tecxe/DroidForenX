"""Samsung Android device adapter.

Specialises ADB detection for Samsung devices. Attempts to read additional
Samsung-relevant build/property keys (e.g. Knox build state) when accessible
through the standard, authorized ADB ``getprop`` interface.

If no Samsung-specific information can be obtained, detection falls back to
the generic ADB path -- the platform never claims Samsung-only data was
captured when it was not.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from .base_adapter import DeviceInfo, DeviceNotFoundError
from .adb_adapter import AdbAdapter

logger = logging.getLogger(__name__)

# Samsung-only property keys that may be accessible via getprop.
SAMSUNG_KEYS = (
    "ro.build.knox.container",
    "ro.config.knox",
    "ro.boot.warranty_bit",
    "ro.boot.secureboot",
    "ro.security.fips_mode",
    "ro.build.version.sem",
    "ro.build.version.sep",
    "ro.crypto.fde_enabled",
    "persist.sys.csc",
)


class SamsungAdapter(AdbAdapter):
    """Adapts Samsung devices. Falls back to generic ADB when needed."""

    manufacturer_code = "samsung"
    display_name = "Samsung Adapter"

    def _apply_properties(self, info: DeviceInfo, props: Dict[str, str]):
        super()._apply_properties(info, props)
        if not props:
            return
        # Capture Samsung-specific properties verbatim into raw metadata only
        # if actually present; do not fabricate any values.
        samsung = {k: props[k] for k in SAMSUNG_KEYS if k in props and props[k]}
        if samsung:
            info.raw_properties["samsung_extras"] = samsung
            info.raw_properties["adapter"] = "samsung"
        else:
            info.raw_properties["adapter"] = "samsung_fallback_generic"
