import json
import unittest
import tempfile
import time
import os
import subprocess
from pathlib import Path
import importlib.util

def load_server(tmpdir):
    env = {
        "ADMIN_CONFIG_FILE": str(tmpdir / "admin-config.json"),
        "ADMIN_CREDENTIALS_FILE": str(tmpdir / "admin-credentials.json"),
        "WARP_ENV_FILE": str(tmpdir / "warp-env"),
        "WATCHDOG_STATE_FILE": str(tmpdir / "watchdog-state.json"),
    }
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

class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tempdir.name)
        self.server = load_server(self.tmp)

    def tearDown(self):
        self.tempdir.cleanup()

    def write_mock_watchdog_state(self, state_dict):
        state_file = self.tmp / "watchdog-state.json"
        state_file.write_text(json.dumps(state_dict))

    def test_mock_watchdog_state_healthy(self):
        mock_state = {
            "updated": "2026-08-22T20:00:00Z",
            "enabled": True,
            "instances": {
                "0": {
                    "status": "healthy",
                    "consecutive_failures": 0,
                    "last_check": "2026-08-22T20:00:00Z",
                    "last_success": "2026-08-22T20:00:00Z",
                    "last_failure": "",
                    "last_reconnect": "",
                    "last_restart": "",
                    "reconnect_count": 0,
                    "restart_count": 0,
                    "recovery_status": "none",
                    "last_error": "",
                    "previous_egress": "",
                    "current_egress": "1.1.1.1",
                    "last_egress_change": ""
                }
            }
        }
        self.write_mock_watchdog_state(mock_state)
        res = self.server.get_watchdog_instance(0)
        self.assertEqual(res["status"], "healthy")
        self.assertEqual(res["current_egress"], "1.1.1.1")

    def test_watchdog_data_merged_into_instances(self):
        mock_state = {
            "instances": {
                "0": {
                    "status": "recovering",
                    "consecutive_failures": 3,
                    "last_check": "2026-08-22T20:00:00Z",
                    "last_success": "",
                    "last_failure": "2026-08-22T20:00:00Z",
                    "last_reconnect": "2026-08-22T20:00:05Z",
                    "last_restart": "",
                    "reconnect_count": 1,
                    "restart_count": 0,
                    "recovery_status": "reconnecting",
                    "last_error": "timeout",
                    "previous_egress": "1.1.1.1",
                    "current_egress": "",
                    "last_egress_change": ""
                }
            }
        }
        self.write_mock_watchdog_state(mock_state)

        # Mock the server cached egress list to contain instance 1
        self.server.STATE["egress"] = {
            1: {
                "instance": 1,
                "proxy_port": 2080,
                "internal_port": 40000,
                "egress_ip": None,
                "warp": False,
                "process_healthy": True,
                "proxy_healthy": True,
                "listener_healthy": True,
                "internal_healthy": True,
                "health": "unknown",
            }
        }

        instances = self.server.get_instances()
        self.assertEqual(len(instances), 1)
        inst = instances[0]
        self.assertEqual(inst["health"], "recovering")
        self.assertIsNotNone(inst["watchdog"])
        self.assertEqual(inst["watchdog"]["recovery_status"], "reconnecting")
        self.assertEqual(inst["watchdog"]["reconnect_count"], 1)

    def test_bash_watchdog_script_syntax(self):
        # Verify syntax of watchdog.sh using bash -n
        res = subprocess.run(["bash", "-n", "watchdog.sh"], capture_output=True)
        self.assertEqual(res.returncode, 0, f"watchdog.sh syntax error: {res.stderr.decode()}")

    def test_watchdog_cooldown_calculation(self):
        # We can test logic or state logic through python tests
        pass

if __name__ == "__main__":
    unittest.main()
