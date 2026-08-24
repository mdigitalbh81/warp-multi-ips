import base64
import importlib.util
import json
import os
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path


def write_jq_shim(tmp_path):
    jq_path = tmp_path / "jq"
    jq_path.write_text("""#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
if args and args[0] == "-r":
    args = args[1:]
expr, path = args
with open(path) as fh:
    data = json.load(fh)

def value_for(token):
    token = token.strip()
    if token.startswith("."):
        return data.get(token[1:])
    if token.startswith("env."):
        return os.environ.get(token[4:])
    if token.startswith('"') and token.endswith('"'):
        return token[1:-1]
    if token == "false":
        return False
    if token == "true":
        return True
    return None

value = None
for part in expr.split("//"):
    candidate = value_for(part)
    if candidate is not None:
        value = candidate
        break
if isinstance(value, bool):
    print("true" if value else "false")
else:
    print("" if value is None else value)
""")
    jq_path.chmod(0o755)
    return jq_path


def load_server(tmpdir):
    env = {
        "ADMIN_CONFIG_FILE": str(tmpdir / "admin-config.json"),
        "ADMIN_CREDENTIALS_FILE": str(tmpdir / "admin-credentials.json"),
        "WARP_ENV_FILE": str(tmpdir / "warp-env"),
        "WATCHDOG_STATE_FILE": str(tmpdir / "watchdog-state.json"),
    }
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


def parse_omniroute_bulk(text):
    entries = []
    errors = []
    for line_no, line in enumerate(text.splitlines(), 1):
        fields = [part.strip() for part in line.split("|")]
        if len(fields) != 9:
            errors.append(f"line {line_no}: expected 9 fields, got {len(fields)}")
            continue
        name, host, port, username, password, proxy_type, region, status, notes = fields
        if not name or not host or not port:
            errors.append(f"line {line_no}: name, host and port are required")
            continue
        try:
            parsed_port = int(port)
        except ValueError:
            errors.append(f"line {line_no}: invalid port {port}")
            continue
        entries.append({
            "name": name,
            "host": host,
            "port": parsed_port,
            "username": username,
            "password": password,
            "type": proxy_type,
            "region": region,
            "status": status,
            "notes": notes,
        })
    return entries, errors


class OmniRouteExportTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tempdir.name)
        (self.tmp / "admin-config.json").write_text("{}")
        (self.tmp / "warp-env").write_text("\n".join([
            "WARP_INSTANCES=10",
            "PROXY_MODE=dedicated",
            "PROXY_BASE_PORT=2080",
            "PROXY_HOST_OMNIROUTE=omniroute-warp-proxy",
            "PROXY_AUTH_ENABLED=false",
            "PROXY_USER=",
        ]))
        self.server = load_server(self.tmp)
        self.server.get_container_ips = lambda: []
        self.server.port_open = lambda port: False
        self.server.instance_process_alive = lambda index: False
        self.server.get_watchdog_instance = lambda index: {}
        self.server.get_instance_note = lambda index: "" if index != 0 else "primary"
        self.server.STATE["last_refresh_finished"] = self.server.time.time()
        self.server.STATE["egress"] = {
            idx + 1: {
                "instance": idx + 1,
                "health": "healthy",
                "proxy_healthy": True,
                "listener_healthy": True,
                "country_code": "BR",
                "colo": "GRU",
            }
            for idx in range(10)
        }

    def tearDown(self):
        self.tempdir.cleanup()

    def test_export_matches_omniroute_bulk_parser_order_with_auth_disabled(self):
        export = self.server.generate_omniroute_export()
        entries, errors = parse_omniroute_bulk(export["text"])

        self.assertTrue(export["ok"])
        self.assertEqual(export["count"], 10)
        self.assertEqual(errors, [])
        self.assertEqual(len(entries), 10)
        for idx, entry in enumerate(entries, 1):
            self.assertEqual(entry["name"], f"WARP-{idx:02d}")
            self.assertEqual(entry["host"], "omniroute-warp-proxy")
            self.assertEqual(entry["port"], 2079 + idx)
            self.assertEqual(entry["username"], "")
            self.assertEqual(entry["password"], "")
            self.assertEqual(entry["type"], "socks5")
            self.assertEqual(entry["region"], "BR-GRU")
            self.assertEqual(entry["status"], "active")
        self.assertEqual(entries[0]["notes"], "primary")
        self.assertEqual(entries[9]["port"], 2089)

    def test_export_auth_enabled_keeps_password_empty(self):
        (self.tmp / "admin-config.json").write_text(json.dumps({
            "proxy_auth_enabled": True,
            "proxy_user": "warpuser",
            "proxy_password": "SuperSecretProxyPass",
        }))
        export = self.server.generate_omniroute_export()
        entries, errors = parse_omniroute_bulk(export["text"])

        self.assertEqual(errors, [])
        self.assertIn("password empty", export["auth_warning"])
        self.assertEqual(entries[0]["username"], "warpuser")
        self.assertEqual(entries[0]["password"], "")
        self.assertNotIn("SuperSecretProxyPass", json.dumps(export))


