import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AdminStaticUiTests(unittest.TestCase):
    def setUp(self):
        self.app_js = (ROOT / "admin/static/app.js").read_text()
        self.style_css = (ROOT / "admin/static/style.css").read_text()

    def test_desktop_has_separate_copy_host_and_copy_port_controls(self):
        self.assertIn("buildProxyPart(\"Host\", host, \"Copy host\")", self.app_js)
        self.assertIn("buildProxyPart(\"Port\", port, \"Copy port\")", self.app_js)
        self.assertIn("Copy Host", self.app_js)
        self.assertIn("Copy Port", self.app_js)
        self.assertNotIn("Copy proxy address", self.app_js)

    def test_mobile_card_has_separate_host_and_port_rows(self):
        self.assertIn("OmniRoute Host", self.app_js)
        self.assertIn("OmniRoute Port", self.app_js)
        self.assertRegex(self.app_js, re.compile(r"copy:\s*host\s*\|\|\s*null"))
        self.assertRegex(self.app_js, re.compile(r"copy:\s*port\s*\|\|\s*null"))

    def test_mobile_and_proxy_styles_avoid_horizontal_overflow(self):
        self.assertIn("overflow-wrap: anywhere;", self.style_css)
        self.assertRegex(self.style_css, re.compile(r"\.mobile-card-value\s*\{[^}]*min-width:\s*0;", re.S))
        self.assertRegex(self.style_css, re.compile(r"\.mobile-card-value\s*\{[^}]*flex:\s*1;", re.S))


if __name__ == "__main__":
    unittest.main()
