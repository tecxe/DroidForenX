"""Tests for the devices app: adapters, normalization, and the Device model."""

from django.test import TestCase

from devices.adapter_utils import (
    normalize_manufacturer,
    parse_property_line,
    props_from_lines,
    yes_no_to_bool,
    try_int,
)
from devices.registry import resolve_adapter, known_manufacturers
from devices.adb_adapter import AdbAdapter
from devices.generic_android_adapter import GenericAndroidAdapter
from devices.samsung_adapter import SamsungAdapter
from devices.google_adapter import GoogleAdapter
from devices.models import Device


class ManufacturerNormalizationTest(TestCase):
    def test_known_manufacturers(self):
        cases = {
            "samsung": "samsung",
            "Samsung": "samsung",
            "Google": "google",
            "Xiaomi": "xiaomi",
            "OnePlus": "oneplus",
            "HUAWEI": "huawei",
            "motorola": "motorola",
        }
        for raw, expected in cases.items():
            self.assertEqual(normalize_manufacturer(raw), expected, f"for {raw}")

    def test_unknown_maps_to_generic(self):
        self.assertEqual(normalize_manufacturer("AcmeCorp"), "generic")
        self.assertEqual(normalize_manufacturer(""), "generic")
        self.assertEqual(normalize_manufacturer(None), "generic")

    def test_brand_aliases(self):
        self.assertEqual(normalize_manufacturer("redmi"), "redmi")
        self.assertEqual(normalize_manufacturer("poco"), "xiaomi")
        self.assertEqual(normalize_manufacturer("honor"), "huawei")


class PropertyParsingTest(TestCase):
    def test_parse_property_line(self):
        self.assertEqual(parse_property_line("ro.product.model=SM-G991B"), ("ro.product.model", "SM-G991B"))
        self.assertIsNone(parse_property_line("not a property"))
        self.assertIsNone(parse_property_line(""))

    def test_props_from_lines(self):
        lines = ["a=1", "b=two", "  c = three  ", "garbage"]
        props = props_from_lines(lines)
        self.assertEqual(props, {"a": "1", "b": "two", "c": "three"})

    def test_yes_no_bool(self):
        self.assertTrue(yes_no_to_bool("true"))
        self.assertTrue(yes_no_to_bool("1"))
        self.assertFalse(yes_no_to_bool("false"))
        self.assertIsNone(yes_no_to_bool("maybe"))

    def test_try_int(self):
        self.assertEqual(try_int("31"), 31)
        self.assertEqual(try_int("35.0"), 35)
        self.assertIsNone(try_int("abc"))


class AdapterRegistryTest(TestCase):
    def test_known_manufacturers_include_key_brands(self):
        man = known_manufacturers()
        self.assertIn("samsung", man)
        self.assertIn("google", man)

    def test_resolve_specialised(self):
        self.assertIsInstance(resolve_adapter("samsung"), SamsungAdapter)
        self.assertIsInstance(resolve_adapter("google"), GoogleAdapter)

    def test_resolve_generic_fallback(self):
        self.assertIsInstance(resolve_adapter("acme"), GenericAndroidAdapter)
        self.assertIsInstance(resolve_adapter(""), GenericAndroidAdapter)
        self.assertIsInstance(resolve_adapter("samsung"), (SamsungAdapter,))
        self.assertIsInstance(resolve_adapter("nokia"), GenericAndroidAdapter)

    def test_generic_is_adb_subclass(self):
        self.assertTrue(issubclass(GenericAndroidAdapter, AdbAdapter))
        self.assertTrue(issubclass(SamsungAdapter, AdbAdapter))


class DeviceModelTest(TestCase):
    def setUp(self):
        self.device = Device.objects.create(
            serial_number="R58M2ABC123",
            manufacturer="samsung",
            manufacturer_raw="samsung",
            model="SM-G991B",
            android_version="12",
            api_level=31,
        )

    def test_display_name_includes_version(self):
        name = self.device.display_name
        self.assertIn("API 31", name)
        self.assertIn("SM-G991B", name)

    def test_manufacturer_choice_display(self):
        self.assertEqual(self.device.get_manufacturer_display(), "Samsung")

    def test_unique_serial_enforced(self):
        import django.db.utils
        with self.assertRaises(django.db.utils.IntegrityError):
            Device.objects.create(serial_number="R58M2ABC123")

    def test_identification_json_default(self):
        self.assertEqual(self.device.identification, {})
