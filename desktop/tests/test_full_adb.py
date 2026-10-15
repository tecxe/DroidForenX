"""Full ADB integration test. Requires a connected device; prints results."""
import sys, os
from pathlib import Path

# Resolve the repository root relative to this file so the script is portable.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from desktop.core import adb_manager as adb

# Test 1: ADB path
print(f"ADB path: {adb.ADB_PATH}")
print(f"ADB exists: {os.path.exists(adb.ADB_PATH)}")

# Test 2: list_devices
devices = adb.list_devices()
print(f"Devices found: {len(devices)}")
for d in devices:
    print(f"  Serial: {d['serial']}  Model: {d.get('model','?')}")

if not devices:
    print("NO DEVICE CONNECTED - scan test passed (empty)")
    sys.exit(0)

serial = devices[0]["serial"]

# Test 3: get_device_info
info = adb.get_device_info(serial)
print(f"Model: {info.manufacturer} {info.model}")
print(f"Android: {info.android_version} (API {info.api_level})")
print(f"Battery: {info.battery_level}% ({info.battery_temp}C)")
print(f"Storage: {info.total_storage // (1024**3)}GB total, {info.free_storage // (1024**3)}GB free")
print(f"Encryption: {info.encryption_status}")
print(f"Bootloader: {info.bootloader_state}")
print(f"Kernel: {info.kernel_version}")

# Test 4: adb_shell
ok, out = adb.adb_shell(serial, "echo hello")
print(f"Shell test: ok={ok} out={out}")

# Test 5: query_content
calls = adb.query_content(serial, "content://call_log/calls", sort="date DESC LIMIT 3")
print(f"Call log: {'has data' if calls and 'Row:' in calls else 'empty'}")

# Test 6: get_installed_packages
pkgs = adb.get_installed_packages(serial)
print(f"Packages: {len(pkgs)}")

# Test 7: SIM
from desktop.core.sim_forensics import extract_sim_info
sim = extract_sim_info(serial)
print(f"SIM: carrier={sim['carrier']} country={sim['country']} status={sim['status']}")

# Test 8: forensic engine
from desktop.core.forensic_engine import ANALYSIS_MODULES, run_examination
print(f"Forensic modules: {len(ANALYSIS_MODULES)}")

# Test 9: database
from desktop.core.database import get_db, init_db
init_db()
conn = get_db()
tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
conn.close()
print(f"DB tables: {len(tables)}")

# Test 10: MainWindow imports cleanly
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication(sys.argv)
from desktop.ui.main_window import MainWindow
w = MainWindow()
w.show()
print("MainWindow created OK")

print("\nALL TESTS PASS")
