#!/usr/bin/env python3

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlparse

DATA_DIR = Path(os.environ.get("WARP_DATA_DIR", "/var/lib/cloudflare-warp"))
CONFIG_FILE = Path(os.environ.get("ADMIN_CONFIG_FILE", DATA_DIR / "admin-config.json"))
CREDENTIALS_FILE = Path(os.environ.get("ADMIN_CREDENTIALS_FILE", DATA_DIR / "admin-credentials.json"))
ENV_FILE = Path(os.environ.get("WARP_ENV_FILE", "/tmp/warp-admin-env"))
GOST_CONFIG_FILE = Path(os.environ.get("GOST_CONFIG_FILE", "/tmp/gost-config.yaml"))
HEALTHY_PORTS_FILE = Path(os.environ.get("HEALTHY_PORTS_FILE", "/tmp/healthy-warp-ports"))
GOST_RESTART_FILE = Path(os.environ.get("GOST_RESTART_FILE", "/tmp/gost-restart-request"))
STATIC_DIR = Path(os.environ.get("ADMIN_STATIC_DIR", Path(__file__).resolve().parent / "static"))
TRACE_URL = "https://www.cloudflare.com/cdn-cgi/trace"
COMMON_SH = "/warp-common.sh"
MAX_INSTANCES = int(os.environ.get("ADMIN_MAX_INSTANCES", "200"))
INITIAL_ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
INITIAL_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ADMIN_AUTH_MAX_FAILURES = int(os.environ.get("ADMIN_AUTH_MAX_FAILURES", "5"))
ADMIN_AUTH_WINDOW_SECONDS = int(os.environ.get("ADMIN_AUTH_WINDOW_SECONDS", "300"))
ADMIN_AUTH_BLOCK_SECONDS = int(os.environ.get("ADMIN_AUTH_BLOCK_SECONDS", "600"))
ADMIN_AUTH_MAX_IPS = int(os.environ.get("ADMIN_AUTH_MAX_IPS", "2048"))
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    ),
}

CONFIG_LOCK = threading.RLock()
CREDENTIALS_LOCK = threading.RLock()
REFRESH_LOCK = threading.RLock()
AUTH_RATE_LOCK = threading.RLock()
AUTH_FAILURES = {}
STATE = {
    "egress": {},
    "operation": None,
    "last_refresh_started": None,
    "last_refresh_finished": None,
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path, default):
    try:
        with path.open() as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


class BadRequest(ValueError):
    pass


def write_json_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except PermissionError:
        result = subprocess.run(
            ["sudo", "tee", str(path)],
            input=json.dumps(data, indent=2) + "\n",
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            raise PermissionError(result.stderr.strip() or f"cannot write {path}")


def write_secret_json(path, data):
    write_json_atomic(path, data)
    try:
        path.chmod(0o600)
    except PermissionError:
        subprocess.run(["sudo", "chmod", "600", str(path)], capture_output=True)


def parse_bool(value):
    return str(value).lower() in ("1", "true", "yes", "on")


def password_policy_errors(password):
    errors = []
    if not password:
        return ["new password is required"]
    if len(password) < 12:
        errors.append("new password must be at least 12 characters")
    classes = [
        bool(re.search(r"[a-z]", password)),
        bool(re.search(r"[A-Z]", password)),
        bool(re.search(r"[0-9]", password)),
        bool(re.search(r"[^A-Za-z0-9]", password)),
    ]
    if sum(classes) < 3:
        errors.append("new password must include at least three of: lowercase, uppercase, number, symbol")
    return errors


def valid_username(username):
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", username or ""))


def hash_password(password):
    salt = secrets.token_bytes(16)
    iterations = 600000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def verify_password(password, encoded):
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            base64.b64decode(salt),
            int(iterations),
        )
        return hmac.compare_digest(base64.b64encode(digest).decode(), expected)
    except Exception:
        return False


def read_admin_credentials():
    return read_json(CREDENTIALS_FILE, {})


