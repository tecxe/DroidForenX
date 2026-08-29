"""Shared helpers for device adapters.

These helpers decode raw device properties (``getprop`` output, build.prop
key/value pairs) into the normalized fields the platform relies on, and
normalize manufacturer strings into stable codes.
"""

from __future__ import annotations

from typing import Dict, Optional

# Map of known full manufacturer names (and common aliases) to a stable code.
MANUFACTURER_ALIASES = {
    "samsung": "samsung",
    "google": "google",
    "xiaomi": "xiaomi",
    "redmi": "redmi",
    "po": "xiaomi",          # Redmi/Poco brand extension
    "poco": "xiaomi",
    "oppo": "oppo",
    "oneplus": "oneplus",
    "vivo": "vivo",
    "huawei": "huawei",
    "honor": "huawei",
    "motorola": "motorola",
    "nokia": "nokia",
    "hmd global": "nokia",
    "sony": "sony",
    "asus": "asus",
    "realme": "realme",
    "tecno": "tecno",
    "infinix": "infinix",
    "zte": "zte",
    "lenovo": "lenovo",
    "lg": "generic",
    "htc": "generic",
}


def normalize_manufacturer(raw: Optional[str]) -> str:
    """Return the stable manufacturer code for a raw manufacturer string.

    Unknown / empty strings map to ``generic``; the platform never hard-codes
    a specific vendor assumption beyond aliasing well-known brands.
    """
    if not raw:
        return "generic"
    cleaned = raw.strip().lower()
    if cleaned in MANUFACTURER_ALIASES:
        return MANUFACTURER_ALIASES[cleaned]
    # Fall back to the leading token of the brand name (e.g. "samsung/xyz").
    first = cleaned.split("/")[0].split()[0]
    return MANUFACTURER_ALIASES.get(first, "generic")


def parse_property_line(line: str) -> Optional[tuple]:
    """Parse a single ``key=value`` property line.

    Returns (key, value) or None if the line is not a property assignment.
    """
    if not line or "=" not in line:
        return None
    key, _, value = line.partition("=")
    key = key.strip()
    value = value.strip()
    if not key:
        return None
    return key, value


def props_from_lines(lines) -> Dict[str, str]:
    """Turn an iterable of ``key=value`` lines into a property dict."""
    props: Dict[str, str] = {}
    for line in lines:
        parsed = parse_property_line(line)
        if parsed:
            key, value = parsed
            props[key] = value
    return props


def yes_no_to_bool(value: Optional[str], default: Optional[bool] = None) -> Optional[bool]:
    """Decode device boolean-ish strings (1/0, true/false, yes/no, on/off)."""
    if value is None:
        return default
    v = value.strip().lower()
    if v in ("1", "true", "yes", "on", "enabled", "active"):
        return True
    if v in ("0", "false", "no", "off", "disabled", "inactive"):
        return False
    return default


def try_int(value: Optional[str]) -> Optional[int]:
    """Safely parse an int (e.g. API level) from a raw string."""
    if value is None:
        return None
    try:
        return int(str(value).strip().split(".")[0])
    except (ValueError, TypeError):
        return None
