from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse


DEFAULTS: Dict[str, Dict[str, Any]] = {
    "ollama": {
        "label": "Ollama",
        "autostart_enabled": False,
        "autostop_enabled": False,
        "command": "",
        "args": "serve",
        "cwd": "",
        "health_url": "http://127.0.0.1:11434",
        "host": "127.0.0.1",
        "port": 11434,
        "wait_seconds": 12,
    },
    "gptsovits": {
        "label": "GPT-SoVITS",
        "autostart_enabled": False,
        "autostop_enabled": False,
        "command": "",
        "args": "",
        "cwd": "",
        "health_url": "http://127.0.0.1:9880",
        "host": "127.0.0.1",
        "port": 9880,
        "wait_seconds": 15,
    },
    "napcat": {
        "label": "NapCat",
        "autostart_enabled": False,
        "autostop_enabled": False,
        "command": "",
        "args": "",
        "cwd": "",
        # Common NapCat HTTP ports vary by package; 6099 is frequently used.
        "health_url": "http://127.0.0.1:6099",
        "host": "127.0.0.1",
        "port": 6099,
        "wait_seconds": 12,
    },
}

# If configured port is offline, still treat these as the same service when open.
# Prevents false offline + accidental second launch after restart.
ALTERNATE_PORTS: Dict[str, List[int]] = {
    "napcat": [6099, 3000, 3001, 6700, 8080],
    "ollama": [11434],
    "gptsovits": [9880],
}

# Processes started by this backend in the current process lifetime.
# Only these are eligible for autostop on exit.
_STARTED: Dict[str, Dict[str, Any]] = {}


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _int(value: Any, default: int) -> int:
    try:
        return int(value if value is not None else default)
    except Exception:
        return int(default)


def parse_host_port(
    *,
    host: str = "",
    port: Any = None,
    health_url: str = "",
    default_host: str = "127.0.0.1",
    default_port: int = 80,
) -> tuple[str, int]:
    h = str(host or "").strip()
    p = _int(port, default_port)
    url = str(health_url or "").strip()
    if url:
        parsed = urlparse(url if "://" in url else f"http://{url}")
        if parsed.hostname:
            h = parsed.hostname
        if parsed.port:
            p = int(parsed.port)
    if h.startswith("http://"):
        h = h[len("http://") :]
    if h.startswith("https://"):
        h = h[len("https://") :]
    h = h.split("/")[0]
    if ":" in h and not h.startswith("["):
        maybe_host, maybe_port = h.rsplit(":", 1)
        if maybe_port.isdigit():
            h = maybe_host
            p = int(maybe_port)
    h = h.split("%")[0].strip() or default_host
    if h in {"0.0.0.0", "::", "[::]", "*"}:
        h = default_host
    return h, max(1, min(65535, p))


def is_port_open(host: str, port: int, timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=max(0.1, float(timeout))):
            return True
    except OSError:
        return False


