import unittest

from desktop.core.update_checker import is_newer_version, parse_latest_release


def release(tag="v1.0.40"):
    return {
        "tag_name": tag,
        "name": f"DroidForenX {tag}",
        "body": "Update checker and reliability improvements.",
        "html_url": f"https://github.com/tecxe/DroidForenX/releases/tag/{tag}",
        "assets": [{
            "name": "DroidForenX_Installer.exe",
            "state": "uploaded",
            "browser_download_url": (
                f"https://github.com/tecxe/DroidForenX/releases/download/"
                f"{tag}/DroidForenX_Installer.exe"),
        }],
    }


class VersionComparisonTest(unittest.TestCase):
    def test_greater_numeric_version_is_newer(self):
        self.assertTrue(is_newer_version("v1.0.40", "1.0.39"))
        self.assertTrue(is_newer_version("1.10.0", "1.9.9"))

    def test_equal_versions_with_trailing_zeroes_are_equal(self):
        self.assertFalse(is_newer_version("v1.0.0", "1.0"))
        self.assertFalse(is_newer_version("1.0.39", "1.0.39"))

    def test_older_version_is_not_newer(self):
        self.assertFalse(is_newer_version("1.0.38", "1.0.39"))

    def test_invalid_version_is_rejected(self):
        with self.assertRaises(ValueError):
            is_newer_version("v1.0.40-rc1", "1.0.39")


class LatestReleaseParsingTest(unittest.TestCase):
    def test_new_release_with_installer_is_available(self):
        update = parse_latest_release(release(), "1.0.39")

        self.assertEqual(update.version, "1.0.40")
        self.assertEqual(update.title, "DroidForenX v1.0.40")
        self.assertIn("update checker", update.notes.lower())
        self.assertTrue(update.installer_url.endswith("DroidForenX_Installer.exe"))

    def test_current_release_does_not_trigger_an_update(self):
        self.assertIsNone(parse_latest_release(release("v1.0.39"), "1.0.39"))

    def test_missing_installer_is_rejected(self):
        payload = release()
        payload["assets"] = []
        with self.assertRaisesRegex(ValueError, "does not include"):
            parse_latest_release(payload, "1.0.39")

    def test_external_installer_url_is_rejected(self):
        payload = release()
        payload["assets"][0]["browser_download_url"] = (
            "https://example.com/DroidForenX_Installer.exe")
        with self.assertRaisesRegex(ValueError, "installer URL"):
            parse_latest_release(payload, "1.0.39")

    def test_external_release_page_is_rejected(self):
        payload = release()
        payload["html_url"] = "https://example.com/releases/v1.0.40"
        with self.assertRaisesRegex(ValueError, "release page URL"):
            parse_latest_release(payload, "1.0.39")


if __name__ == "__main__":
    unittest.main()