def write_admin_credentials(username, password):
    write_secret_json(CREDENTIALS_FILE, {
        "username": username,
        "password_hash": hash_password(password),
        "updated_at": utc_now(),
    })


def ensure_admin_credentials():
    with CREDENTIALS_LOCK:
        stored = read_admin_credentials()
        if stored.get("username") and stored.get("password_hash"):
            return True, None

        username = INITIAL_ADMIN_USER.strip()
        password = INITIAL_ADMIN_PASSWORD
        if not username or not password:
            return False, "ADMIN_USER and ADMIN_PASSWORD are required when ADMIN_ENABLED=true"
        if not valid_username(username):
            return False, "ADMIN_USER must be 3-64 characters and contain only letters, numbers, dot, underscore, or hyphen"
        errors = password_policy_errors(password)
        if errors:
            return False, "ADMIN_PASSWORD is not strong enough: " + "; ".join(errors)
        write_admin_credentials(username, password)
        return True, None


def authenticate_admin(username, password):
    stored = read_admin_credentials()
    return (
        bool(username)
        and bool(password)
        and username == stored.get("username")
        and verify_password(password, stored.get("password_hash", ""))
    )


def cleanup_auth_failures(now=None):
    now = now or time.time()
    cutoff = now - ADMIN_AUTH_WINDOW_SECONDS
    with AUTH_RATE_LOCK:
        for ip in list(AUTH_FAILURES):
            record = AUTH_FAILURES[ip]
            record["failures"] = [ts for ts in record.get("failures", []) if ts >= cutoff]
            if not record["failures"] and record.get("blocked_until", 0) <= now:
                AUTH_FAILURES.pop(ip, None)

        if len(AUTH_FAILURES) <= ADMIN_AUTH_MAX_IPS:
            return
        for ip, _record in sorted(AUTH_FAILURES.items(), key=lambda item: item[1].get("last_seen", 0)):
            if len(AUTH_FAILURES) <= ADMIN_AUTH_MAX_IPS:
                break
            AUTH_FAILURES.pop(ip, None)


def auth_retry_after(ip, now=None):
    now = now or time.time()
    with AUTH_RATE_LOCK:
        record = AUTH_FAILURES.get(ip)
        if not record:
            return 0
        blocked_until = record.get("blocked_until", 0)
        if blocked_until <= now:
            return 0
        return max(1, int(blocked_until - now))


def record_auth_failure(ip, now=None):
    now = now or time.time()
    cleanup_auth_failures(now)
    cutoff = now - ADMIN_AUTH_WINDOW_SECONDS
    with AUTH_RATE_LOCK:
        record = AUTH_FAILURES.setdefault(ip, {"failures": [], "blocked_until": 0, "last_seen": now})
        record["last_seen"] = now
        record["failures"] = [ts for ts in record.get("failures", []) if ts >= cutoff]
        record["failures"].append(now)
        if len(record["failures"]) >= ADMIN_AUTH_MAX_FAILURES:
            record["blocked_until"] = now + ADMIN_AUTH_BLOCK_SECONDS
        retry_after = auth_retry_after(ip, now)
    cleanup_auth_failures(now)
    return retry_after


def clear_auth_failures(ip):
    with AUTH_RATE_LOCK:
        AUTH_FAILURES.pop(ip, None)


def public_admin_account():
    stored = read_admin_credentials()
    return {
        "username": stored.get("username", ""),
        "credentials_configured": bool(stored.get("username") and stored.get("password_hash")),
    }


