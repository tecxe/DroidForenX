"""
Disk-image writer tests.

These run without a device. The writers never talk to ADB -- RAW reads from a
file-like object and ZIP reads a folder -- so everything except the device
source layer can be exercised here.

What is pinned:

  1. RAW segmentation produces byte-for-byte ``.001/.002/...`` segments whose
     concatenation hashes to the same digest as the source, and the sidecar +
     ``.imageinfo.txt`` record that digest.
  2. ``verify()`` catches a modified image instead of trusting the write.
  3. A failed or empty acquisition leaves no half-written image behind.
  4. ZIP containers round-trip, record per-file hashes, and verify.
  5. The device-source layer builds the right ``adb`` command and refuses to
     claim an unreadable partition is available.

Run with:  python -m unittest desktop.tests.test_imaging -v
"""

import io
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from desktop.core.imaging import (
    ImageRequest,
    RawImageWriter,
    ZipImageWriter,
    create_image,
    writer_for,
)
from desktop.core.imaging import sources

MIB = 1024 * 1024


class Reader:
    """Minimal file-like source: ``read(size)`` plus a ``size`` hint."""

    def __init__(self, data: bytes, size=None):
        self._buffer = io.BytesIO(data)
        self.size = len(data) if size is None else size

    def read(self, size=-1) -> bytes:
        return self._buffer.read(size)


