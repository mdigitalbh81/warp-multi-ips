import os
import time
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
import yaml

ROOT_DIR = Path(__file__).resolve().parents[1]


class DedicatedModeAndLimitsTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.tmp = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def _run_dedicated_gost(self, instances, base_port=2080, verified_count=12):
        verify_dir = self.tmp / "verify"
        verify_dir.mkdir(parents=True, exist_ok=True)
        for i in range(verified_count):
            (verify_dir / str(i)).write_text("OK\n")

        gost_yaml = self.tmp / f"gost-{instances}.yaml"
        healthy_file = self.tmp / f"healthy-{instances}.txt"

        cmd = (
            f". ./warp-common.sh; "
            f"WARP_INSTANCES={instances} "
            f"PROXY_BASE_PORT={base_port} "
            f"PROXY_MODE=dedicated "
            f"generate_gost_config_dedicated {verify_dir} {gost_yaml} {healthy_file}"
        )
        res = subprocess.run(["bash", "-c", cmd], cwd=ROOT_DIR, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Command failed: {res.stderr}\n{res.stdout}")
        doc = yaml.safe_load(gost_yaml.read_text())
        healthy_ports = [p.strip() for p in healthy_file.read_text().splitlines() if p.strip()]
        return doc, healthy_ports

    def test_scenario_dedicated_20_instances_12_verified(self):
        doc, healthy_ports = self._run_dedicated_gost(instances=20, base_port=2080, verified_count=12)
        socks_services = [s for s in doc["services"] if s["name"].startswith("socks5-warp-")]
        chains = [c for c in doc["chains"] if c["name"].startswith("warp-chain-")]

        self.assertEqual(len(socks_services), 20)
        self.assertEqual(len(chains), 20)
        self.assertEqual(len(healthy_ports), 12)

        chains_by_name = {c["name"]: c for c in chains}
        for i in range(20):
            svc = socks_services[i]
            expected_service_name = f"socks5-warp-{i}"
            expected_listen = f":{2080 + i}"
            expected_chain = f"warp-chain-{i}"
            expected_target = f"127.0.0.1:{40000 + i}"

            self.assertEqual(svc["name"], expected_service_name)
            self.assertEqual(svc["addr"], expected_listen)
            self.assertEqual(svc["handler"]["chain"], expected_chain)

            chain = chains_by_name[expected_chain]
            target = chain["hops"][0]["nodes"][0]["addr"]
            self.assertEqual(target, expected_target)

    def test_scenario_instance_19_missing_initially_mapping_exists(self):
        doc, _ = self._run_dedicated_gost(instances=20, base_port=2080, verified_count=12)
        svc_19 = next((s for s in doc["services"] if s["name"] == "socks5-warp-19"), None)
        chain_19 = next((c for c in doc["chains"] if c["name"] == "warp-chain-19"), None)

        self.assertIsNotNone(svc_19)
        self.assertEqual(svc_19["addr"], ":2099")
        self.assertIsNotNone(chain_19)
        self.assertEqual(chain_19["hops"][0]["nodes"][0]["addr"], "127.0.0.1:40019")

    def test_scenario_instance_19_healthy_later_no_regeneration_needed(self):
        doc, _ = self._run_dedicated_gost(instances=20, base_port=2080, verified_count=12)
        svc_19 = next(s for s in doc["services"] if s["name"] == "socks5-warp-19")
        chain_19 = next(c for c in doc["chains"] if c["name"] == "warp-chain-19")
        self.assertEqual(svc_19["addr"], ":2099")
        self.assertEqual(chain_19["hops"][0]["nodes"][0]["addr"], "127.0.0.1:40019")

    def test_scenario_max_instances_45(self):
        doc, _ = self._run_dedicated_gost(instances=45, base_port=2080, verified_count=45)
        socks_services = [s for s in doc["services"] if s["name"].startswith("socks5-warp-")]
        chains = [c for c in doc["chains"] if c["name"].startswith("warp-chain-")]

        self.assertEqual(len(socks_services), 45)
        self.assertEqual(len(chains), 45)

        first_svc = socks_services[0]
        first_chain = next(c for c in chains if c["name"] == first_svc["handler"]["chain"])
        self.assertEqual(first_svc["addr"], ":2080")
        self.assertEqual(first_chain["hops"][0]["nodes"][0]["addr"], "127.0.0.1:40000")

        last_svc = socks_services[-1]
        last_chain = next(c for c in chains if c["name"] == last_svc["handler"]["chain"])
        self.assertEqual(last_svc["addr"], ":2124")
        self.assertEqual(last_chain["hops"][0]["nodes"][0]["addr"], "127.0.0.1:40044")

    def test_scenario_instances_46_rejected(self):
        # 1. Runtime shell validation
        cmd = (
            ". ./warp-common.sh; "
            "WARP_INSTANCES=46 PROXY_BASE_PORT=2080 PROXY_MODE=dedicated validate_runtime_config"
        )
        res = subprocess.run(["bash", "-c", cmd], cwd=ROOT_DIR, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("45", res.stdout + res.stderr)

        # 2. Server validation
        import sys
        if str(ROOT_DIR / "admin") not in sys.path:
            sys.path.insert(0, str(ROOT_DIR / "admin"))
        import server
        errors = server.validate_config(
            {
                "instances": 46,
                "proxy_mode": "dedicated",
                "proxy_base_port": 2080,
                "proxy_max_rps": 50,
                "warp_connect_timeout": 30,
                "auto_refresh_interval": 60,
            }
        )
        self.assertTrue(any("instances must be between 1 and 45" in err for err in errors))

    def test_scenario_admin_api_configured_20_vs_max_45(self):
        import sys
        if str(ROOT_DIR / "admin") not in sys.path:
            sys.path.insert(0, str(ROOT_DIR / "admin"))
        import server

        old_env = os.environ.copy()
        try:
            os.environ["WARP_INSTANCES"] = "20"
            os.environ["PROXY_MODE"] = "dedicated"
            os.environ["PROXY_BASE_PORT"] = "2080"
            server.CONFIG_FILE = self.tmp / "empty-config.json"

            cfg = server.public_config()
            self.assertEqual(cfg["instances"], 20)
            self.assertEqual(cfg["max_instances"], 45)

            instances = server.get_instances()
            self.assertEqual(len(instances), 20)
            self.assertEqual(instances[0]["instance"], 1)
            self.assertEqual(instances[0]["proxy_port"], 2080)
            self.assertEqual(instances[-1]["instance"], 20)
            self.assertEqual(instances[-1]["proxy_port"], 2099)
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_scenario_roundrobin_preserves_policy_of_healthy_upstreams(self):
        verify_dir = self.tmp / "verify_rr"
        verify_dir.mkdir(parents=True, exist_ok=True)
        for i in range(12):
            (verify_dir / str(i)).write_text("OK\n")

        gost_yaml = self.tmp / "gost-rr.yaml"
        healthy_file = self.tmp / "healthy-rr.txt"

        cmd = (
            f". ./warp-common.sh; "
            f"WARP_INSTANCES=20 "
            f"PROXY_BASE_PORT=2080 "
            f"PROXY_MODE=round-robin "
            f"generate_gost_config_roundrobin {verify_dir} {gost_yaml} {healthy_file}"
        )
        res = subprocess.run(["bash", "-c", cmd], cwd=ROOT_DIR, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        doc = yaml.safe_load(gost_yaml.read_text())

        warp_chain = next(c for c in doc["chains"] if c["name"] == "warp-chain")
        nodes = warp_chain["hops"][0]["nodes"]
        self.assertEqual(len(nodes), 12)
        node_addrs = [n["addr"] for n in nodes]
        for i in range(12):
            self.assertIn(f"127.0.0.1:{40000 + i}", node_addrs)
        for i in range(12, 20):
            self.assertNotIn(f"127.0.0.1:{40000 + i}", node_addrs)


    def test_scenario_a_sigterm_cleanup_no_registration_delete(self):
        entrypoint_text = (ROOT_DIR / "entrypoint.sh").read_text()
        self.assertNotIn("registration delete", entrypoint_text)
        self.assertIn("trap cleanup SIGTERM SIGINT", entrypoint_text)

        spy_dir = self.tmp / "spy_bin"
        spy_dir.mkdir(parents=True, exist_ok=True)
        log_file = self.tmp / "warp_cli_calls.log"
        spy_script = spy_dir / "warp-cli"
        spy_script.write_text(f"""#!/bin/bash
echo "$@" >> "{log_file}"
exit 0
""")
        spy_script.chmod(0o755)

        script = f"""
export PATH="{spy_dir}:$PATH"
WARP_INSTANCES=20
INSTANCE_PIDS=()
ADMIN_PID=99999
GOST_PID=99999
WATCHDOG_PID=99999
sed -n '/^cleanup()/,/^trap cleanup/p' entrypoint.sh > "{self.tmp}/cleanup_fn.sh"
source "{self.tmp}/cleanup_fn.sh"
cleanup
"""
        res = subprocess.run(["bash", "-c", script], cwd=ROOT_DIR, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        calls = log_file.read_text() if log_file.exists() else ""
        self.assertNotIn("registration delete", calls)

    def test_scenario_b_sigint_cleanup_no_registration_delete(self):
        spy_dir = self.tmp / "spy_bin_b"
        spy_dir.mkdir(parents=True, exist_ok=True)
        log_file = self.tmp / "warp_cli_calls_b.log"
        spy_script = spy_dir / "warp-cli"
        spy_script.write_text(f"""#!/bin/bash
echo "$@" >> "{log_file}"
exit 0
""")
        spy_script.chmod(0o755)

        sub_script = f"""#!/bin/bash
export PATH="{spy_dir}:$PATH"
WARP_INSTANCES=5
INSTANCE_PIDS=()
ADMIN_PID=99999
GOST_PID=99999
WATCHDOG_PID=99999
sed -n '/^cleanup()/,/^trap cleanup/p' "{ROOT_DIR}/entrypoint.sh" > "{self.tmp}/cleanup_fn_b.sh"
source "{self.tmp}/cleanup_fn_b.sh"
kill -INT $$
"""
        res = subprocess.run(["bash", "-c", sub_script], cwd=ROOT_DIR, capture_output=True, text=True)

        calls = log_file.read_text() if log_file.exists() else ""
        self.assertNotIn("registration delete", calls)

    def test_scenario_c_shutdown_normal_preserves_reg_json(self):
        data_dir = self.tmp / "warp-data"
        inst0_dir = data_dir / "instance-0"
        inst0_dir.mkdir(parents=True, exist_ok=True)
        reg_file = inst0_dir / "reg.json"
        sample_reg = json.dumps({"account_id": "test-account-123", "client_id": "client-abc"})
        reg_file.write_text(sample_reg)

        script = f"""
WARP_INSTANCES=20
INSTANCE_PIDS=()
ADMIN_PID=99999
GOST_PID=99999
WATCHDOG_PID=99999
sed -n '/^cleanup()/,/^trap cleanup/p' entrypoint.sh > "{self.tmp}/cleanup_fn_c.sh"
source "{self.tmp}/cleanup_fn_c.sh"
cleanup
"""
        res = subprocess.run(["bash", "-c", script], cwd=ROOT_DIR, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(reg_file.exists(), "reg.json must not be deleted on normal shutdown")
        self.assertEqual(reg_file.read_text(), sample_reg)

    def test_scenario_d_20_internal_healthy_instances_status_reports_20(self):
        import sys
        if str(ROOT_DIR / "admin") not in sys.path:
            sys.path.insert(0, str(ROOT_DIR / "admin"))
        import server
        old_env = os.environ.copy()
        try:
            os.environ["WARP_INSTANCES"] = "20"
            os.environ["PROXY_MODE"] = "dedicated"
            os.environ["PROXY_BASE_PORT"] = "2080"
            server.CONFIG_FILE = self.tmp / "cfg_d.json"
            # Seed egress state with confirmed warp=True for all 20 instances
            server.STATE["egress"] = {
                idx + 1: {
                    "instance": idx + 1,
                    "warp": True,
                    "warp_connected": True,
                    "health": "healthy",
                    "egress_ip": f"100.64.0.{idx+1}",
                    "process_running": True,
                    "internal_socks_ready": True,
                    "dedicated_proxy_ready": True,
                }
                for idx in range(20)
            }
            server.STATE["last_refresh_finished"] = time.time()

            orig_proc_alive = server.instance_process_alive
            orig_port_open = server.port_open
            orig_wd = server.get_watchdog_instance

            server.instance_process_alive = lambda idx: True
            server.port_open = lambda port, timeout=0.5: True
            server.get_watchdog_instance = lambda idx: {"status": "healthy", "current_egress": f"100.64.0.{idx+1}"}

            try:
                instances = server.get_instances()
                self.assertEqual(len(instances), 20)
                for inst in instances:
                    self.assertTrue(inst["process_running"])
                    self.assertTrue(inst["internal_socks_ready"])
                    self.assertTrue(inst["dedicated_proxy_ready"])
                    self.assertTrue(inst["warp_connected"])
                    self.assertEqual(inst["health"], "healthy")

                healthy_count = sum(1 for item in instances if item["health"] == "healthy")
                self.assertEqual(healthy_count, 20)
            finally:
                server.instance_process_alive = orig_proc_alive
                server.port_open = orig_port_open
                server.get_watchdog_instance = orig_wd
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_scenario_e_transient_egress_failure_does_not_mark_warp_dead(self):
        import sys
        if str(ROOT_DIR / "admin") not in sys.path:
            sys.path.insert(0, str(ROOT_DIR / "admin"))
        import server
        old_env = os.environ.copy()
        try:
            os.environ["WARP_INSTANCES"] = "20"
            os.environ["PROXY_MODE"] = "dedicated"
            os.environ["PROXY_BASE_PORT"] = "2080"
            server.CONFIG_FILE = self.tmp / "cfg_e.json"
            server.STATE["egress"] = {}
            cfg = server.get_config(True)

            orig_proc_alive = server.instance_process_alive
            orig_port_open = server.port_open
            orig_trace = server.trace_for_instance
            orig_wd = server.get_watchdog_instance

            server.instance_process_alive = lambda idx: True
            server.port_open = lambda port, timeout=0.5: True
            server.get_watchdog_instance = lambda idx: {"status": "healthy", "current_egress": "198.51.100.5"}
            # Seed grace period: last confirmed warp=on was recent
            server._WARP_LAST_CONFIRMED[5] = time.time()
            server.trace_for_instance = lambda port, timeout=8: (_ for _ in ()).throw(RuntimeError("Connection timed out to Cloudflare"))

            try:
                inst = server.refresh_instance(5, cfg)
                self.assertTrue(inst["process_running"])
                self.assertTrue(inst["internal_socks_ready"])
                self.assertTrue(inst["warp_connected"], "WARP must remain connected on transient egress failure")
                self.assertTrue(inst["warp"])
                self.assertEqual(inst["health"], "healthy")
                self.assertEqual(inst["egress_ip"], "198.51.100.5")
                self.assertIn("transient egress check warning", inst["error"])
            finally:
                server.instance_process_alive = orig_proc_alive
                server.port_open = orig_port_open
                server.trace_for_instance = orig_trace
                server.get_watchdog_instance = orig_wd
                server._WARP_LAST_CONFIRMED.pop(5, None)
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_scenario_f_internal_socks_failure_marks_instance_degraded(self):
        import sys
        if str(ROOT_DIR / "admin") not in sys.path:
            sys.path.insert(0, str(ROOT_DIR / "admin"))
        import server
        old_env = os.environ.copy()
        try:
            os.environ["WARP_INSTANCES"] = "20"
            os.environ["PROXY_MODE"] = "dedicated"
            os.environ["PROXY_BASE_PORT"] = "2080"
            server.CONFIG_FILE = self.tmp / "cfg_f.json"
            cfg = server.get_config(True)

            orig_proc_alive = server.instance_process_alive
            orig_port_open = server.port_open
            orig_wd = server.get_watchdog_instance

            server.instance_process_alive = lambda idx: True
            server.port_open = lambda port, timeout=0.5: False if port == 40005 else True
            server.get_watchdog_instance = lambda idx: None

            try:
                inst = server.refresh_instance(5, cfg)
                self.assertFalse(inst["internal_socks_ready"])
                self.assertFalse(inst["warp_connected"])
                self.assertFalse(inst["warp"])
                self.assertEqual(inst["health"], "degraded")
            finally:
                server.instance_process_alive = orig_proc_alive
                server.port_open = orig_port_open
                server.get_watchdog_instance = orig_wd
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_scenario_g_warp_instances_20_lists_exactly_20_ports_2080_to_2099(self):
        import sys
        if str(ROOT_DIR / "admin") not in sys.path:
            sys.path.insert(0, str(ROOT_DIR / "admin"))
        import server
        old_env = os.environ.copy()
        try:
            os.environ["WARP_INSTANCES"] = "20"
            os.environ["PROXY_MODE"] = "dedicated"
            os.environ["PROXY_BASE_PORT"] = "2080"
            server.CONFIG_FILE = self.tmp / "cfg_g.json"
            server.STATE["egress"] = {}
            server.STATE["last_refresh_finished"] = time.time()

            instances = server.get_instances()
            self.assertEqual(len(instances), 20)
            for idx, inst in enumerate(instances):
                self.assertEqual(inst["instance"], idx + 1)
                self.assertEqual(inst["proxy_port"], 2080 + idx)
                self.assertEqual(inst["internal_port"], 40000 + idx)
            self.assertEqual(instances[0]["proxy_port"], 2080)
            self.assertEqual(instances[-1]["proxy_port"], 2099)
        finally:
            os.environ.clear()
            os.environ.update(old_env)

if __name__ == "__main__":
    unittest.main()