def update_admin_credentials(body):
    current_password = body.get("current_password", "")
    new_username = (body.get("new_username") or "").strip()
    new_password = body.get("new_password") or ""
    confirm_password = body.get("confirm_password") or ""

    stored = read_admin_credentials()
    username = stored.get("username", "")
    if not verify_password(current_password, stored.get("password_hash", "")):
        return {"ok": False, "errors": ["current password is incorrect"]}, 400

    target_username = new_username or username
    if not valid_username(target_username):
        return {"ok": False, "errors": ["new username must be 3-64 characters and contain only letters, numbers, dot, underscore, or hyphen"]}, 400

    changing_password = bool(new_password or confirm_password)
    if changing_password:
        if new_password != confirm_password:
            return {"ok": False, "errors": ["new password and confirmation do not match"]}, 400
        errors = password_policy_errors(new_password)
        if errors:
            return {"ok": False, "errors": errors}, 400
    elif target_username == username:
        return {"ok": False, "errors": ["provide a new username or new password"]}, 400

    write_secret_json(CREDENTIALS_FILE, {
        "username": target_username,
        "password_hash": hash_password(new_password) if changing_password else stored["password_hash"],
        "updated_at": utc_now(),
    })
    return {
        "ok": True,
        "message": "Administrator credentials updated successfully.",
        "account": public_admin_account(),
    }, 200


def read_env_file():
    env = {}
    if not ENV_FILE.exists():
        return env
    for line in ENV_FILE.read_text().splitlines():
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key] = value
    return env


def base_config():
    env = read_env_file()
    return {
        "instances": int(env.get("WARP_INSTANCES") or os.environ.get("WARP_INSTANCES", "1")),
        "proxy_mode": env.get("PROXY_MODE") or os.environ.get("PROXY_MODE", "round-robin"),
        "proxy_base_port": int(env.get("PROXY_BASE_PORT") or os.environ.get("PROXY_BASE_PORT", "2080")),
        "proxy_max_rps": int(env.get("PROXY_MAX_RPS") or os.environ.get("PROXY_MAX_RPS", "50")),
        "warp_connect_timeout": int(env.get("WARP_CONNECT_TIMEOUT") or os.environ.get("WARP_CONNECT_TIMEOUT", "30")),
        "auto_refresh_interval": int(env.get("AUTO_REFRESH_INTERVAL") or os.environ.get("AUTO_REFRESH_INTERVAL", "60")),
        "proxy_auth_enabled": parse_bool(env.get("PROXY_AUTH_ENABLED", "false")),
        "proxy_user": env.get("PROXY_USER", ""),
        "proxy_password": os.environ.get("PROXY_PASS", "") if parse_bool(env.get("PROXY_AUTH_ENABLED", "false")) else "",
    }


def get_config(include_secret=False):
    cfg = base_config()
    stored = read_json(CONFIG_FILE, {})
    cfg.update(stored)
    cfg["instances"] = int(cfg.get("instances", 1))
    cfg["proxy_base_port"] = int(cfg.get("proxy_base_port", 2080))
    cfg["proxy_max_rps"] = int(cfg.get("proxy_max_rps", 50))
    cfg["warp_connect_timeout"] = int(cfg.get("warp_connect_timeout", 30))
    cfg["auto_refresh_interval"] = int(cfg.get("auto_refresh_interval", 60))
    cfg["proxy_auth_enabled"] = bool(cfg.get("proxy_auth_enabled", False))
    cfg["proxy_user"] = cfg.get("proxy_user") or ""
    if include_secret:
        cfg["proxy_password"] = cfg.get("proxy_password") or ""
    else:
        cfg.pop("proxy_password", None)
        cfg["proxy_password_set"] = bool(stored.get("proxy_password"))
    return cfg


