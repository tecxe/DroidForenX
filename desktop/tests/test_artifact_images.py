"""Tests for the image sample the report's plate appendix is built from.

The selection is the part a reader can challenge: why *these* photographs and
not others. So the rules are pinned here -- fixed category order, no repeats,
a spread across the set, and a limit that is always respected -- without
touching a real evidence store or a connected handset.

Run with:  python -m unittest desktop.tests.test_artifact_images -v
"""

import unittest
from pathlib import Path

from desktop.report import artifact_images as images
from desktop.report import investigation_report as report


def item(name, category, size=100_000):
    return {"path": Path(name), "name": name, "source": name,
            "category": category, "size": size, "sha256": ""}


class CategoryTest(unittest.TestCase):
    def test_camera_roll_is_recognised_under_a_mounted_sdcard(self):
        self.assertEqual(
            images._category_of("filesystem/sdcard_dcim/DCIM/Camera/IMG_1.jpg"), "Camera")

    def test_screenshots_are_not_confused_with_camera_output(self):
        self.assertEqual(
            images._category_of("filesystem/sdcard_dcim/Pictures/Screenshots/s.png"),
            "Screenshots")

    def test_application_media_is_its_own_bucket(self):
        self.assertEqual(
            images._category_of("filesystem/Android/media/com.whatsapp/WhatsApp/x.jpg"),
            "Application media")

    def test_anything_else_still_gets_a_bucket(self):
        self.assertEqual(images._category_of("deleted_files/odd.jpg"), "Other")


class SelectionTest(unittest.TestCase):
    def test_every_category_is_represented_before_any_is_repeated(self):
        candidates = (
            [item(f"cam{i}.jpg", "Camera") for i in range(20)]
            + [item(f"shot{i}.jpg", "Screenshots") for i in range(5)]
            + [item(f"app{i}.jpg", "Application media") for i in range(50)]
        )
        picked = images._select(candidates, 6)
        self.assertEqual(len(picked), 6)
        self.assertEqual([entry["category"] for entry in picked[:3]],
                         ["Camera", "Screenshots", "Application media"])

    def test_the_limit_is_respected(self):
        candidates = [item(f"cam{i}.jpg", "Camera") for i in range(100)]
        self.assertEqual(len(images._select(candidates, 18)), 18)

    def test_no_image_is_picked_twice(self):
        candidates = [item(f"cam{i}.jpg", "Camera") for i in range(9)]
        picked = images._select(candidates, 5)
        self.assertEqual(len({entry["name"] for entry in picked}), len(picked))

    def test_a_smaller_set_is_returned_whole(self):
        candidates = [item("a.jpg", "Camera"), item("b.jpg", "Screenshots")]
        self.assertEqual(len(images._select(candidates, 18)), 2)

    def test_nothing_to_choose_from_is_not_an_error(self):
        self.assertEqual(images._select([], 18), [])


class SpreadTest(unittest.TestCase):
    def test_spread_returns_everything_exactly_once(self):
        items = [item(f"i{i}.jpg", "Camera") for i in range(40)]
        ordered = images._spread(items)
        self.assertEqual(len(ordered), 40)
        self.assertEqual({entry["name"] for entry in ordered},
                         {entry["name"] for entry in items})

    def test_spread_does_not_start_with_the_first_dozen_in_a_row(self):
        items = [item(f"i{i:03d}.jpg", "Camera") for i in range(40)]
        head = [entry["name"] for entry in images._spread(items)[:4]]
        self.assertNotEqual(head, ["i000.jpg", "i001.jpg", "i002.jpg", "i003.jpg"])


class CollectTest(unittest.TestCase):
    def test_a_case_with_no_session_and_no_store_returns_nothing(self):
        original = images._acquisition_dirs
        images._acquisition_dirs = lambda serial: []
        try:
            self.assertEqual(images.collect(None, None, limit=5), [])
        finally:
            images._acquisition_dirs = original


class SizeLabelTest(unittest.TestCase):
    def test_sizes_read_the_way_people_write_them(self):
        self.assertEqual(images.SMALL_BUDGET, 60)
        self.assertEqual(report._bytes_label(0), "0 B")
        self.assertEqual(report._bytes_label(1536), "1.5 KB")
        self.assertEqual(report._bytes_label(2_500_000), "2.4 MB")
        self.assertEqual(report._bytes_label(None), "size not recorded")


if __name__ == "__main__":
    unittest.main()
