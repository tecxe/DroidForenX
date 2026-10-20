"""Where image bytes come from.

Two kinds of source exist:

* **device partition** -- a block device on a connected handset, streamed over
  ``adb exec-out``. Only ``boot``/``recovery`` and partitions the current
  adbd user can actually open are offered; the rest need root and are marked as
  such rather than being hidden, so the operator can see why.
* **case folder** -- an already-acquired ``acq_*/`` directory. Always available,
  no root.

Nothing in this module runs unless it is called; :func:`list_partitions` and
:func:`is_root` are the only functions that talk to a device.
"""
from __future__ import annotations

import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

#: ``(ok, output) -> ...`` ADB shell runner. Defaults to the project's adb_manager.
ShellRunner = Callable[..., tuple]

_LINK = re.compile(r"\s(\S+)\s+->\s+(\S+)\s*$")

#: Block-device names in ``/dev/block/by-name`` that are readable without root,
#: and are the ones an unrooted acquisition can actually capture.
BOOT_PARTITIONS = (
    "boot", "boot_a", "boot_b",
    "init_boot", "init_boot_a", "init_boot_b",
    "recovery", "recovery_a", "recovery_b",
    "vendor_boot", "vendor_boot_a", "vendor_boot_b",
)


@dataclass
class Partition:
    """One entry from ``/dev/block/by-name``."""

    name: str
    block: str
    size_bytes: int = 0
    readable: bool = False
    needs_root: bool = False

    @property
    def is_boot(self) -> bool:
        return self.name in BOOT_PARTITIONS

    def label(self) -> str:
        size = _human(self.size_bytes)
        tags = []
        if self.is_boot:
            tags.append("boot")
        if self.needs_root:
            tags.append("needs root")
        suffix = f" [{', '.join(tags)}]" if tags else ""
        return f"{self.name}  ({size}) -> {self.block}{suffix}"


def _human(size: int) -> str:
    if not size:
        return "size unknown"
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def _shell(serial: str, command: str, timeout: int = 20) -> tuple:
    from .. import adb_manager as adb  # local import keeps this module import-cheap
    return adb.adb_shell(serial, command, timeout=timeout)


def is_root(serial: str, shell: Optional[ShellRunner] = None) -> bool:
    """True if the shell on ``serial`` is running as uid 0 (or ``su`` works)."""
    run = shell or _shell
    ok, output = run(serial, "id -u")
    if ok and output.strip().splitlines()[:1] == ["0"]:
        return True
    ok, output = run(serial, "su -c id")
    return bool(ok and "uid=0" in output)


def list_partitions(serial: str, shell: Optional[ShellRunner] = None, probe: bool = False) -> List[Partition]:
    """Enumerate ``/dev/block/by-name``.

    With ``probe=False`` (the default) only the symlink list is read -- one round
    trip, fast enough for a dialog. With ``probe=True`` each partition's size and
    readability is queried, which costs a round trip per partition and belongs on
    a worker thread.
    """
    run = shell or _shell
    ok, output = run(serial, "ls -la /dev/block/by-name/")
    if not ok:
        return []

    rooted = is_root(serial, shell=run) if probe else False
    partitions: List[Partition] = []
    for line in output.splitlines():
        match = _LINK.search(line)
        if not match:
            continue
        name, block = match.group(1), match.group(2)
        item = Partition(name=name, block=block)
        if probe:
            item.size_bytes = partition_size(serial, block, shell=run)
            if rooted:
                item.readable = True
            elif item.is_boot:
                item.readable = True
            else:
                item.readable = probe_readable(serial, block, shell=run)
            item.needs_root = not item.readable
        else:
            item.readable = item.is_boot
            item.needs_root = not item.is_boot
        partitions.append(item)
    partitions.sort(key=lambda p: p.name)
    return partitions