def validate_config(cfg):
    errors = []
    instances = cfg.get("instances")
    mode = cfg.get("proxy_mode")
    base_port = cfg.get("proxy_base_port")
    max_rps = cfg.get("proxy_max_rps")
    timeout = cfg.get("warp_connect_timeout")
    interval = cfg.get("auto_refresh_interval")

    if not isinstance(instances, int) or instances < 1 or instances > MAX_INSTANCES:
        errors.append(f"instances must be between 1 and {MAX_INSTANCES}")
    if mode not in ("round-robin", "dedicated"):
        errors.append("proxy_mode must be round-robin or dedicated")
    for name, value in (
        ("proxy_base_port", base_port),
        ("proxy_max_rps", max_rps),
        ("warp_connect_timeout", timeout),
        ("auto_refresh_interval", interval),
    ):
        if not isinstance(value, int):
            errors.append(f"{name} must be an integer")
    if isinstance(base_port, int) and (base_port < 1 or base_port > 65535):
        errors.append("proxy_base_port must be between 1 and 65535")
    if isinstance(max_rps, int) and (max_rps < 1 or max_rps > 100000):
        errors.append("proxy_max_rps must be between 1 and 100000")
    if isinstance(timeout, int) and (timeout < 5 or timeout > 600):
        errors.append("warp_connect_timeout must be between 5 and 600 seconds")
    if isinstance(interval, int) and (interval < 15 or interval > 3600):
        errors.append("auto_refresh_interval must be between 15 and 3600 seconds")
    if (
        mode == "dedicated"
        and isinstance(base_port, int)
        and isinstance(instances, int)
        and base_port + instances - 1 > 65535
    ):
        errors.append("dedicated proxy ports exceed TCP port range")
    if isinstance(base_port, int) and isinstance(instances, int) and 1 <= instances <= MAX_INSTANCES:
        fixed_ports = {1081, 8080, 8081, 8388, 8389, int(os.environ.get("ADMIN_PORT", "9090"))}
        dedicated_ports = set(range(base_port, base_port + max(instances, 0)))
        conflicts = sorted(fixed_ports.intersection(dedicated_ports))
        if mode == "dedicated" and conflicts:
            errors.append(f"dedicated proxy ports conflict with fixed ports: {conflicts}")
        internal_conflicts = sorted(dedicated_ports.intersection(range(40000, 40000 + max(instances, 0))))
        if mode == "dedicated" and internal_conflicts:
            errors.append(f"dedicated proxy ports conflict with internal WARP ports: {internal_conflicts}")
    if cfg.get("proxy_auth_enabled"):
        if not cfg.get("proxy_user"):
            errors.append("proxy_user is required when proxy authentication is enabled")
        if not cfg.get("proxy_password"):
            errors.append("proxy password is required when enabling proxy authentication")
    return errors


def public_config():
    cfg = get_config(False)
    cfg["admin_enabled"] = parse_bool(os.environ.get("ADMIN_ENABLED", "false"))
    cfg["admin_port"] = int(os.environ.get("ADMIN_PORT", "9090"))
    cfg["config_source"] = "persistent" if CONFIG_FILE.exists() else "environment"
    cfg["admin_account"] = public_admin_account()
    return cfg


def port_open(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.35)
        return sock.connect_ex(("127.0.0.1", int(port))) == 0


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (ProcessLookupError, ValueError):
        return False
    except PermissionError:
        return True


def instance_process_alive(index):
    pid_file = Path(f"/tmp/warp-instance-{index}.pid")
    if pid_file.exists() and pid_alive(pid_file.read_text().strip()):
        return True
    result = subprocess.run(["pgrep", "-f", f"STATE_DIRECTORY=.*instance-{index}"], capture_output=True)
    return result.returncode == 0


def proxy_url(port, cfg):
    if cfg.get("proxy_auth_enabled") and cfg.get("proxy_user") and cfg.get("proxy_password"):
        username = quote(str(cfg["proxy_user"]), safe="")
        password = quote(str(cfg["proxy_password"]), safe="")
        return f"socks5h://{username}:{password}@127.0.0.1:{port}"
    return f"socks5h://127.0.0.1:{port}"


def trace_for_proxy(port, cfg):
    result = subprocess.run(
        ["curl", "-fsS", "--max-time", "20", "--proxy", proxy_url(port, cfg), TRACE_URL],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "trace request failed")
    data = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            data[key] = value
    return data


