"""Physical acquisition: chipset naming, boot-mode detection, image validation.

Every test builds its own fixture. None of these need a handset, which is the
point: the parser and planner have to be right before a powered-off phone is
put into a boot ROM.
"""
import hashlib
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path

from desktop.core import physical

SECTOR = 512


def _gpt_image(path: Path, entries):
    """Write a minimal but structurally valid GPT image.

    ``entries`` is a list of ``(name, first_lba, last_lba, fs_magic)`` where
    ``fs_magic`` is ``(offset_in_partition, bytes)`` or ``None``.
    """
    total_sectors = max(last for _n, _f, last, _m in entries) + 8
    size = total_sectors * SECTOR
    data = bytearray(size)

    # Protective MBR.
    data[446 + 4] = 0xEE
    struct.pack_into("<II", data, 446 + 8, 1, total_sectors - 1)
    data[510:512] = b"\x55\xaa"

    # GPT header.
    header = bytearray(92)
    header[0:8] = b"EFI PART"
    struct.pack_into("<I", header, 8, 0x00010000)
    struct.pack_into("<I", header, 12, 92)
    struct.pack_into("<Q", header, 24, 1)
    struct.pack_into("<Q", header, 32, total_sectors - 1)
    struct.pack_into("<Q", header, 40, 34)
    struct.pack_into("<Q", header, 48, total_sectors - 34)
    struct.pack_into("<Q", header, 72, 2)      # entries at LBA 2
    struct.pack_into("<I", header, 80, 4)      # four entries
    struct.pack_into("<I", header, 84, 128)
    data[512:512 + 92] = header

    table = bytearray(4 * 128)
    for index, (name, first_lba, last_lba, fs_magic) in enumerate(entries):
        base = index * 128
        table[base:base + 16] = bytes(range(1, 17))
        table[base + 16:base + 32] = bytes(range(17, 33))
        struct.pack_into("<QQ", table, base + 32, first_lba, last_lba)
        encoded = name.encode("utf-16-le")
        table[base + 56:base + 56 + len(encoded)] = encoded
        if fs_magic:
            relative, magic = fs_magic
            start = first_lba * SECTOR + relative
            data[start:start + len(magic)] = magic
    data[2 * SECTOR:2 * SECTOR + len(table)] = table

    path.write_bytes(bytes(data))
    return size


class SocTest(unittest.TestCase):
    def test_a_transsion_mediatek_handset_is_named(self):
        soc = physical.normalise_soc({
            "ro.soc.manufacturer": "MediaTek",
            "ro.board.platform": "mt6768",
            "ro.hardware": "mt6768",
            "ro.product.manufacturer": "TECNO",
        })
        self.assertEqual(soc["family"], "mediatek")
        self.assertEqual(soc["label"], "MediaTek")
        self.assertEqual(soc["model"], "mt6768")
        self.assertIn("ro.board.platform", soc["evidence"])

    def test_a_qualcomm_handset_is_named_from_the_platform(self):
        soc = physical.normalise_soc({"ro.board.platform": "sm6375"})
        self.assertEqual(soc["family"], "qualcomm")

    def test_an_unstated_chipset_is_unknown_not_guessed(self):
        soc = physical.normalise_soc({})
        self.assertEqual(soc["family"], "unknown")
        self.assertEqual(soc["label"], "Unknown")

    def test_a_non_mediatek_mt_string_does_not_match(self):
        # "mt" followed by four digits is a MediaTek part; "mtp" is not.
        soc = physical.normalise_soc({"ro.hardware": "mtpdevice"})
        self.assertEqual(soc["family"], "unknown")


class EncryptionTest(unittest.TestCase):
    def test_fbe_is_reported_as_ciphertext_at_rest(self):
        state = physical.encryption_state({
            "ro.crypto.state": "encrypted", "ro.crypto.type": "file",
            "ro.boot.flash.locked": "1",
        })
        self.assertTrue(state["encrypted"])
        self.assertEqual(state["type"], "file")
        self.assertIn("ciphertext", state["label"])
        self.assertEqual(state["bootloader_locked"], "1")

    def test_an_unencrypted_device_is_not_labelled_encrypted(self):
        state = physical.encryption_state({"ro.crypto.state": "unencrypted"})
        self.assertFalse(state["encrypted"])
        self.assertIn("Not encrypted", state["label"])