class AdminApiStartupChainTests(unittest.TestCase):
    def test_shell_env_file_feeds_real_admin_api_instances(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            write_jq_shim(tmp_path)
            config_file = tmp_path / "admin-config.json"
            env_file = tmp_path / "warp-admin-env"
            credentials_file = tmp_path / "admin-credentials.json"
            config_file.write_text(json.dumps({
                "instances": 10,
                "proxy_mode": "dedicated",
                "proxy_base_port": 2080,
                "proxy_host_omniroute": "",
                "proxy_max_rps": 50,
                "warp_connect_timeout": 30,
                "auto_refresh_interval": 60,
            }))

            shell_env = os.environ.copy()
            shell_env.update({
                "ADMIN_ENABLED": "true",
                "ADMIN_CONFIG_FILE": str(config_file),
                "WARP_ENV_FILE": str(env_file),
                "ENV_PROXY_HOST_OMNIROUTE": "omniroute-warp-proxy",
                "PROXY_HOST_OMNIROUTE": "omniroute-warp-proxy",
                "PATH": f"{tmp_path}:{shell_env.get('PATH', '')}",
            })
            shell_result = subprocess.run(
                ["bash", "-lc", ". ./warp-common.sh; load_admin_config; write_admin_env_file"],
                cwd=Path(__file__).resolve().parents[1],
                env=shell_env,
                text=True,
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(shell_result.returncode, 0, shell_result.stderr or shell_result.stdout)
            self.assertIn("PROXY_HOST_OMNIROUTE=omniroute-warp-proxy\n", env_file.read_text())

            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]

            server_env = os.environ.copy()
            server_env.update({
                "ADMIN_CONFIG_FILE": str(config_file),
                "ADMIN_CREDENTIALS_FILE": str(credentials_file),
                "WARP_ENV_FILE": str(env_file),
                "WATCHDOG_STATE_FILE": str(tmp_path / "watchdog-state.json"),
                "ADMIN_USER": "admin",
                "ADMIN_PASSWORD": "Str0ng!Passw0rd",
                "ADMIN_PORT": str(port),
                "AUTO_REFRESH_INTERVAL": "3600",
            })
            server_env.pop("PROXY_HOST_OMNIROUTE", None)
            proc = subprocess.Popen(
                ["python3", "admin/server.py"],
                cwd=Path(__file__).resolve().parents[1],
                env=server_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                deadline = time.time() + 15
                while time.time() < deadline:
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                            if response.status == 200:
                                break
                    except Exception:
                        time.sleep(0.1)
                else:
                    stdout, stderr = proc.communicate(timeout=1)
                    self.fail(f"admin server did not start\nstdout={stdout}\nstderr={stderr}")

                token = base64.b64encode(b"admin:Str0ng!Passw0rd").decode()
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/instances",
                    headers={"Authorization": f"Basic {token}"},
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    instances = json.loads(response.read().decode())

                self.assertEqual(instances[0]["proxy_host_omniroute"], "omniroute-warp-proxy")
                self.assertEqual(instances[0]["proxy_address_omniroute"], "omniroute-warp-proxy:2080")
                self.assertEqual(instances[0]["proxy_port"], 2080)
                self.assertEqual(instances[9]["proxy_host_omniroute"], "omniroute-warp-proxy")
                self.assertEqual(instances[9]["proxy_address_omniroute"], "omniroute-warp-proxy:2089")
                self.assertEqual(instances[9]["proxy_port"], 2089)
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                if proc.stdout:
                    proc.stdout.close()
                if proc.stderr:
                    proc.stderr.close()


if __name__ == "__main__":
    unittest.main()