def refresh_instance(index, cfg):
    proxy_port = cfg["proxy_base_port"] + index if cfg["proxy_mode"] == "dedicated" else 1080
    internal_port = 40000 + index
    item = {
        "instance": index + 1,
        "proxy_port": proxy_port,
        "internal_port": internal_port,
        "egress_ip": None,
        "warp": False,
        "colo": None,
        "location": None,
        "process_healthy": instance_process_alive(index),
        "proxy_healthy": port_open(proxy_port),
        "listener_healthy": port_open(proxy_port),
        "internal_healthy": port_open(internal_port),
        "last_check": utc_now(),
        "error": None,
    }
    try:
        trace = trace_for_proxy(proxy_port, cfg)
        item["egress_ip"] = trace.get("ip")
        item["warp"] = trace.get("warp") in ("on", "plus")
        item["colo"] = trace.get("colo")
        item["location"] = trace.get("loc")
        item["proxy_healthy"] = True
        item["health"] = "healthy" if item["warp"] and item["process_healthy"] else "degraded"
    except Exception as exc:
        item["error"] = str(exc)
        item["health"] = "degraded" if item["process_healthy"] or item["proxy_healthy"] else "unavailable"
    return item


def refresh_all(force=False):
    cfg = get_config(True)
    with REFRESH_LOCK:
        if not force and STATE["last_refresh_started"]:
            age = time.time() - STATE["last_refresh_started"]
            if age < cfg["auto_refresh_interval"] and STATE["egress"]:
                return list(STATE["egress"].values())
        STATE["last_refresh_started"] = time.time()
        results = {}
        results_lock = threading.Lock()
        threads = []

        def worker(i):
            item = refresh_instance(i, cfg)
            with results_lock:
                results[i + 1] = item

        for idx in range(cfg["instances"]):
            t = threading.Thread(target=worker, args=(idx,), daemon=True)
            threads.append(t)
            t.start()
        for t in threads:
            t.join(timeout=30)
        for idx in range(cfg["instances"]):
            results.setdefault(idx + 1, {
                "instance": idx + 1,
                "proxy_port": cfg["proxy_base_port"] + idx if cfg["proxy_mode"] == "dedicated" else 1080,
                "internal_port": 40000 + idx,
                "egress_ip": None,
                "warp": False,
                "colo": None,
                "location": None,
                "process_healthy": instance_process_alive(idx),
                "proxy_healthy": False,
                "listener_healthy": False,
                "internal_healthy": port_open(40000 + idx),
                "health": "unavailable",
                "last_check": utc_now(),
                "error": "refresh timed out",
            })
        STATE["egress"] = results
        STATE["last_refresh_finished"] = time.time()
        return [results[i + 1] for i in range(cfg["instances"])]


def get_instances():
    cfg = get_config(False)
    now = time.time()
    last = STATE.get("last_refresh_finished")
    if not last or now - last > cfg["auto_refresh_interval"]:
        threading.Thread(target=refresh_all, kwargs={"force": True}, daemon=True).start()
    cached = STATE["egress"]
    items = []
    for idx in range(cfg["instances"]):
        existing = cached.get(idx + 1, {})
        item = {
            "instance": idx + 1,
            "proxy_port": cfg["proxy_base_port"] + idx if cfg["proxy_mode"] == "dedicated" else 1080,
            "internal_port": 40000 + idx,
            "egress_ip": None,
            "warp": False,
            "colo": None,
            "location": None,
            "process_healthy": instance_process_alive(idx),
            "proxy_healthy": port_open(cfg["proxy_base_port"] + idx if cfg["proxy_mode"] == "dedicated" else 1080),
            "listener_healthy": port_open(cfg["proxy_base_port"] + idx if cfg["proxy_mode"] == "dedicated" else 1080),
            "internal_healthy": port_open(40000 + idx),
            "health": "unknown",
            "last_check": None,
            "error": None,
        }
        item.update(existing)
        if item["health"] == "unknown":
            if item["process_healthy"] and item["proxy_healthy"]:
                item["health"] = "degraded"
            elif not item["process_healthy"] and not item["proxy_healthy"]:
                item["health"] = "unavailable"
        items.append(item)
    return items


