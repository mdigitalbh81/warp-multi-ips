import importlib.util
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path


def load_server(tmpdir, proxy_host=None):
    env = {
        "ADMIN_CONFIG_FILE": str(tmpdir / "admin-config.json"),
        "ADMIN_CREDENTIALS_FILE": str(tmpdir / "admin-credentials.json"),
        "WARP_ENV_FILE": str(tmpdir / "warp-env"),
    }
    if proxy_host is not None:
        env["PROXY_HOST_OMNIROUTE"] = proxy_host

    old_env = os.environ.copy()
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
def proxy_host_env(value):
    old_env = os.environ.copy()
    if value is None:
        os.environ.pop("PROXY_HOST_OMNIROUTE", None)
    else:
        os.environ["PROXY_HOST_OMNIROUTE"] = value
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

    def test_persistent_empty_env_filled_uses_env(self):
        self.write_config({"proxy_host_omniroute": ""})
        server = load_server(self.tmp)
        with proxy_host_env("omniroute_warp-proxy"):
            self.assertEqual(server.get_config()["proxy_host_omniroute"], "omniroute_warp-proxy")

    def test_persistent_absent_env_filled_uses_env(self):
        self.write_config({"instances": 10})
        server = load_server(self.tmp)
        with proxy_host_env("omniroute_warp-proxy"):
            self.assertEqual(server.get_config()["proxy_host_omniroute"], "omniroute_warp-proxy")

    def test_persistent_filled_env_filled_uses_persistent(self):
        self.write_config({"proxy_host_omniroute": "custom-proxy-host"})
        server = load_server(self.tmp)
        with proxy_host_env("omniroute_warp-proxy"):
            self.assertEqual(server.get_config()["proxy_host_omniroute"], "custom-proxy-host")

    def test_both_empty_stays_empty(self):
        self.write_config({"proxy_host_omniroute": ""})
        server = load_server(self.tmp)
        with proxy_host_env(""):
            self.assertEqual(server.get_config()["proxy_host_omniroute"], "")


if __name__ == "__main__":
    unittest.main()
