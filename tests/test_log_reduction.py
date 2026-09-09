import os
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
import sys
if str(ROOT_DIR / "admin") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "admin"))
import server
import yaml


class LogReductionAndPassiveListenerTests(unittest.TestCase):
    def test_1_listener_present_2080_detects_listen_without_accepting_connection(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 2080))
        s.listen(1)
        s.setblocking(False)
        try:
            self.assertTrue(server.listener_present(2080))
            with self.assertRaises((BlockingIOError, OSError)):
                s.accept()
        finally:
            s.close()

    def test_2_listener_present_40000_detects_listen_without_socks_greeting(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 40000))
        s.listen(1)
        s.setblocking(False)
        try:
            self.assertTrue(server.listener_present(40000))
            with self.assertRaises((BlockingIOError, OSError)):
                s.accept()
        finally:
            s.close()

    def test_3_closed_port_listener_present_false(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        self.assertFalse(server.listener_present(port))

    def test_4_refresh_20_dedicated_instances_opens_zero_tcp_connections_to_gost(self):
        sockets = []
        try:
            for i in range(20):
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("127.0.0.1", 2080 + i))
                sock.listen(1)
                sock.setblocking(False)
                sockets.append(sock)

            cfg = {
                "instances": 20,
                "proxy_mode": "dedicated",
                "proxy_base_port": 2080,
                "proxy_host_omniroute": "proxy.example.com",
            }
            orig_proc = server.instance_process_alive
            orig_trace = server.trace_for_instance
            server.instance_process_alive = lambda idx: True
            server.trace_for_instance = lambda port, timeout=8: {"warp": "on", "ip": "100.64.0.1"}
            try:
                for idx in range(20):
                    item = server.refresh_instance(idx, cfg)
                    self.assertTrue(item["dedicated_proxy_ready"])
                for sock in sockets:
                    with self.assertRaises((BlockingIOError, OSError)):
                        sock.accept()
            finally:
                server.instance_process_alive = orig_proc
                server.trace_for_instance = orig_trace
        finally:
            for sock in sockets:
                sock.close()

    def test_5_get_instances_opens_no_partial_socks_connection_to_gost(self):
        sockets = []
        try:
            for i in range(20):
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("127.0.0.1", 2080 + i))
                sock.listen(1)
                sock.setblocking(False)
                sockets.append(sock)

            orig_cfg = server.get_config
            server.get_config = lambda reload=False: {
                "instances": 20,
                "proxy_mode": "dedicated",
                "proxy_base_port": 2080,
                "proxy_host_omniroute": "proxy.example.com",
                "auto_refresh_interval": 60,
            }
            orig_proc = server.instance_process_alive
            server.instance_process_alive = lambda idx: True
            server.STATE["egress"] = {}
            server.STATE["last_refresh_finished"] = time.time()
            try:
                instances = server.get_instances()
                self.assertEqual(len(instances), 20)
                for item in instances:
                    self.assertTrue(item["dedicated_proxy_ready"])
                for sock in sockets:
                    with self.assertRaises((BlockingIOError, OSError)):
                        sock.accept()
            finally:
                server.get_config = orig_cfg
                server.instance_process_alive = orig_proc
        finally:
            for sock in sockets:
                sock.close()

    def test_6_internal_socks_ready_correct(self):
        orig_lp = server.listener_present
        orig_proc = server.instance_process_alive
        orig_trace = server.trace_for_instance
        try:
            cfg = {"instances": 1, "proxy_mode": "dedicated", "proxy_base_port": 2080}
            server.instance_process_alive = lambda idx: True
            server.trace_for_instance = lambda port, timeout=8: {"warp": "on", "ip": "1.2.3.4"}

            server.listener_present = lambda port, *a, **kw: port in (40000, 2080)
            item = server.refresh_instance(0, cfg)
            self.assertTrue(item["internal_socks_ready"])

            server.listener_present = lambda port, *a, **kw: port == 2080
            item = server.refresh_instance(0, cfg)
            self.assertFalse(item["internal_socks_ready"])
        finally:
            server.listener_present = orig_lp
            server.instance_process_alive = orig_proc
            server.trace_for_instance = orig_trace

    def test_7_dedicated_proxy_ready_correct(self):
        orig_lp = server.listener_present
        orig_proc = server.instance_process_alive
        orig_trace = server.trace_for_instance
        try:
            cfg = {"instances": 1, "proxy_mode": "dedicated", "proxy_base_port": 2080}
            server.instance_process_alive = lambda idx: True
            server.trace_for_instance = lambda port, timeout=8: {"warp": "on", "ip": "1.2.3.4"}

            server.listener_present = lambda port, *a, **kw: port in (40000, 2080)
            item = server.refresh_instance(0, cfg)
            self.assertTrue(item["dedicated_proxy_ready"])

            server.listener_present = lambda port, *a, **kw: port == 40000
            item = server.refresh_instance(0, cfg)
            self.assertFalse(item["dedicated_proxy_ready"])
        finally:
            server.listener_present = orig_lp
            server.instance_process_alive = orig_proc
            server.trace_for_instance = orig_trace

    def test_8_warp_connected_depends_on_real_trace_not_just_listener(self):
        orig_lp = server.listener_present
        orig_trace = server.trace_for_instance
        orig_proc = server.instance_process_alive
        orig_conf = server._WARP_LAST_CONFIRMED.copy()
        try:
            cfg = {"instances": 1, "proxy_mode": "dedicated", "proxy_base_port": 2080}
            server.instance_process_alive = lambda idx: True
            server.listener_present = lambda port, *a, **kw: True
            server._WARP_LAST_CONFIRMED.pop(0, None)

            # Real trace reports warp=off
            server.trace_for_instance = lambda port, timeout=8: {"warp": "off", "ip": "1.2.3.4"}
            item = server.refresh_instance(0, cfg)
            self.assertTrue(item["internal_socks_ready"])
            self.assertTrue(item["dedicated_proxy_ready"])
            self.assertFalse(item["warp_connected"])
            self.assertFalse(item["warp"])
            self.assertNotEqual(item["health"], "healthy")

            # Real trace times out without prior history
            server.trace_for_instance = lambda port, timeout=8: (_ for _ in ()).throw(RuntimeError("timeout"))
            item = server.refresh_instance(0, cfg)
            self.assertFalse(item["warp_connected"])
            self.assertNotEqual(item["health"], "healthy")
        finally:
            server.listener_present = orig_lp
            server.trace_for_instance = orig_trace
            server.instance_process_alive = orig_proc
            server._WARP_LAST_CONFIRMED.clear()
            server._WARP_LAST_CONFIRMED.update(orig_conf)

    def test_9_health_all_true_is_healthy(self):
        orig_lp = server.listener_present
        orig_trace = server.trace_for_instance
        orig_proc = server.instance_process_alive
        orig_wd = server.get_watchdog_instance
        try:
            cfg = {"instances": 1, "proxy_mode": "dedicated", "proxy_base_port": 2080}
            server.instance_process_alive = lambda idx: True
            server.listener_present = lambda port, *a, **kw: True
            server.trace_for_instance = lambda port, timeout=8: {"warp": "on", "ip": "100.64.0.1"}
            server.get_watchdog_instance = lambda idx: {"status": "healthy"}

            item = server.refresh_instance(0, cfg)
            self.assertTrue(item["process_running"])
            self.assertTrue(item["internal_socks_ready"])
            self.assertTrue(item["dedicated_proxy_ready"])
            self.assertTrue(item["warp_connected"])
            self.assertEqual(item["health"], "healthy")
        finally:
            server.listener_present = orig_lp
            server.trace_for_instance = orig_trace
            server.instance_process_alive = orig_proc
            server.get_watchdog_instance = orig_wd

    def test_10_gost_listener_absent_is_degraded(self):
        orig_lp = server.listener_present
        orig_trace = server.trace_for_instance
        orig_proc = server.instance_process_alive
        orig_wd = server.get_watchdog_instance
        try:
            cfg = {"instances": 1, "proxy_mode": "dedicated", "proxy_base_port": 2080}
            server.instance_process_alive = lambda idx: True
            server.listener_present = lambda port, *a, **kw: port == 40000
            server.trace_for_instance = lambda port, timeout=8: {"warp": "on", "ip": "100.64.0.1"}
            server.get_watchdog_instance = lambda idx: {"status": "healthy"}

            item = server.refresh_instance(0, cfg)
            self.assertTrue(item["process_running"])
            self.assertTrue(item["internal_socks_ready"])
            self.assertFalse(item["dedicated_proxy_ready"])
            self.assertTrue(item["warp_connected"])
            self.assertEqual(item["health"], "degraded")
        finally:
            server.listener_present = orig_lp
            server.trace_for_instance = orig_trace
            server.instance_process_alive = orig_proc
            server.get_watchdog_instance = orig_wd

    def test_filter_warp_logs_drops_stats_and_unexpected_eof(self):
        test_input = """2026-09-07T14:30:00 DEBUG warp-network-health-stats: rtt=15ms
2026-09-07T14:30:01 DEBUG main_loop:connect_inner:tunnel_stats_reporting_task: cwnd=200
2026-09-07T14:30:02 INFO MasqueTunnelStatsUpdated(quic_stats: rtt 12)
2026-09-07T14:30:03 INFO warp-connection-stats: sent=5000 recv=10000
2026-09-07T14:30:04 INFO mundane periodic status log
2026-09-07T14:30:05 INFO Tunnel connected successfully
2026-09-07T14:30:06 WARN run: proxy: Socks greeting failed: error=Transient(Custom { kind: UnexpectedEof, error: "failed to fill whole buffer" })
2026-09-07T14:30:07 WARN DNS probe slow
2026-09-07T14:30:08 ERROR Registration token expired
"""
        cmd = f". {ROOT_DIR}/warp-common.sh; printf '%s' \"$INPUT\" | WARP_LOG_LEVEL=warn filter_warp_logs"
        res = subprocess.run(["bash", "-c", cmd], env={**os.environ, "INPUT": test_input}, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        out = res.stdout

        self.assertNotIn("MasqueTunnelStatsUpdated", out)
        self.assertNotIn("warp-network-health-stats", out)
        self.assertNotIn("tunnel_stats_reporting_task", out)
        self.assertNotIn("warp-connection-stats", out)
        self.assertNotIn("mundane periodic status log", out)
        self.assertNotIn("Socks greeting failed", out)

        self.assertIn("Tunnel connected successfully", out)
        self.assertIn("WARN DNS probe slow", out)
        self.assertIn("ERROR Registration token expired", out)

    def test_filter_warp_logs_warn_error_preservation_and_telemetry_drop(self):
        test_input = """INFO warp-connection-stats: sent=5000 recv=10000
DEBUG warp-network-health-stats: rtt=15ms
ERROR warp-connection-stats exporter failed
WARN warp-network-health-stats collection failed
panic: unexpected failure
"""
        cmd = f". '{ROOT_DIR}/warp-common.sh'; printf '%s' \"$INPUT\" | WARP_LOG_LEVEL=warn filter_warp_logs"
        res = subprocess.run(["bash", "-c", cmd], env={**os.environ, "INPUT": test_input}, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        out = res.stdout
        self.assertNotIn("INFO warp-connection-stats", out)
        self.assertNotIn("DEBUG warp-network-health-stats", out)
        self.assertIn("ERROR warp-connection-stats exporter failed", out)
        self.assertIn("WARN warp-network-health-stats collection failed", out)
        self.assertIn("panic: unexpected failure", out)

    def test_filter_warp_logs_socks_greeting_unexpected_eof_selective(self):
        test_input = """WARN Socks greeting failed: unexpected EOF
WARN Socks greeting failed: UnexpectedEof
WARN Socks greeting failed: unexpected-eof
WARN Socks greeting failed: unexpected_eof
WARN Socks greeting failed: unexpectedXeof
ERROR Socks greeting failed: authentication backend unavailable
WARN Socks greeting failed: malformed authentication response
INFO Socks greeting failed: unexpected EOF
"""
        cmd = f". '{ROOT_DIR}/warp-common.sh'; printf '%s' \"$INPUT\" | WARP_LOG_LEVEL=warn filter_warp_logs"
        res = subprocess.run(["bash", "-c", cmd], env={**os.environ, "INPUT": test_input}, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        out = res.stdout
        self.assertNotIn("WARN Socks greeting failed: unexpected EOF", out)
        self.assertNotIn("WARN Socks greeting failed: UnexpectedEof", out)
        self.assertIn("WARN Socks greeting failed: unexpected-eof", out)
        self.assertIn("WARN Socks greeting failed: unexpected_eof", out)
        self.assertIn("WARN Socks greeting failed: unexpectedXeof", out)
        self.assertIn("ERROR Socks greeting failed: authentication backend unavailable", out)
        self.assertIn("WARN Socks greeting failed: malformed authentication response", out)
        self.assertNotIn("INFO Socks greeting failed: unexpected EOF", out)

    def test_filter_warp_logs_ansi_normalization_cases(self):
        esc = chr(27)
        plain_debug = "2026-09-07T14:30:00 DEBUG watchdog: tick periodic\n"
        ansi_debug = f"{esc}[2m2026-09-09T05:37:24.112Z{esc}[0m {esc}[34mDEBUG{esc}[0m {esc}[1mupload_stats{esc}[0m: Starting upload stats\n"
        ansi_trace = f"{esc}[2m2026-09-09T05:37:24.112Z{esc}[0m {esc}[35mTRACE{esc}[0m actor_connectivity: trace handshake step\n"
        ansi_warn = f"{esc}[2m2026-09-09T05:37:24.112Z{esc}[0m {esc}[33m WARN{esc}[0m DNS probe slow\n"
        ansi_error = f"{esc}[2m2026-09-09T05:37:24.112Z{esc}[0m {esc}[31mERROR{esc}[0m Registration token expired\n"
        ansi_quic_debug = f"{esc}[2m2026-09-09T05:37:24.112Z{esc}[0m {esc}[34mDEBUG{esc}[0m {esc}[2mwarp_edge::h3_tun{esc}[0m: Reporting QUIC Stats: sent=500 recv=1000\n"
        ansi_benign_socks = f"{esc}[33m WARN{esc}[0m run: proxy: Socks greeting failed: error=Transient(Custom {{ kind: UnexpectedEof, error: \"failed to fill whole buffer\" }})\n"
        ansi_real_socks_err = f"{esc}[31mERROR{esc}[0m Socks greeting failed: authentication backend unavailable\n"

        warn_input = plain_debug + ansi_debug + ansi_trace + ansi_warn + ansi_error + ansi_quic_debug + ansi_benign_socks + ansi_real_socks_err
        cmd_warn = f". '{ROOT_DIR}/warp-common.sh'; printf '%s' \"$INPUT\" | WARP_LOG_LEVEL=warn filter_warp_logs"
        res_warn = subprocess.run(["bash", "-c", cmd_warn], env={**os.environ, "INPUT": warn_input}, capture_output=True, text=True)
        self.assertEqual(res_warn.returncode, 0)
        out_warn = res_warn.stdout
        self.assertNotIn("watchdog: tick periodic", out_warn)
        self.assertNotIn("upload_stats", out_warn)
        self.assertNotIn("actor_connectivity", out_warn)
        self.assertIn(ansi_warn.strip(), out_warn)
        self.assertIn(ansi_error.strip(), out_warn)
        self.assertNotIn("Reporting QUIC Stats", out_warn)
        self.assertNotIn("UnexpectedEof", out_warn)
        self.assertIn(ansi_real_socks_err.strip(), out_warn)

        error_input = ansi_warn + ansi_error
        cmd_error = f". '{ROOT_DIR}/warp-common.sh'; printf '%s' \"$INPUT\" | WARP_LOG_LEVEL=error filter_warp_logs"
        res_error = subprocess.run(["bash", "-c", cmd_error], env={**os.environ, "INPUT": error_input}, capture_output=True, text=True)
        self.assertEqual(res_error.returncode, 0)
        out_error = res_error.stdout
        self.assertNotIn("DNS probe slow", out_error)
        self.assertIn(ansi_error.strip(), out_error)

    def test_gost_config_contains_log_level_warn(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            cfg_file = tmp / "gost.yaml"
            hl_file = tmp / "healthy.txt"
            cmd = f". {ROOT_DIR}/warp-common.sh; PROXY_LOG_LEVEL=warn WARP_INSTANCES=2 PROXY_BASE_PORT=2080 PROXY_MODE=round-robin generate_gost_config_roundrobin '' '{cfg_file}' '{hl_file}'"
            res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr)

            doc = yaml.safe_load(cfg_file.read_text())
            self.assertIn("log", doc)
            self.assertEqual(doc["log"]["level"], "warn")

    def test_docker_compose_canonical_log_rotation(self):
        compose_file = ROOT_DIR / "docker-compose.yml"
        doc = yaml.safe_load(compose_file.read_text())
        warp_service = doc["services"]["warp"]
        self.assertIn("logging", warp_service)
        logging_cfg = warp_service["logging"]
        self.assertEqual(logging_cfg["driver"], "json-file")
        self.assertEqual(logging_cfg["options"]["max-size"], "20m")
        self.assertEqual(logging_cfg["options"]["max-file"], "5")


    def test_listener_present_uses_no_sockets(self):
        """Prove listener_present and get_listening_ports never touch socket.socket."""
        import unittest.mock as mock
        original_socket = socket.socket

        def exploding_socket(*args, **kwargs):
            raise AssertionError("socket.socket must not be called by listener_present")

        with mock.patch.object(socket, 'socket', exploding_socket):
            # Should work fine via /proc even with socket.socket blocked
            result_2080 = server.listener_present(2080)
            result_40000 = server.listener_present(40000)
            # Also test with pre-computed listening_ports
            lp = server.get_listening_ports()
            result_batch = server.listener_present(2080, listening_ports=lp)

        # Results depend on what's actually listening, but no AssertionError means success
        self.assertIsInstance(result_2080, bool)
        self.assertIsInstance(result_40000, bool)
        self.assertIsInstance(result_batch, bool)

if __name__ == "__main__":
    unittest.main()