def healthy_verify_dir(cfg):
    tmp = Path(tempfile.mkdtemp(prefix="warp-verify-"))
    for idx in range(cfg["instances"]):
        if port_open(40000 + idx):
            (tmp / str(idx)).write_text("OK\n")
    return tmp


def run_shell_script(script, env):
    result = subprocess.run(["bash", "-lc", script], env=env, text=True, capture_output=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return result.stdout


def regenerate_gost(cfg):
    env = os.environ.copy()
    env.update({
        "WARP_INSTANCES": str(cfg["instances"]),
        "PROXY_MODE": cfg["proxy_mode"],
        "PROXY_BASE_PORT": str(cfg["proxy_base_port"]),
        "PROXY_MAX_RPS": str(cfg["proxy_max_rps"]),
        "WARP_CONNECT_TIMEOUT": str(cfg["warp_connect_timeout"]),
        "AUTO_REFRESH_INTERVAL": str(cfg["auto_refresh_interval"]),
        "PROXY_USER": cfg.get("proxy_user", "") if cfg.get("proxy_auth_enabled") else "",
        "PROXY_PASS": cfg.get("proxy_password", "") if cfg.get("proxy_auth_enabled") else "",
    })
    verify_dir = healthy_verify_dir(cfg)
    func = "generate_gost_config_dedicated" if cfg["proxy_mode"] == "dedicated" else "generate_gost_config_roundrobin"
    try:
        run_shell_script(f". {COMMON_SH}; {func} {verify_dir} {GOST_CONFIG_FILE} {HEALTHY_PORTS_FILE}", env)
    finally:
        subprocess.run(["rm", "-rf", str(verify_dir)])


def gost_pids():
    result = subprocess.run(["pgrep", "-x", "gost"], text=True, capture_output=True)
    if result.returncode != 0:
        return []
    return [int(pid) for pid in result.stdout.split()]


def reload_gost(cfg):
    regenerate_gost(cfg)
    # GOST v3 does not provide a documented stable config reload path here.
    # The entrypoint owns GOST as the container's foreground process, so the
    # admin process requests a GOST-only restart instead of killing it directly.
    GOST_RESTART_FILE.write_text(utc_now())
    deadline = time.time() + 20
    target_port = cfg["proxy_base_port"] if cfg["proxy_mode"] == "dedicated" else 1080
    while time.time() < deadline:
        if not GOST_RESTART_FILE.exists() and port_open(target_port):
            return
        time.sleep(0.5)
    if GOST_RESTART_FILE.exists():
        raise RuntimeError("timed out waiting for GOST restart")


def start_instance(index, cfg):
    subprocess.Popen([
        "/start-warp-instance.sh",
        str(index),
        str(40000 + index),
        os.environ.get("LICENSE_KEYS_CSV", os.environ.get("WARP_LICENSE_KEY", "")),
        str(cfg["warp_connect_timeout"]),
    ], env=os.environ.copy())


def stop_instance(index):
    pid_file = Path(f"/tmp/warp-instance-{index}.pid")
    if pid_file.exists():
        try:
            os.kill(int(pid_file.read_text().strip()), signal.SIGTERM)
        except (ProcessLookupError, ValueError):
            pass
        try:
            pid_file.unlink()
        except FileNotFoundError:
            pass
    subprocess.run(["pkill", "-f", f"STATE_DIRECTORY=.*instance-{index}"], capture_output=True)


def wait_internal(index, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_open(40000 + index):
            return True
        time.sleep(2)
    return False


def rollback_config(old_cfg, started_indices, stopped_indices, config_saved):
    errors = []
    for index in reversed(started_indices):
        stop_instance(index)
        STATE["egress"].pop(index + 1, None)
    for index in stopped_indices:
        start_instance(index, old_cfg)
        if not wait_internal(index, old_cfg["warp_connect_timeout"]):
            errors.append(f"WARP instance {index + 1} did not come back during rollback")
    if config_saved:
        try:
            write_json_atomic(CONFIG_FILE, old_cfg)
        except Exception as exc:
            errors.append(f"failed to restore admin config: {exc}")
    try:
        reload_gost(old_cfg)
    except Exception as exc:
        errors.append(f"failed to restore GOST config: {exc}")
    return errors


def apply_config(new_cfg):
    with CONFIG_LOCK:
        old_cfg = get_config(True)
        errors = validate_config(new_cfg)
        if errors:
            return {"ok": False, "errors": errors}, 400
        old_instances = old_cfg["instances"]
        new_instances = new_cfg["instances"]
        total_steps = abs(new_instances - old_instances) + 3
        STATE["operation"] = {
            "status": "running",
            "started": utc_now(),
            "message": "Validating configuration",
            "current": 0,
            "total": total_steps,
        }

        def set_progress(message, current=None):
            op = STATE.get("operation") or {}
            op.update({"status": "running", "message": message})
            if current is not None:
                op["current"] = current
            STATE["operation"] = op

        try:
            step = 0
            started_indices = []
            stopped_indices = []
            config_saved = False
            if new_instances > old_instances:
                for index in range(old_instances, new_instances):
                    step += 1
                    set_progress(f"Starting WARP instance {index + 1}", step)
                    start_instance(index, new_cfg)
                    started_indices.append(index)
                    set_progress(f"Waiting for WARP instance {index + 1}", step)
                    if not wait_internal(index, new_cfg["warp_connect_timeout"]):
                        raise RuntimeError(f"WARP instance {index + 1} did not become ready")
            elif new_instances < old_instances:
                for index in range(new_instances, old_instances):
                    step += 1
                    set_progress(f"Stopping WARP instance {index + 1}", step)
                    stop_instance(index)
                    stopped_indices.append(index)
                    STATE["egress"].pop(index + 1, None)

            step += 1
            set_progress("Saving configuration", step)
            write_json_atomic(CONFIG_FILE, new_cfg)
            config_saved = True
            step += 1
            set_progress("Restarting GOST listeners", step)
            reload_gost(new_cfg)
            step += 1
            set_progress("Refreshing Current Egress IPs", step)
            refresh_all(force=True)
            STATE["operation"] = {
                "status": "idle",
                "finished": utc_now(),
                "message": "Applied",
                "current": total_steps,
                "total": total_steps,
            }
            return {"ok": True, "config": public_config()}, 200
        except Exception as exc:
            rollback_errors = rollback_config(old_cfg, started_indices, stopped_indices, config_saved)
            errors = [str(exc)]
            if rollback_errors:
                errors.append("Rollback errors: " + "; ".join(rollback_errors))
            STATE["operation"] = {
                "status": "error",
                "finished": utc_now(),
                "message": "Failed",
                "error": "; ".join(errors),
                "current": (STATE.get("operation") or {}).get("current", 0),
                "total": total_steps,
            }
            return {"ok": False, "errors": errors}, 500


def parse_basic_auth(header):
    if not header.startswith("Basic "):
        return None, None
    try:
        raw = base64.b64decode(header.split(" ", 1)[1]).decode()
        if ":" not in raw:
            return None, None
        return raw.split(":", 1)
    except Exception:
        return None, None


class Handler(SimpleHTTPRequestHandler):
    server_version = "WarpAdmin/1.0"

    def end_headers(self):
        for header, value in SECURITY_HEADERS.items():
            self.send_header(header, value)
        super().end_headers()

    def translate_path(self, path):
        parsed = urlparse(path)
        rel = parsed.path.lstrip("/") or "index.html"
        return str(STATIC_DIR / rel)

    def client_ip(self):
        return self.client_address[0] if self.client_address else "unknown"

    def require_auth(self):
        ok, error = ensure_admin_credentials()
        if not ok:
            self.send_json({"error": error}, 503)
            return False
        ip = self.client_ip()
        retry_after = auth_retry_after(ip)
        if retry_after:
            self.send_json(
                {"error": "too many authentication failures; try again later"},
                429,
                {"Retry-After": str(retry_after)},
            )
            return False
        got_user, got_password = parse_basic_auth(self.headers.get("Authorization", ""))
        if authenticate_admin(got_user, got_password):
            clear_auth_failures(ip)
            return True
        if self.headers.get("Authorization"):
            retry_after = record_auth_failure(ip)
            if retry_after:
                self.send_json(
                    {"error": "too many authentication failures; try again later"},
                    429,
                    {"Retry-After": str(retry_after)},
                )
                return False
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="WARP Admin"')
        self.end_headers()
        return False

    def send_json(self, data, status=200, headers=None):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for header, value in (headers or {}).items():
            self.send_header(header, value)
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except json.JSONDecodeError as exc:
            raise BadRequest(f"invalid JSON payload: {exc.msg}") from exc

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self.send_json({"ok": True, "time": utc_now()})
            return
        if parsed.path.startswith("/api/") and not self.require_auth():
            return
        if parsed.path == "/api/config":
            self.send_json(public_config())
        elif parsed.path == "/api/admin/account":
            self.send_json(public_admin_account())
        elif parsed.path == "/api/instances":
            self.send_json(get_instances())
        elif parsed.path == "/api/status":
            instances = get_instances()
            healthy = sum(1 for item in instances if item["health"] == "healthy")
            cfg = public_config()
            self.send_json({
                "configured_instances": cfg["instances"],
                "healthy_instances": healthy,
                "proxy_mode": cfg["proxy_mode"],
                "proxy_base_port": cfg["proxy_base_port"],
                "operation": STATE.get("operation"),
                "last_refresh": STATE.get("last_refresh_finished"),
            })
        else:
            if not self.require_auth():
                return
            return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if not self.require_auth():
            return
        if parsed.path in ("/api/refresh", "/api/instances/refresh"):
            self.send_json(refresh_all(force=True))
            return
        if parsed.path == "/api/config":
            try:
                body = self.read_body()
            except BadRequest as exc:
                self.send_json({"ok": False, "errors": [str(exc)]}, 400)
                return
            current = get_config(True)
            new_cfg = current.copy()
            for key in (
                "instances",
                "proxy_mode",
                "proxy_base_port",
                "proxy_max_rps",
                "warp_connect_timeout",
                "auto_refresh_interval",
                "proxy_auth_enabled",
                "proxy_user",
            ):
                if key in body:
                    new_cfg[key] = body[key]
            if body.get("proxy_password"):
                new_cfg["proxy_password"] = body["proxy_password"]
            elif not new_cfg.get("proxy_auth_enabled"):
                new_cfg["proxy_password"] = ""
            try:
                for key in ("instances", "proxy_base_port", "proxy_max_rps", "warp_connect_timeout", "auto_refresh_interval"):
                    new_cfg[key] = int(new_cfg[key])
            except (TypeError, ValueError):
                self.send_json({"ok": False, "errors": ["numeric fields must be valid integers"]}, 400)
                return
            response, status = apply_config(new_cfg)
            self.send_json(response, status)
            return
        if parsed.path == "/api/admin/credentials":
            try:
                body = self.read_body()
            except BadRequest as exc:
                self.send_json({"ok": False, "errors": [str(exc)]}, 400)
                return
            response, status = update_admin_credentials(body)
            self.send_json(response, status)
            return
        self.send_json({"error": "not found"}, 404)


def auto_refresh_loop():
    while True:
        try:
            cfg = get_config(False)
            time.sleep(max(15, int(cfg["auto_refresh_interval"])))
            refresh_all(force=True)
        except Exception:
            time.sleep(60)


def main():
    ok, error = ensure_admin_credentials()
    if not ok:
        print(f"Error: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1)
    threading.Thread(target=auto_refresh_loop, daemon=True).start()
    port = int(os.environ.get("ADMIN_PORT", "9090"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"WARP admin panel listening on :{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
