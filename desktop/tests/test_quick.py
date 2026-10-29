"""Quick smoke test after fixes."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.chdir(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from desktop.core.database import init_db, get_db
init_db()
conn = get_db()
tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print(f"DB tables: {len(tables)} tables")
conn.close()

from desktop.ui.pages.acquire_page import AcquirePage, AcquisitionWorker
from desktop.ui.pages.examine_page import ExaminePage, ExaminationWorker
from desktop.ui.pages.identify_page import IdentifyPage, IdentificationWorker
from desktop.ui.pages.device_page import DevicePage, DeviceScanWorker, DeviceInfoWorker
from desktop.ui.main_window import MainWindow
print("All imports OK")

from desktop.core.adb_manager import list_devices
devs = list_devices()
print(f"Devices connected: {len(devs)}")
if devs:
    print(f"  {devs[0]['model']} ({devs[0]['serial']})")
print("PASS")
