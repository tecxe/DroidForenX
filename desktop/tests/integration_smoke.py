"""DroidForenX Desktop — Integration Tests"""
import sys, os, inspect, traceback
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# QApplication must exist before any Qt widget
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication(sys.argv)

PASSED = 0
FAILED = 0
ERRORS = []

def test(name, fn):
    global PASSED, FAILED, ERRORS
    try:
        fn()
        PASSED += 1
        print(f"  PASS  {name}")
    except Exception as e:
        FAILED += 1
        ERRORS.append((name, str(e)))
        print(f"  FAIL  {name}: {e}")

print("=" * 60)
print("DroidForenX Desktop — Integration Tests")
print("=" * 60)

# --- Core imports ---
print("\n[CORE IMPORTS]")
def t_import_database():
    from desktop.core.database import init_db, get_db, audit_log
test("database module imports", t_import_database)

def t_import_adb():
    from desktop.core.adb_manager import list_devices, connect_device, get_device_info
    from desktop.core.adb_manager import DeviceInfo, get_installed_packages
test("adb_manager module imports", t_import_adb)

def t_import_forensic_engine():
    from desktop.core.forensic_engine import ANALYSIS_MODULES
    assert len(ANALYSIS_MODULES) >= 10, f"Expected 10+ modules, got {len(ANALYSIS_MODULES)}"
test(f"forensic_engine has {len([1])}+ modules", t_import_forensic_engine)

def t_import_sim():
    from desktop.core.sim_forensics import extract_sim_info
test("sim_forensics module imports", t_import_sim)

# --- UI imports ---
print("\n[UI IMPORTS]")
def t_import_theme():
    from desktop.ui.theme import MAIN_STYLESHEET
    assert len(MAIN_STYLESHEET) > 1000
test("theme stylesheet present", t_import_theme)

def t_import_widgets():
    from desktop.ui.widgets.stage_bar import StageBar
    from desktop.ui.widgets.data_table import DataTable
    from desktop.ui.widgets.log_console import LogConsole
test("all widgets import", t_import_widgets)

def t_import_pages():
    from desktop.ui.pages.home_page import HomePage
    from desktop.ui.pages.case_page import CasePage
    from desktop.ui.pages.device_page import DevicePage
    from desktop.ui.pages.identify_page import IdentifyPage
    from desktop.ui.pages.acquire_page import AcquirePage
    from desktop.ui.pages.examine_page import ExaminePage
    from desktop.ui.pages.report_page import ReportPage
test("all 7 pages import", t_import_pages)

def t_import_main_window():
    from desktop.ui.main_window import MainWindow
test("main_window imports", t_import_main_window)

# --- Bug fix: acquire_page uses self._serial not self.serial ---
print("\n[BUG FIXES]")
def t_acquire_has_serial():
    from desktop.ui.pages.acquire_page import AcquirePage
    src = inspect.getsource(AcquirePage._start_acquisition)
    assert "self.serial" not in src, "acquire_page still uses self.serial instead of self._serial"
    assert "self._serial" in src
test("acquire_page uses self._serial (not self.serial)", t_acquire_has_serial)

def t_identify_worker_emits_packages():
    from desktop.ui.pages.identify_page import IdentificationWorker
    src = inspect.getsource(IdentificationWorker.run)
    assert "get_installed_packages" in src
    assert "finished.emit(info, packages)" in src
test("identify worker emits (info, packages) tuple", t_identify_worker_emits_packages)

def t_main_window_menu_handlers():
    from desktop.ui.main_window import MainWindow
    src = inspect.getsource(MainWindow._setup_menu)
    assert "open_case.triggered.connect" in src, "Open Case menu not connected"
    assert "export_action.triggered.connect" in src, "Export Report menu not connected"
test("menu actions have connected handlers", t_main_window_menu_handlers)

# --- Forensic modules ---
print("\n[FORENSIC ENGINE]")
def t_module_count():
    from desktop.core.forensic_engine import ANALYSIS_MODULES
    assert len(ANALYSIS_MODULES) >= 11, f"Expected 11+ modules, got {len(ANALYSIS_MODULES)}"
test(f"forensic engine has 11+ modules", t_module_count)

def t_module_names():
    from desktop.core.forensic_engine import ANALYSIS_MODULES
    for required in ["call_log", "sms_mms", "contacts", "browser", "gps", "deleted"]:
        assert required in ANALYSIS_MODULES, f"Missing module: {required}"
test("forensic engine has key modules", t_module_names)

# --- ADB ---
print("\n[ADB MANAGER]")
def t_adb_device_class():
    from desktop.core.adb_manager import DeviceInfo
    d = DeviceInfo(serial="ABC", manufacturer="Test", model="X", device_name="X",
                   android_version="15", api_level=35, security_patch="2025-01-01",
                   build_fingerprint="test/test/abc:15/ABC/123:userdebug/test-keys",
                   kernel_version="5.15.0", encryption_status="Encrypted",
                   bootloader_state="Unlocked", sim_state="Ready",
                   battery_level=85, battery_temp=30.5, total_storage=128*1024**3,
                   free_storage=50*1024**3)
    assert d.serial == "ABC"
    assert d.battery_level == 85
test("DeviceInfo dataclass works", t_adb_device_class)

# --- Stage bar ---
print("\n[WIDGETS]")
def t_stage_bar_stages():
    from desktop.ui.widgets.stage_bar import StageBar
    src = inspect.getsource(StageBar)
    assert "Seizure" not in src
    assert "Identification" in src
    assert "Acquisition" in src
    assert "Examination" in src
    assert "Reporting" in src
test("stage bar has 4 forensic stages", t_stage_bar_stages)

def t_data_table():
    from desktop.ui.widgets.data_table import DataTable
    t = DataTable(["Col1", "Col2"])
    t.load_data([["a", "1"], ["b", "2"]])
    assert t.rowCount() == 2
test("data_table load_data works", t_data_table)

# --- Report ---
print("\n[REPORT]")
def t_report_gen():
    from desktop.ui.pages.report_page import ReportPage
    src = inspect.getsource(ReportPage)
    assert "sha256" in src.lower()
    assert "generate_report" in src
test("report_page has SHA-256 and generate_report", t_report_gen)

# --- main.py ---
print("\n[ENTRY POINT]")
def t_main_exists():
    assert os.path.exists(os.path.join(os.path.dirname(__file__), "..", "main.py"))
test("main.py exists", t_main_exists)

# --- Summary ---
print("\n" + "=" * 60)
print(f"RESULTS: {PASSED} passed, {FAILED} failed out of {PASSED + FAILED}")
if ERRORS:
    print("\nFailed tests:")
    for name, err in ERRORS:
        print(f"  - {name}: {err}")
print("=" * 60)

sys.exit(1 if FAILED else 0)
