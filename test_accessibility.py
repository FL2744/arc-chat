import unittest
from html.parser import HTMLParser
from pathlib import Path


HTML = Path(__file__).with_name("arc-chat.html")


class FormAudit(HTMLParser):
    def __init__(self):
        super().__init__()
        self.label_depth = 0
        self.labels_for = set()
        self.controls = []
        self.button_stack = []
        self.buttons = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "label":
            self.label_depth += 1
            if values.get("for"):
                self.labels_for.add(values["for"])
        elif tag in {"input", "select", "textarea"}:
            self.controls.append({
                "tag": tag,
                "id": values.get("id", ""),
                "type": values.get("type", ""),
                "wrapped": self.label_depth > 0,
                "aria": values.get("aria-label") or values.get("aria-labelledby") or "",
            })
        elif tag == "button":
            self.button_stack.append([])

    def handle_endtag(self, tag):
        if tag == "label" and self.label_depth:
            self.label_depth -= 1
        elif tag == "button" and self.button_stack:
            self.buttons.append("".join(self.button_stack.pop()).strip())

    def handle_data(self, data):
        if self.button_stack:
            self.button_stack[-1].append(data)


class AccessibilitySmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = HTML.read_text(encoding="utf-8")
        cls.audit = FormAudit()
        cls.audit.feed(cls.source)

    def test_visible_form_controls_have_accessible_labels(self):
        unlabeled = []
        for control in self.audit.controls:
            if control["type"] == "hidden":
                continue
            if control["wrapped"] or control["aria"] or control["id"] in self.audit.labels_for:
                continue
            unlabeled.append(f"{control['tag']}#{control['id']}")
        self.assertEqual(unlabeled, [])

    def test_static_buttons_have_names(self):
        self.assertTrue(self.audit.buttons)
        self.assertNotIn("", self.audit.buttons)

    def test_keyboard_focus_and_skip_link_are_present(self):
        self.assertIn(":focus-visible", self.source)
        self.assertIn('class="skip-link" href="#main-content"', self.source)
        self.assertIn('id="main-content" tabindex="-1"', self.source)

    def test_dynamic_media_is_described_and_sandboxed(self):
        self.assertIn("img.alt='Python plot'", self.source)
        self.assertIn("f.title='Sandboxed Python HTML output'", self.source)
        self.assertIn("f.setAttribute('sandbox','')", self.source)

    def test_live_regions_and_encoding_are_clean(self):
        self.assertIn('id="status" role="status" aria-live="polite"', self.source)
        self.assertIn('id="log" aria-live="polite"', self.source)
        self.assertNotIn("\ufffd", self.source)
        controls = {ord(ch) for ch in self.source if ord(ch) < 32 and ch not in "\n\r\t"}
        self.assertEqual(controls, set())
        self.assertNotIn(" ? ", self.source)


if __name__ == "__main__":
    unittest.main()
