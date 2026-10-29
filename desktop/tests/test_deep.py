"""Deep test — all pages, all modules."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.chdir(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

print("=== DEEP INTEGRATION TEST ===")

from desktop.core.database import init_db, get_db
init_db()

from desktop.core import adb_manager as adb
from desktop.core import forensic_engine as engine

print(f"ADB path: {adb.ADB_PATH}")
devs = adb.list_devices()
print(f"Devices: {len(devs)}")

print(f"\nForensic modules: {len(engine.ANALYSIS_MODULES)}")
for k, (name, _) in engine.ANALYSIS_MODULES.items():
    print(f"  {k}: {name}")

from desktop.ui.pages.home_page import HomePage
from desktop.ui.pages.case_page import CasePage
from desktop.ui.pages.device_page import DevicePage
from desktop.ui.pages.identify_page import IdentifyPage
from desktop.ui.pages.acquire_page import AcquirePage
from desktop.ui.pages.examine_page import ExaminePage
from desktop.ui.pages.report_page import ReportPage
from desktop.ui.pages.browse_page import BrowsePage
from desktop.ui.pages.artifacts_page import ArtifactsPage
from desktop.ui.main_window import MainWindow
print("\nAll 9 pages import OK")
print("MainWindow imports OK")

from desktop.ui.widgets.stage_bar import StageBar
from desktop.ui.widgets.data_table import DataTable
from desktop.ui.widgets.log_console import LogConsole
from desktop.ui.widgets.device_status_bar import DeviceStatusBar
print("All widgets import OK")

from desktop.ui.theme import MAIN_STYLESHEET
print(f"Theme loaded ({len(MAIN_STYLESHEET)} chars)")

print("\n=== ALL TESTS PASSED ===")
