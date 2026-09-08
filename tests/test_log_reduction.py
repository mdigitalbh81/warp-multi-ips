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


class LogReductionAndSocksProbeTests(unittest.TestCase):

    def test_socks5_probe_success_no_unexpected_eof(self):
        ready = threading.Event()
        server_received = []

        def mock_socks_server(sock):
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            port = sock.getsockname()[1]
            ready.set()
            conn, _ = sock.accept()
            data = conn.recv(1024)
            server_received.append(data)
            conn.sendall(b"\x05\x00")
            conn.close()
            sock.close()

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        t = threading.Thread(target=mock_socks_server, args=(s,), daemon=True)
        t.start()
        ready.wait(timeout=2)
        port = s.getsockname()[1]

        res = server.port_open(port, timeout=1.0)
        t.join(timeout=2)

        self.assertTrue(res)
        self.assertEqual(len(server_received), 1)
        self.assertEqual(server_received[0], b"\x05\x02\x00\x02")

    def test_socks5_probe_rejects_non_socks_or_closed_port(self):
        self.assertFalse(server.port_open(39999, timeout=0.1))

        ready = threading.Event()

        def mock_raw_server(sock):
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            ready.set()
            conn, _ = sock.accept()
            conn.sendall(b"HTTP/1.1 200 OK\r\n\r\n")
            conn.close()
            sock.close()

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        t = threading.Thread(target=mock_raw_server, args=(s,), daemon=True)
        t.start()
        ready.wait(timeout=2)
        port = s.getsockname()[1]

        res = server.port_open(port, timeout=1.0)
        t.join(timeout=2)
        self.assertFalse(res)

    def _probe_mock_response(self, response_bytes=None, hang=False):
        ready = threading.Event()

        def mock_server(sock):
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            ready.set()
            try:
                conn, _ = sock.accept()
                _ = conn.recv(1024)
                if hang:
                    time.sleep(0.3)
                elif response_bytes is not None:
                    conn.sendall(response_bytes)
                conn.close()
            except Exception:
                pass
            finally:
                sock.close()

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        t = threading.Thread(target=mock_server, args=(s,), daemon=True)
        t.start()
        ready.wait(timeout=2)
        port = s.getsockname()[1]
        res = server.port_open(port, timeout=0.1 if hang else 1.0)
        t.join(timeout=2)
        return res

    def test_socks5_probe_method_validation(self):
        self.assertTrue(self._probe_mock_response(b"\x05\x00"))
        self.assertTrue(self._probe_mock_response(b"\x05\x02"))
        self.assertFalse(self._probe_mock_response(b"\x05\xff"))
        self.assertFalse(self._probe_mock_response(b"\x05\x01"))
        self.assertFalse(self._probe_mock_response(b"\x04\x00"))
        self.assertFalse(self._probe_mock_response(b"\x05"))
        self.assertFalse(self._probe_mock_response(b""))
        self.assertFalse(self._probe_mock_response(hang=True))

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
        test_input = """WARN Socks greeting failed: UnexpectedEof
ERROR Socks greeting failed: authentication backend unavailable
WARN Socks greeting failed: malformed authentication response
INFO Socks greeting failed: unexpected EOF
"""
        cmd = f". '{ROOT_DIR}/warp-common.sh'; printf '%s' \"$INPUT\" | WARP_LOG_LEVEL=warn filter_warp_logs"
        res = subprocess.run(["bash", "-c", cmd], env={**os.environ, "INPUT": test_input}, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        out = res.stdout
        self.assertNotIn("WARN Socks greeting failed: UnexpectedEof", out)
        self.assertIn("ERROR Socks greeting failed: authentication backend unavailable", out)
        self.assertIn("WARN Socks greeting failed: malformed authentication response", out)
        self.assertNotIn("INFO Socks greeting failed: unexpected EOF", out)

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


if __name__ == "__main__":
    unittest.main()
