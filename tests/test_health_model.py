"""Tests for the warp_connected / healthy health model.

Verifies that:
- internal SOCKS open alone does NOT prove warp_connected
- trace warp=off => NOT healthy
- trace timeout without prior confirmation => NOT healthy
- grace period preserves state after recent warp=on
- grace period expiry => degraded
- 20 real warp=on instances => 20 healthy
- losing warp on one => 19 healthy
- round-robin does not require dedicated_proxy_ready
"""
import os
import sys
import time
import unittest
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR / "admin") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "admin"))

import server


class HealthModelTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.tmp = Path(self.td.name)
        self.old_env = os.environ.copy()
        os.environ["WARP_INSTANCES"] = "20"
        os.environ["PROXY_MODE"] = "dedicated"
        os.environ["PROXY_BASE_PORT"] = "2080"
        server.CONFIG_FILE = self.tmp / "cfg.json"
        # Save originals
        self._orig_proc = server.instance_process_alive
        self._orig_port = server.port_open
        self._orig_trace = server.trace_for_instance
        self._orig_wd = server.get_watchdog_instance
        self._orig_confirmed = server._WARP_LAST_CONFIRMED.copy()
        server.STATE["egress"] = {}
        server.STATE["last_refresh_finished"] = None

    def tearDown(self):
        server.instance_process_alive = self._orig_proc
        server.port_open = self._orig_port
        server.trace_for_instance = self._orig_trace
        server.get_watchdog_instance = self._orig_wd
        server._WARP_LAST_CONFIRMED.clear()
        server._WARP_LAST_CONFIRMED.update(self._orig_confirmed)
        server.STATE["egress"] = {}
        server.STATE["last_refresh_finished"] = None
        os.environ.clear()
        os.environ.update(self.old_env)
        self.td.cleanup()

    def _stub_all_up(self):
        """Stub process/port checks to return True."""
        server.instance_process_alive = lambda idx: True
        server.port_open = lambda port, timeout=0.5: True

    # ------------------------------------------------------------------
    # Test 1: process=true, internal_socks=true, gost=true, trace warp=off
    #         => NOT healthy
    # ------------------------------------------------------------------
    def test_1_trace_warp_off_not_healthy(self):
        self._stub_all_up()
        server.get_watchdog_instance = lambda idx: None
        server.trace_for_instance = lambda port, timeout=8: {
            "warp": "off", "ip": "203.0.113.1", "colo": "GRU", "loc": "BR"
        }
        cfg = server.get_config(True)
        inst = server.refresh_instance(0, cfg)
        self.assertTrue(inst["process_running"])
        self.assertTrue(inst["internal_socks_ready"])
        self.assertTrue(inst["dedicated_proxy_ready"])
        self.assertFalse(inst["warp"])
        self.assertFalse(inst["warp_connected"])
        self.assertNotEqual(inst["health"], "healthy")

    # ------------------------------------------------------------------
    # Test 2: process=true, internal_socks=true, gost=true,
    #         trace timeout, NO prior warp=on history => NOT healthy
    # ------------------------------------------------------------------
    def test_2_trace_timeout_no_history_not_healthy(self):
        self._stub_all_up()
        server.get_watchdog_instance = lambda idx: {"status": "healthy"}
        server.trace_for_instance = lambda port, timeout=8: (_ for _ in ()).throw(
            RuntimeError("Connection timed out")
        )
        # No entry in _WARP_LAST_CONFIRMED
        cfg = server.get_config(True)
        inst = server.refresh_instance(3, cfg)
        self.assertFalse(inst["warp_connected"])
        self.assertNotEqual(inst["health"], "healthy")

    # ------------------------------------------------------------------
    # Test 3: last warp=on recent, new refresh timeout,
    #         process/internal/gost alive => may remain healthy (grace)
    # ------------------------------------------------------------------
    def test_3_grace_period_preserves_healthy(self):
        self._stub_all_up()
        server.get_watchdog_instance = lambda idx: {"status": "healthy"}
        server._WARP_LAST_CONFIRMED[7] = time.time()  # just confirmed
        server.trace_for_instance = lambda port, timeout=8: (_ for _ in ()).throw(
            RuntimeError("timeout")
        )
        cfg = server.get_config(True)
        inst = server.refresh_instance(7, cfg)
        self.assertTrue(inst["warp_connected"])
        self.assertTrue(inst["warp"])
        self.assertEqual(inst["health"], "healthy")
        self.assertIn("transient", inst["error"])

    # ------------------------------------------------------------------
    # Test 4: last warp=on OLD (beyond grace), refreshes failing
    #         => degraded/unknown, NOT healthy
    # ------------------------------------------------------------------
    def test_4_grace_expired_becomes_degraded(self):
        self._stub_all_up()
        server.get_watchdog_instance = lambda idx: {"status": "healthy"}
        # Set confirmation way in the past (beyond grace)
        server._WARP_LAST_CONFIRMED[2] = time.time() - server.WARP_CONNECTED_GRACE_SECONDS - 60
        server.trace_for_instance = lambda port, timeout=8: (_ for _ in ()).throw(
            RuntimeError("timeout")
        )
        cfg = server.get_config(True)
        inst = server.refresh_instance(2, cfg)
        self.assertFalse(inst["warp_connected"])
        self.assertFalse(inst["warp"])
        self.assertNotEqual(inst["health"], "healthy")
        self.assertIn("expired", inst.get("error", ""))

    # ------------------------------------------------------------------
    # Test 5: internal SOCKS open but WARP real off => never healthy
    #         just because port is open
    # ------------------------------------------------------------------
    def test_5_socks_open_warp_off_never_healthy(self):
        self._stub_all_up()
        server.get_watchdog_instance = lambda idx: None
        # Trace returns warp=off
        server.trace_for_instance = lambda port, timeout=8: {
            "warp": "off", "ip": "203.0.113.5", "colo": "GRU", "loc": "BR"
        }
        cfg = server.get_config(True)
        inst = server.refresh_instance(10, cfg)
        self.assertTrue(inst["internal_socks_ready"])
        self.assertFalse(inst["warp_connected"])
        self.assertNotEqual(inst["health"], "healthy")

    # ------------------------------------------------------------------
    # Test 6: 20 instances all with warp=on real => healthy_instances=20
    # ------------------------------------------------------------------
    def test_6_twenty_warp_on_all_healthy(self):
        self._stub_all_up()
        server.get_watchdog_instance = lambda idx: {"status": "healthy"}
        server.trace_for_instance = lambda port, timeout=8: {
            "warp": "on", "ip": f"100.64.0.{port - 40000 + 1}",
            "colo": "GRU", "loc": "BR"
        }
        cfg = server.get_config(True)
        results = []
        for idx in range(20):
            results.append(server.refresh_instance(idx, cfg))
        healthy = sum(1 for r in results if r["health"] == "healthy")
        self.assertEqual(healthy, 20)
        for r in results:
            self.assertTrue(r["warp_connected"])
            self.assertTrue(r["warp"])

    # ------------------------------------------------------------------
    # Test 7: one of 20 loses WARP real => healthy_instances=19
    # ------------------------------------------------------------------
    def test_7_one_loses_warp_nineteen_healthy(self):
        self._stub_all_up()
        server.get_watchdog_instance = lambda idx: {"status": "healthy"}
        lost_idx = 13
        def fake_trace(port, timeout=8):
            idx = port - 40000
            if idx == lost_idx:
                return {"warp": "off", "ip": "203.0.113.99", "colo": "GRU", "loc": "BR"}
            return {"warp": "on", "ip": f"100.64.0.{idx+1}", "colo": "GRU", "loc": "BR"}
        server.trace_for_instance = fake_trace
        cfg = server.get_config(True)
        results = []
        for idx in range(20):
            results.append(server.refresh_instance(idx, cfg))
        healthy = sum(1 for r in results if r["health"] == "healthy")
        self.assertEqual(healthy, 19)
        self.assertFalse(results[lost_idx]["warp_connected"])
        self.assertNotEqual(results[lost_idx]["health"], "healthy")

    # ------------------------------------------------------------------
    # Test 8: round-robin does not depend on dedicated_proxy_ready
    # ------------------------------------------------------------------
    def test_8_roundrobin_no_dedicated_proxy_dependency(self):
        os.environ["PROXY_MODE"] = "round-robin"
        server.instance_process_alive = lambda idx: True
        # Internal SOCKS port (40000+idx) open, but external port 1080 is down
        server.port_open = lambda port, timeout=0.5: port >= 40000
        server.get_watchdog_instance = lambda idx: {"status": "healthy"}
        server.trace_for_instance = lambda port, timeout=8: {
            "warp": "on", "ip": f"100.64.0.{port - 40000 + 1}",
            "colo": "GRU", "loc": "BR"
        }
        cfg = server.get_config(True)
        inst = server.refresh_instance(0, cfg)
        self.assertTrue(inst["warp_connected"])
        self.assertFalse(inst["dedicated_proxy_ready"])
        self.assertEqual(inst["health"], "healthy",
                         "round-robin instance should be healthy without dedicated proxy port")


if __name__ == "__main__":
    unittest.main()
