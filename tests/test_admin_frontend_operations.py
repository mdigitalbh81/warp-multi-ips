import base64
import http.client
import importlib.util
import json
import os
import re
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def load_server_module(tmpdir, env_overrides=None):
    config_file = tmpdir / "admin-config.json"
    env_file = tmpdir / "warp-admin-env"
    creds_file = tmpdir / "admin-credentials.json"
    env = {
        "WARP_DATA_DIR": str(tmpdir),
        "ADMIN_CONFIG_FILE": str(config_file),
        "ADMIN_CREDENTIALS_FILE": str(creds_file),
        "WARP_ENV_FILE": str(env_file),
        "ADMIN_USER": "admin",
        "ADMIN_PASSWORD": "AdminPassword123!",
    }
    if env_overrides:
        env.update(env_overrides)

    old_env = os.environ.copy()
    os.environ.update(env)
    try:
        spec = importlib.util.spec_from_file_location(
            f"admin_server_{tmpdir.name}_{time.time_ns()}",
            ROOT / "admin" / "server.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        os.environ.clear()
        os.environ.update(old_env)


class AdminFrontendOperationsTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.tmp = Path(self.td.name)
        self.config_file = self.tmp / "admin-config.json"
        self.creds_file = self.tmp / "admin-credentials.json"
        self.env_file = self.tmp / "warp-admin-env"
        self.server_mod = load_server_module(self.tmp)

        self.server_mod.CONFIG_FILE = self.config_file
        self.server_mod.CREDENTIALS_FILE = self.creds_file
        self.server_mod.ENV_FILE = self.env_file
        self.server_mod.WARP_DATA_DIR = self.tmp

        # Initialize config & credentials
        self.config_file.write_text(
            json.dumps(
                {
                    "instances": 2,
                    "proxy_mode": "dedicated",
                    "proxy_base_port": 2080,
                    "proxy_host_omniroute": "omniroute_warp-proxy",
                    "warp_engine": "wireproxy",
                    "lightweight_egress_family": "ipv6",
                    "lightweight_require_unique_egress": True,
                }
            )
        )
        self.server_mod.ensure_admin_credentials()

        self.port = get_free_port()
        self.httpd = self.server_mod.ThreadingHTTPServer(("127.0.0.1", self.port), self.server_mod.Handler)
        self.server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.server_thread.start()

        self.auth_header = "Basic " + base64.b64encode(b"admin:AdminPassword123!").decode("ascii")

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.td.cleanup()

    def request(self, method, path, headers=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        req_headers = headers or {}
        if body is not None and "Content-Type" not in req_headers:
            req_headers["Content-Type"] = "application/json"
        conn.request(method, path, body=body, headers=req_headers)
        res = conn.getresponse()
        data = res.read()
        conn.close()
        return res.status, dict(res.getheaders()), data

    def test_static_index_served_with_auth(self):
        # Without auth: 401
        status, headers, _ = self.request("GET", "/")
        self.assertEqual(status, 401)
        self.assertIn("WWW-Authenticate", headers)

        # With auth: 200
        status, headers, data = self.request("GET", "/", {"Authorization": self.auth_header})
        self.assertEqual(status, 200)
        html = data.decode("utf-8")
        self.assertIn("<title>WARP Multi IPs</title>", html)
        self.assertIn("OmniRoute Export", html)
        self.assertIn("Diagnostics & Status", html)
        self.assertIn("warp_engine", html)
        self.assertIn("proxy_host_omniroute", html)

    def test_static_assets_app_js_and_style_css_served(self):
        status, headers, data = self.request("GET", "/app.js", {"Authorization": self.auth_header})
        self.assertEqual(status, 200)
        js = data.decode("utf-8")
        self.assertIn("buildInstanceRow", js)
        self.assertIn("Reprovision", js)
        self.assertIn("updateOmniRouteSection", js)

        status, headers, data = self.request("GET", "/style.css", {"Authorization": self.auth_header})
        self.assertEqual(status, 200)
        css = data.decode("utf-8")
        self.assertIn(".omniroute-section", css)
        self.assertIn(".diagnostics-section", css)

    def test_health_endpoint_no_auth_required(self):
        status, headers, data = self.request("GET", "/health")
        self.assertEqual(status, 200)
        resp = json.loads(data.decode("utf-8"))
        self.assertTrue(resp.get("ok"))

    def test_api_status_and_provisioning_non_blocking(self):
        # Write operation state
        op_file = Path("/tmp/operation-state.json")
        op_file.write_text(json.dumps({
            "status": "running",
            "message": "Starting instance 2/2...",
            "current": 2,
            "total": 2,
            "timestamp": "2026-09-27T12:00:00Z"
        }))

        status, _, data = self.request("GET", "/api/status", {"Authorization": self.auth_header})
        self.assertEqual(status, 200)
        resp = json.loads(data.decode("utf-8"))
        self.assertEqual(resp["engine"], "wireproxy")
        self.assertEqual(resp["configured_instances"], 2)
        self.assertEqual(resp["proxy_mode"], "dedicated")
        self.assertEqual(resp["proxy_host_omniroute"], "omniroute_warp-proxy")
        self.assertIsNotNone(resp.get("operation"))
        self.assertEqual(resp["operation"]["status"], "running")

    def test_api_instances_structure(self):
        status, _, data = self.request("GET", "/api/instances", {"Authorization": self.auth_header})
        self.assertEqual(status, 200)
        instances = json.loads(data.decode("utf-8"))
        self.assertEqual(len(instances), 2)
        inst0 = instances[0]
        self.assertIn("instance", inst0)
        self.assertIn("engine", inst0)
        self.assertIn("proxy_port", inst0)
        self.assertIn("internal_port", inst0)
        self.assertIn("proxy_host_omniroute", inst0)
        self.assertIn("egress_ip", inst0)
        self.assertIn("previous_ipv6_egress", inst0)
        self.assertIn("egress_unique", inst0)
        self.assertIn("country_code", inst0)
        self.assertIn("colo", inst0)
        self.assertIn("note", inst0)
        self.assertIn("warp", inst0)
        self.assertIn("health", inst0)

    def test_api_omniroute_export_format(self):
        status, _, data = self.request("GET", "/api/export/omniroute", {"Authorization": self.auth_header})
        self.assertEqual(status, 200)
        resp = json.loads(data.decode("utf-8"))
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["host"], "omniroute_warp-proxy")
        self.assertEqual(resp["port_range"], "2080-2081")
        self.assertEqual(resp["count"], 2)
        self.assertIn("WARP-01 | omniroute_warp-proxy | 2080", resp["text"])
        self.assertIn("WARP-02 | omniroute_warp-proxy | 2081", resp["text"])

    def test_no_secret_leakage(self):
        status, _, data = self.request("GET", "/api/config", {"Authorization": self.auth_header})
        self.assertEqual(status, 200)
        cfg = json.loads(data.decode("utf-8"))
        self.assertNotIn("proxy_password", cfg)
        self.assertNotIn("private_key", str(cfg))
        self.assertNotIn("token", str(cfg))

        status, _, data = self.request("GET", "/api/instances", {"Authorization": self.auth_header})
        instances = json.loads(data.decode("utf-8"))
        for item in instances:
            self.assertNotIn("private_key", str(item))
            self.assertNotIn("password", str(item))
            self.assertNotIn("token", str(item))

    def test_reprovision_wireproxy_vs_official_endpoints(self):
        # Official engine should reject reprovision with 400
        self.config_file.write_text(json.dumps({"instances": 2, "warp_engine": "official"}))
        status, _, data = self.request("POST", "/api/instances/1/reprovision", {"Authorization": self.auth_header}, body="{}")
        self.assertEqual(status, 400)
        resp = json.loads(data.decode("utf-8"))
        self.assertIn("only supported for wireproxy", resp["error"].lower())

        # Wireproxy engine endpoint routes to manual_reprovision_instance
        self.config_file.write_text(json.dumps({"instances": 2, "warp_engine": "wireproxy"}))
        original_reprov = self.server_mod.manual_reprovision_instance
        self.server_mod.manual_reprovision_instance = lambda idx: ({"ok": True, "message": f"instance {idx+1} reprovisioned"}, 200)
        try:
            status, _, data = self.request("POST", "/api/instances/1/reprovision", {"Authorization": self.auth_header}, body="{}")
            self.assertEqual(status, 200)
            resp = json.loads(data.decode("utf-8"))
            self.assertTrue(resp.get("ok"))
        finally:
            self.server_mod.manual_reprovision_instance = original_reprov


if __name__ == "__main__":
    unittest.main()

    def test_settings_persistence_across_restart(self):
        # Update config via POST /api/config
        payload = {
            "instances": 3,
            "proxy_mode": "dedicated",
            "proxy_base_port": 2080,
            "proxy_host_omniroute": "omniroute_custom_host",
            "warp_engine": "wireproxy",
            "lightweight_egress_family": "ipv6",
            "lightweight_require_unique_egress": True,
            "proxy_max_rps": 60,
            "warp_connect_timeout": 40,
            "auto_refresh_interval": 90,
            "proxy_auth_enabled": False,
            "proxy_user": "",
        }
        # Mock reload_gost and refresh_all to avoid host missing binaries
        orig_reload = self.server_mod.reload_gost
        orig_refresh = self.server_mod.refresh_all
        orig_start = self.server_mod.start_instance
        orig_wait = self.server_mod.wait_internal
        self.server_mod.reload_gost = lambda cfg: None
        self.server_mod.refresh_all = lambda force=False: []
        self.server_mod.start_instance = lambda idx, cfg: None
        self.server_mod.wait_internal = lambda idx, timeout: True
        try:
            status, _, data = self.request(
                "POST",
                "/api/config",
                {"Authorization": self.auth_header},
                body=json.dumps(payload),
            )
            self.assertEqual(status, 200)
            resp = json.loads(data.decode("utf-8"))
            self.assertTrue(resp["ok"])
            self.assertEqual(resp["config"]["instances"], 3)
            self.assertEqual(resp["config"]["proxy_host_omniroute"], "omniroute_custom_host")
            self.assertEqual(resp["config"]["warp_engine"], "wireproxy")

            # Check persisted file on disk
            persisted = json.loads(self.config_file.read_text())
            self.assertEqual(persisted["instances"], 3)
            self.assertEqual(persisted["proxy_host_omniroute"], "omniroute_custom_host")
            self.assertEqual(persisted["warp_engine"], "wireproxy")

            # Spin up a new server instance from same persisted files (simulating container restart)
            new_server_mod = load_server_module(self.tmp)
            new_cfg = new_server_mod.get_config()
            self.assertEqual(new_cfg["instances"], 3)
            self.assertEqual(new_cfg["proxy_host_omniroute"], "omniroute_custom_host")
            self.assertEqual(new_cfg["warp_engine"], "wireproxy")
            self.assertEqual(new_cfg["proxy_max_rps"], 60)
            self.assertEqual(new_cfg["warp_connect_timeout"], 40)
            self.assertEqual(new_cfg["auto_refresh_interval"], 90)
        finally:
            self.server_mod.reload_gost = orig_reload
            self.server_mod.refresh_all = orig_refresh
            self.server_mod.start_instance = orig_start
            self.server_mod.wait_internal = orig_wait

    def test_frontend_required_elements_present_in_static_files(self):
        index_html = (ROOT / "admin/static/index.html").read_text()
        app_js = (ROOT / "admin/static/app.js").read_text()
        style_css = (ROOT / "admin/static/style.css").read_text()

        # Engine selector in settings form
        self.assertIn('<select name="warp_engine">', index_html)
        self.assertIn('<option value="official">', index_html)
        self.assertIn('<option value="wireproxy">', index_html)

        # OmniRoute Host in settings form
        self.assertIn('<input name="proxy_host_omniroute"', index_html)

        # OmniRoute Export UI section & buttons
        self.assertIn('id="omnirouteExportSection"', index_html)
        self.assertIn('id="sectionExportCopy"', index_html)
        self.assertIn('id="sectionExportDownload"', index_html)
        self.assertIn('id="sectionExportText"', index_html)

        # 12 columns in table headers
        headers = [
            "Instance", "Engine", "OmniRoute Proxy", "Current Egress IP",
            "Previous IPv6", "Unique", "Country", "Colo", "Notes", "WARP",
            "Health", "Actions"
        ]
        for h in headers:
            self.assertIn(h, index_html)

        # JavaScript functions and bindings
        self.assertIn("updateOmniRouteSection", app_js)
        self.assertIn("updateDiagnostics", app_js)
        self.assertIn("buildProxyParts", app_js)
        self.assertIn("reprovBtn", app_js)
        self.assertIn("sectionExportCopy", app_js)
        self.assertIn("sectionExportDownload", app_js)

        # CSS classes
        self.assertIn(".omniroute-section", style_css)
        self.assertIn(".omniroute-textarea", style_css)
        self.assertIn(".diagnostics-section", style_css)
        self.assertIn(".diag-grid", style_css)