def _windows_create_no_window_flag() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _decode_process_output(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    for encoding in ("utf-8", "gbk", "cp936", "latin-1"):
        try:
            return raw.decode(encoding)
        except Exception:
            continue
    return raw.decode("utf-8", errors="replace")


def find_listening_pids(port: int) -> List[int]:
    """Best-effort: find PIDs listening on a TCP port (Windows netstat)."""
    port_i = int(port or 0)
    if port_i <= 0:
        return []
    pids: List[int] = []
    try:
        if os.name == "nt":
            completed = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"],
                capture_output=True,
                text=False,
                timeout=5,
                check=False,
                creationflags=_windows_create_no_window_flag(),
            )
            stdout = _decode_process_output(completed.stdout)
            needle = f":{port_i}"
            for line in stdout.splitlines():
                text = " ".join(line.split())
                if not text:
                    continue
                upper = text.upper()
                # English: LISTENING; Chinese locale often uses 侦听/LISTENING mixed.
                if "LISTENING" not in upper and "LISTEN" not in upper and "侦听" not in text:
                    continue
                if needle not in text:
                    continue
                # Typical: TCP 0.0.0.0:3000 0.0.0.0:0 LISTENING 12345
                parts = text.split()
                if len(parts) < 4:
                    continue
                local = parts[1] if len(parts) > 1 else ""
                if local and not (
                    local.endswith(needle)
                    or local == needle.lstrip(":")
                    or local.endswith(f"]{needle}")
                ):
                    # avoid matching :30001 when looking for :3000
                    if f"{needle} " not in f"{text} ":
                        continue
                try:
                    pid = int(parts[-1])
                except Exception:
                    continue
                if pid > 0 and pid not in pids:
                    pids.append(pid)
        else:
            completed = subprocess.run(
                ["ss", "-ltnp"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            needle = f":{port_i}"
            for line in (completed.stdout or "").splitlines():
                if needle not in line:
                    continue
                # users:(("name",pid=123,fd=...))
                marker = "pid="
                if marker not in line:
                    continue
                chunk = line.split(marker, 1)[1]
                digits = ""
                for ch in chunk:
                    if ch.isdigit():
                        digits += ch
                    else:
                        break
                if not digits:
                    continue
                pid = int(digits)
                if pid > 0 and pid not in pids:
                    pids.append(pid)
    except Exception:
        return []
    return pids


def _build_launch_argv(command: str, args: List[str]) -> tuple[List[str], int, Any]:
    """Build silent launch argv/flags.

    Windows .bat/.cmd are executed under a hidden cmd.exe (no `start /b`),
    so we keep a process handle long enough for port warmup and tree kill.
    """
    creationflags = 0
    startupinfo = None
    argv = [command, *args]
    if os.name == "nt":
        creationflags = int(
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
        lower = str(command or "").lower()
        if lower.endswith((".bat", ".cmd")):
            # Keep cmd as parent until the bat returns. Avoid `start /b`
            # which detaches immediately and breaks PID tracking/status.
            argv = ["cmd.exe", "/d", "/c", "call", command, *args]
        elif lower.endswith(".ps1"):
            argv = [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-WindowStyle",
                "Hidden",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                command,
                *args,
            ]
        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # SW_HIDE
        except Exception:
            startupinfo = None
    return argv, creationflags, startupinfo


def candidate_ports(service_id: str, primary_port: int) -> List[int]:
    sid = str(service_id or "").strip().lower()
    ports: List[int] = []
    primary = int(primary_port or 0)
    if primary > 0:
        ports.append(primary)
    for item in ALTERNATE_PORTS.get(sid) or []:
        value = int(item or 0)
        if value > 0 and value not in ports:
            ports.append(value)
    return ports


def resolve_live_endpoint(
    *,
    service_id: str,
    host: str,
    port: int,
    health_url: str = "",
    timeout: float = 0.6,
) -> Dict[str, Any]:
    """Find a live host/port for a service, including common alternates."""
    h = str(host or "127.0.0.1").strip() or "127.0.0.1"
    primary = int(port or 0)
    for candidate in candidate_ports(service_id, primary):
        if is_port_open(h, candidate, timeout=timeout):
            url = str(health_url or "").strip()
            if candidate != primary or not url:
                url = f"http://{h}:{candidate}"
            return {
                "running": True,
                "host": h,
                "port": candidate,
                "health_url": url,
                "matched_primary": candidate == primary,
                "listening_pids": find_listening_pids(candidate),
            }
    return {
        "running": False,
        "host": h,
        "port": primary,
        "health_url": str(health_url or f"http://{h}:{primary}"),
        "matched_primary": False,
        "listening_pids": [],
    }


def probe_service_health(
    *,
    host: str,
    port: int,
    health_url: str = "",
    timeout: float = 1.2,
    service_id: str = "",
) -> Dict[str, Any]:
    """Detect online status with TCP first, then optional HTTP probe.

    Online means the service accepts connections on host:port.
    If service_id is provided, common alternate ports are also accepted so a
    running NapCat on 6099 is not reported offline when config still says 3000.
    """
    endpoint = resolve_live_endpoint(
        service_id=service_id,
        host=host,
        port=port,
        health_url=health_url,
        timeout=min(timeout, 0.8),
    )
    live_host = str(endpoint.get("host") or host)
    live_port = int(endpoint.get("port") or port or 0)
    tcp_ok = bool(endpoint.get("running"))
    http_status = None
    http_ok = False
    detail = ""
    url = str(endpoint.get("health_url") or health_url or "").strip()
    if not url:
        url = f"http://{live_host}:{live_port}"
    listening_pids = list(endpoint.get("listening_pids") or [])
    if tcp_ok:
        try:
            import urllib.request

            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=max(0.3, float(timeout))) as resp:
                http_status = int(getattr(resp, "status", 0) or 0)
                http_ok = 200 <= http_status < 500
                detail = f"HTTP {http_status}"
        except Exception as exc:
            # TCP open but HTTP failed/refused path still means process is listening.
            detail = f"TCP open, HTTP probe failed: {exc}"
            http_ok = False
        if not bool(endpoint.get("matched_primary")) and int(port or 0) and live_port != int(port):
            detail = (
                f"在备用端口 {live_host}:{live_port} 在线"
                f"（配置端口为 {port}）"
                + (f" · {detail}" if detail else "")
            )
        if listening_pids:
            detail = (detail + f" · pid={listening_pids[0]}").strip(" ·")
    else:
        detail = f"TCP {host}:{port} unreachable"
    return {
        "running": bool(tcp_ok),
        "tcp_ok": bool(tcp_ok),
        "http_ok": bool(http_ok),
        "http_status": http_status,
        "health_url": url,
        "live_host": live_host,
        "live_port": live_port,
        "matched_primary": bool(endpoint.get("matched_primary")),
        "listening_pids": listening_pids,
        "detail": detail,
    }


def _split_args(raw: str) -> List[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        import shlex

        return [part for part in shlex.split(text, posix=os.name != "nt") if part]
    except Exception:
        return [part for part in text.split() if part]


def _resolve_command(service_id: str, command: str) -> str:
    cmd = str(command or "").strip().strip('"')
    if cmd and Path(cmd).exists():
        return str(Path(cmd))
    if cmd and shutil.which(cmd):
        return str(shutil.which(cmd))
    if service_id == "ollama":
        which = shutil.which("ollama")
        if which:
            return which
        local = os.environ.get("LOCALAPPDATA") or ""
        candidates = [
            Path(local) / "Programs" / "Ollama" / "ollama.exe",
            Path(local) / "Ollama" / "ollama.exe",
            Path(r"C:\Program Files\Ollama\ollama.exe"),
        ]
        for item in candidates:
            if item.exists():
                return str(item)
    return cmd


def _resolve_launch_spec(
    service_id: str,
    command: str,
    args: List[str],
    cwd: str,
) -> Dict[str, Any]:
    """Resolve a configured executable or program directory to a direct entrypoint."""
    sid = str(service_id or "").strip().lower()
    resolved = _resolve_command(sid, command)
    path = Path(resolved)
    configured_cwd = str(cwd or "").strip()
    launch_args = list(args or [])

    if sid == "gptsovits" and path.is_file() and path.suffix.lower() in {".bat", ".cmd"}:
        root = path.parent
        api = root / "api_v2.py"
        interpreters = [root / "runtime" / "pythonw.exe", root / "runtime" / "python.exe"]
        interpreter = next((item for item in interpreters if item.is_file()), None)
        if api.is_file() and interpreter is not None:
            return {
                "ok": True,
                "command": str(interpreter),
                "args": [str(api), *launch_args],
                "cwd": configured_cwd or str(root),
            }

    if sid == "napcat" and path.is_file() and path.suffix.lower() in {".bat", ".cmd"}:
        entry = path.parent / "NapCatWinBootMain.exe"
        if entry.is_file():
            account_args = launch_args
            if not account_args:
                script = path.read_text(encoding="utf-8", errors="ignore")
                match = re.search(r"NapCatWinBootMain\.exe\"?\s+(\d+)\b", script, re.IGNORECASE)
                account_args = [match.group(1)] if match else []
            return {
                "ok": True,
                "command": str(entry),
                "args": account_args,
                "cwd": configured_cwd or str(path.parent),
            }

    if not path.is_dir():
        return {
            "ok": True,
            "command": resolved,
            "args": launch_args,
            "cwd": configured_cwd,
        }

    if sid == "gptsovits":
        api = path / "api_v2.py"
        interpreters = [path / "runtime" / "pythonw.exe", path / "runtime" / "python.exe"]
        interpreter = next((item for item in interpreters if item.is_file()), None)
        if api.is_file() and interpreter is not None:
            return {
                "ok": True,
                "command": str(interpreter),
                "args": [str(api), *launch_args],
                "cwd": configured_cwd or str(path),
            }

    elif sid == "napcat":
        candidates = sorted(
            (
                item
                for item in path.rglob("NapCatWinBootMain.exe")
                if item.is_file()
            ),
            key=lambda item: str(item).casefold(),
        )
        account_entries = [
            item
            for item in candidates
            if item.parent.name.lower().startswith("napcat.")
            and item.parent.name.lower().endswith(".shell")
        ]
        preferred = account_entries or [item for item in candidates if item.parent == path]
        preferred = preferred or candidates
        if len(preferred) == 1:
            entry = preferred[0]
            return {
                "ok": True,
                "command": str(entry),
                "args": launch_args,
                "cwd": configured_cwd or str(entry.parent),
            }
        if len(preferred) > 1:
            return {
                "ok": False,
                "error": "ambiguous_command",
                "message": "NapCat 目录中发现多个账号启动入口，请改填具体 NapCat.*.Shell 目录",
                "candidates": [str(item) for item in preferred],
            }

    elif sid == "ollama":
        entry = path / "ollama.exe"
        if entry.is_file():
            return {
                "ok": True,
                "command": str(entry),
                "args": launch_args,
                "cwd": configured_cwd or str(path),
            }

    return {
        "ok": False,
        "error": "directory_entry_not_found",
        "message": f"无法在 {path} 中识别 {sid or 'service'} 的启动入口",
        "candidates": [],
    }


def service_ids() -> List[str]:
    return list(DEFAULTS.keys())


def normalize_service_config(service_id: str, raw: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    sid = str(service_id or "").strip().lower()
    base = dict(DEFAULTS.get(sid) or {"label": sid or "service"})
    data = {**base, **_as_dict(raw)}
    host, port = parse_host_port(
        host=str(data.get("host") or base.get("host") or "127.0.0.1"),
        port=data.get("port", base.get("port") or 80),
        health_url=str(data.get("health_url") or base.get("health_url") or ""),
        default_host=str(base.get("host") or "127.0.0.1"),
        default_port=_int(base.get("port"), 80),
    )
    command = str(data.get("command") or "").strip()
    if not command and sid == "ollama":
        command = _resolve_command(sid, "")
    args = data.get("args")
    if isinstance(args, list):
        args_text = " ".join(str(item) for item in args if str(item).strip())
    else:
        args_text = str(args or base.get("args") or "").strip()
    return {
        "id": sid,
        "label": str(data.get("label") or base.get("label") or sid),
        "autostart_enabled": _bool(data.get("autostart_enabled"), False),
        "autostop_enabled": _bool(data.get("autostop_enabled"), False),
        "command": command,
        "args": args_text,
        "cwd": str(data.get("cwd") or "").strip(),
        "health_url": str(data.get("health_url") or base.get("health_url") or f"http://{host}:{port}"),
        "host": host,
        "port": port,
        "wait_seconds": max(1, _int(data.get("wait_seconds"), _int(base.get("wait_seconds"), 10))),
    }


def load_services_settings(runtime: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
    data = _as_dict(runtime)
    block = _as_dict(data.get("external_services"))
    # Back-compat with earlier ollama-only key.
    if "ollama_autostart_enabled" in data and "ollama" not in block:
        block = {
            **block,
            "ollama": {
                **DEFAULTS["ollama"],
                "autostart_enabled": _bool(data.get("ollama_autostart_enabled"), False),
            },
        }
    out: Dict[str, Dict[str, Any]] = {}
    for sid in service_ids():
        out[sid] = normalize_service_config(sid, block.get(sid))
    return out


def dump_services_settings(services: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for sid, raw in (services or {}).items():
        cfg = normalize_service_config(sid, raw)
        out[sid] = {
            "autostart_enabled": bool(cfg["autostart_enabled"]),
            "autostop_enabled": bool(cfg["autostop_enabled"]),
            "command": str(cfg["command"] or ""),
            "args": str(cfg["args"] or ""),
            "cwd": str(cfg["cwd"] or ""),
            "health_url": str(cfg["health_url"] or ""),
            "host": str(cfg["host"] or ""),
            "port": int(cfg["port"] or 0),
            "wait_seconds": int(cfg["wait_seconds"] or 10),
        }
    return out


def service_status(service_id: str, runtime: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    services = load_services_settings(runtime)
    cfg = services.get(str(service_id).lower()) or normalize_service_config(service_id, {})
    sid = str(service_id).lower()
    probe = probe_service_health(
        host=str(cfg["host"]),
        port=int(cfg["port"]),
        health_url=str(cfg.get("health_url") or ""),
        service_id=sid,
    )
    running = bool(probe.get("running"))
    started = _STARTED.get(sid) or {}
    # Prefer a live listening PID when available; keep our managed PID as fallback.
    listening = list(probe.get("listening_pids") or [])
    managed_pid = int(started.get("pid") or 0) or None
    live_pid = int(listening[0]) if listening else managed_pid
    live_host = str(probe.get("live_host") or cfg["host"])
    live_port = int(probe.get("live_port") or cfg["port"] or 0)
    if running and started and live_pid and live_pid != managed_pid:
        # Refresh managed PID to the real listener so later stop is accurate.
        started = {
            **started,
            "pid": int(live_pid),
            "listener_pids": listening,
            "adopted_at": time.time(),
        }
        _STARTED[sid] = started
        managed_pid = int(live_pid)
    if running:
        message = f"{cfg['label']} 在线 ({live_host}:{live_port})"
        if probe.get("detail"):
            message = f"{message} · {probe.get('detail')}"
    else:
        message = (
            f"{cfg['label']} 未运行 · 探测 {cfg['host']}:{cfg['port']} 失败"
            f"{' · ' + str(probe.get('detail') or '') if probe.get('detail') else ''}"
        )
        if started:
            message = f"{message} · 本会话曾拉起 pid={started.get('pid')}"
    return {
        "ok": True,
        "id": cfg["id"],
        "label": cfg["label"],
        "running": running,
        "tcp_ok": bool(probe.get("tcp_ok")),
        "http_ok": bool(probe.get("http_ok")),
        "http_status": probe.get("http_status"),
        "probe_detail": probe.get("detail") or "",
        "autostart_enabled": bool(cfg["autostart_enabled"]),
        "autostop_enabled": bool(cfg["autostop_enabled"]),
        "command": cfg["command"],
        "args": cfg["args"],
        "cwd": cfg["cwd"],
        "health_url": cfg["health_url"],
        "host": cfg["host"],
        "port": cfg["port"],
        "live_host": live_host,
        "live_port": live_port,
        "wait_seconds": cfg["wait_seconds"],
        "started_by_us": bool(started.get("pid")),
        "pid": managed_pid or live_pid,
        "message": message,
    }


def list_services_status(runtime: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    services = load_services_settings(runtime)
    rows = [service_status(sid, runtime) for sid in services.keys()]
    return {"ok": True, "services": rows}


def start_service(
    service_id: str,
    runtime: Optional[Dict[str, Any]] = None,
    *,
    force: bool = False,
    wait_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    sid = str(service_id or "").strip().lower()
    cfg = load_services_settings(runtime).get(sid) or normalize_service_config(sid, {})
    live = resolve_live_endpoint(
        service_id=sid,
        host=str(cfg["host"]),
        port=int(cfg["port"]),
        health_url=str(cfg.get("health_url") or ""),
    )
    if live.get("running"):
        listener_pids = list(live.get("listening_pids") or [])
        live_pid = int(listener_pids[0]) if listener_pids else (_STARTED.get(sid) or {}).get("pid")
        # Remember live listener only if we already manage this service; do not
        # claim ownership of manually started instances.
        if sid in _STARTED and live_pid:
            _STARTED[sid] = {
                **(_STARTED.get(sid) or {}),
                "pid": int(live_pid),
                "listener_pids": listener_pids,
                "adopted_at": time.time(),
            }
        return {
            "ok": True,
            "id": sid,
            "started": False,
            "running": True,
            "message": (
                f"{cfg['label']} 已在运行"
                + (
                    f" ({live.get('host')}:{live.get('port')})"
                    if live.get("port")
                    else ""
                )
                + (
                    ""
                    if live.get("matched_primary")
                    else f"；配置端口 {cfg['port']} 未开，但备用端口在线，已跳过重复拉起"
                )
            ),
            "host": live.get("host") or cfg["host"],
            "port": live.get("port") or cfg["port"],
            "pid": live_pid,
        }
    if not force and not cfg.get("autostart_enabled"):
        return {
            "ok": True,
            "id": sid,
            "started": False,
            "running": False,
            "message": f"未开启 {cfg['label']} 自动拉起",
            "host": cfg["host"],
            "port": cfg["port"],
        }

    command = _resolve_command(sid, str(cfg.get("command") or ""))
    if not command:
        return {
            "ok": False,
            "id": sid,
            "started": False,
            "running": False,
            "error": "command_required",
            "message": f"请先配置 {cfg['label']} 启动路径/命令",
            "host": cfg["host"],
            "port": cfg["port"],
        }
    if not Path(command).exists() and not shutil.which(command):
        return {
            "ok": False,
            "id": sid,
            "started": False,
            "running": False,
            "error": "command_not_found",
            "message": f"找不到启动命令: {command}",
            "host": cfg["host"],
            "port": cfg["port"],
        }

    args = _split_args(str(cfg.get("args") or ""))
    # Ollama convenience: if no args provided, default to serve.
    if sid == "ollama" and not args:
        args = ["serve"]
    cwd = str(cfg.get("cwd") or "").strip()
    if cwd and not Path(cwd).exists():
        return {
            "ok": False,
            "id": sid,
            "started": False,
            "running": False,
            "error": "cwd_not_found",
            "message": f"工作目录不存在: {cwd}",
        }
    launch = _resolve_launch_spec(sid, command, args, cwd)
    if not launch.get("ok"):
        return {
            "ok": False,
            "id": sid,
            "started": False,
            "running": False,
            "error": str(launch.get("error") or "command_not_found"),
            "message": str(launch.get("message") or "无法识别启动入口"),
            "candidates": list(launch.get("candidates") or []),
            "host": cfg["host"],
            "port": cfg["port"],
        }
    command = str(launch.get("command") or command)
    args = list(launch.get("args") or [])
    cwd = str(launch.get("cwd") or "").strip()
    if not cwd:
        maybe_dir = Path(command).parent
        cwd = str(maybe_dir) if maybe_dir.exists() else None

    # Silent launch by default: no console window, independent process group.
    argv, creationflags, startupinfo = _build_launch_argv(command, args)
    try:
        proc = subprocess.Popen(
            argv,
            cwd=cwd or None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
            startupinfo=startupinfo,
        )
    except Exception as exc:
        return {
            "ok": False,
            "id": sid,
            "started": False,
            "running": False,
            "error": str(exc) or "start_failed",
            "message": f"启动 {cfg['label']} 失败: {exc}",
            "command": command,
            "args": args,
        }

    launcher_pid = int(proc.pid)
    _STARTED[sid] = {
        "pid": launcher_pid,
        "launcher_pid": launcher_pid,
        "command": command,
        "args": args,
        "cwd": cwd or "",
        "started_at": time.time(),
        "argv": argv,
    }

    def _ready_response(endpoint: Dict[str, Any]) -> Dict[str, Any]:
        live_host = str(endpoint.get("host") or cfg["host"])
        live_port = int(endpoint.get("port") or cfg["port"])
        listener_pids = list(endpoint.get("listening_pids") or [])
        service_pid = int(listener_pids[0]) if listener_pids else launcher_pid
        _STARTED[sid] = {
            **(_STARTED.get(sid) or {}),
            "pid": service_pid,
            "launcher_pid": launcher_pid,
            "listener_pids": listener_pids,
            "ready_at": time.time(),
        }
        return {
            "ok": True,
            "id": sid,
            "started": True,
            "running": True,
            "pid": service_pid,
            "message": f"已启动 {cfg['label']}",
            "host": live_host,
            "port": live_port,
            "command": command,
            "args": args,
        }

    timeout = float(wait_seconds if wait_seconds is not None else cfg.get("wait_seconds") or 10)
    deadline = time.time() + max(0.5, timeout)
    host = str(cfg["host"])
    port = int(cfg["port"])
    launcher_exited_early = False
    while time.time() < deadline:
        endpoint = resolve_live_endpoint(
            service_id=sid,
            host=host,
            port=port,
            health_url=str(cfg.get("health_url") or ""),
        )
        if endpoint.get("running"):
            return _ready_response(endpoint)
        if is_port_open(host, port):
            listener_pids = find_listening_pids(port)
            service_pid = int(listener_pids[0]) if listener_pids else launcher_pid
            _STARTED[sid] = {
                **(_STARTED.get(sid) or {}),
                "pid": service_pid,
                "launcher_pid": launcher_pid,
                "listener_pids": listener_pids,
                "ready_at": time.time(),
            }
            return {
                "ok": True,
                "id": sid,
                "started": True,
                "running": True,
                "pid": service_pid,
                "message": f"已启动 {cfg['label']}",
                "host": host,
                "port": port,
                "command": command,
                "args": args,
            }
        # .bat launchers often exit after spawning the real service.
        # Do not treat launcher exit as failure while still waiting for the port.
        if proc.poll() is not None:
            launcher_exited_early = True
        time.sleep(0.25)

    # Final probe after wait window.
    endpoint = resolve_live_endpoint(
        service_id=sid,
        host=host,
        port=port,
        health_url=str(cfg.get("health_url") or ""),
    )
    if endpoint.get("running"):
        return _ready_response(endpoint)
    if is_port_open(host, port):
        listener_pids = find_listening_pids(port)
        service_pid = int(listener_pids[0]) if listener_pids else launcher_pid
        _STARTED[sid] = {
            **(_STARTED.get(sid) or {}),
            "pid": service_pid,
            "launcher_pid": launcher_pid,
            "listener_pids": listener_pids,
            "ready_at": time.time(),
        }
        return {
            "ok": True,
            "id": sid,
            "started": True,
            "running": True,
            "pid": service_pid,
            "message": f"已启动 {cfg['label']}",
            "host": host,
            "port": port,
            "command": command,
            "args": args,
        }

    # Help diagnose common misconfigured NapCat ports.
    alt_hint = ""
    if sid == "napcat":
        for alt in (3000, 3001, 6099, 6700, 8080):
            if alt == port:
                continue
            if is_port_open(host, alt, timeout=0.2):
                alt_hint = f" 检测到 {host}:{alt} 已在监听，可能是探测端口配错了。"
                break

    if launcher_exited_early and proc.poll() is not None:
        _STARTED.pop(sid, None)
        return {
            "ok": False,
            "id": sid,
            "started": True,
            "running": False,
            "error": "process_exited",
            "message": (
                f"{cfg['label']} 启动脚本已退出，且 {timeout:.0f}s 内端口 {port} 仍不可用。"
                f"请确认 health 端口是否正确（当前 {host}:{port}），或改填真实 exe。"
                f"{alt_hint}"
            ),
            "command": command,
            "args": args,
            "host": host,
            "port": port,
        }

    return {
        "ok": False,
        "id": sid,
        "started": True,
        "running": False,
        "pid": launcher_pid,
        "error": "start_timeout",
        "message": (
            f"已尝试启动 {cfg['label']}，但 {timeout:.0f}s 内端口 {port} 仍不可用。"
            f"若服务其实已开，请检查探测地址 {host}:{port} / {cfg.get('health_url')}。"
            f"{alt_hint}"
        ),
        "host": host,
        "port": port,
        "command": command,
        "args": args,
    }


def ensure_service(
    service_id: str,
    runtime: Optional[Dict[str, Any]] = None,
    *,
    force: bool = False,
    wait_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    return start_service(service_id, runtime, force=force, wait_seconds=wait_seconds)


def ensure_enabled_services(
    runtime: Optional[Dict[str, Any]] = None,
    *,
    wait_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    services = load_services_settings(runtime)
    results = []
    for sid, cfg in services.items():
        if not cfg.get("autostart_enabled"):
            continue
        results.append(
            ensure_service(sid, runtime, force=False, wait_seconds=wait_seconds)
        )
    return {"ok": True, "results": results}


def stop_service(service_id: str, *, only_if_started_by_us: bool = True) -> Dict[str, Any]:
    sid = str(service_id or "").strip().lower()
    started = _STARTED.get(sid)
    if only_if_started_by_us and not started:
        return {
            "ok": True,
            "id": sid,
            "stopped": False,
            "message": "非本程序拉起，已跳过关闭",
        }
    pid = int((started or {}).get("pid") or 0)
    launcher_pid = int((started or {}).get("launcher_pid") or 0)
    listener_pids = [
        int(item)
        for item in list((started or {}).get("listener_pids") or [])
        if str(item).isdigit() or isinstance(item, int)
    ]
    targets: List[int] = []
    for candidate in [pid, launcher_pid, *listener_pids]:
        value = int(candidate or 0)
        if value > 0 and value not in targets:
            targets.append(value)
    if not targets:
        return {"ok": True, "id": sid, "stopped": False, "message": "没有可关闭的进程"}
    try:
        killed: List[int] = []
        for target in targets:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(target), "/T", "/F"],
                    capture_output=True,
                    text=False,
                    check=False,
                    creationflags=_windows_create_no_window_flag(),
                )
            else:
                try:
                    os.kill(target, 15)
                except ProcessLookupError:
                    continue
            killed.append(target)
        _STARTED.pop(sid, None)
        return {
            "ok": True,
            "id": sid,
            "stopped": True,
            "pid": targets[0],
            "pids": killed,
            "message": f"已关闭 pid={','.join(str(item) for item in killed)}",
        }
    except Exception as exc:
        return {
            "ok": False,
            "id": sid,
            "stopped": False,
            "pid": targets[0] if targets else 0,
            "error": str(exc) or "stop_failed",
            "message": f"关闭失败: {exc}",
        }


def stop_managed_services(runtime: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Stop services that were started by us and have autostop enabled."""
    services = load_services_settings(runtime)
    results = []
    for sid, cfg in services.items():
        if not cfg.get("autostop_enabled"):
            continue
        if sid not in _STARTED:
            results.append(
                {
                    "ok": True,
                    "id": sid,
                    "stopped": False,
                    "message": "本会话未拉起，跳过",
                }
            )
            continue
        results.append(stop_service(sid, only_if_started_by_us=True))
    return {"ok": True, "results": results}
