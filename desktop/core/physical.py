"""Physical acquisition on a handset that has not been rooted.

What "physical" means here
--------------------------

A physical image is the raw contents of the flash, taken below the filesystem.
On Android that cannot be done through the normal shell: ``/dev/block`` is
denied to the ``adb`` user, which is why the rooted loader in ``acquire_page``
could only ever image ``boot``. Getting the flash without root means talking to
the *chip*, not the OS, and there are exactly two open routes:

* **MediaTek BootROM / Preloader.** MediaTek exposes a USB boot ROM that
  accepts a download agent before Android starts. ``mtkclient`` drives it and
  can read every partition (``rl``) without root and without unlocking the
  bootloader. Transsion handsets (TECNO, Infinix, itel) are mostly MediaTek.
* **Qualcomm EDL (9008).** The Emergency Download mode accepts a signed
  firehose programmer; ``edl.py`` speaks that protocol. Secure-boot devices
  need a programmer for the exact chip, which is why a loader file is required.

This module does not re-implement either exploit chain. It detects what the
handset is, which of those modes it is currently in, whether the engine for
that mode is installed, builds the exact invocation, runs it with progress and
cancellation, and then validates and partitions the image that comes back.

The honest ceiling
------------------

Modern Android encrypts ``userdata`` with File-Based Encryption. A physical
read of an encrypted partition yields ciphertext; neither route above derives
the key from a locked screen without an exploit this project does not ship.
What the image is still good for: the partition layout, boot and vendor
partitions, metadata, timestamps, unencrypted volumes, and an integrity-bound
copy of the ciphertext for a laboratory with the key material. The report says
which partitions are encrypted, from the device's own ``ro.crypto.*``
properties, instead of implying the content is readable.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
try:
    import ctypes
except ImportError:  # pragma: no cover - never on the platforms we ship
    ctypes = None
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Iterable, Optional

logger = logging.getLogger(__name__)

#: Chipset families this module can name.
SOC_FAMILIES = {
    "mediatek": "MediaTek",
    "qualcomm": "Qualcomm",
    "exynos": "Samsung Exynos",
    "tensor": "Google Tensor",
    "unisoc": "Unisoc",
    "kirin": "HiSilicon Kirin",
    "unknown": "Unknown",
}

#: USB vendor/product pairs that mean "this is a boot-mode, not Android".
USB_MODES = {
    ("0e8d", "0003"): ("mtk_brom", "MediaTek BootROM"),
    ("0e8d", "2000"): ("mtk_preloader", "MediaTek Preloader"),
    ("0e8d", "2001"): ("mtk_da", "MediaTek Download Agent"),
    ("05c6", "9008"): ("qualcomm_edl", "Qualcomm EDL (9008)"),
    ("18d1", "4ee0"): ("fastboot", "Fastboot"),
    ("18d1", "4ee2"): ("fastboot", "Fastboot"),
    ("18d1", "4ee7"): ("fastboot", "Fastboot"),
    ("04e8", "685d"): ("samsung_download", "Samsung Download mode"),
}

#: Modes that can be driven to a full image by an open engine.
IMAGING_MODES = ("mtk_brom", "mtk_preloader", "mtk_da", "qualcomm_edl")

#: Told to the operator when a boot-mode device is on the bus but no driver is
#: bound to it. Windows lets the device be enumerated and then refuses every
#: read, which looks identical to a dead handset until it is named.
DRIVER_HELP = (
    "Windows sees {label} ({vid}:{pid}) but has bound no driver to it, so "
    "every USB read is refused before the handshake. Bind WinUSB once, with "
    "the device plugged in: Zadig -> Options -> List All Devices -> {label} "
    "-> WinUSB -> Replace Driver. The binding is permanent for this model."
)

#: Filesystem magics checked at fixed offsets inside a partition.
_FS_MAGIC = (
    ("ext2/3/4", 0x438, b"\x53\xef"),
    ("f2fs", 0x400, b"\x10\x20\xf5\xf2"),
    ("erofs", 0x400, b"\xe2\xe1\xf5\xe0"),
    ("ntfs", 0x03, b"NTFS    "),
    ("fat32", 0x52, b"FAT32"),
    ("fat16", 0x36, b"FAT16"),
    ("fat12", 0x36, b"FAT12"),
)

SPARSE_MAGIC = 0xED26FF3A
GPT_SIGNATURE = b"EFI PART"


# ---------------------------------------------------------------------------
# chipset and boot mode
# ---------------------------------------------------------------------------

def normalise_soc(props: dict) -> dict:
    """Name the chipset from Android properties.

    Transsion firmware is not consistent about which property carries the SoC:
    ``ro.hardware`` may be ``mt6768``, ``ro.board.platform`` may be ``mt6768``,
    and ``ro.soc.model`` may be absent entirely. Every field is checked and the
    first one that matches wins, with the evidence kept so a report can say why.
    """
    props = {str(k): str(v or "") for k, v in (props or {}).items()}
    joined = " ".join(
        props.get(key, "") for key in (
            "ro.soc.manufacturer", "ro.soc.model", "ro.board.platform",
            "ro.hardware", "ro.product.board", "ro.boot.hardware",
            "ro.product.cpu.abi", "ro.chipname", "ro.mediatek.platform",
        )).lower()
    manufacturer = props.get("ro.soc.manufacturer", "").lower()

    family = "unknown"
    model = props.get("ro.soc.model") or props.get("ro.board.platform") or props.get("ro.hardware")

    if "mediatek" in manufacturer or re.search(r"\bmt\d{4}\b", joined) or "mediatek" in joined:
        family = "mediatek"
    elif "qualcomm" in manufacturer or re.search(
            r"\b(msm|sdm|sm|qcm|qcs|apq)\d{3,5}\b", joined) or "qcom" in joined:
        family = "qualcomm"
    elif "exynos" in joined or "samsung" in manufacturer:
        family = "exynos"
    elif "tensor" in joined or re.search(r"\bgs\d{3}\b", joined):
        family = "tensor"
    elif "unisoc" in joined or "spreadtrum" in joined or re.search(r"\b(sc|ums)\d{4}\b", joined):
        family = "unisoc"
    elif "kirin" in joined or "hisilicon" in joined:
        family = "kirin"

    return {
        "family": family,
        "label": SOC_FAMILIES.get(family, SOC_FAMILIES["unknown"]),
        "model": model,
        "manufacturer": props.get("ro.soc.manufacturer", "")
                        or props.get("ro.product.manufacturer", ""),
        "evidence": {k: props.get(k, "") for k in (
            "ro.soc.manufacturer", "ro.soc.model", "ro.board.platform",
            "ro.hardware", "ro.product.board", "ro.boot.hardware") if props.get(k)},
    }


def encryption_state(props: dict) -> dict:
    """Read the device's own encryption claims.

    ``ro.crypto.state`` and ``ro.crypto.type`` are set by the bootloader and
    init, so they are the device's statement about itself. ``file`` means
    File-Based Encryption: ``userdata`` is ciphertext at rest.
    """
    props = {str(k): str(v or "") for k, v in (props or {}).items()}
    state = props.get("ro.crypto.state", "").lower()
    kind = props.get("ro.crypto.type", "").lower()
    encrypted = state == "encrypted"
    if kind == "file":
        label = "File-Based Encryption (FBE) - userdata is ciphertext at rest"
    elif kind == "block":
        label = "Full-Disk Encryption (FDE) - userdata is ciphertext at rest"
    elif encrypted:
        label = "Encrypted (mode not stated)"
    else:
        label = "Not encrypted (or not stated)"
    return {
        "encrypted": encrypted,
        "type": kind or ("file" if encrypted else ""),
        "label": label,
        "verified_boot": props.get("ro.boot.verifiedbootstate", ""),
        "bootloader_locked": props.get("ro.boot.flash.locked", ""),
    }


def parse_usb_listing(text: str, source: str = "") -> list[dict]:
    """Parse a USB inventory from ``lsusb``, PnP or ``system_profiler`` output.

    Three formats, one result shape. Only IDs that appear in :data:`USB_MODES`
    are returned; everything else is another peripheral.
    """
    found = []
    seen = set()

    def add(vid, pid, name=""):
        pair = (str(vid).lower().zfill(4), str(pid).lower().zfill(4))
        if pair in USB_MODES and pair not in seen:
            seen.add(pair)
            kind, label = USB_MODES[pair]
            found.append({
                "vid": pair[0], "pid": pair[1], "mode": kind,
                "label": label, "device": name, "source": source,
            })

    text = text or ""
    # lsusb:  Bus 001 Device 003: ID 0e8d:0003 MediaTek Inc. MT6227
    for match in re.finditer(
            r"ID\s+([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\s*(.*)", text):
        add(match.group(1), match.group(2), match.group(3).strip())
    # Windows PnP: USB\VID_0E8D&PID_0003\...
    for match in re.finditer(
            r"VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})", text):
        add(match.group(1), match.group(2))
    # system_profiler: "idVendor" = 0x0e8d / "idProduct" = 0x0003
    for match in re.finditer(
            r'"idVendor"\s*=\s*0x([0-9a-fA-F]{4}).*?"idProduct"\s*=\s*0x([0-9a-fA-F]{4})',
            text, re.S):
        add(match.group(1), match.group(2))
    return found


def _run(argv: list, timeout: int = 20) -> str:
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return (result.stdout or "") + (result.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return ""


def detect_usb_modes() -> list[dict]:
    """Ask the host which boot-mode USB devices are present right now.

    On Windows the enumeration is done in-process through SetupAPI. Spawning
    PowerShell costs up to a second and a half per call, and a preloader is on
    the bus for only two or three seconds, so the subprocess route silently
    misses the window it is supposed to catch.
    """
    modes = []
    if sys.platform.startswith("win"):
        try:
            modes = _windows_usb_probe()
        except Exception:
            # Only a broken probe falls back; an empty result is a real answer
            # ("no boot-mode device") and must not cost a subprocess.
            logger.debug("SetupAPI probe unavailable", exc_info=True)
            modes = parse_pnp_binding(_run_pnp_inventory())
    elif sys.platform == "darwin":
        modes = parse_usb_listing(
            _run(["system_profiler", "SPUSBDataType"]), "system_profiler")
    else:
        modes = parse_usb_listing(_run(["lsusb"]), "lsusb")
    # Deduplicate by mode while keeping the first device name.
    by_mode = {}
    for entry in modes:
        by_mode.setdefault(entry["mode"], entry)
    return list(by_mode.values())


# --- Windows: SetupAPI enumeration -----------------------------------------
#
# DIGCF_PRESENT | DIGCF_ALLCLASSES over the "USB" enumerator. Each node yields
# its instance ID, the friendly name, the function driver it is bound to, and
# the Config Manager problem number, which is all the plan needs to know.

_DIGCF_PRESENT = 0x02
_DIGCF_ALLCLASSES = 0x04
_SPDRP_DEVICEDESC = 0x00
_SPDRP_SERVICE = 0x04
_SPDRP_FRIENDLYNAME = 0x0C
_KEY_READ = 0x20019


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


class _SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("ClassGuid", _GUID),
                ("DevInst", ctypes.c_ulong),
                ("Reserved", ctypes.c_void_p)]


def _setupapi():
    """Load and sign the two libraries once. Returns (setupapi, cfgmgr32)."""
    global _SETUPAPI
    if _SETUPAPI is None:
        setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
        cfgmgr = ctypes.WinDLL("cfgmgr32", use_last_error=True)
        setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
        setupapi.SetupDiGetClassDevsW.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_ulong]
        setupapi.SetupDiEnumDeviceInfo.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_SP_DEVINFO_DATA)]
        setupapi.SetupDiGetDeviceInstanceIdW.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(_SP_DEVINFO_DATA),
            ctypes.c_wchar_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
        setupapi.SetupDiGetDeviceRegistryPropertyW.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(_SP_DEVINFO_DATA), ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p, ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong)]
        setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
        cfgmgr.CM_Get_DevNode_Status.argtypes = [
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
            ctypes.c_ulong, ctypes.c_ulong]
        _SETUPAPI = (setupapi, cfgmgr)
    return _SETUPAPI


_SETUPAPI = None


def _devnode_string(setupapi, info, prop: int) -> str:
    buffer = ctypes.create_unicode_buffer(1024)
    required = ctypes.c_ulong(0)
    ok = setupapi.SetupDiGetDeviceRegistryPropertyW(
        None, ctypes.byref(info), prop, None, buffer,
        ctypes.sizeof(buffer), ctypes.byref(required))
    if not ok:
        return ""
    return buffer.value.strip()


def _windows_usb_probe() -> list[dict]:
    """Present USB nodes with the driver Windows bound to each."""
    setupapi, cfgmgr = _setupapi()
    handle = setupapi.SetupDiGetClassDevsW(
        None, "USB", None, _DIGCF_PRESENT | _DIGCF_ALLCLASSES)
    if not handle or handle == ctypes.c_void_p(-1).value:
        return []
    found = []
    seen = set()
    try:
        index = 0
        while True:
            info = _SP_DEVINFO_DATA()
            info.cbSize = ctypes.sizeof(_SP_DEVINFO_DATA)
            if not setupapi.SetupDiEnumDeviceInfo(handle, index, ctypes.byref(info)):
                break
            index += 1
            name_buffer = ctypes.create_unicode_buffer(512)
            required = ctypes.c_ulong(0)
            if not setupapi.SetupDiGetDeviceInstanceIdW(
                    handle, ctypes.byref(info), name_buffer,
                    ctypes.sizeof(name_buffer), ctypes.byref(required)):
                continue
            match = re.search(r"VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})",
                              name_buffer.value)
            if not match:
                continue
            pair = (match.group(1).lower(), match.group(2).lower())
            if pair not in USB_MODES or pair in seen:
                continue
            seen.add(pair)
            status_bits = ctypes.c_ulong(0)
            problem = ctypes.c_ulong(0)
            cfgmgr.CM_Get_DevNode_Status(
                ctypes.byref(status_bits), ctypes.byref(problem), info.DevInst, 0)
            service = _devnode_string(setupapi, info, _SPDRP_SERVICE).lower()
            name = (_devnode_string(setupapi, info, _SPDRP_FRIENDLYNAME)
                    or _devnode_string(setupapi, info, _SPDRP_DEVICEDESC))
            kind, label = USB_MODES[pair]
            found.append({
                "vid": pair[0], "pid": pair[1], "mode": kind, "label": label,
                "device": name, "source": "Windows SetupAPI",
                "driver": service,
                "status": "Error" if problem.value else "OK",
                "problem": problem.value != 0,
                "bound": bool(service) and problem.value == 0,
            })
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(
            ctypes.c_void_p(handle) if not isinstance(handle, int) else handle)
    return found


def _run_pnp_inventory() -> str:
    """One PowerShell pass: ID, name, driver service, status, problem code."""
    script = (
        "Get-CimInstance Win32_PnPEntity | "
        "Where-Object { $_.PNPDeviceID -like 'USB*' } | "
        "ForEach-Object { $_.PNPDeviceID + '|' + $_.Name + '|' + "
        "$_.Service + '|' + $_.Status + '|' + $_.ConfigManagerErrorCode }")
    return _run(["powershell", "-NoProfile", "-Command", script])


def parse_pnp_binding(text: str) -> list[dict]:
    """Parse the Windows inventory into boot-mode entries with their driver.

    ``Service`` is the name of the function driver the device node is bound to
    and ``ConfigManagerErrorCode`` is 28 when Windows has no driver at all.
    Both matter: a node with neither cannot be read from user mode, whatever
    the engine does.
    """
    found = []
    seen = set()
    for line in (text or "").splitlines():
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 5:
            continue
        pnp, name, service, status, code = parts[:5]
        match = re.search(r"VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})", pnp)
        if not match:
            continue
        pair = (match.group(1).lower().zfill(4), match.group(2).lower().zfill(4))
        if pair in seen or pair not in USB_MODES:
            continue
        seen.add(pair)
        kind, label = USB_MODES[pair]
        found.append({
            "vid": pair[0], "pid": pair[1], "mode": kind, "label": label,
            "device": name, "source": "Windows PnP",
            "driver": service.lower(), "status": status,
            "problem": code not in ("", "0"),
            "bound": bool(service) and code in ("", "0"),
        })
    return found


def unbound_modes(transports: list) -> list:
    """Boot-mode devices present on the bus whose node has no driver."""
    return [t for t in (transports or [])
            if t.get("mode") in IMAGING_MODES and t.get("bound") is False]


def driver_message(entry: dict) -> str:
    """The actionable sentence for one unbounded boot-mode device.

    The name matters: Zadig lists the node by the name Windows gave it, which
    is ``MT65xx Preloader``, not the nicer label this module uses.
    """
    return DRIVER_HELP.format(
        label=entry.get("device") or entry.get("label")
        or "the boot-mode device",
        vid=entry.get("vid", ""), pid=entry.get("pid", ""))


# ---------------------------------------------------------------------------
# acquisition engines
# ---------------------------------------------------------------------------

#: Environment overrides for engines installed outside PATH.
TOOL_ENV = {
    "mtkclient": ("DROIDFORENX_MTKCLIENT", "MTKCLIENT"),
    "edl": ("DROIDFORENX_EDL", "EDL"),
    "python": ("DROIDFORENX_PYTHON",),
}

#: Names each engine may be installed under.
TOOL_NAMES = {
    "mtkclient": ("mtk", "mtkclient", "mtk.py"),
    "edl": ("edl", "edl.py"),
    "heimdall": ("heimdall",),
}

#: A frozen build has no interpreter of its own, so the engine path and the
#: Python that can import pyusb are remembered in one small file. The installer
#: (or the operator, once) writes it; every later launch finds the engine with
#: no environment setup at all.
CONFIG_PATH = Path.home() / ".droidforenx" / "tools.json"


def read_tool_config() -> dict:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_tool_config(values: dict) -> bool:
    """Merge ``values`` into the remembered tools. Never raises."""
    merged = read_tool_config()
    merged.update({k: str(v) for k, v in values.items() if v})
    if not merged:
        return False
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(merged, indent=2), encoding="utf-8")
        return True
    except OSError:
        logger.warning("cannot write %s", CONFIG_PATH, exc_info=True)
        return False


def _search_dirs() -> list:
    """Folders that may hold an engine, most specific first."""
    dirs = []
    if getattr(sys, "frozen", False):
        dirs.append(Path(sys.executable).resolve().parent)
    dirs.append(Path(__file__).resolve().parents[2])
    dirs.append(Path.home() / ".droidforenx" / "tools")
    dirs.append(Path.home() / "droidforenx-tools")
    return dirs


def _resolve_tool(names: Iterable[str], env_keys: Iterable[str] = (),
                  config_key: str = "") -> str:
    config = read_tool_config()
    candidates = list(env_keys) + ([config_key] if config_key else [])
    for key in candidates:
        value = (os.environ.get(key) or config.get(key) or "").strip()
        if value and Path(value).exists():
            return value
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    for folder in _search_dirs():
        for parent in (folder, folder / "mtkclient", folder / "edl"):
            for name in names:
                candidate = parent / name
                if candidate.is_file():
                    return str(candidate)
    return ""


def find_python() -> str:
    """The interpreter that can import pyusb, for a ``.py`` engine.

    When running from a checkout ``sys.executable`` is already that
    interpreter. A frozen executable is not, so the configured path, the
    environment and PATH are checked instead.
    """
    config = read_tool_config()
    for value in (os.environ.get("DROIDFORENX_PYTHON", ""), config.get("python", "")):
        value = (value or "").strip()
        if value and Path(value).exists():
            return value
    if not getattr(sys, "frozen", False):
        return sys.executable
    for name in ("python", "python3", "py"):
        path = shutil.which(name)
        if path:
            return path
    return ""


def find_zadig() -> str:
    """Locate Zadig, the signed-driver installer for a raw USB endpoint."""
    for name in ("zadig", "zadig.exe", "Zadig.exe"):
        path = shutil.which(name)
        if path:
            return path
    for folder in _search_dirs():
        for name in ("zadig.exe", "Zadig.exe", "zadig-2.9.exe"):
            candidate = folder / name
            if candidate.is_file():
                return str(candidate)
    return ""


def find_tools() -> dict:
    """Locate the open acquisition engines. Nothing is installed by us."""
    mtk = _resolve_tool(TOOL_NAMES["mtkclient"], TOOL_ENV["mtkclient"], "mtkclient")
    edl = _resolve_tool(TOOL_NAMES["edl"], TOOL_ENV["edl"], "edl")
    mtk_module = ""
    if not mtk:
        try:
            import importlib.util
            if importlib.util.find_spec("mtkclient") is not None:
                mtk_module = "mtkclient"
        except (ImportError, ValueError):
            pass
    python = find_python()
    # Remember what was found so the next launch is a single click even if the
    # operator never opens a terminal.
    write_tool_config({"mtkclient": mtk, "edl": edl, "python": python})
    return {
        "mtkclient": mtk,
        "mtkclient_module": mtk_module,
        "edl": edl,
        "heimdall": _resolve_tool(TOOL_NAMES["heimdall"]),
        "python": python,
    }


def _script_argv(tool: str, *args: str, python: str = "") -> list:
    """Run a ``.py`` engine with an interpreter, an exe directly."""
    if tool.lower().endswith(".py"):
        return [python or sys.executable, tool, *args]
    return [tool, *args]


def build_mtk_argv(tool: str, outdir, *, module: str = "", python: str = "") -> list:
    """``mtk rl <dir>`` reads every partition into ``<dir>``.

    ``rl`` is mtkclient's "read all partitions to a folder" verb. Each
    partition lands as its own file, which is both faster to triage and easier
    to hash separately than one multi-gigabyte blob.
    """
    if module:
        return [python or sys.executable, "-m", module, "rl", str(outdir)]
    return _script_argv(tool, "rl", str(outdir), python=python)


def build_edl_argv(tool: str, outdir, loader: str = "", python: str = "") -> list:
    """``edl rl <dir>`` with an optional firehose loader.

    A Qualcomm device in EDL needs a programmer that matches its chip. Without
    one the command still runs and ``edl`` reports the missing loader; the
    caller surfaces that rather than pretending the image failed for no reason.
    """
    args = ["rl", str(outdir)]
    if loader:
        args += ["--loader", loader]
    return _script_argv(tool, *args, python=python)


@dataclass
class Plan:
    """One viable acquisition route, or the reason none is viable."""

    route: str
    viable: bool
    reason: str
    argv: list = field(default_factory=list)
    note: str = ""
    #: Directory the child must have on PATH to load libusb on Windows. Only a
    #: ``.py`` engine needs it: the DLL sits beside the interpreter.
    env_path: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    def __post_init__(self):
        # A .py engine is launched as [python, script, ...]. The libusb DLL is
        # installed beside that interpreter, so its directory has to be on the
        # child's PATH or pyusb reports "no backend available" before the
        # engine ever reaches the handset.
        if self.env_path or len(self.argv) < 2:
            return
        if str(self.argv[1]).lower().endswith(".py"):
            self.env_path = str(Path(self.argv[0]).resolve().parent)


def build_plan(soc: dict, transports: list[dict], tools: dict,
               loader: str = "") -> Plan:
    """Choose the acquisition route this device can actually take."""
    modes = {t["mode"] for t in transports}
    family = (soc or {}).get("family", "unknown")

    # A device on the bus with no driver bound is a Windows fault, not a
    # handset fault. Running the engine against it produces nothing but
    # "Handshake failed" for minutes, so name the real cause and stop.
    blocked = unbound_modes(transports)
    if blocked:
        return Plan(
            route="driver",
            viable=False,
            reason=driver_message(blocked[0]),
            note=("The chip is reachable; the host has no function driver on "
                  "the port, so no engine can read it yet."))

    if modes & {"mtk_brom", "mtk_preloader", "mtk_da"}:
        if tools.get("mtkclient") or tools.get("mtkclient_module"):
            tool = tools.get("mtkclient", "")
            if (tool.lower().endswith(".py") and getattr(sys, "frozen", False)
                    and not tools.get("python")):
                return Plan(
                    route="mtkclient",
                    viable=False,
                    reason=("mtkclient was found, but this build has no Python "
                            "to run it with. Set DROIDFORENX_PYTHON to a Python "
                            "that can import pyusb."),
                    note="The handset and the engine are both available.")
            return Plan(
                route="mtkclient",
                viable=True,
                reason="MediaTek boot mode detected and mtkclient is installed.",
                argv=build_mtk_argv(
                    tool, "<evidence>/physical",
                    module=tools.get("mtkclient_module", ""),
                    python=tools.get("python", "")),
                note="Reads every partition over the BootROM/Preloader; no root.")
        return Plan(
            route="mtkclient",
            viable=False,
            reason=("MediaTek boot mode detected, but mtkclient is not "
                    "installed. Install it with 'pip install mtkclient' or set "
                    "DROIDFORENX_MTKCLIENT to its path."),
            note="The handset is reachable; only the engine is missing.")

    if "qualcomm_edl" in modes:
        if not tools.get("edl"):
            return Plan(
                route="edl",
                viable=False,
                reason=("Qualcomm EDL (9008) detected, but edl.py is not "
                        "installed. Install it from bkerler/edl and set "
                        "DROIDFORENX_EDL to its path."))
        if not loader:
            return Plan(
                route="edl",
                viable=False,
                reason=("Qualcomm EDL detected, but no firehose loader was "
                        "given. Secure-boot chips need the programmer that "
                        "matches the SoC; supply it as a .mbn/.elf file."))
        return Plan(
            route="edl",
            viable=True,
            reason="Qualcomm EDL detected, edl.py and a loader are present.",
            argv=build_edl_argv(tools["edl"], "<evidence>/physical", loader,
                                python=tools.get("python", "")),
            note="Reads every partition over the firehose protocol.")

    if family == "mediatek":
        tool = tools.get("mtkclient", "")
        if tool or tools.get("mtkclient_module"):
            # mtkclient waits for the port, so the correct order is: start the
            # engine, then plug the powered-off handset in. Requiring the
            # boot-mode device to already exist would make the click
            # impossible: the preloader is only present for a few seconds.
            return Plan(
                route="mtkclient",
                viable=True,
                reason=("Ready. Click Acquire, then power the handset off and "
                        "hold Volume Up + Volume Down while plugging USB in. "
                        "mtkclient waits for the boot ROM."),
                argv=build_mtk_argv(
                    tool, "<evidence>/physical",
                    module=tools.get("mtkclient_module", ""),
                    python=tools.get("python", "")),
                note="The engine must be running before the handset is plugged in.")
        return Plan(
            route="mediatek",
            viable=False,
            reason=("This is a MediaTek handset but it is not in BootROM/"
                    "Preloader mode. Power it off, then hold Volume Up and "
                    "Volume Down while connecting USB (or short the test "
                    "points) to enter BROM."),
            note="ADB cannot see /dev/block without root; the chip can.")
    if family == "qualcomm":
        if tools.get("edl") and loader:
            return Plan(
                route="edl",
                viable=True,
                reason=("Ready. Click Acquire, then power the handset off and "
                        "connect USB while holding the EDL key combination "
                        "(often Volume Up + Volume Down). edl.py waits for 9008."),
                argv=build_edl_argv(tools["edl"], "<evidence>/physical", loader,
                                    python=tools.get("python", "")),
                note="The engine must be running before the handset is plugged in.")
        return Plan(
            route="qualcomm",
            viable=False,
            reason=("This is a Qualcomm handset but it is not in EDL. Power it "
                    "off and connect USB while holding the EDL key combination "
                    "for this model (often Volume Up + Volume Down)."))
    return Plan(
        route="logical",
        viable=False,
        reason=("No boot-mode transport was found. A physical image needs the "
                "chip's boot ROM or EDL mode; over ADB the block devices are "
                "denied to the shell user. Logical acquisition still runs."),
        note="Attach the powered-off handset in BROM/EDL to image the flash.")


class _LineReader:
    """Run a subprocess and hand each output line to a callback."""

    def __init__(self, argv: list, on_line: Callable[[str], None],
                 env_path: str = ""):
        self.argv = list(argv)
        self.on_line = on_line
        self.env_path = env_path
        self._process: Optional[subprocess.Popen] = None
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        process = self._process
        if process and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    def run(self, timeout: Optional[int] = None) -> dict:
        env = dict(os.environ)
        if self.env_path:
            env["PATH"] = self.env_path + os.pathsep + env.get("PATH", "")
        try:
            self._process = subprocess.Popen(
                self.argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as exc:
            return {"ok": False, "message": str(exc), "argv": self.argv}
        output = []
        try:
            for line in iter(self._process.stdout.readline, ""):
                if self._cancelled:
                    break
                line = line.rstrip()
                if line:
                    output.append(line)
                    try:
                        self.on_line(line)
                    except Exception:  # a UI callback must not kill the run
                        logger.debug("progress callback failed", exc_info=True)
            self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.cancel()
            return {"ok": False, "message": "engine timed out", "output": output[-40:]}
        finally:
            if self._process.stdout is not None:
                self._process.stdout.close()
        code = self._process.returncode
        return {
            "ok": (code == 0 and not self._cancelled),
            "cancelled": self._cancelled,
            "returncode": code,
            "output": output[-200:],
            "argv": self.argv,
        }


def run_acquisition(plan: Plan, on_line: Callable[[str], None] = None,
                    on_reader: Callable[["_LineReader"], None] = None) -> dict:
    """Execute a viable plan, streaming its output to ``on_line``.

    ``on_reader`` hands the caller the running reader so a UI can cancel a
    multi-gigabyte read without killing the application.
    """
    if not plan.viable or not plan.argv:
        return {"ok": False, "message": plan.reason, "argv": plan.argv}
    holder = acquire_engine_lock()
    if holder is not None:
        return {
            "ok": False,
            "message": (
                "Another acquisition engine is already running (pid "
                f"{holder}). Only one process may drive the boot-mode port: a "
                "second one makes both fail their handshake. Stop it, or wait "
                "for it to finish."),
            "argv": plan.argv,
        }
    reader = _LineReader(plan.argv, on_line or (lambda _line: None),
                         env_path=getattr(plan, "env_path", ""))
    if on_reader is not None:
        try:
            on_reader(reader)
        except Exception:
            logger.debug("on_reader callback failed", exc_info=True)
    try:
        return reader.run()
    finally:
        release_engine_lock()


#: One boot-mode port, one engine. Two processes on the same endpoint make
#: both fail the handshake, which reads like a broken handset.
ENGINE_LOCK = Path.home() / ".droidforenx" / "engine.lock"


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform.startswith("win"):
        out = _run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], timeout=10)
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def acquire_engine_lock() -> Optional[int]:
    """Take the engine lock. Returns the holding pid if it is already taken."""
    try:
        ENGINE_LOCK.parent.mkdir(parents=True, exist_ok=True)
        if ENGINE_LOCK.exists():
            try:
                holder = int(ENGINE_LOCK.read_text(encoding="utf-8").strip() or 0)
            except (OSError, ValueError):
                holder = 0
            if holder and _process_alive(holder):
                return holder
            # A stale lock from a crash must not block every future run.
            ENGINE_LOCK.unlink(missing_ok=True)
        ENGINE_LOCK.write_text(str(os.getpid()), encoding="utf-8")
        return None
    except OSError:
        logger.warning("cannot take the engine lock", exc_info=True)
        return None


def release_engine_lock():
    try:
        ENGINE_LOCK.unlink(missing_ok=True)
    except OSError:
        logger.debug("cannot release the engine lock", exc_info=True)


# ---------------------------------------------------------------------------
# image validation and partitioning
# ---------------------------------------------------------------------------

@dataclass
class PartitionEntry:
    index: int
    name: str
    type_guid: str
    unique_guid: str
    first_lba: int
    last_lba: int
    offset_bytes: int
    size_bytes: int
    filesystem: str = ""
    encrypted: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class ImageReport:
    path: str
    size_bytes: int
    sha256: str = ""
    container: str = "raw"
    table: str = "none"
    sector_size: int = 512
    partitions: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["partitions"] = [p.as_dict() if hasattr(p, "as_dict") else p
                                 for p in self.partitions]
        return payload

    def summary(self) -> str:
        if self.partitions:
            return (f"{len(self.partitions)} partitions, "
                    f"{self.table.upper()} table, {self.size_bytes:,} bytes")
        return f"{self.size_bytes:,} bytes, no partition table found"


def _guid(raw: bytes) -> str:
    # GPT stores the first three fields little-endian.
    if len(raw) != 16:
        return ""
    a, b, c = struct.unpack_from("<IHH", raw, 0)
    rest = raw[8:16]
    return (f"{a:08x}-{b:04x}-{c:04x}-"
            f"{rest[0]:02x}{rest[1]:02x}-"
            + "".join(f"{byte:02x}" for byte in rest[2:]))


def _detect_filesystem(read_at: Callable[[int, int], bytes], offset: int,
                       size: int) -> str:
    for label, relative, magic in _FS_MAGIC:
        if relative + len(magic) > size:
            continue
        data = read_at(offset + relative, len(magic))
        if data == magic:
            return label
    return ""


def parse_gpt(handle, size: int, read_at: Callable[[int, int], bytes],
              sector: int = 512) -> list[PartitionEntry]:
    """Parse a GPT into partitions, with offsets and filesystems resolved."""
    header = read_at(sector, 92)
    if not header.startswith(GPT_SIGNATURE):
        return []
    entry_lba = struct.unpack_from("<Q", header, 72)[0]
    count = struct.unpack_from("<I", header, 80)[0]
    entry_size = struct.unpack_from("<I", header, 84)[0]
    if not (entry_lba and 0 < count <= 512 and 128 <= entry_size <= 1024):
        return []
    entries = []
    # Read in blocks instead of one round trip per entry; a phone GPT has 20-70.
    block_bytes = entry_size * min(count, 64)
    for start in range(0, count, 64):
        chunk_count = min(64, count - start)
        offset = entry_lba * sector + start * entry_size
        if offset + chunk_count * entry_size > size:
            break
        chunk = read_at(offset, chunk_count * entry_size)
        for i in range(chunk_count):
            raw = chunk[i * entry_size:(i + 1) * entry_size]
            if len(raw) < 56:
                continue
            type_guid = _guid(raw[0:16])
            if not type_guid or type_guid == "00000000-0000-0000-0000-000000000000":
                continue
            first_lba, last_lba = struct.unpack_from("<QQ", raw, 32)
            name = raw[56:128].decode("utf-16-le", errors="ignore").split("\x00")[0]
            offset_bytes = first_lba * sector
            part_size = max(0, (last_lba - first_lba + 1) * sector)
            fs = _detect_filesystem(read_at, offset_bytes, part_size)
            entries.append(PartitionEntry(
                index=start + i, name=name or f"partition{start + i}",
                type_guid=type_guid, unique_guid=_guid(raw[16:32]),
                first_lba=first_lba, last_lba=last_lba,
                offset_bytes=offset_bytes, size_bytes=part_size,
                filesystem=fs))
    return entries


def parse_mbr(read_at: Callable[[int, int], bytes], size: int,
              sector: int = 512) -> list[PartitionEntry]:
    """Parse a classic MBR partition table (four primary entries)."""
    table = read_at(446, 64)
    entries = []
    for index in range(4):
        raw = table[index * 16:(index + 1) * 16]
        if len(raw) < 16:
            continue
        kind = raw[4]
        if kind == 0:
            continue
        first_lba, sectors = struct.unpack_from("<II", raw, 8)
        offset_bytes = first_lba * sector
        part_size = sectors * sector
        if offset_bytes <= 0 or part_size <= 0 or offset_bytes >= size:
            continue
        fs = _detect_filesystem(read_at, offset_bytes, part_size)
        entries.append(PartitionEntry(
            index=index, name=f"mbr{index + 1}", type_guid=f"0x{kind:02x}",
            unique_guid="", first_lba=first_lba,
            last_lba=first_lba + max(0, sectors - 1),
            offset_bytes=offset_bytes, size_bytes=part_size, filesystem=fs))
    return entries


def _sparse_info(read_at: Callable[[int, int], bytes], size: int) -> dict:
    header = read_at(0, 28)
    if len(header) < 28:
        return {}
    (magic, major, _minor, file_hdr, chunk_hdr,
     block_size, total_blocks, chunks) = struct.unpack_from("<IHHHHIII", header, 0)
    if magic != SPARSE_MAGIC:
        return {}
    return {
        "major": major, "block_size": block_size, "total_blocks": total_blocks,
        "chunks": chunks, "raw_bytes": total_blocks * block_size,
        "file_header": file_hdr, "chunk_header": chunk_hdr,
    }


def _hash_file(path: Path, algo: str = "sha256") -> str:
    digest = hashlib.new(algo)
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_at_for(handle) -> Callable[[int, int], bytes]:
    def read_at(offset: int, length: int) -> bytes:
        handle.seek(offset)
        return handle.read(length)
    return read_at


def validate_image(path, *, hash_algo: str = "sha256") -> ImageReport:
    """Hash an image and inventory it: container, table, partitions, filesystems.

    This is the same conclusion an examiner would reach by opening the image,
    produced from the bytes rather than from the filename.
    """
    path = Path(path)
    size = path.stat().st_size
    report = ImageReport(path=str(path), size_bytes=size)
    if size <= 0:
        report.notes.append("File is empty.")
        return report
    report.sha256 = _hash_file(path, hash_algo)
    with open(path, "rb") as handle:
        read_at = _read_at_for(handle)
        sparse = _sparse_info(read_at, size)
        if sparse:
            report.container = "android-sparse"
            report.table = "sparse"
            report.notes.append(
                f"Android sparse image: {sparse['chunks']} chunks, "
                f"{sparse['raw_bytes']:,} bytes expanded, "
                f"{sparse['block_size']}-byte blocks.")
            return report
        mbr = read_at(510, 2)
        has_mbr = mbr == b"\x55\xaa"
        header = read_at(512, 8)
        if header.startswith(GPT_SIGNATURE):
            report.table = "gpt"
            report.partitions = parse_gpt(handle, size, read_at)
        elif has_mbr:
            report.table = "mbr"
            report.partitions = parse_mbr(read_at, size)
        else:
            report.table = "none"
            report.notes.append(
                "No MBR signature or GPT header in the first sectors. This may "
                "be a partition image rather than a whole-disk image.")
        if not report.partitions and report.table != "none":
            report.notes.append(
                "The table is present but no usable entries were decoded.")
        detected = sum(1 for p in report.partitions if p.filesystem)
        if report.partitions:
            report.notes.append(
                f"{detected} of {len(report.partitions)} partitions have a "
                "recognisable filesystem signature.")
    return report


def extract_partitions(image_path, report: ImageReport, outdir) -> list[dict]:
    """Write each partition of ``report`` to its own hashed file.

    Slicing the image is what makes the acquisition usable: an examiner opens
    ``userdata.img``, not a 128 GiB blob. Each slice is hashed on write.
    """
    image_path = Path(image_path)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    written = []
    with open(image_path, "rb") as source:
        for entry in report.partitions:
            if entry.size_bytes <= 0:
                continue
            name = re.sub(r"[^A-Za-z0-9._-]+", "_", entry.name) or f"part{entry.index}"
            target = outdir / f"{entry.index:02d}_{name}.img"
            digest = hashlib.sha256()
            remaining = entry.size_bytes
            source.seek(entry.offset_bytes)
            with open(target, "wb") as handle:
                while remaining > 0:
                    block = source.read(min(1024 * 1024, remaining))
                    if not block:
                        break
                    handle.write(block)
                    digest.update(block)
                    remaining -= len(block)
            written.append({
                "name": entry.name,
                "index": entry.index,
                "path": str(target),
                "size_bytes": target.stat().st_size,
                "sha256": digest.hexdigest(),
                "filesystem": entry.filesystem,
            })
    return written


def image_set_report(folder, *, hash_algo: str = "sha256") -> dict:
    """Inventory a folder of per-partition images produced by an engine."""
    folder = Path(folder)
    files = sorted(p for p in folder.rglob("*") if p.is_file())
    entries = []
    total = 0
    for path in files:
        size = path.stat().st_size
        total += size
        entries.append({
            "name": path.name,
            "path": str(path),
            "size_bytes": size,
            "sha256": _hash_file(path, hash_algo),
            "filesystem": _probe_partition_file(path),
        })
    return {
        "folder": str(folder),
        "files": entries,
        "count": len(entries),
        "total_bytes": total,
    }


def _probe_partition_file(path: Path) -> str:
    try:
        with open(path, "rb") as handle:
            read_at = _read_at_for(handle)
            return _detect_filesystem(read_at, 0, path.stat().st_size)
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# device-side discovery, used by the acquisition page
# ---------------------------------------------------------------------------

#: Properties that identify the chipset and its encryption at rest.
PROBE_PROPS = (
    "ro.soc.manufacturer", "ro.soc.model", "ro.board.platform",
    "ro.hardware", "ro.product.board", "ro.boot.hardware",
    "ro.product.manufacturer", "ro.product.model", "ro.build.version.release",
    "ro.build.version.sdk", "ro.crypto.state", "ro.crypto.type",
    "ro.boot.verifiedbootstate", "ro.boot.flash.locked",
    "ro.build.type", "ro.debuggable", "ro.boot.bootloader",
)


def read_device_profile(serial: str) -> dict:
    """Read the SoC and encryption properties over ADB, in one round trip."""
    from . import adb_manager as adb
    command = "getprop; echo '---'; cat /proc/cpuinfo 2>/dev/null | head -40"
    ok, output = adb.adb_shell(serial, command, timeout=25)
    props = {}
    for line in (output or "").splitlines():
        match = re.match(r"\[([^\]]+)\]:\s*\[(.*)\]", line.strip())
        if match:
            props[match.group(1)] = match.group(2)
    soc = normalise_soc(props)
    crypto = encryption_state(props)
    return {
        "ok": bool(ok),
        "serial": serial,
        "props": {key: props.get(key, "") for key in PROBE_PROPS},
        "soc": soc,
        "encryption": crypto,
        "cpuinfo": (output or "").split("---", 1)[-1].strip()[:2000],
    }


def detect(serial: str = "", loader: str = "") -> dict:
    """Everything the page needs to describe the physical route."""
    profile = read_device_profile(serial) if serial else {"soc": normalise_soc({})}
    transports = detect_usb_modes()
    tools = find_tools()
    plan = build_plan(profile.get("soc", {}), transports, tools, loader)
    return {
        "profile": profile,
        "transports": transports,
        "tools": tools,
        "plan": plan.as_dict(),
        "ceiling": (
            "A physical image gives the raw flash. On an encrypted handset the "
            "userdata partition is ciphertext without the key; the image is "
            "still evidence, and boot/vendor/metadata remain readable."),
    }


__all__ = [
    "IMAGING_MODES",
    "ImageReport",
    "PartitionEntry",
    "Plan",
    "SOC_FAMILIES",
    "USB_MODES",
    "build_edl_argv",
    "build_mtk_argv",
    "build_plan",
    "detect",
    "detect_usb_modes",
    "encryption_state",
    "extract_partitions",
    "find_tools",
    "image_set_report",
    "normalise_soc",
    "parse_gpt",
    "parse_mbr",
    "parse_usb_listing",
    "read_device_profile",
    "run_acquisition",
    "validate_image",
]
