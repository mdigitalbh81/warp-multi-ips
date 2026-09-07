import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]


def write_jq_shim(tmp_path):
    jq_path = tmp_path / "jq"
    jq_path.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
while args and args[0] == "-r":
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
"""
    )
    jq_path.chmod(0o755)
    return jq_path


@contextmanager
def env_context(env_dict=None):
    old_env = os.environ.copy()
    if env_dict:
        os.environ.update(env_dict)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(old_env)


def load_server(tmpdir, env_overrides=None):
    config_file = tmpdir / "admin-config.json"
    env_file = tmpdir / "warp-admin-env"
    creds_file = tmpdir / "admin-credentials.json"
    env = {
        "ADMIN_CONFIG_FILE": str(config_file),
        "ADMIN_CREDENTIALS_FILE": str(creds_file),
        "WARP_ENV_FILE": str(env_file),
    }
    if env_overrides:
        env.update(env_overrides)
    old_env = os.environ.copy()
    for k in (
        "WARP_INSTANCES",
        "PROXY_MODE",
        "PROXY_BASE_PORT",
        "PROXY_MAX_RPS",
        "WARP_CONNECT_TIMEOUT",
        "AUTO_REFRESH_INTERVAL",
        "PROXY_HOST_OMNIROUTE",
        "PROXY_HOST",
        "ENV_WARP_INSTANCES_SET",
        "ENV_PROXY_MODE_SET",
        "ENV_PROXY_BASE_PORT_SET",
        "ENV_PROXY_MAX_RPS_SET",
        "ENV_WARP_CONNECT_TIMEOUT_SET",
        "ENV_AUTO_REFRESH_INTERVAL_SET",
        "ENV_PROXY_HOST_OMNIROUTE_SET",
    ):
        os.environ.pop(k, None)
    os.environ.update(env)
    try:
        spec = importlib.util.spec_from_file_location(
            f"admin_server_prec_{tmpdir.name}", ROOT_DIR / "admin" / "server.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        os.environ.clear()
        os.environ.update(old_env)


class ConfigPrecedenceTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.tmp = Path(self.td.name)
        self.config_file = self.tmp / "admin-config.json"
        self.env_file = self.tmp / "warp-admin-env"
        write_jq_shim(self.tmp)

    def tearDown(self):
        self.td.cleanup()

    def run_shell(self, env_vars=None):
        env = os.environ.copy()
        env.update(
            {
                "ADMIN_ENABLED": "true",
                "ADMIN_CONFIG_FILE": str(self.config_file),
                "WARP_ENV_FILE": str(self.env_file),
                "WARP_DATA_DIR": str(self.tmp),
                "PATH": f"{self.tmp}:{env.get('PATH', '')}",
            }
        )
        for k in (
            "WARP_INSTANCES",
            "PROXY_MODE",
            "PROXY_BASE_PORT",
            "PROXY_MAX_RPS",
            "WARP_CONNECT_TIMEOUT",
            "AUTO_REFRESH_INTERVAL",
            "PROXY_HOST_OMNIROUTE",
            "PROXY_HOST",
            "ENV_WARP_INSTANCES",
            "ENV_WARP_INSTANCES_VALUE",
            "ENV_WARP_INSTANCES_SET",
            "ENV_PROXY_BASE_PORT",
            "ENV_PROXY_BASE_PORT_VALUE",
            "ENV_PROXY_BASE_PORT_SET",
        ):
            env.pop(k, None)
        if env_vars:
            env.update(env_vars)
        script = (
            ". ./warp-common.sh; "
            'ENV_WARP_INSTANCES="${WARP_INSTANCES+x}"; '
            'ENV_WARP_INSTANCES_VALUE="${WARP_INSTANCES:-}"; '
            'ENV_PROXY_BASE_PORT="${PROXY_BASE_PORT+x}"; '
            'ENV_PROXY_BASE_PORT_VALUE="${PROXY_BASE_PORT:-}"; '
            "load_admin_config; "
            "write_admin_env_file; "
            'printf "%s|%s|%s|%s\\n" "$WARP_INSTANCES" "$PROXY_BASE_PORT" "$PROXY_MODE" "$ENV_WARP_INSTANCES_SET"'
        )
        res = subprocess.run(
            ["bash", "-lc", script],
            cwd=ROOT_DIR,
            env=env,
            text=True,
            capture_output=True,
            timeout=15,
        )
        self.assertEqual(res.returncode, 0, f"stderr: {res.stderr}\nstdout: {res.stdout}")
        parts = res.stdout.strip().split("|")
        return {
            "instances": int(parts[0]),
            "base_port": int(parts[1]),
            "mode": parts[2],
            "env_instances_set": parts[3],
        }

    # 1. env WARP_INSTANCES=50 e admin-config.json instances=10 => resultado efetivo 50
    def test_scenario_1_env_warp_instances_50_admin_config_10(self):
        self.config_file.write_text(json.dumps({"instances": 10, "proxy_base_port": 2080}))
        # Shell flow
        sh_out = self.run_shell({"WARP_INSTANCES": "50"})
        self.assertEqual(sh_out["instances"], 50)
        self.assertEqual(sh_out["env_instances_set"], "true")
        # Persisted file is synced to 50
        persisted = json.loads(self.config_file.read_text())
        self.assertEqual(persisted["instances"], 50)
        # Admin server API reflects 50
        server = load_server(self.tmp, {"WARP_INSTANCES": "50"})
        with env_context({"WARP_INSTANCES": "50"}):
            cfg = server.get_config()
            self.assertEqual(cfg["instances"], 50)

    # 2. env WARP_INSTANCES não definido e admin-config.json instances=10 => resultado efetivo 10
    def test_scenario_2_env_warp_instances_unset_admin_config_10(self):
        self.config_file.write_text(json.dumps({"instances": 10, "proxy_base_port": 2080}))
        # Shell flow
        sh_out = self.run_shell()
        self.assertEqual(sh_out["instances"], 10)
        self.assertNotEqual(sh_out["env_instances_set"], "true")
        # Persisted file remains 10
        persisted = json.loads(self.config_file.read_text())
        self.assertEqual(persisted["instances"], 10)
        # Admin server API reflects 10
        server = load_server(self.tmp)
        cfg = server.get_config()
        self.assertEqual(cfg["instances"], 10)

    # 3. env PROXY_BASE_PORT=2080 e admin-config.json proxy_base_port=3000 => resultado efetivo 2080
    def test_scenario_3_env_proxy_base_port_2080_admin_config_3000(self):
        self.config_file.write_text(json.dumps({"instances": 1, "proxy_base_port": 3000}))
        # Shell flow
        sh_out = self.run_shell({"PROXY_BASE_PORT": "2080"})
        self.assertEqual(sh_out["base_port"], 2080)
        # Persisted file is synced to 2080
        persisted = json.loads(self.config_file.read_text())
        self.assertEqual(persisted["proxy_base_port"], 2080)
        # Admin server API reflects 2080
        server = load_server(self.tmp, {"PROXY_BASE_PORT": "2080"})
        with env_context({"PROXY_BASE_PORT": "2080"}):
            cfg = server.get_config()
            self.assertEqual(cfg["proxy_base_port"], 2080)

    # 4. env não define PROXY_BASE_PORT e admin-config.json proxy_base_port=3000 => resultado efetivo 3000
    def test_scenario_4_env_proxy_base_port_unset_admin_config_3000(self):
        self.config_file.write_text(json.dumps({"instances": 1, "proxy_base_port": 3000}))
        # Shell flow
        sh_out = self.run_shell()
        self.assertEqual(sh_out["base_port"], 3000)
        # Persisted file remains 3000
        persisted = json.loads(self.config_file.read_text())
        self.assertEqual(persisted["proxy_base_port"], 3000)
        # Admin server API reflects 3000
        server = load_server(self.tmp)
        cfg = server.get_config()
        self.assertEqual(cfg["proxy_base_port"], 3000)

    # 5. env WARP_INSTANCES=50 e admin-config.json antigo=10 => configuração exposta no admin panel deve refletir 50
    def test_scenario_5_admin_panel_exposes_effective_env_value(self):
        self.config_file.write_text(json.dumps({"instances": 10, "proxy_base_port": 2080}))
        server = load_server(self.tmp, {"WARP_INSTANCES": "50"})
        with env_context({"WARP_INSTANCES": "50"}):
            # public_config() is what /api/config returns to the frontend
            pub_cfg = server.public_config()
            self.assertEqual(pub_cfg["instances"], 50)
            # sync_persisted_config updates admin-config.json on startup
            server.sync_persisted_config()
            persisted = json.loads(self.config_file.read_text())
            self.assertEqual(persisted["instances"], 50)

    # 6. nenhuma configuração persistida existente => usar defaults atuais sem regressão
    def test_scenario_6_no_persisted_config_uses_defaults(self):
        # Ensure no config file exists
        if self.config_file.exists():
            self.config_file.unlink()
        # Shell flow
        sh_out = self.run_shell()
        self.assertEqual(sh_out["instances"], 1)
        self.assertEqual(sh_out["base_port"], 2080)
        self.assertEqual(sh_out["mode"], "round-robin")
        # Admin server API
        server = load_server(self.tmp)
        cfg = server.get_config()
        self.assertEqual(cfg["instances"], 1)
        self.assertEqual(cfg["proxy_base_port"], 2080)
        self.assertEqual(cfg["proxy_mode"], "round-robin")
        self.assertEqual(cfg["proxy_max_rps"], 50)
        self.assertEqual(cfg["warp_connect_timeout"], 30)
        self.assertEqual(cfg["auto_refresh_interval"], 60)

    # Extra: Watchdog reload_instance_count precedence
    def test_watchdog_retains_explicit_env_instances_over_persisted_json(self):
        self.config_file.write_text(json.dumps({"instances": 10}))
        # Run watchdog script function reload_instance_count with WARP_INSTANCES=50 and ENV_WARP_INSTANCES_SET=true
        script = (
            'WARP_INSTANCES=50; ENV_WARP_INSTANCES_SET="true"; '
            f'ADMIN_CONFIG_FILE="{self.config_file}"; '
            f'PATH="{self.tmp}:$PATH"; '
            '. ./watchdog.sh; '
            "reload_instance_count; "
            'echo "$WARP_INSTANCES"'
        )
        res = subprocess.run(
            ["bash", "-c", script],
            cwd=ROOT_DIR,
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.strip(), "50")

    def test_watchdog_reloads_persisted_json_when_env_unset(self):
        self.config_file.write_text(json.dumps({"instances": 10}))
        # Run watchdog script function reload_instance_count with unset env
        script = (
            'unset WARP_INSTANCES; ENV_WARP_INSTANCES_SET="false"; '
            'WARP_INSTANCES=1; '
            f'ADMIN_CONFIG_FILE="{self.config_file}"; '
            f'PATH="{self.tmp}:$PATH"; '
            '. ./watchdog.sh; '
            "reload_instance_count; "
            'echo "$WARP_INSTANCES"'
        )
        res = subprocess.run(
            ["bash", "-c", script],
            cwd=ROOT_DIR,
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.strip().splitlines()[-1], "10")


if __name__ == "__main__":
    unittest.main()
