"""Where image bytes come from.

Two kinds of source exist:

* **device partition** -- a block device on a connected handset, streamed over
  ``adb exec-out``. Every partition is probed before it is offered: the shell
  user only ever gets what it can actually open, and on modern (unrooted)
  Android that is typically *nothing* -- ``boot`` included -- so partitions are
  marked "needs root" rather than being hidden, so the operator can see why.
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

#: Block-device names in ``/dev/block/by-name`` worth tagging as ``boot`` in the
#: picker. They are probed like any other partition -- the old assumption that
#: ``boot`` is readable without root does not hold on modern devices, where the
#: shell user is typically denied every block device.
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


#: One device shell round trip that reports, per partition: name, resolved
#: block node, size in 512-byte sectors (0 when unreadable) and how many bytes a
#: one-sector read returned. A shell loop is used because Android 15 denies the
#: shell user both ``/proc/partitions`` and ``sysfs`` sizes on locked-down
#: devices; probing per partition costs one round trip each and took 22s on a
#: 65-partition handset versus ~5s for this.
_PROBE_LOOP = (
    "for b in /dev/block/by-name/*; do n=${b##*/}; "
    "r=$(readlink -f ${b} 2>/dev/null); k=${r##*/}; "
    "s=$(cat /sys/class/block/${k}/size 2>/dev/null); "
    "c=$(dd if=${b} bs=512 count=1 2>/dev/null | wc -c); "
    "echo ${n} ${r} ${s:-0} ${c:-0}; done"
)


def _parse_links(output: str) -> List[Partition]:
    partitions = []
    for line in output.splitlines():
        match = _LINK.search(line)
        if match:
            partitions.append(Partition(name=match.group(1), block=match.group(2)))
    return partitions


def _bulk_probe(serial: str, run: ShellRunner):
    """Probe every partition in one round trip, or ``None`` if unsupported."""
    ok, output = run(serial, _PROBE_LOOP)
    if not ok:
        return None
    probed = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) != 4:
            continue
        name, block, sectors, read_bytes = fields
        try:
            size = int(sectors) * 512
            readable = int(read_bytes) > 0
        except ValueError:
            continue
        probed[name] = Partition(name=name, block=block, size_bytes=size,
                                 readable=readable, needs_root=not readable)
    return probed or None


def list_partitions(serial: str, shell: Optional[ShellRunner] = None, probe: bool = False) -> List[Partition]:
    """Enumerate ``/dev/block/by-name``.

    With ``probe=False`` (the default) only the symlink list is read -- one round
    trip, fast enough for a dialog. With ``probe=True`` every partition's size and
    readability is verified, in one batched shell loop where the device supports
    it, and nothing is claimed readable that was not actually read.
    """
    run = shell or _shell
    ok, output = run(serial, "ls -la /dev/block/by-name/")
    if not ok:
        return []

    partitions = _parse_links(output)
    if not probe:
        # The fast listing verifies nothing, so it claims nothing: even a boot
        # partition may be denied to the shell user on this device.
        for item in partitions:
            item.readable = False
            item.needs_root = True
        partitions.sort(key=lambda p: p.name)
        return partitions

    probed = _bulk_probe(serial, run)
    if probed is None:
        # Fallback: one round trip per attribute for shells that cannot run the
        # loop (some vendor shells mangle command substitution).
        rooted = is_root(serial, shell=run)
        for item in partitions:
            item.size_bytes = partition_size(serial, item.block, shell=run)
            item.readable = bool(rooted) or probe_readable(serial, item.block, shell=run)
            item.needs_root = not item.readable
    else:
        for item in partitions:
            result = probed.get(item.name)
            if result is not None:
                item.block = result.block or item.block
                item.size_bytes = result.size_bytes
                item.readable = result.readable
                item.needs_root = result.needs_root
            else:
                item.readable = False
                item.needs_root = True

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
        self._close_lock = threading.Lock()
        self._process = subprocess.Popen(
            self.argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        # A full stderr pipe can deadlock the writer if nobody drains it, so a
        # small daemon thread collects whatever ``adb`` says while data streams.
        self._drain = threading.Thread(target=self._drain_stderr, daemon=True)
        self._drain.start()

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
        with self._close_lock:
            self._close_locked()

    def _close_locked(self) -> None:
        process = self._process
        # Kill the child before touching the pipes. The drain thread is blocked
        # in ``stderr.read()`` holding that stream's lock; closing stderr first
        # (or closing stdout under a concurrent writer read) deadlocks forever.
        # This used to hang every cancel/stop of a long /sdcard stream.
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:  # pragma: no cover - already gone
                pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                try:
                    process.kill()
                except OSError:
                    pass
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:  # pragma: no cover
                    pass

        # The child is gone, so the drain thread sees EOF and releases the
        # stream; only then is it safe to close.
        if self._drain is not None:
            self._drain.join(timeout=2)
        for stream in (process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                try:
                    stream.close()
                except (OSError, ValueError):  # pragma: no cover
                    pass

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


#: Area streamed by the logical source. Fixed on purpose: it is a constant of
#: the tool, never operator-provided text fed into a shell command.
LOGICAL_ROOT = "/sdcard"


def logical_stream_argv(adb_path, serial: str, root: str = LOGICAL_ROOT) -> list:
    """The ``adb`` argv that streams a directory as a POSIX tar archive.

    ``toybox tar`` has shipped with Android since 6.0, and ``exec-out`` carries
    the bytes raw (no CRLF translation). stderr is discarded inside the device
    shell for the same reason as in :func:`partition_stream_argv` -- a stray
    warning must never be spliced into an image.
    """
    command = f"sh -c 'tar -cf - -C {root} . 2>/dev/null'"
    return [str(adb_path), "-s", serial, "exec-out", command]


def open_logical_reader(serial: str, adb_path=None, root: str = LOGICAL_ROOT) -> ProcessReader:
    """Open a streaming reader for the device's user storage as a tar archive.

    This is the imaging path that actually works on unrooted devices: the shell
    user cannot read block devices, but it can read ``/sdcard``, so the logical
    image is an exact hashed capture of the files the unrooted tool can see.
    """
    if adb_path is None:
        from .. import adb_manager as adb
        adb_path = adb.find_adb()
    return ProcessReader(logical_stream_argv(adb_path, serial, root), size=0)


def folder_source(path) -> Path:
    """Validate a case folder and return it as a ``Path``."""
    folder = Path(path)
    if not folder.is_dir():
        raise ValueError(f"not a folder: {folder}")
    return folder


__all__ = [
    "BOOT_PARTITIONS",
    "LOGICAL_ROOT",
    "Partition",
    "ProcessReader",
    "folder_source",
    "is_root",
    "list_partitions",
    "logical_stream_argv",
    "open_logical_reader",
    "open_partition_reader",
    "partition_size",
    "partition_stream_argv",
    "probe_readable",
]