def partition_size(serial: str, block: str, shell: Optional[ShellRunner] = None) -> int:
    """Size of a block device in bytes, or 0 if it cannot be read.

    ``/sys/class/block/<kname>/size`` is in 512-byte sectors.
    """
    run = shell or _shell
    kname = block.rstrip("/").rsplit("/", 1)[-1]
    ok, output = run(serial, f"cat /sys/class/block/{kname}/size")
    if not ok:
        return 0
    try:
        return int(output.strip().splitlines()[0]) * 512
    except (ValueError, IndexError):
        return 0


def probe_readable(serial: str, block: str, shell: Optional[ShellRunner] = None) -> bool:
    """Can the current adbd user read this block device?

    Asked as a one-sector read piped into ``wc -c`` on the device, so a
    permission error is detected without moving any real data.
    """
    run = shell or _shell
    ok, output = run(serial, f"dd if={block} bs=512 count=1 2>/dev/null | wc -c")
    if not ok:
        return False
    try:
        return int(output.strip().splitlines()[0]) > 0
    except (ValueError, IndexError):
        return False


def partition_stream_argv(adb_path, serial: str, block: str, rooted: bool = False) -> list:
    """The ``adb`` argv that streams a block device to stdout.

    ``adb`` joins its trailing arguments and hands them to the device's shell, so
    the whole ``dd`` invocation is passed as a single quoted argument. stderr is
    discarded inside the device shell -- with ``exec-out`` a stray warning would
    otherwise be spliced into the image.
    """
    dd = f"dd if={block} bs=1048576 2>/dev/null"
    command = f"sh -c '{dd}'"
    if rooted:
        command = f"su 0 {command}"
    return [str(adb_path), "-s", serial, "exec-out", command]


class ProcessReader:
    """A file-like view of a subprocess' stdout.

    Exposes ``read(size)`` and a ``size`` attribute, which is everything the RAW
    writer needs. ``close`` also tears the process down, so cancelling an
    acquisition does not leave a running ``dd`` behind.
    """

    def __init__(self, argv: list, size: int = 0):
        self.argv = list(argv)
        self.size = int(size or 0)
        self._stderr = bytearray()
        self._process = subprocess.Popen(
            self.argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        # A full stderr pipe can deadlock the writer if nobody drains it, so a
        # small daemon thread collects whatever ``adb`` says while data streams.
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stderr(self) -> None:
        try:
            for block in iter(lambda: self._process.stderr.read(4096), b""):
                self._stderr.extend(block)
        except Exception:
            pass  # the pipe dies when close() tears the process down

    def error_output(self, limit: int = 400) -> str:
        """Whatever ``adb`` printed on stderr, for failure diagnostics."""
        return self._stderr.decode("utf-8", errors="replace").strip()[:limit]

    @property
    def stdout(self):
        return self._process.stdout

    def read(self, size: int = -1) -> bytes:
        return self._process.stdout.read(size)

    def close(self) -> None:
        for stream in (self._process.stdout, self._process.stderr):
            if stream is not None and not stream.closed:
                stream.close()
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                self._process.kill()

    def __enter__(self) -> "ProcessReader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def open_partition_reader(serial: str, block: str, rooted: bool = False,
                          adb_path=None, size: int = 0) -> ProcessReader:
    """Open a streaming reader for a device block device."""
    if adb_path is None:
        from .. import adb_manager as adb
        adb_path = adb.find_adb()
    return ProcessReader(partition_stream_argv(adb_path, serial, block, rooted), size=size)


def folder_source(path) -> Path:
    """Validate a case folder and return it as a ``Path``."""
    folder = Path(path)
    if not folder.is_dir():
        raise ValueError(f"not a folder: {folder}")
    return folder


__all__ = [
    "BOOT_PARTITIONS",
    "Partition",
    "ProcessReader",
    "folder_source",
    "is_root",
    "list_partitions",
    "open_partition_reader",
    "partition_size",
    "partition_stream_argv",
    "probe_readable",
]
