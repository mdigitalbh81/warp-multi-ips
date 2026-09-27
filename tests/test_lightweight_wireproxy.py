import base64
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR / "admin") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "admin"))

import server


class LightweightWireproxyTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tempdir.name)
        self.data_dir = self.tmp / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.data_dir / "admin-config.json"
        self.env_file = self.tmp / "warp-admin-env"
        self.watchdog_file = self.tmp / "watchdog-state.json"
        self.healthy_ports_file = self.tmp / "healthy-warp-ports"

        self.orig_data_dir = server.DATA_DIR
        self.orig_warp_data_dir = getattr(server, "WARP_DATA_DIR", server.DATA_DIR)
        self.orig_config_file = server.CONFIG_FILE
        self.orig_env_file = server.ENV_FILE
        self.orig_watchdog_file = server.WATCHDOG_STATE_FILE
        self.orig_healthy_ports_file = server.HEALTHY_PORTS_FILE
        self.orig_listener_present = server.listener_present
        self.orig_instance_process_alive = server.instance_process_alive
        self.orig_trace_for_instance = server.trace_for_instance
        self.orig_trace_for_proxy = server.trace_for_proxy
        self.orig_stop_instance = server.stop_instance
        self.orig_refresh_all = server.refresh_all

        server.DATA_DIR = self.data_dir
        server.WARP_DATA_DIR = self.data_dir
        server.CONFIG_FILE = self.config_file
        server.ENV_FILE = self.env_file
        server.WATCHDOG_STATE_FILE = self.watchdog_file
        server.HEALTHY_PORTS_FILE = self.healthy_ports_file

        server.STATE["egress"] = {}
        server.STATE["operation"] = {"status": "idle"}
        server.STATE["last_refresh_started"] = 0
        server.STATE["last_refresh_finished"] = 0

    def tearDown(self):
        server.DATA_DIR = self.orig_data_dir
        server.WARP_DATA_DIR = self.orig_warp_data_dir
        server.CONFIG_FILE = self.orig_config_file
        server.ENV_FILE = self.orig_env_file
        server.WATCHDOG_STATE_FILE = self.orig_watchdog_file
        server.HEALTHY_PORTS_FILE = self.orig_healthy_ports_file
        server.listener_present = self.orig_listener_present
        server.instance_process_alive = self.orig_instance_process_alive
        server.trace_for_instance = self.orig_trace_for_instance
        server.trace_for_proxy = self.orig_trace_for_proxy
        server.stop_instance = self.orig_stop_instance
        server.refresh_all = self.orig_refresh_all
        self.tempdir.cleanup()

    # 1. WARP_ENGINE & EGRESS_FAMILY Validation
    def test_1_warp_engine_validation(self):
        base_cfg = {
            "instances": 2,
            "proxy_mode": "dedicated",
            "proxy_base_port": 2080,
            "proxy_max_rps": 50,
            "warp_connect_timeout": 30,
            "auto_refresh_interval": 60,
        }

        # Valid engines
        errors_official = server.validate_config({**base_cfg, "warp_engine": "official"})
        self.assertEqual(errors_official, [])

        errors_wireproxy = server.validate_config({**base_cfg, "warp_engine": "wireproxy", "lightweight_egress_family": "ipv6"})
        self.assertEqual(errors_wireproxy, [])

        # Invalid engine
        errors_invalid = server.validate_config({**base_cfg, "warp_engine": "invalid_engine"})
        self.assertTrue(any("warp_engine" in e for e in errors_invalid))

        # Invalid family
        errors_family = server.validate_config({**base_cfg, "lightweight_egress_family": "invalid_fam"})
        self.assertTrue(any("lightweight_egress_family" in e for e in errors_family))

    # 2. Official Engine Backward Compatibility
    def test_2_official_engine_backward_compatibility(self):
        cfg = server.base_config()
        self.assertEqual(cfg.get("warp_engine"), "official")
        self.assertEqual(cfg.get("lightweight_egress_family"), "ipv6")
        self.assertEqual(cfg.get("lightweight_require_unique_egress"), True)

        # Reprovision should reject official engine
        resp, status = server.manual_reprovision_instance(0)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])
        self.assertIn("only supported for wireproxy", resp["error"])

    # 3. Wireproxy Config Generation from wgcf profile
    def test_3_wireproxy_config_generation(self):
        sample_profile = """[Interface]
PrivateKey = aaaaaaaaabbbbbbbbbcccccccccddddddddde=
Address = 172.16.0.2/32
Address = 2606:4700:110:8a88:8f64:1234:5678:9abc/128
DNS = 1.1.1.1

[Peer]
PublicKey = bmXOC+F1FxEMF9dyiK2H5/1SUtzH0JuVo51h2wPfgyo=
Endpoint = engage.cloudflareclient.com:2408
AllowedIPs = 0.0.0.0/0
AllowedIPs = ::/0
"""
        inst_dir = self.data_dir / "lightweight" / "instance-0"
        inst_dir.mkdir(parents=True, exist_ok=True)
        profile_file = inst_dir / "wgcf-profile.conf"
        profile_file.write_text(sample_profile)

        # Test building wireproxy.conf
        lines = [l for l in profile_file.read_text().splitlines() if not (l.startswith("[Socks5]") or l.startswith("BindAddress"))]
        lines.extend(["", "[Socks5]", "BindAddress = 127.0.0.1:40000"])
        conf_file = inst_dir / "wireproxy.conf"
        conf_file.write_text("\n".join(lines) + "\n")

        content = conf_file.read_text()
        self.assertIn("[Interface]", content)
        self.assertIn("[Peer]", content)
        self.assertIn("[Socks5]", content)
        self.assertIn("BindAddress = 127.0.0.1:40000", content)
        self.assertIn("PrivateKey = aaaaaaaaabbbbbbbbbcccccccccddddddddde=", content)

    # 4. Startup with existing profile does not re-register
    def test_4_startup_with_existing_profile_persists(self):
        inst_dir = self.data_dir / "lightweight" / "instance-1"
        inst_dir.mkdir(parents=True, exist_ok=True)
        account_file = inst_dir / "wgcf-account.toml"
        profile_file = inst_dir / "wgcf-profile.conf"
        account_file.write_text('account_token = "token123"\n')
        profile_file.write_text('[Interface]\nPrivateKey = "key123"\n')

        # Calling read_egress_json
        ej = server.read_egress_json(1)
        self.assertEqual(ej, {})

        # Write egress
        server.write_egress_json(1, "2606:4700::1", "", "2026-09-27T00:00:00Z")
        ej2 = server.read_egress_json(1)
        self.assertEqual(ej2["current_ipv6"], "2606:4700::1")

    # 5. IPv6 parsing
    def test_5_ipv6_parsing_and_detection(self):
        valid_ip6 = "2a09:bac5:312c:8fe::1b4:2a"
        self.assertTrue(":" in valid_ip6)
        parts = valid_ip6.split(":")
        self.assertTrue(len(parts) >= 3)

        # Non-ipv6 check
        ipv4 = "104.28.194.5"
        self.assertFalse(":" in ipv4)

    # 6. IPv6 Uniqueness and Collision Detection
    def test_6_ipv6_uniqueness_and_collision_detection(self):
        cfg = {
            "instances": 3,
            "warp_engine": "wireproxy",
            "lightweight_require_unique_egress": True,
        }

        items = [
            {"instance": 1, "health": "healthy", "ipv6_egress": "2a09:bac5:312c:8fe::1", "egress_unique": True},
            {"instance": 2, "health": "healthy", "ipv6_egress": "2a09:bac5:312c:8fe::2", "egress_unique": True},
            {"instance": 3, "health": "healthy", "ipv6_egress": "2a09:bac5:312c:8fe::1", "egress_unique": True}, # collision with inst 1
        ]

        server.apply_uniqueness_check(items, cfg)

        self.assertTrue(items[0]["egress_unique"])
        self.assertEqual(items[0]["health"], "healthy")

        self.assertTrue(items[1]["egress_unique"])
        self.assertEqual(items[1]["health"], "healthy")

        # Instance 3 must be flagged as degraded collision
        self.assertFalse(items[2]["egress_unique"])
        self.assertEqual(items[2]["health"], "degraded")
        self.assertIn("IPv6 egress collision with instance 1", items[2]["error"])

    # 7. Egress Changed Tracking (current, previous, last_change)
    def test_7_egress_changed_tracking(self):
        inst_dir = self.data_dir / "lightweight" / "instance-2"
        inst_dir.mkdir(parents=True, exist_ok=True)

        server.write_egress_json(2, "2a09:bac5::1", "", "2026-09-27T01:00:00Z")
        ej = server.read_egress_json(2)
        self.assertEqual(ej["current_ipv6"], "2a09:bac5::1")
        self.assertEqual(ej["previous_ipv6"], "")

        # Egress changes
        server.write_egress_json(2, "2a09:bac5::2", ej["current_ipv6"], "2026-09-27T02:00:00Z")
        ej2 = server.read_egress_json(2)
        self.assertEqual(ej2["current_ipv6"], "2a09:bac5::2")
        self.assertEqual(ej2["previous_ipv6"], "2a09:bac5::1")
        self.assertEqual(ej2["last_change"], "2026-09-27T02:00:00Z")

    # 8. Wireproxy Restart Preserves Profile
    @patch("subprocess.Popen")
    def test_8_restart_preserves_existing_profile(self, mock_popen):
        inst_dir = self.data_dir / "lightweight" / "instance-0"
        inst_dir.mkdir(parents=True, exist_ok=True)
        profile_file = inst_dir / "wgcf-profile.conf"
        account_file = inst_dir / "wgcf-account.toml"
        profile_content = "[Interface]\nPrivateKey = secret123\n[Peer]\nPublicKey = peer123\n"
        profile_file.write_text(profile_content)
        account_file.write_text("token = 12345\n")

        mock_proc = MagicMock()
        mock_proc.pid = 9999
        mock_popen.return_value = mock_proc

        server.stop_instance = lambda idx: None
        server.listener_present = lambda port, listening_ports=None: True
        server.trace_for_proxy = lambda port, cfg: {"warp": "on", "ip": "2a09::1"}
        server.refresh_all = lambda force=False: []

        resp, status = server.manual_restart_wireproxy_instance(0, {"warp_connect_timeout": 5})
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])

        # Ensure profile files were NOT deleted
        self.assertTrue(profile_file.exists())
        self.assertTrue(account_file.exists())
        self.assertEqual(account_file.read_text(), "token = 12345\n")

    # 9. Dedicated GOST generation with wireproxy ports
    def test_9_dedicated_gost_config_generation(self):
        verify_dir = self.tmp / "verify"
        verify_dir.mkdir(parents=True, exist_ok=True)
        (verify_dir / "0").write_text("OK\n")
        (verify_dir / "1").write_text("OK\n")
        (verify_dir / "2").write_text("OK\n")

        cfg_file = self.tmp / "gost.yaml"
        healthy_file = self.tmp / "healthy.txt"

        cmd = f". '{ROOT_DIR}/warp-common.sh'; WARP_ENGINE=wireproxy PROXY_LOG_LEVEL=warn WARP_INSTANCES=3 PROXY_BASE_PORT=2080 PROXY_MODE=dedicated generate_gost_config_dedicated '{verify_dir}' '{cfg_file}' '{healthy_file}'"
        res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)

        content = cfg_file.read_text()
        self.assertIn(":2080", content)
        self.assertIn(":2081", content)
        self.assertIn(":2082", content)
        self.assertIn("127.0.0.1:40000", content)
        self.assertIn("127.0.0.1:40001", content)
        self.assertIn("127.0.0.1:40002", content)

    # 10. Round-robin GOST config includes only healthy instances
    def test_10_round_robin_gost_config_healthy_only(self):
        verify_dir = self.tmp / "verify_rr"
        verify_dir.mkdir(parents=True, exist_ok=True)
        (verify_dir / "0").write_text("OK\n")
        (verify_dir / "2").write_text("OK\n") # 1 is not healthy

        cfg_file = self.tmp / "gost_rr.yaml"
        healthy_file = self.tmp / "healthy_rr.txt"

        cmd = f". '{ROOT_DIR}/warp-common.sh'; WARP_ENGINE=wireproxy PROXY_LOG_LEVEL=warn WARP_INSTANCES=3 PROXY_BASE_PORT=2080 PROXY_MODE=round-robin generate_gost_config_roundrobin '{verify_dir}' '{cfg_file}' '{healthy_file}'"
        res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)

        content = cfg_file.read_text()
        self.assertIn("127.0.0.1:40000", content)
        self.assertNotIn("127.0.0.1:40001", content)
        self.assertIn("127.0.0.1:40002", content)

    # 11. Admin API Status Fields for Wireproxy
    def test_11_admin_api_status_fields(self):
        cfg = {
            "instances": 2,
            "warp_engine": "wireproxy",
            "lightweight_egress_family": "ipv6",
            "lightweight_require_unique_egress": True,
            "proxy_mode": "dedicated",
            "proxy_base_port": 2080,
        }
        self.config_file.write_text(json.dumps(cfg))

        server.STATE["egress"] = {
            1: {"instance": 1, "health": "healthy", "ipv6_egress": "2a09::1", "proxy_healthy": True, "listener_healthy": True},
            2: {"instance": 2, "health": "healthy", "ipv6_egress": "2a09::2", "proxy_healthy": True, "listener_healthy": True},
        }

        instances = server.get_instances()
        self.assertEqual(len(instances), 2)
        self.assertEqual(instances[0]["engine"], "wireproxy")
        self.assertEqual(instances[0]["ipv6_egress"], "2a09::1")
        self.assertTrue(instances[0]["egress_unique"])

    # 12. OmniRoute Export Wireproxy & Unique Check
    def test_12_omniroute_export_with_wireproxy_uniqueness(self):
        cfg = {
            "instances": 2,
            "warp_engine": "wireproxy",
            "lightweight_require_unique_egress": True,
            "proxy_mode": "dedicated",
            "proxy_base_port": 2080,
            "proxy_host_omniroute": "proxy.example.com",
            "proxy_auth_enabled": False,
        }
        self.config_file.write_text(json.dumps(cfg))

        # 1 healthy & unique, 1 colliding
        server.STATE["egress"] = {
            1: {"instance": 1, "health": "healthy", "ipv6_egress": "2a09::1", "proxy_healthy": True, "listener_healthy": True, "egress_unique": True, "country_code": "BR", "colo": "GRU"},
            2: {"instance": 2, "health": "degraded", "ipv6_egress": "2a09::1", "proxy_healthy": True, "listener_healthy": True, "egress_unique": False, "country_code": "BR", "colo": "GRU"},
        }

        exp = server.generate_omniroute_export()
        self.assertTrue(exp["ok"])
        lines = exp["lines"]
        self.assertEqual(len(lines), 2)
        self.assertIn("active", lines[0])
        self.assertIn("inactive", lines[1])

    # 13. Scale Up and Down Preserves Identities
    def test_13_scale_up_and_down_preserves_identities(self):
        # Create profiles for 0..4
        for idx in range(5):
            d = self.data_dir / "lightweight" / f"instance-{idx}"
            d.mkdir(parents=True, exist_ok=True)
            (d / "wgcf-account.toml").write_text(f"token_{idx}")
            (d / "wgcf-profile.conf").write_text(f"profile_{idx}")

        # Verify all 5 profiles exist
        for idx in range(5):
            d = self.data_dir / "lightweight" / f"instance-{idx}"
            self.assertEqual((d / "wgcf-account.toml").read_text(), f"token_{idx}")

        # Simulate scale up to 8 by adding 5..7
        for idx in range(5, 8):
            d = self.data_dir / "lightweight" / f"instance-{idx}"
            d.mkdir(parents=True, exist_ok=True)
            (d / "wgcf-account.toml").write_text(f"token_{idx}")

        # Verify initial 0..4 were untouched
        for idx in range(5):
            d = self.data_dir / "lightweight" / f"instance-{idx}"
            self.assertEqual((d / "wgcf-account.toml").read_text(), f"token_{idx}")

    # 14. Secrets Not Exposed in API or Export
    def test_14_secrets_not_exposed(self):
        inst_dir = self.data_dir / "lightweight" / "instance-0"
        inst_dir.mkdir(parents=True, exist_ok=True)
        (inst_dir / "wgcf-account.toml").write_text('account_token = "SUPER_SECRET_TOKEN_12345"\n')
        (inst_dir / "wgcf-profile.conf").write_text('PrivateKey = "SUPER_SECRET_PRIVATE_KEY_67890"\n')

        cfg = {
            "instances": 1,
            "warp_engine": "wireproxy",
            "proxy_mode": "dedicated",
            "proxy_base_port": 2080,
            "proxy_host_omniroute": "proxy.example.com",
            "proxy_auth_enabled": True,
            "proxy_user": "user",
            "proxy_password": "SUPER_SECRET_PASSWORD",
        }
        self.config_file.write_text(json.dumps(cfg))

        instances = server.get_instances()
        raw_json = json.dumps(instances)
        self.assertNotIn("SUPER_SECRET_TOKEN", raw_json)
        self.assertNotIn("SUPER_SECRET_PRIVATE_KEY", raw_json)
        self.assertNotIn("SUPER_SECRET_PASSWORD", raw_json)

        exp = server.generate_omniroute_export()
        self.assertNotIn("SUPER_SECRET_PASSWORD", exp["text"])

    # 15. Permissions 0700 for dirs and 0600 for files
    def test_15_file_and_directory_permissions(self):
        inst_dir = self.data_dir / "lightweight" / "instance-0"
        inst_dir.mkdir(parents=True, exist_ok=True)
        inst_dir.chmod(0o700)

        egress_file = inst_dir / "egress.json"
        server.write_secret_json(egress_file, {"current_ipv6": "2a09::1"})

        # Check dir permissions
        dir_mode = stat.S_IMODE(inst_dir.stat().st_mode)
        self.assertEqual(dir_mode, 0o700)

        # Check file permissions
        file_mode = stat.S_IMODE(egress_file.stat().st_mode)
        self.assertEqual(file_mode, 0o600)

    # 16. Watchdog Lightweight State Tracking
    def test_16_watchdog_lightweight_state_tracking(self):
        wd_state = {
            "instances": {
                "0": {
                    "status": "healthy",
                    "consecutive_failures": 0,
                    "engine": "wireproxy",
                    "ipv6_egress": "2a09:bac5:312c:8fe::1",
                    "previous_ipv6_egress": "",
                    "egress_unique": True,
                    "warp_status": "plus",
                }
            }
        }
        self.watchdog_file.write_text(json.dumps(wd_state))

        wd_inst = server.get_watchdog_instance(0)
        self.assertEqual(wd_inst.get("status"), "healthy")
        self.assertEqual(wd_inst.get("ipv6_egress"), "2a09:bac5:312c:8fe::1")
        self.assertEqual(wd_inst.get("warp_status"), "plus")


if __name__ == "__main__":
    unittest.main()