class UsbTest(unittest.TestCase):
    def test_lsusb_bootrom_line_is_recognised(self):
        text = ("Bus 001 Device 004: ID 0e8d:0003 MediaTek Inc. MT6227 phone\n"
                "Bus 001 Device 005: ID 046d:c52b Logitech, Inc. Unifying Receiver")
        modes = physical.parse_usb_listing(text, "lsusb")
        self.assertEqual([m["mode"] for m in modes], ["mtk_brom"])
        self.assertEqual(modes[0]["vid"], "0e8d")
        self.assertEqual(modes[0]["pid"], "0003")

    def test_windows_pnp_ids_are_recognised(self):
        text = ("USB\\VID_0E8D&PID_2000\\5&2a3f&0&1\n"
                "USB\\VID_05C6&PID_9008\\5&1f2e&0&2\n"
                "USB\\VID_046D&PID_C52B\\5&3c4d")
        modes = {m["mode"] for m in physical.parse_usb_listing(text, "pnp")}
        self.assertEqual(modes, {"mtk_preloader", "qualcomm_edl"})

    def test_macos_system_profiler_blocks_are_recognised(self):
        text = ('    USB 3.1 Bus:\n\n      MT6227:\n'
                '        "idVendor" = 0x0e8d\n'
                '        "idProduct" = 0x0003\n')
        modes = physical.parse_usb_listing(text, "mac")
        self.assertEqual([m["mode"] for m in modes], ["mtk_brom"])

    def test_unrelated_devices_are_dropped(self):
        self.assertEqual(
            physical.parse_usb_listing("ID 0781:5567 SanDisk Cruzer"), [])


class DriverBindingTest(unittest.TestCase):
    """A device on the bus with no driver is a host fault, not a handset one."""

    def _line(self, pnp, name, service, status="Error", code="28"):
        return f"{pnp}|{name}|{service}|{status}|{code}"

    def test_a_bound_device_reports_its_service(self):
        text = self._line(r"USB\VID_0E8D&PID_0003\5&1", "MediaTek BootROM",
                          "WINUSB", "OK", "0")
        entry = physical.parse_pnp_binding(text)[0]
        self.assertEqual(entry["mode"], "mtk_brom")
        self.assertEqual(entry["driver"], "winusb")
        self.assertTrue(entry["bound"])
        self.assertFalse(entry["problem"])

    def test_an_unbound_preloader_is_flagged_and_explained(self):
        text = self._line(r"USB\VID_0E8D&PID_2000\5&347706db&0&1",
                          "MT65xx Preloader", "", "Error", "28")
        entry = physical.parse_pnp_binding(text)[0]
        self.assertFalse(entry["bound"])
        self.assertEqual(physical.unbound_modes([entry]), [entry])
        message = physical.driver_message(entry)
        self.assertIn("MT65xx Preloader", message)
        self.assertIn("0e8d", message)
        self.assertIn("WinUSB", message)

    def test_a_stopped_device_is_not_called_bound(self):
        text = self._line(r"USB\VID_0E8D&PID_2000\5&1", "MT65xx Preloader",
                          "WINUSB", "Error", "10")
        entry = physical.parse_pnp_binding(text)[0]
        self.assertFalse(entry["bound"])

    def test_non_boot_mode_devices_are_ignored(self):
        text = self._line(r"USB\VID_046D&PID_C52B\5&1", "Receiver",
                          "usbhid", "OK", "0")
        self.assertEqual(physical.parse_pnp_binding(text), [])

    def test_the_plan_refuses_an_unbound_port_and_names_the_fix(self):
        entry = {"mode": "mtk_preloader", "label": "MediaTek Preloader",
                 "vid": "0e8d", "pid": "2000", "bound": False}
        plan = physical.build_plan({"family": "mediatek"}, [entry],
                                   {"mtkclient": "/bin/mtk"})
        self.assertFalse(plan.viable)
        self.assertEqual(plan.route, "driver")
        self.assertIn("WinUSB", plan.reason)

    def test_a_bound_port_still_builds_the_engine_plan(self):
        entry = {"mode": "mtk_preloader", "label": "MediaTek Preloader",
                 "vid": "0e8d", "pid": "2000", "bound": True}
        plan = physical.build_plan({"family": "mediatek"}, [entry],
                                   {"mtkclient": "/bin/mtk"})
        self.assertTrue(plan.viable)
        self.assertEqual(plan.route, "mtkclient")

    def test_an_empty_probe_is_an_answer_not_a_reason_to_spawn_powershell(self):
        """The preloader lives for seconds; a subprocess per poll misses it."""
        from unittest import mock
        with mock.patch.object(physical.sys, "platform", "win32"), \
                mock.patch.object(physical, "_windows_usb_probe", return_value=[]), \
                mock.patch.object(physical, "_run") as shell:
            self.assertEqual(physical.detect_usb_modes(), [])
        shell.assert_not_called()

    def test_a_broken_probe_falls_back_to_the_powershell_inventory(self):
        from unittest import mock
        text = ("USB\\VID_0E8D&PID_2000\\5&1|MT65xx Preloader||Error|28")
        with mock.patch.object(physical.sys, "platform", "win32"), \
                mock.patch.object(physical, "_windows_usb_probe",
                                  side_effect=OSError("no setupapi")), \
                mock.patch.object(physical, "_run_pnp_inventory", return_value=text):
            modes = physical.detect_usb_modes()
        self.assertEqual([m["mode"] for m in modes], ["mtk_preloader"])