def sha256(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


class ImagingTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dfx-image-"))
        self.dest = self.tmp / "out"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def request(self, **overrides) -> ImageRequest:
        options = dict(dest_dir=self.dest, base_name="evidence", image_format="raw")
        options.update(overrides)
        return ImageRequest(**options)

    def folder(self, files: dict) -> Path:
        root = self.tmp / "src"
        for name, content in files.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        return root


class RawWriterTest(ImagingTestCase):
    def test_single_file_matches_source_digest(self):
        data = b"forensic payload" * 5000
        result = RawImageWriter().write(Reader(data), self.request(segment_size=0))

        self.assertTrue(result.verified)
        self.assertEqual(result.digest, sha256(data))
        self.assertEqual(result.total_bytes, len(data))
        self.assertEqual([p.name for p in result.segments], ["evidence.dd"])
        self.assertEqual(result.primary.read_bytes(), data)

    def test_segments_concatenate_to_the_original(self):
        data = b"A" * MIB + b"B" * MIB + b"C" * (MIB // 2)
        result = RawImageWriter().write(Reader(data), self.request(segment_size=MIB))

        self.assertTrue(result.verified)
        self.assertEqual(
            [p.name for p in result.segments],
            ["evidence.001", "evidence.002", "evidence.003"])
        self.assertEqual([p.stat().st_size for p in result.segments], [MIB, MIB, MIB // 2])
        self.assertEqual(b"".join(p.read_bytes() for p in result.segments), data)
        self.assertEqual(result.digest, sha256(data))

    def test_exact_multiple_leaves_no_empty_trailing_segment(self):
        data = b"y" * (2 * MIB)
        result = RawImageWriter().write(Reader(data), self.request(segment_size=MIB))

        self.assertEqual([p.name for p in result.segments], ["evidence.001", "evidence.002"])
        self.assertEqual([p.stat().st_size for p in result.segments], [MIB, MIB])
        self.assertTrue(result.verified)

    def test_sidecar_and_info_file_record_the_digest(self):
        data = b"x" * MIB
        result = RawImageWriter().write(Reader(data), self.request(segment_size=MIB))

        sidecar = self.dest / "evidence.sha256"
        self.assertIn(str(sidecar), [str(p) for p in result.sidecars])
        self.assertEqual(
            sidecar.read_text(encoding="utf-8").strip(),
            f"{sha256(data)}  evidence.001")

        info = (self.dest / "evidence.imageinfo.txt").read_text(encoding="utf-8")
        self.assertIn(f"image_hash: {sha256(data)}", info)
        self.assertIn("verified: true", info)
        self.assertIn(f"total_bytes: {len(data)}", info)

    def test_verify_detects_a_modified_segment(self):
        data = b"z" * (2 * MIB)
        request = self.request(segment_size=MIB, verify=False)
        writer = RawImageWriter()
        result = writer.write(Reader(data), request)

        self.assertTrue(writer.verify(result, request), "untouched image should verify")

        second = result.segments[1]
        blob = bytearray(second.read_bytes())
        blob[0] ^= 0xFF
        second.write_bytes(bytes(blob))

        self.assertFalse(writer.verify(result, request), "modified image must fail verification")

    def test_empty_source_writes_nothing(self):
        with self.assertRaises(ValueError):
            RawImageWriter().write(Reader(b""), self.request())
        self.assertEqual([p for p in self.dest.iterdir()], [])

    def test_a_stream_that_fails_midway_leaves_no_partial_segment(self):
        class Broken:
            size = 0

            def __init__(self):
                self.calls = 0

            def read(self, size=-1):
                self.calls += 1
                if self.calls == 1:
                    return b"x" * 4096
                raise ValueError("image stream closed before the source ended")

        with self.assertRaises(ValueError):
            RawImageWriter().write(Broken(), self.request(segment_size=0))
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_a_cancelled_segmented_stream_removes_completed_and_partial_segments(self):
        class Broken:
            size = 0

            def __init__(self):
                self.calls = 0

            def read(self, size=-1):
                self.calls += 1
                if self.calls <= 2:
                    return b"x" * MIB
                raise ValueError("image stream closed before the source ended")

        with self.assertRaises(ValueError):
            RawImageWriter().write(Broken(), self.request(segment_size=MIB))
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_progress_is_reported_against_the_known_size(self):
        calls = []
        data = b"p" * (2 * MIB)
        RawImageWriter().write(
            Reader(data),
            self.request(segment_size=MIB),
            progress=lambda done, total, stage: calls.append((done, total, stage)))

        self.assertTrue(calls)
        self.assertTrue(all(total == len(data) for _, total, _ in calls))
        self.assertEqual(max(done for done, _, _ in calls), len(data))
        self.assertEqual(calls[-1][2], "done")


class RequestValidationTest(ImagingTestCase):
    def test_rejects_unknown_format(self):
        with self.assertRaises(ValueError):
            self.request(image_format="e01")

    def test_rejects_unknown_hash(self):
        with self.assertRaises(ValueError):
            self.request(hash_algo="sha512")

    def test_rejects_unknown_compression(self):
        with self.assertRaises(ValueError):
            self.request(compression="turbo")

    def test_rejects_path_traversal_in_base_name(self):
        with self.assertRaises(ValueError):
            self.request(base_name="../../etc/passwd")

    def test_rejects_tiny_segment_size(self):
        with self.assertRaises(ValueError):
            self.request(segment_size=512)

    def test_writer_for_rejects_unknown_format(self):
        with self.assertRaises(ValueError):
            writer_for("ewf")


class OverwriteProtectionTest(ImagingTestCase):
    """A finished image is evidence: a new run must never replace it silently."""

    def test_raw_refuses_to_overwrite_a_previous_image(self):
        self.dest.mkdir(parents=True)
        (self.dest / "evidence.dd").write_bytes(b"old image")
        with self.assertRaises(ValueError):
            RawImageWriter().write(Reader(b"new bytes"), self.request(segment_size=0))
        self.assertEqual((self.dest / "evidence.dd").read_bytes(), b"old image")

    def test_raw_refuses_to_overwrite_a_previous_segment_chain(self):
        self.dest.mkdir(parents=True)
        (self.dest / "evidence.001").write_bytes(b"old segment")
        with self.assertRaises(ValueError):
            RawImageWriter().write(Reader(b"x" * MIB), self.request(segment_size=MIB))
        self.assertEqual((self.dest / "evidence.001").read_bytes(), b"old segment")

    def test_zip_refuses_to_overwrite_a_previous_image(self):
        source = self.folder({"a.txt": b"alpha"})
        self.dest.mkdir(parents=True)
        (self.dest / "evidence.zip").write_bytes(b"old container")
        with self.assertRaises(ValueError):
            ZipImageWriter().write(source, self.request(image_format="zip"))
        self.assertEqual((self.dest / "evidence.zip").read_bytes(), b"old container")


class ContainerGuardsTest(ImagingTestCase):
    def test_a_cancelled_zip_leaves_no_container_behind(self):
        source = self.folder({"a.txt": b"alpha"})

        class Stop(Exception):
            pass

        def progress(done, total, stage):
            raise Stop()

        with self.assertRaises(Stop):
            ZipImageWriter().write(
                source, self.request(image_format="zip"), progress=progress)
        self.assertFalse((self.dest / "evidence.zip").exists())

    def test_raw_refuses_when_the_destination_is_too_small(self):
        from collections import namedtuple
        from desktop.core.imaging import raw as raw_module

        usage = namedtuple("usage", "total used free")
        with mock.patch.object(raw_module.shutil, "disk_usage",
                              return_value=usage(10, 0, 1)):
            with self.assertRaises(ValueError):
                RawImageWriter().write(Reader(b"x" * 4096), self.request(segment_size=0))
        self.assertFalse((self.dest / "evidence.dd").exists())

    def test_the_zip_is_not_packed_into_itself_when_written_inside_the_source(self):
        source = self.folder({"a.txt": b"alpha"})
        result = ZipImageWriter().write(
            source, self.request(image_format="zip", dest_dir=source))
        with zipfile.ZipFile(result.primary) as archive:
            self.assertNotIn("evidence.zip", archive.namelist())

    def test_a_failed_zip_leaves_no_container_behind(self):
        source = self.folder({"a.txt": b"alpha"})
        with mock.patch.object(zipfile.ZipFile, "write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                ZipImageWriter().write(source, self.request(image_format="zip"))
        self.assertFalse((self.dest / "evidence.zip").exists())


class BaseNameTest(unittest.TestCase):
    def test_reserved_windows_device_names_are_rejected(self):
        from desktop.core.imaging.base import safe_base_name

        for name in ("con", "NUL", "com1", "lpt9.txt", "aux "):
            with self.assertRaises(ValueError, msg=name):
                safe_base_name(name)

    def test_a_trailing_dot_is_rejected(self):
        from desktop.core.imaging.base import safe_base_name

        for name in ("evidence.", "evidence.."):
            with self.assertRaises(ValueError, msg=name):
                safe_base_name(name)


class ZipWriterTest(ImagingTestCase):
    def test_folder_round_trips_with_a_manifest(self):
        source = self.folder({"a.txt": b"alpha", "sub/b.bin": b"beta" * 10})
        result = ZipImageWriter().write(source, self.request(image_format="zip"))

        self.assertTrue(result.verified)
        self.assertEqual(result.primary.name, "evidence.zip")

        with zipfile.ZipFile(result.primary) as archive:
            self.assertIsNone(archive.testzip())
            names = set(archive.namelist())
            self.assertIn("a.txt", names)
            self.assertIn("sub/b.bin", names)
            self.assertEqual(archive.read("a.txt"), b"alpha")

            manifest = json.loads(archive.read("MANIFEST.json"))
            self.assertEqual(manifest["file_count"], 2)
            by_path = {entry["path"]: entry for entry in manifest["files"]}
            self.assertEqual(by_path["a.txt"]["sha256"], sha256(b"alpha"))

        self.assertTrue((self.dest / "evidence.zip.sha256").exists())

    def test_verify_detects_a_modified_archive(self):
        source = self.folder({"a.txt": b"alpha"})
        request = self.request(image_format="zip", verify=False)
        writer = ZipImageWriter()
        result = writer.write(source, request)
        self.assertTrue(writer.verify(result, request))

        result.primary.write_bytes(result.primary.read_bytes() + b"\x00" * 32)
        self.assertFalse(writer.verify(result, request))

    def test_requires_a_folder(self):
        with self.assertRaises(ValueError):
            ZipImageWriter().write(self.tmp / "missing", self.request(image_format="zip"))



class CreateImageDispatchTest(ImagingTestCase):
    def test_dispatches_to_the_folder_writer(self):
        source = self.folder({"a.txt": b"alpha"})
        result = create_image(source, self.request(image_format="zip"))
        self.assertEqual(result.image_format, "zip")
        self.assertTrue(result.verified)


class FakeShell:
    """Stands in for ``adb_manager.adb_shell`` with canned device output."""

    LS = (
        "lrwxrwxrwx 1 root root 16 1970-01-01 00:00 boot_a -> /dev/block/sde13\n"
        "lrwxrwxrwx 1 root root 16 1970-01-01 00:00 userdata -> /dev/block/sda21\n"
        "lrwxrwxrwx 1 root root 16 1970-01-01 00:00 protect_f -> /dev/block/sda12\n"
    )
    SIZES = {"/dev/block/sde13": 131072, "/dev/block/sda21": 2097152, "/dev/block/sda12": 8192}
    READABLE = {"/dev/block/sde13": True, "/dev/block/sda21": True, "/dev/block/sda12": False}

    def __call__(self, serial, command, timeout=20):
        if command.startswith("ls -la /dev/block/by-name"):
            return (True, self.LS)
        if command.strip() == "id -u":
            return (True, "2000\n")
        if "su -c id" in command:
            return (True, "uid=2000(u0_a1) gid=2000(u0_a1)\n")
        for block, sectors in self.SIZES.items():
            if f"cat /sys/class/block/{block.rsplit('/', 1)[-1]}/size" in command:
                return (True, f"{sectors}\n")
        for block, readable in self.READABLE.items():
            if f"if={block}" in command:
                return (True, "512\n" if readable else "0\n")
        return (False, "")


class DeviceSourceTest(unittest.TestCase):
    def test_stream_argv_discards_stderr_inside_the_device_shell(self):
        argv = sources.partition_stream_argv("/usr/bin/adb", "SER123", "/dev/block/sda1")
        self.assertEqual(
            argv,
            ["/usr/bin/adb", "-s", "SER123", "exec-out",
             "sh -c 'dd if=/dev/block/sda1 bs=1048576 2>/dev/null'"])

    def test_stream_argv_wraps_su_when_rooted(self):
        argv = sources.partition_stream_argv("/usr/bin/adb", "SER", "/dev/block/sda1", rooted=True)
        self.assertEqual(argv[-1], "su 0 sh -c 'dd if=/dev/block/sda1 bs=1048576 2>/dev/null'")

    def test_fast_listing_claims_nothing_readable_without_a_probe(self):
        partitions = sources.list_partitions("SER", shell=FakeShell(), probe=False)
        by_name = {p.name: p for p in partitions}
        self.assertFalse(by_name["boot_a"].readable)
        self.assertTrue(by_name["boot_a"].needs_root)
        self.assertFalse(by_name["userdata"].readable)
        self.assertTrue(by_name["userdata"].needs_root)

    def test_probed_listing_uses_size_and_readability(self):
        partitions = sources.list_partitions("SER", shell=FakeShell(), probe=True)
        by_name = {p.name: p for p in partitions}
        self.assertEqual(by_name["boot_a"].size_bytes, 131072 * 512)
        self.assertTrue(by_name["userdata"].readable)
        self.assertFalse(by_name["userdata"].needs_root)
        self.assertFalse(by_name["protect_f"].readable)
        self.assertTrue(by_name["protect_f"].needs_root)

    def test_probed_listing_on_an_unrooted_device_marks_every_partition(self):
        class LockedShell(FakeShell):
            READABLE = {
                "/dev/block/sde13": False,
                "/dev/block/sda21": False,
                "/dev/block/sda12": False,
            }

        partitions = sources.list_partitions("SER", shell=LockedShell(), probe=True)
        self.assertTrue(partitions)
        self.assertTrue(all(not p.readable and p.needs_root for p in partitions))

    def test_probing_uses_one_batched_round_trip(self):
        class CountingShell(FakeShell):
            def __init__(self):
                self.bulk_calls = 0

            def __call__(self, serial, command, timeout=20):
                if "readlink -f" in command and "by-name/*" in command:
                    self.bulk_calls += 1
                    return (True,
                            "boot_a /dev/block/sde13 131072 512\n"
                            "userdata /dev/block/sda21 2097152 512\n"
                            "protect_f /dev/block/sda12 8192 0\n")
                return super().__call__(serial, command, timeout)

        shell = CountingShell()
        partitions = sources.list_partitions("SER", shell=shell, probe=True)
        by_name = {p.name: p for p in partitions}

        self.assertEqual(shell.bulk_calls, 1)
        self.assertEqual(by_name["boot_a"].size_bytes, 131072 * 512)
        self.assertTrue(by_name["boot_a"].readable)
        self.assertTrue(by_name["userdata"].readable)
        self.assertFalse(by_name["protect_f"].readable)
        self.assertTrue(by_name["protect_f"].needs_root)

    def test_is_root_checks_id_and_su(self):
        self.assertFalse(sources.is_root("SER", shell=FakeShell()))

        def rooted(serial, command, timeout=20):
            return (True, "0\n")

        self.assertTrue(sources.is_root("SER", shell=rooted))


class DeviceSourceWiringTest(unittest.TestCase):
    """The *default* shell/ADB path, with nothing injected.

    Every other device test passes ``shell=FakeShell()``, which bypassed the real
    lookup entirely -- so a wrong relative import inside ``_shell`` and
    ``open_partition_reader`` sailed through the whole suite while the dialog
    failed on every device with "cannot import name 'adb_manager'". These tests
    exercise the unwired path on purpose.
    """

    def test_default_shell_delegates_to_adb_manager(self):
        from desktop.core import adb_manager

        calls = []

        def fake_shell(serial, command, timeout=20):
            calls.append((serial, command))
            return (True, "ok")

        with mock.patch.object(adb_manager, "adb_shell", fake_shell):
            ok, output = sources._shell("SER123", "id -u")

        self.assertTrue(ok)
        self.assertEqual(output, "ok")
        self.assertEqual(calls, [("SER123", "id -u")])

    def test_list_partitions_works_without_an_injected_shell(self):
        from desktop.core import adb_manager

        def fake_shell(serial, command, timeout=20):
            if command.startswith("ls -la /dev/block/by-name"):
                return (True, FakeShell.LS)
            return (False, "")

        with mock.patch.object(adb_manager, "adb_shell", fake_shell):
            partitions = sources.list_partitions("SER123")

        self.assertEqual([p.name for p in partitions], ["boot_a", "protect_f", "userdata"])
        self.assertTrue(all(p.needs_root for p in partitions))  # fast path claims nothing

    def test_open_partition_reader_resolves_adb_and_builds_the_command(self):
        from desktop.core import adb_manager

        captured = {}

        class FakeProcess:
            stdout = io.BytesIO(b"")

            def poll(self):
                return 0

            def terminate(self):  # pragma: no cover - already exited
                pass

            def wait(self, timeout=None):  # pragma: no cover
                return 0

        def fake_popen(argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs
            return FakeProcess()

        with mock.patch.object(adb_manager, "find_adb", lambda: "C:/sdk/adb.exe"), \
                mock.patch.object(sources.subprocess, "Popen", fake_popen):
            reader = sources.open_partition_reader("SER123", "/dev/block/sda21", size=4096)

        self.assertEqual(reader.size, 4096)
        self.assertEqual(captured["argv"][0], "C:/sdk/adb.exe")
        self.assertEqual(captured["argv"][1:3], ["-s", "SER123"])
        self.assertEqual(
            captured["argv"][-1], "sh -c 'dd if=/dev/block/sda21 bs=1048576 2>/dev/null'")
        self.assertEqual(captured["kwargs"]["stdout"], sources.subprocess.PIPE)


class ProcessReaderDiagnosticsTest(unittest.TestCase):
    """A stream that dies must report what adb said, not swallow it.

    This was the failure behind "no data was read from the source": the reader
    threw adb's stderr away, so an unauthorized device, a disconnection or a
    permission error all looked identical to an empty source.
    """

    def test_stderr_is_captured_for_diagnostics(self):
        import sys
        import time

        reader = sources.ProcessReader([
            sys.executable, "-c",
            "import sys; sys.stderr.write('boom-diagnostic'); "
            "sys.stdout.buffer.write(b'bytes'); sys.stdout.flush()",
        ])
        try:
            self.assertEqual(reader.read(5), b"bytes")
            self.assertEqual(reader.error_output(), "")
        finally:
            reader.close()

        deadline = time.monotonic() + 5
        while "boom-diagnostic" not in reader.error_output() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertIn("boom-diagnostic", reader.error_output())

    def test_close_returns_promptly_on_a_live_long_stream(self):
        """Regression: close() deadlocked on stop of a large /sdcard stream.

        Closing stderr while the drain thread was blocked inside read() waited
        for a lock that could only be released after close(). A full-storage
        image stopped/finished and then hung forever; small folders finished
        before the drain thread mattered.
        """
        import sys
        import time

        script = (
            "import sys, time\n"
            "while True:\n"
            "    sys.stdout.buffer.write(b'x' * 65536)\n"
            "    sys.stdout.flush()\n"
            "    time.sleep(0.01)\n"
        )
        reader = sources.ProcessReader([sys.executable, "-c", script])
        self.assertTrue(reader.read(4096))

        started = time.monotonic()
        reader.close()
        self.assertLess(time.monotonic() - started, 10)
        self.assertIsNotNone(reader._process.poll())

    def test_a_clean_process_reports_no_error(self):
        import sys
        import time

        reader = sources.ProcessReader([
            sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'ok')",
        ])
        try:
            reader.read(2)
        finally:
            reader.close()
        time.sleep(0.1)  # let the drain thread see EOF
        self.assertEqual(reader.error_output(), "")


if __name__ == "__main__":
    unittest.main()
