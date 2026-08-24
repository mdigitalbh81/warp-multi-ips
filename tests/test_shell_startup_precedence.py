import json
import os
import subprocess
import tempfile
import unittest
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


class ShellStartupPrecedenceTests(unittest.TestCase):
    def run_shell_flow(self, env_host=None, persisted_host="", legacy_host=None):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            write_jq_shim(tmp_path)
            config_file = tmp_path / "admin-config.json"
            env_file = tmp_path / "warp-admin-env"
            config_file.write_text(json.dumps({
                "instances": 10,
                "proxy_mode": "dedicated",
                "proxy_base_port": 2080,
                "proxy_host_omniroute": persisted_host,
            }))
            env = os.environ.copy()
            env.update({
                "ADMIN_ENABLED": "true",
                "ADMIN_CONFIG_FILE": str(config_file),
                "WARP_ENV_FILE": str(env_file),
                "WARP_DATA_DIR": str(tmp_path),
                "PATH": f"{tmp_path}:{env.get('PATH', '')}",
            })
            env.pop("PROXY_HOST_OMNIROUTE", None)
            env.pop("ENV_PROXY_HOST_OMNIROUTE", None)
            env.pop("PROXY_HOST", None)
            if env_host is not None:
                env["PROXY_HOST_OMNIROUTE"] = env_host
                env["ENV_PROXY_HOST_OMNIROUTE"] = env_host
            if legacy_host is not None:
                env["PROXY_HOST"] = legacy_host

            script = ". ./warp-common.sh; load_admin_config; write_admin_env_file; printf '%s\\n' \"$PROXY_HOST_OMNIROUTE\""
            result = subprocess.run(
                ["bash", "-lc", script],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                text=True,
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            return result.stdout.strip(), env_file.read_text()

    def test_env_wins_over_persisted_empty_and_writes_env_file(self):
        effective, env_text = self.run_shell_flow(env_host="omniroute-warp-proxy", persisted_host="")
        self.assertEqual(effective, "omniroute-warp-proxy")
        self.assertIn("PROXY_HOST_OMNIROUTE=omniroute-warp-proxy\n", env_text)

    def test_env_wins_over_persisted_filled(self):
        effective, env_text = self.run_shell_flow(env_host="omniroute-warp-proxy", persisted_host="10.0.0.254")
        self.assertEqual(effective, "omniroute-warp-proxy")
        self.assertIn("PROXY_HOST_OMNIROUTE=omniroute-warp-proxy\n", env_text)

    def test_persisted_used_when_env_empty(self):
        effective, env_text = self.run_shell_flow(env_host=None, persisted_host="custom-host")
        self.assertEqual(effective, "custom-host")
        self.assertIn("PROXY_HOST_OMNIROUTE=custom-host\n", env_text)

    def test_legacy_proxy_host_used_as_last_fallback(self):
        effective, env_text = self.run_shell_flow(env_host=None, persisted_host="", legacy_host="legacy-host")
        self.assertEqual(effective, "legacy-host")
        self.assertIn("PROXY_HOST_OMNIROUTE=legacy-host\n", env_text)

    def test_all_empty_stays_empty(self):
        effective, env_text = self.run_shell_flow(env_host=None, persisted_host="")
        self.assertEqual(effective, "")
        self.assertIn("PROXY_HOST_OMNIROUTE=\n", env_text)


if __name__ == "__main__":
    unittest.main()