class EngineLockTest(unittest.TestCase):
    """Two engines on one boot-mode port make both fail the handshake."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._original = physical.ENGINE_LOCK
        physical.ENGINE_LOCK = Path(self.tmp.name) / "engine.lock"
        self.addCleanup(lambda: setattr(physical, "ENGINE_LOCK", self._original))

    def test_the_first_caller_takes_the_lock(self):
        self.assertIsNone(physical.acquire_engine_lock())
        self.assertTrue(physical.ENGINE_LOCK.exists())
        physical.release_engine_lock()
        self.assertFalse(physical.ENGINE_LOCK.exists())

    def test_a_live_holder_is_reported(self):
        physical.ENGINE_LOCK.write_text(str(os.getpid()), encoding="utf-8")
        self.assertEqual(physical.acquire_engine_lock(), os.getpid())

    def test_a_stale_lock_is_reclaimed(self):
        physical.ENGINE_LOCK.write_text("999999999", encoding="utf-8")
        self.assertIsNone(physical.acquire_engine_lock())
        self.assertEqual(physical.ENGINE_LOCK.read_text(encoding="utf-8"),
                         str(os.getpid()))
        physical.release_engine_lock()

    def test_a_second_run_is_refused_instead_of_failing_the_handshake(self):
        physical.ENGINE_LOCK.write_text(str(os.getpid()), encoding="utf-8")
        result = physical.run_acquisition(
            physical.Plan(route="mtkclient", viable=True, reason="",
                          argv=["mtk", "rl", "out"]))
        self.assertFalse(result["ok"])
        self.assertIn("already running", result["message"])


class PlanTest(unittest.TestCase):
    def setUp(self):
        self.mediatek = {"family": "mediatek"}
        self.qualcomm = {"family": "qualcomm"}
        self.brom = [{"mode": "mtk_brom"}]
        self.edl = [{"mode": "qualcomm_edl"}]

    def test_mediatek_in_brom_with_mtkclient_is_viable(self):
        plan = physical.build_plan(
            self.mediatek, self.brom, {"mtkclient": "/usr/bin/mtk"})
        self.assertTrue(plan.viable)
        self.assertEqual(plan.route, "mtkclient")
        self.assertEqual(plan.argv[-2:], ["rl", "<evidence>/physical"])

    def test_mediatek_in_brom_without_mtkclient_explains_the_install(self):
        plan = physical.build_plan(self.mediatek, self.brom, {})
        self.assertFalse(plan.viable)
        self.assertIn("pip install mtkclient", plan.reason)

    def test_a_python_module_engine_is_invoked_with_the_interpreter(self):
        plan = physical.build_plan(
            self.mediatek, self.brom, {"mtkclient_module": "mtkclient"})
        self.assertTrue(plan.viable)
        self.assertEqual(plan.argv[1:3], ["-m", "mtkclient"])

    def test_qualcomm_edl_without_a_loader_is_not_viable(self):
        plan = physical.build_plan(
            self.qualcomm, self.edl, {"edl": "/opt/edl/edl.py"})
        self.assertFalse(plan.viable)
        self.assertIn("firehose loader", plan.reason)

    def test_qualcomm_edl_with_loader_builds_the_command(self):
        plan = physical.build_plan(
            self.qualcomm, self.edl, {"edl": "/opt/edl/edl.py"},
            loader="/firmware/prog_firehose.mbn")
        self.assertTrue(plan.viable)
        self.assertEqual(plan.argv[-2:], ["--loader", "/firmware/prog_firehose.mbn"])

    def test_a_mediatek_handset_over_adb_is_armed_before_plugging_in(self):
        # mtkclient waits for the port, so the engine is viable over ADB; the
        # instructions tell the operator to start it first, then plug in.
        plan = physical.build_plan(self.mediatek, [], {"mtkclient": "/bin/mtk"})
        self.assertTrue(plan.viable)
        self.assertIn("Volume Up", plan.reason)
        self.assertIn("waits", plan.reason)

    def test_a_mediatek_handset_without_an_engine_says_how_to_enter_brom(self):
        plan = physical.build_plan(self.mediatek, [], {})
        self.assertFalse(plan.viable)
        self.assertIn("BootROM", plan.reason)

    def test_an_unknown_handset_falls_back_to_logical(self):
        plan = physical.build_plan({"family": "unknown"}, [], {})
        self.assertEqual(plan.route, "logical")
        self.assertFalse(plan.viable)

    def test_a_script_engine_is_run_with_the_configured_python(self):
        plan = physical.build_plan(
            self.mediatek, self.brom,
            {"mtkclient": "/opt/mtk/mtk.py", "python": "/usr/bin/python3"})
        self.assertEqual(plan.argv, ["/usr/bin/python3", "/opt/mtk/mtk.py",
                                     "rl", "<evidence>/physical"])

    def test_a_frozen_build_without_python_refuses_instead_of_crashing(self):
        import sys
        tools = {"mtkclient": "/opt/mtk/mtk.py", "python": ""}
        original = getattr(sys, "frozen", None)
        sys.frozen = True
        try:
            plan = physical.build_plan(self.mediatek, self.brom, tools)
        finally:
            if original is None:
                del sys.frozen
            else:
                sys.frozen = original
        self.assertFalse(plan.viable)
        self.assertIn("DROIDFORENX_PYTHON", plan.reason)


class GptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dfx-phy-"))
        self.image = self.tmp / "flash.bin"
        _gpt_image(self.image, [
            ("boot", 2048, 4095, (0x438, b"\x53\xef")),
            ("userdata", 4096, 8191, (0x400, b"\x10\x20\xf5\xf2")),
        ])

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_gpt_partitions_are_named_with_offsets(self):
        report = physical.validate_image(self.image)
        self.assertEqual(report.table, "gpt")
        self.assertEqual([p.name for p in report.partitions], ["boot", "userdata"])
        boot, userdata = report.partitions
        self.assertEqual(boot.offset_bytes, 2048 * SECTOR)
        self.assertEqual(boot.size_bytes, 2048 * SECTOR)
        self.assertEqual(userdata.offset_bytes, 4096 * SECTOR)

    def test_filesystem_signatures_are_identified(self):
        report = physical.validate_image(self.image)
        self.assertEqual(report.partitions[0].filesystem, "ext2/3/4")
        self.assertEqual(report.partitions[1].filesystem, "f2fs")

    def test_the_image_hash_covers_the_whole_file(self):
        report = physical.validate_image(self.image)
        self.assertEqual(
            report.sha256,
            hashlib.sha256(self.image.read_bytes()).hexdigest())

    def test_partitions_are_sliced_and_hashed(self):
        report = physical.validate_image(self.image)
        out = self.tmp / "parts"
        written = physical.extract_partitions(self.image, report, out)
        self.assertEqual(len(written), 2)
        userdata = [w for w in written if w["name"] == "userdata"][0]
        self.assertEqual(userdata["size_bytes"], 4096 * SECTOR)
        self.assertTrue(Path(userdata["path"]).exists())
        self.assertEqual(
            userdata["sha256"],
            hashlib.sha256(Path(userdata["path"]).read_bytes()).hexdigest())

    def test_a_partition_image_without_a_table_is_reported_as_such(self):
        single = self.tmp / "userdata.img"
        single.write_bytes(b"\x00" * (SECTOR * 4) + b"\x10\x20\xf5\xf2")
        report = physical.validate_image(single)
        self.assertEqual(report.table, "none")
        self.assertTrue(any("partition image" in note for note in report.notes))


class MbrTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dfx-mbr-"))
        self.image = self.tmp / "mbr.bin"
        data = bytearray(SECTOR * 4096)
        raw = bytearray(16)
        raw[4] = 0x83
        struct.pack_into("<II", raw, 8, 2048, 2048)
        data[446:462] = raw
        data[510:512] = b"\x55\xaa"
        data[2048 * SECTOR + 0x438:2048 * SECTOR + 0x43a] = b"\x53\xef"
        self.image.write_bytes(bytes(data))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_mbr_entry_is_decoded(self):
        report = physical.validate_image(self.image)
        self.assertEqual(report.table, "mbr")
        self.assertEqual(len(report.partitions), 1)
        self.assertEqual(report.partitions[0].offset_bytes, 2048 * SECTOR)
        self.assertEqual(report.partitions[0].filesystem, "ext2/3/4")


class SparseTest(unittest.TestCase):
    def test_a_sparse_header_is_recognised_not_treated_as_gpt(self):
        tmp = Path(tempfile.mkdtemp(prefix="dfx-sparse-"))
        try:
            image = tmp / "system.img"
            header = struct.pack(
                "<IHHHHIII", physical.SPARSE_MAGIC, 1, 0, 28, 12, 4096, 10, 3)
            image.write_bytes(header + b"\x00" * 64)
            report = physical.validate_image(image)
            self.assertEqual(report.container, "android-sparse")
            self.assertEqual(report.table, "sparse")
            self.assertTrue(any("40,960 bytes expanded" in n for n in report.notes))
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class ImageSetTest(unittest.TestCase):
    def test_a_folder_of_partition_images_is_inventoried(self):
        tmp = Path(tempfile.mkdtemp(prefix="dfx-set-"))
        try:
            boot = tmp / "boot.img"
            boot.write_bytes(b"\x00" * 0x438 + b"\x53\xef")
            userdata = tmp / "userdata.img"
            userdata.write_bytes(b"\x00" * 0x400 + b"\x10\x20\xf5\xf2")
            report = physical.image_set_report(tmp)
            self.assertEqual(report["count"], 2)
            names = {entry["name"]: entry for entry in report["files"]}
            self.assertEqual(names["boot.img"]["filesystem"], "ext2/3/4")
            self.assertEqual(names["userdata.img"]["filesystem"], "f2fs")
            self.assertEqual(
                names["boot.img"]["sha256"],
                hashlib.sha256(boot.read_bytes()).hexdigest())
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class DetectTest(unittest.TestCase):
    def test_detect_without_a_device_still_returns_a_plan(self):
        result = physical.detect("")
        self.assertIn("plan", result)
        self.assertIn("reason", result["plan"])
        self.assertIn("ceiling", result)

    def test_a_device_profile_reads_props_in_one_round_trip(self):
        captured = {}

        def fake_shell(serial, command, timeout=25):
            captured["command"] = command
            return True, ("[ro.soc.manufacturer]: [MediaTek]\n"
                          "[ro.board.platform]: [mt6768]\n"
                          "[ro.crypto.state]: [encrypted]\n"
                          "[ro.crypto.type]: [file]\n"
                          "---\nProcessor : AArch64 Processor")

        import desktop.core.adb_manager as adb
        original = adb.adb_shell
        adb.adb_shell = fake_shell
        try:
            profile = physical.read_device_profile("ABC123")
        finally:
            adb.adb_shell = original

        self.assertTrue(profile["ok"])
        self.assertEqual(profile["soc"]["family"], "mediatek")
        self.assertEqual(profile["encryption"]["type"], "file")
        self.assertIn("getprop", captured["command"])
        self.assertIn("cpuinfo", captured["command"])


if __name__ == "__main__":
    unittest.main()
