"""Android version / API compatibility layer.

Maps an Android API level (and marketing version) to the set of collection
techniques and artifact categories that are *technically feasible* through
legitimate, authorized channels.

This is the single source of truth the platform consults before attempting any
acquisition or analysis. It deliberately distinguishes three tiers:

    SUPPORTED        - the technique is expected to work on this API level.
    PARTIAL          - only a subset of data is expected to be reachable.
    UNAVAILABLE      - the technique is not expected to work on this API level.

Nothing here guarantees a *specific* outcome for a given physical device; it
only reflects platform-level capability changes across Android versions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List

# ---------------------------------------------------------------------------
# Capability tiers
# ---------------------------------------------------------------------------
SUPPORTED = "supported"
PARTIAL = "partial"
UNAVAILABLE = "unavailable"

TIER_LABELS = {
    SUPPORTED: "Supported",
    PARTIAL: "Partially supported",
    UNAVAILABLE: "Unavailable",
}

# ---------------------------------------------------------------------------
# Artifact / technique identifiers
# ---------------------------------------------------------------------------
ARTIFACTS = {
    # Device identification properties
    "device_identification": "Device identification (build.prop/getprop)",
    # Shared/public storage
    "shared_storage": "Shared/public storage (media, downloads, documents)",
    "scoped_storage_recent": "Recently-accessed files under scoped storage",
    # Logical data
    "instant_app_data": "Instant-app data",
    "app_public_data": "App public data dirs (/sdcard/Android/data)",
    "app_private_data": "App private data (/data/data) via authorized backup",
    "app_private_data_run_as": "App private data (/data/data) via authorized run-as",
    # Backups
    "adb_backup_auto": "ADB auto-backup of app data (Android <=8)",
    "adb_backup_full": "ADB full backup (deprecated on newer Android)",
    "d2d_transfer": "Device-to-device transfer data",
    # Databases & misc
    "sqlite_databases": "SQLite databases in reachable app data",
    "accessibility_cache": "Accessibility service data (where authorized)",
    "notification_history": "Notification history (API 28+, where accessible)",
    "call_logs": "Call logs via authorized provider",
    "sms": "SMS/MMS via authorized provider",
    "contacts": "Contacts via authorized provider",
    "browser_history": "Browser history via authorized provider",
    "bluetooth_history": "Bluetooth pairing history (authorized)",
    "wifi_history": "Saved Wi-Fi networks (authorized, encrypted on newer)",
}

# ---------------------------------------------------------------------------
# API-level thresholds (cutoffs that gate techniques)
# ---------------------------------------------------------------------------
API_8 = 26      # Android 8.0 - scoped-ish changes; adb backup still full-ish
API_9 = 28      # Android 9   - starred app data auto-backup changes
API_11 = 30     # Android 11  - scoped storage enforced, /Android/data harder
API_12 = 31     # Android 12  - adb full backup of app data disabled
API_13 = 33     # Android 13  - photo picker, scoped storage tightened
API_14 = 34     # Android 14  - /sdcard/Android/data access further restricted
API_15 = 35     # Android 15  - latest stable in the compatibility table


# ---------------------------------------------------------------------------
# Explicit per-API capability tables
# ---------------------------------------------------------------------------
# Each artifact maps to a list of (max_api_inclusive, tier). The first entry
# whose max_api >= queried API wins. Ordered ascending by max_api.
_CAPABILITY_TABLE: Dict[str, List[tuple]] = {
    # Stable across all supported versions.
    "device_identification": [(9999, SUPPORTED)],
    "shared_storage": [(9999, SUPPORTED)],
    "sqlite_databases": [(9999, SUPPORTED)],  # where DBs are reachable

    # Call logs / SMS / contacts / browser via authorized providers.
    "call_logs": [(9999, SUPPORTED)],
    "sms": [(9999, SUPPORTED)],
    "contacts": [(9999, SUPPORTED)],
    "browser_history": [(9999, SUPPORTED)],
    "bluetooth_history": [(9999, PARTIAL)],
    "wifi_history": [(9999, PARTIAL)],
    "notification_history": [(27, UNAVAILABLE), (9999, SUPPORTED)],

    # App private data paths.
    "app_private_data_run_as": [
        # run-as is app-debuggable-only; availability depends on the app, but
        # on modern Android even debuggable apps are harder to reach.
        (22, SUPPORTED),
        (28, SUPPORTED),
        (30, PARTIAL),
        (9999, PARTIAL),
    ],
    "app_private_data": [
        (22, SUPPORTED),
        (28, SUPPORTED),
        (9999, UNAVAILABLE),  # adb backup of app data deprecated/disabled
    ],
    "adb_backup_auto": [
        (22, SUPPORTED),
        (26, SUPPORTED),
        (28, PARTIAL),
        (9999, UNAVAILABLE),
    ],
    "adb_backup_full": [
        (22, SUPPORTED),
        (28, PARTIAL),
        (9999, UNAVAILABLE),
    ],

    # Scoped storage progressively restricts /sdcard/Android/data etc.
    "app_public_data": [
        (28, SUPPORTED),
        (30, PARTIAL),
        (9999, PARTIAL),
    ],
    "scoped_storage_recent": [
        (29, UNAVAILABLE),
        (9999, PARTIAL),
    ],
    "accessibility_cache": [(9999, UNAVAILABLE)],
    "instant_app_data": [(9999, PARTIAL)],
    "d2d_transfer": [(9999, UNAVAILABLE)],
}


@dataclass
class CapabilityReport:
    """Result of a compatibility query for a single artifact."""
    artifact_key: str
    artifact_label: str
    tier: str
    api_level: int
    note: str = ""

    @property
    def label(self) -> str:
        return TIER_LABELS.get(self.tier, self.tier)


def capability_for(artifact_key: str, api_level) -> str:
    """Return the capability tier (supported/partial/unavailable) for an artifact.

    ``api_level`` may be None (unknown) in which case unsupported is assumed
    with a conservative default of ``partial`` where uncertain -- the caller
    decides whether to treat unknowns as unavailable.
    """
    if artifact_key not in _CAPABILITY_TABLE:
        return UNAVAILABLE
    rules = _CAPABILITY_TABLE[artifact_key]
    if api_level is None:
        # With unknown API, assume the most permissive modern default but flag
        # it as partial so the platform never over-claims.
        return PARTIAL
    for max_api, tier in rules:
        if api_level <= max_api:
            return tier
    return UNAVAILABLE


def get_capability(artifact_key: str, api_level) -> CapabilityReport:
    """Return a full CapabilityReport for a single artifact."""
    tier = capability_for(artifact_key, api_level)
    return CapabilityReport(
        artifact_key=artifact_key,
        artifact_label=ARTIFACTS.get(artifact_key, artifact_key),
        tier=tier,
        api_level=api_level or -1,
        note=TIER_LABELS.get(tier, tier),
    )


def full_compatibility_report(api_level) -> Dict[str, CapabilityReport]:
    """Return a report over every known artifact for a given API level."""
    return {
        key: get_capability(key, api_level)
        for key in ARTIFACTS
    }


def summarize_report(api_level) -> Dict[str, List[str]]:
    """Group every artifact by tier, as lists of labels -- for the UI/report.

    Example result::

        {
          "supported": ["Device identification ...", "Shared/public storage ..."],
          "partial": ["App private data (/data/data) via authorized run-as"],
          "unavailable": ["ADB auto-backup of app data (Android <=8)", ...],
        }
    """
    grouped: Dict[str, List[str]] = {SUPPORTED: [], PARTIAL: [], UNAVAILABLE: []}
    for key in ARTIFACTS:
        tier = capability_for(key, api_level)
        grouped[tier].append(ARTIFACTS[key])
    return grouped


# ---------------------------------------------------------------------------
# Version string parsing
# ---------------------------------------------------------------------------
# Marketing release -> API level lookups for both display and capability work.
VERSION_TO_API = {
    "1.5": 3, "1.6": 4, "2.0": 5, "2.0.1": 6, "2.1": 7,
    "2.2": 8, "2.3": 9, "2.3.3": 10, "3.0": 11, "3.1": 12,
    "3.2": 13, "4.0": 14, "4.0.3": 15, "4.1": 16, "4.2": 17,
    "4.3": 18, "4.4": 19, "5.0": 21, "5.1": 22, "6.0": 23,
    "7.0": 24, "7.1": 25, "8.0": 26, "8.1": 27, "9": 28,
    "10": 29, "11": 30, "12": 31, "12L": 32, "13": 33,
    "14": 34, "15": 35,
}

API_TO_VERSION = {v: k for k, v in VERSION_TO_API.items()}

# Codename hints (from ro.build.version.codename) for newer builds.
CODENAME_TO_API = {
    "REL": None,  # REL means a finalized release; use the version number.
    "VanillaIceCream": 35,
}


def api_from_version(version: str) -> int | None:
    """Map a marketing version string (e.g. '12', '15') to an API level."""
    if not version:
        return None
    v = str(version).strip()
    # Prefer exact or normalized match.
    if v in VERSION_TO_API:
        return VERSION_TO_API[v]
    # Try to strip trailing letters, e.g. "12L" -> "12".
    base = re.sub(r"[a-zA-Z]+$", "", v).strip()
    if base in VERSION_TO_API:
        return VERSION_TO_API[base]
    # The version may itself be reported as an int.
    try:
        return int(base)
    except (ValueError, TypeError):
        return None


def version_from_api(api_level) -> str:
    """Return the marketing version string for an API level."""
    if isinstance(api_level, str):
        api_level = api_from_version(api_level)
    if api_level in API_TO_VERSION:
        return API_TO_VERSION[api_level]
    return f"API {api_level}"


def compatibility_for_device(api_level, version: str = "") -> Dict[str, dict]:
    """Convenience: full report as plain dicts for a device (for reports/API)."""
    api = api_level
    if api is None and version:
        api = api_from_version(version)
    report = full_compatibility_report(api)
    return {k: {"tier": v.tier, "label": v.label, "note": v.note}
            for k, v in report.items()}
