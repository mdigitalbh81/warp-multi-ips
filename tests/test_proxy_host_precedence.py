import importlib.util
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path


def load_server(tmpdir, proxy_host=None, legacy_proxy_host=None):
    env = {
        "ADMIN_CONFIG_FILE": str(tmpdir / "admin-config.json"),
        "ADMIN_CREDENTIALS_FILE": str(tmpdir / "admin-credentials.json"),
        "WARP_ENV_FILE": str(tmpdir / "warp-env"),
    }
    if proxy_host is not None:
        env["PROXY_HOST_OMNIROUTE"] = proxy_host
    if legacy_proxy_host is not None:
        env["PROXY_HOST"] = legacy_proxy_host

    old_env = os.environ.copy()
    os.environ.pop("PROXY_HOST_OMNIROUTE", None)
    os.environ.pop("PROXY_HOST", None)
    os.environ.update(env)
    try:
        spec = importlib.util.spec_from_file_location("admin_server", Path("admin/server.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        os.environ.clear()
        os.environ.update(old_env)


@contextmanager
def proxy_host_env(value, legacy_value=None):
    old_env = os.environ.copy()
    if value is None:
        os.environ.pop("PROXY_HOST_OMNIROUTE", None)
    else:
        os.environ["PROXY_HOST_OMNIROUTE"] = value
    if legacy_value is None:
        os.environ.pop("PROXY_HOST", None)
    else:
        os.environ["PROXY_HOST"] = legacy_value
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(old_env)


class ProxyHostPrecedenceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def write_config(self, data):
        (self.tmp / "admin-config.json").write_text(json.dumps(data))

    def write_env(self, data):
        lines = [f"{key}={value}" for key, value in data.items()]
        (self.tmp / "warp-env").write_text("\n".join(lines))

    def test_env_filled_persistent_empty_uses_env(self):
        self.write_config({"proxy_host_omniroute": ""})
        server = load_server(self.tmp)
        with proxy_host_env("omniroute-warp-proxy"):
            self.assertEqual(server.get_config()["proxy_host_omniroute"], "omniroute-warp-proxy")

    def test_env_filled_persistent_filled_uses_env(self):
        self.write_config({"proxy_host_omniroute": "10.0.0.254"})
        server = load_server(self.tmp)
        with proxy_host_env("omniroute-warp-proxy"):
            self.assertEqual(server.get_config()["proxy_host_omniroute"], "omniroute-warp-proxy")

    def test_env_empty_persistent_filled_uses_persistent(self):
        self.write_config({"proxy_host_omniroute": "custom-proxy-host"})
        server = load_server(self.tmp)
        with proxy_host_env(""):
            self.assertEqual(server.get_config()["proxy_host_omniroute"], "custom-proxy-host")

    def test_both_empty_stays_empty(self):
        self.write_config({"proxy_host_omniroute": ""})
        server = load_server(self.tmp)
        with proxy_host_env(""):
            self.assertEqual(server.get_config()["proxy_host_omniroute"], "")

    def test_legacy_proxy_host_is_last_fallback(self):
        self.write_config({"proxy_host_omniroute": ""})
        server = load_server(self.tmp)
        with proxy_host_env("", legacy_value="legacy-host"):
            self.assertEqual(server.get_config()["proxy_host_omniroute"], "legacy-host")

    def test_env_file_filled_persistent_filled_uses_env_file(self):
        self.write_config({"proxy_host_omniroute": "10.0.0.254"})
        self.write_env({"PROXY_HOST_OMNIROUTE": "omniroute-warp-proxy"})
        server = load_server(self.tmp)
        with proxy_host_env(None):
            self.assertEqual(server.get_config()["proxy_host_omniroute"], "omniroute-warp-proxy")

    def test_cached_proxy_fields_do_not_override_current_config(self):
        self.write_env({
            "PROXY_HOST_OMNIROUTE": "omniroute-warp-proxy",
            "PROXY_BASE_PORT": "2080",
            "PROXY_MODE": "dedicated",
            "WARP_INSTANCES": "10",
        })
        server = load_server(self.tmp)
        server.get_container_ips = lambda: []
        server.port_open = lambda port: False
        server.instance_process_alive = lambda index: False
        server.get_instance_note = lambda index: ""
        server.get_watchdog_instance = lambda index: {}
        server.STATE["last_refresh_finished"] = server.time.time()
        server.STATE["egress"] = {
            1: {
                "proxy_host_omniroute": "",
                "proxy_address_omniroute": "",
                "proxy_port": 9999,
                "health": "healthy",
                "egress_ip": "198.51.100.1",
            },
            10: {
                "proxy_host_omniroute": "",
                "proxy_address_omniroute": "",
                "proxy_port": 9999,
                "health": "healthy",
                "egress_ip": "198.51.100.10",
            },
        }

        instances = server.get_instances()

        self.assertEqual(instances[0]["proxy_host_omniroute"], "omniroute-warp-proxy")
        self.assertEqual(instances[0]["proxy_port"], 2080)
        self.assertEqual(instances[0]["proxy_address_omniroute"], "omniroute-warp-proxy:2080")
        self.assertEqual(instances[9]["proxy_host_omniroute"], "omniroute-warp-proxy")
        self.assertEqual(instances[9]["proxy_port"], 2089)
        self.assertEqual(instances[9]["proxy_address_omniroute"], "omniroute-warp-proxy:2089")


if __name__ == "__main__":
    unittest.main()
