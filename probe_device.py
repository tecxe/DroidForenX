"""
Read-only device capability probe.

Answers one question honestly: on THIS device, what can DroidForenX actually
acquire? It performs no writes, pulls no data, and changes no device state.
It only asks whether each source is readable, so we know the ceiling of the
baseline before we claim anything about it.

Usage:  python probe_device.py [serial]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from desktop.core import adb_manager as adb
from desktop.core.forensic_engine import ROOT_REQUIRED_MODULES


def check(serial, label, command, expect_rows=None):
    """
    Run a read-only query. Returns (status, detail) where status is one of
    READABLE / EMPTY / DENIED / UNSUPPORTED. Never guesses.

    Error detection deliberately does NOT scan the whole payload for words like
    "denied" or "unknown". Real forensic data contains those words - a contact
    named "Unknown", an SMS from "Unknown number" - and matching against
    payload turns genuine evidence into a false failure. An error is only
    believed when the command produced no data rows at all.
    """
    ok, out = adb.adb_shell(serial, command, timeout=25)
    if not ok:
        return "UNSUPPORTED", (out or "command failed")[:90]

    lines = [ln for ln in (out or "").splitlines() if ln.strip()]
    data_rows = [ln for ln in lines if ln.startswith("Row:")]

    # The payload itself counts as data, even when it is not "Row:"-prefixed
    # (dumpsys output, pm output, ls output).
    if data_rows:
        detail = f"{len(data_rows)} row(s)"
        if expect_rows:
            detail += f" of {', '.join(expect_rows)}"
        return "READABLE", detail

    low = "\n".join(lines[:15]).lower()
    if "permission denied" in low or "operation not permitted" in low \
            or "securityexception" in low:
        return "DENIED", "permission denied by Android"
    if "not found" in low or "no such" in low or "unknown url" in low \
            or "does not exist" in low or "exception occurred" in low:
        return "UNSUPPORTED", "provider or path absent on this build"

    if not lines:
        return "EMPTY", "query succeeded, device holds nothing of this type"

    # Anything else with real content is data. dumpsys, pm and ls do not use
    # the "Row:" prefix that content providers emit, so absence of that prefix
    # is not evidence of failure.
    detail = f"{len(lines)} line(s) of output"
    if expect_rows:
        detail += f" of {', '.join(expect_rows)}"
    return "READABLE", detail


def main():
    serial = sys.argv[1] if len(sys.argv) > 1 else None
    if not serial:
        devices = adb.ready_devices()
        if not devices:
            print(adb.describe_connection_state())
            return 1
        serial = devices[0]["serial"]

    print("=" * 72)
    print("DEVICE CAPABILITY PROBE  (read-only, no data pulled)")
    print("=" * 72)

    info = adb.get_device_info(serial)
    print(f"\n  model        : {info.manufacturer} {info.model}")
    print(f"  android      : {info.android_version} (API {info.api_level})")
    print(f"  build        : {info.build_fingerprint}")
    print(f"  kernel       : {info.kernel_version}")
    print(f"  bootloader   : {info.bootloader_state}")
    print(f"  encryption   : {info.encryption_status}")
    print(f"  sim          : {info.sim_state}")
    print(f"  storage      : {info.free_storage / 1e9:.1f} GB free of {info.total_storage / 1e9:.1f} GB")

    summary = adb.device_root_summary(serial)
    print(f"\n  ROOTED       : {summary['rooted']}")
    if summary["limitation"]:
        print(f"  LIMITATION   : {summary['limitation']}")

    # ---- what an unrooted device should be able to read -------------------
    print("\n" + "-" * 72)
    print("ACQUISABLE SOURCES  (content providers / dumpsys / pm / shared storage)")
    print("-" * 72)

    sources = [
        ("call_log",    "Call log",          "content query --uri content://call_log/calls --projection _id:number"),
        ("sms_mms",     "SMS / MMS",         "content query --uri content://sms/inbox --projection _id:address"),
        ("contacts",    "Contacts",          "content query --uri content://contacts/people --projection display_name"),
        ("calendar",    "Calendar",          "content query --uri content://com.android.calendar/events --projection title"),
        ("gps",         "GPS / location",    "dumpsys location | head -n 20"),
        ("wifi",        "WiFi scan results", "dumpsys wifi | head -n 20"),
        ("bluetooth",   "Bluetooth pairings","dumpsys bluetooth_manager | head -n 20"),
        ("apps",        "Running processes", "ps -A | head -n 5"),
        ("installed_apps","Installed apps",  "pm list packages --user 0 | head -n 5"),
        ("app_permissions","Permissions",     "pm list permissions | head -n 5"),
        ("usage_stats", "Usage stats",       "dumpsys usagestats | head -n 10"),
        ("notifications","Notifications",    "dumpsys notification --noredact | head -n 10"),
        ("clipboard",   "Clipboard",         "service call clipboard 1 2 2 s16 com.android.shell | head -n 5"),
        ("notes",       "Shared storage",    "ls /sdcard/ | head -n 8"),
    ]

    verdicts = {}
    for key, label, cmd in sources:
        if key in ROOT_REQUIRED_MODULES:
            continue
        status, detail = check(serial, label, cmd)
        verdicts[key] = status
        mark = {"READABLE": "[ OK ]", "EMPTY": "[EMPTY]", "DENIED": "[DENIED]",
                "UNSUPPORTED": "[N/A ]"}[status]
        print(f"  {mark} {label:22} {detail}")

    # ---- root-gated, for the record ---------------------------------------
    print("\n" + "-" * 72)
    print("ROOT-GATED SOURCES  (expected to fail; recorded, not hidden)")
    print("-" * 72)
    for key in sorted(ROOT_REQUIRED_MODULES):
        if key in ("wifi_passwords",):
            cmd = "ls /data/misc/wifi/ 2>&1 | head -n 3"
        else:
            cmd = "ls /data/data/ 2>&1 | head -n 3"
        status, detail = check(serial, key, cmd)
        verdicts[key] = status
        mark = {"READABLE": "[READS]", "EMPTY": "[EMPTY]", "DENIED": "[DENIED]",
                "UNSUPPORTED": "[N/A ]"}[status]
        print(f"  {mark} {key:22} {detail}")

    # ---- summary ----------------------------------------------------------
    readable = [k for k, v in verdicts.items() if v == "READABLE"]
    denied = [k for k, v in verdicts.items() if v == "DENIED"]
    empty = [k for k, v in verdicts.items() if v == "EMPTY"]
    na = [k for k, v in verdicts.items() if v == "UNSUPPORTED"]

    print("\n" + "=" * 72)
    print("BASELINE VERDICT")
    print("=" * 72)
    print(f"  readable     ({len(readable):2}) : {', '.join(sorted(readable)) or 'none'}")
    print(f"  empty        ({len(empty):2}) : {', '.join(sorted(empty)) or 'none'}")
    print(f"  denied       ({len(denied):2}) : {', '.join(sorted(denied)) or 'none'}")
    print(f"  unsupported  ({len(na):2}) : {', '.join(sorted(na)) or 'none'}")
    print()
    if not summary["rooted"]:
        print("  Device is NOT rooted. The root-gated sources above cannot be")
        print("  acquired. Report them as NOT_ACCESSIBLE, never as recovered.")
    print()
    print("  NOTHING WAS PULLED OR MODIFIED BY THIS PROBE.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())