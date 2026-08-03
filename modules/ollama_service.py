from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional


OLLAMA_AUTOSTART_KEY = "ollama_autostart_enabled"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 11434


def _host_port_from_settings(settings: Optional[Dict[str, Any]] = None) -> tuple[str, int]:
    data = settings if isinstance(settings, dict) else {}
    host = str(data.get("ollama_host") or os.getenv("OLLAMA_HOST") or DEFAULT_HOST).strip()
    if host.startswith("http://"):
        host = host[len("http://") :]
    if host.startswith("https://"):
        host = host[len("https://") :]
    host = host.split("/")[0]
    # OLLAMA_HOST may be "0.0.0.0:11434" for bind address; connect via loopback.
    if ":" in host and not host.startswith("["):
        maybe_host, maybe_port = host.rsplit(":", 1)
        if maybe_port.isdigit():
            host = maybe_host
            data = {**data, "ollama_port": data.get("ollama_port") or maybe_port}
    host = host.split("%")[0] or DEFAULT_HOST
    if host in {"0.0.0.0", "::", "[::]", "*"}:
        host = DEFAULT_HOST
    try:
        port = int(data.get("ollama_port") or os.getenv("OLLAMA_PORT") or DEFAULT_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    return host, max(1, min(65535, port))


def is_ollama_running(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=max(0.1, float(timeout))):
            return True
    except OSError:
        return False


def resolve_ollama_executable() -> str:
    env_path = str(os.getenv("OLLAMA_PATH") or "").strip()
    if env_path and Path(env_path).exists():
        return str(Path(env_path))
    which = shutil.which("ollama")
    if which:
        return which
    local = os.environ.get("LOCALAPPDATA") or ""
    candidates = [
        Path(local) / "Programs" / "Ollama" / "ollama.exe",
        Path(local) / "Ollama" / "ollama.exe",
        Path(r"C:\Program Files\Ollama\ollama.exe"),
        Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe",
    ]
    for item in candidates:
        if item.exists():
            return str(item)
    return ""


def start_ollama_service(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    wait_seconds: float = 8.0,
) -> Dict[str, Any]:
    """Start `ollama serve` detached if the API port is down."""
    if is_ollama_running(host, port):
        return {
            "ok": True,
            "started": False,
            "running": True,
            "host": host,
            "port": port,
            "message": "Ollama 已在运行",
        }

    exe = resolve_ollama_executable()
    if not exe:
        return {
            "ok": False,
            "started": False,
            "running": False,
            "host": host,
            "port": port,
            "error": "ollama_not_found",
            "message": "未找到 ollama 可执行文件，请确认已安装并加入 PATH",
        }

    creationflags = 0
    if os.name == "nt":
        creationflags = int(
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    try:
        subprocess.Popen(
            [exe, "serve"],
            cwd=str(Path(exe).parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )
    except Exception as exc:
        return {
            "ok": False,
            "started": False,
            "running": False,
            "host": host,
            "port": port,
            "error": str(exc) or "start_failed",
            "message": f"启动 Ollama 失败: {exc}",
            "executable": exe,
        }

    deadline = time.time() + max(0.5, float(wait_seconds))
    while time.time() < deadline:
        if is_ollama_running(host, port):
            return {
                "ok": True,
                "started": True,
                "running": True,
                "host": host,
                "port": port,
                "message": "已启动 Ollama 服务",
                "executable": exe,
            }
        time.sleep(0.25)

    return {
        "ok": False,
        "started": True,
        "running": False,
        "host": host,
        "port": port,
        "error": "start_timeout",
        "message": f"已尝试启动 Ollama，但 {wait_seconds:.0f}s 内端口 {port} 仍不可用",
        "executable": exe,
    }


def ensure_ollama_service(
    settings: Optional[Dict[str, Any]] = None,
    *,
    force: bool = False,
    wait_seconds: float = 8.0,
) -> Dict[str, Any]:
    """Ensure Ollama is up when enabled by runtime settings or forced."""
    data = settings if isinstance(settings, dict) else {}
    enabled = bool(data.get(OLLAMA_AUTOSTART_KEY, False)) if not force else True
    host, port = _host_port_from_settings(data)
    if is_ollama_running(host, port):
        return {
            "ok": True,
            "enabled": enabled,
            "started": False,
            "running": True,
            "host": host,
            "port": port,
            "message": "Ollama 已在运行",
        }
    if not enabled and not force:
        return {
            "ok": True,
            "enabled": False,
            "started": False,
            "running": False,
            "host": host,
            "port": port,
            "message": "未开启自动拉起 Ollama",
        }
    result = start_ollama_service(host=host, port=port, wait_seconds=wait_seconds)
    result["enabled"] = enabled or force
    return result


def ollama_status(settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = settings if isinstance(settings, dict) else {}
    host, port = _host_port_from_settings(data)
    running = is_ollama_running(host, port)
    return {
        "ok": True,
        "enabled": bool(data.get(OLLAMA_AUTOSTART_KEY, False)),
        "running": running,
        "host": host,
        "port": port,
        "executable": resolve_ollama_executable(),
        "message": "Ollama 在线" if running else "Ollama 未运行",
    }
