# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build specification for DroidForenX.

Produces a single self-contained executable with no installation step, so the
target machine needs neither Python nor the Android SDK.

    python build.py            # build for the current platform
    python build.py --clean    # discard previous build output first

The spec is deliberately platform-agnostic: on Windows it emits DroidForenX.exe,
on Linux/macOS it emits DroidForenX. Run it on the platform you want a binary
for -- a Windows exe cannot execute on Linux and vice versa.

ADB is bundled under adb_bundle/ inside the archive. adb_manager.find_adb()
checks sys._MEIPASS for exactly that layout, so the frozen app finds its own
adb before falling back to PATH.
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve()
IS_WINDOWS = sys.platform == "win32"
ADB_NAME = "adb.exe" if IS_WINDOWS else "adb"
ADB_SRC = ROOT / "desktop" / "adb_bundle" / ADB_NAME

# desktop.core / desktop.ui / desktop.report pull each other in at runtime, so
# let PyInstaller see the whole tree rather than trusting import analysis alone.
HIDDEN = (
    collect_submodules("desktop.core")
    + collect_submodules("desktop.report")
    + collect_submodules("desktop.ui")
)

# Qt ships an enormous amount of unused surface. Dropping the modules below
# roughly halves the output size. desktop.core uses the stdlib sqlite3 module,
# not QtSql, so that one is safe to exclude.
EXCLUDES = [
    "PyQt6.QtWebEngineCore", "PyQt6.QtWebEngineWidgets", "PyQt6.QtWebEngineQuick",
    "PyQt6.QtWebChannel", "PyQt6.QtWebSockets", "PyQt6.QtWebView",
    "PyQt6.QtQuick", "PyQt6.QtQuick3D", "PyQt6.QtQuickWidgets", "PyQt6.QtQml",
    "PyQt6.Qt3DCore", "PyQt6.Qt3DRender", "PyQt6.Qt3DInput", "PyQt6.Qt3DLogic",
    "PyQt6.Qt3DAnimation", "PyQt6.Qt3DExtras",
    "PyQt6.QtCharts", "PyQt6.QtDataVisualization", "PyQt6.QtGraphs",
    "PyQt6.QtMultimedia", "PyQt6.QtMultimediaWidgets",
    "PyQt6.QtBluetooth", "PyQt6.QtNfc", "PyQt6.QtPositioning", "PyQt6.QtLocation",
    "PyQt6.QtSensors", "PyQt6.QtSerialPort", "PyQt6.QtSerialBus",
    "PyQt6.QtDesigner", "PyQt6.QtUiTools", "PyQt6.QtHelp", "PyQt6.QtTest",
    "PyQt6.QtSql", "PyQt6.QtPdf", "PyQt6.QtPdfWidgets", "PyQt6.QtSpatialAudio",
    "PyQt6.QtTextToSpeech", "PyQt6.QtRemoteObjects", "PyQt6.QtScxml",
    "PyQt6.QtStateMachine", "PyQt6.QtHttpServer",
    "tkinter", "unittest", "pydoc_data", "test",
]

datas = []
if ADB_SRC.exists():
    # Lands at _MEIPASS/adb_bundle/<adb[.exe]>, which find_adb() probes.
    datas.append((str(ADB_SRC.parent), "adb_bundle"))
else:
    print(
        f"[spec] WARNING: {ADB_SRC} not found. The executable will rely on adb "
        "being on PATH at runtime. Run `python setup_adb.py` to bundle it."
    )

# The wordmark, the window icon and the assistant avatar. Kept at their
# repository path inside the archive so branding.asset_path() can find them with
# one rule for both a checkout and a frozen build. The .ico is consumed by
# PyInstaller itself rather than shipped.
BRANDING_SRC = ROOT / "desktop" / "assets" / "branding"
if BRANDING_SRC.exists():
    for asset in sorted(BRANDING_SRC.glob("*.*")):
        if asset.suffix.lower() == ".ico":
            continue
        datas.append((str(asset), "desktop/assets/branding"))
else:
    print(f"[spec] WARNING: {BRANDING_SRC} not found. The build will use text branding.")

ICON_FILE = BRANDING_SRC / "icon.ico"
ICON = str(ICON_FILE) if IS_WINDOWS and ICON_FILE.exists() else None

a = Analysis(
    [str(ROOT / "desktop" / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="DroidForenX",
    icon=ICON,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    # No console: this is a GUI tool. main.py already routes every exception
    # through sys.excepthook into ~/.droidforenx/droidforenx.log, so failures
    # stay diagnosable without a console window.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)