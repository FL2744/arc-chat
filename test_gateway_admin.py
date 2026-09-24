import json
from pathlib import Path
import unittest

from hosted_gateway.admin import validate_project_spec
from identifiers import is_canonical_id


class GatewayAdminSpecTests(unittest.TestCase):
    def test_operator_spec_generates_opaque_project_and_application_ids(self):
        spec = validate_project_spec({
            "slug": "fl2744",
            "name": "FL 2744",
            "kind": "course",
            "audience": "course",
            "allowed_providers": ["browser", "arc"],
            "course_groups": [{"name": "FL2744-students", "role": "student"}],
            "applications": [{
                "key": "notebook", "name": "Course notebook", "application_type": "browser",
                "provider": "browser", "audience": "course",
            }],
        })
        self.assertTrue(is_canonical_id("project", spec["project"].id))
        self.assertTrue(is_canonical_id("application", spec["applications"][0]["manifest"].id))
        self.assertEqual(spec["course_groups"][0]["role"], "student")

    def test_operator_spec_rejects_unmapped_roles_unsupported_providers_and_secret_metadata(self):
        base = {"slug": "fl2744", "name": "FL 2744"}
        with self.assertRaisesRegex(ValueError, "role"):
            validate_project_spec({**base, "course_groups": [{"name": "students", "role": "owner"}]})
        with self.assertRaisesRegex(ValueError, "supported provider"):
            validate_project_spec({**base, "allowed_providers": ["shell"]})
        with self.assertRaisesRegex(ValueError, "credential-like"):
            validate_project_spec({**base, "metadata": {"client_secret": "must-not-persist"}})

    def test_environment_project_templates_cannot_be_applied_before_classification(self):
        root = Path(__file__).parent / "deploy" / "kubernetes" / "environments"
        for environment in ("dvlp", "pprd", "prod"):
            with self.subTest(environment=environment):
                spec = json.loads((root / environment / "project-mapping.example.json").read_text(encoding="utf-8"))
                self.assertEqual(spec["course_groups"], [])
                with self.assertRaisesRegex(ValueError, "data classification"):
                    validate_project_spec(spec)


if __name__ == "__main__":
    unittest.main()
