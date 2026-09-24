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
            self.assertNotIn("__GATEWAY_ORIGIN__", index)
            self.assertIn("connect-src 'self' 'none'", index)
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
        with self.assertRaises(ValueError):
            validate_config({
                "version": 1, "title": "x", "gateway_url": "https://api.example.edu/api/v1",
                "notebook_url": "", "applications": [],
            })
        with self.assertRaises(ValueError):
            validate_config({
                "version": 1, "title": "x", "gateway_url": "",
                "notebook_url": "https://notebooks.example.edu/lab/index.html?token=bad", "applications": [],
            })
        with self.assertRaisesRegex(ValueError, "invalid port"):
            validate_config({
                "version": 1, "title": "x", "gateway_url": "",
                "notebook_url": "https://notebooks.example.edu:bad/lab", "applications": [],
            })

    def test_gateway_origin_is_injected_into_the_static_connect_policy(self):
        value = validate_config({
            "version": 1,
            "title": "x",
            "gateway_url": "https://api.example.edu",
            "notebook_url": "",
            "applications": [],
        })
        with open("web/student/index.html", encoding="utf-8") as source:
            index = source.read()
        from distribution.build_student_web import _index_with_gateway_csp
        built = _index_with_gateway_csp(index, value["gateway_url"])
        self.assertIn("connect-src 'self' https://api.example.edu", built)
        self.assertNotIn("__GATEWAY_ORIGIN__", built)

    def test_gateway_origin_with_default_port_is_normalized(self):
        value = validate_config({
            "version": 1,
            "title": "x",
            "gateway_url": "https://API.example.edu:443/",
            "notebook_url": "",
            "applications": [],
        })
        self.assertEqual(value["gateway_url"], "https://api.example.edu")


if __name__ == "__main__":
    unittest.main()
