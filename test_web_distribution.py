import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from distribution.build_student_web import build_archive, validate_config


class StudentWebDistributionTests(unittest.TestCase):
    def test_public_config_is_valid_and_bundle_contains_only_static_surface(self):
        with tempfile.TemporaryDirectory() as directory:
            archive, checksum = build_archive(Path(directory))
            self.assertTrue(archive.is_file())
            self.assertTrue(checksum.is_file())
            self.assertLess(archive.stat().st_size, 5 * 1024 * 1024)
            with zipfile.ZipFile(archive) as bundle:
                self.assertEqual(set(bundle.namelist()), {"index.html", "config.json", "README.md"})
                config = json.loads(bundle.read("config.json"))
                index = bundle.read("index.html").decode("utf-8")
            self.assertNotIn("127.0.0.1", index)
            self.assertNotIn("localhost", index)
            self.assertNotIn("/ws?token=", index)
            self.assertIn('sandbox="allow-scripts allow-same-origin allow-forms allow-downloads"', index)
            self.assertEqual(config["notebook_url"], "https://fl2744.github.io/jupyterlite/lab/index.html")
            self.assertTrue(any(item["id"] == "jupyterlite" and item["enabled"] for item in config["applications"]))

    def test_rejects_credentials_non_https_and_missing_notebook_target(self):
        with self.assertRaises(ValueError):
            validate_config({"title": "x", "api_key": "secret", "applications": []})
        with self.assertRaises(ValueError):
            validate_config({
                "title": "x", "notebook_url": "http://example.org/lab",
                "applications": [{"id": "jupyterlite", "view": "notebook"}],
            })
        with self.assertRaises(ValueError):
            validate_config({
                "title": "x", "notebook_url": "",
                "applications": [{"id": "jupyterlite", "view": "notebook"}],
            })


if __name__ == "__main__":
    unittest.main()
