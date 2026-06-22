from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

from integrations.gui_access import get_or_create_gui_access_token


class ActivitySidecarManager:
    DEFAULT_ENDPOINT = "http://127.0.0.1:8097/gui/activity-ingest"

    def __init__(self, logger=None, endpoint: str = ""):
        self.logger = logger
        self.process: Optional[subprocess.Popen] = None
        self.endpoint = str(endpoint or "").strip() or self.DEFAULT_ENDPOINT
        self._log_file = None

    @staticmethod
    def build_endpoint(host: str, port: int, prefix: str) -> str:
        host_value = str(host or "127.0.0.1").strip()
        if host_value in {"0.0.0.0", "::", "[::]", ""}:
            host_value = "127.0.0.1"
        if ":" in host_value and not host_value.startswith("[") and host_value.count(":") > 1:
            host_value = f"[{host_value}]"
        prefix_value = str(prefix or "/gui").strip() or "/gui"
        if not prefix_value.startswith("/"):
            prefix_value = f"/{prefix_value}"
        prefix_value = prefix_value.rstrip("/")
        return f"http://{host_value}:{int(port)}{prefix_value}/activity-ingest"

    def _binary_path(self) -> Path:
        root = Path(__file__).resolve().parent.parent
        return (
            root
            / "rust-activity-agent"
            / "target"
            / "release"
            / "rust-activity-agent.exe"
        )

    def start(self):
        binary = self._binary_path()
        if not binary.exists():
            if self.logger:
                self.logger.warning(f"Rust activity agent 未找到: {binary}")
            return False
        if self.process and self.process.poll() is None:
            return True
        if self.process and self.process.poll() is not None:
            self.process = None
            self._close_log_file()

        env = os.environ.copy()
        env["ACTIVITY_AGENT_ENDPOINT"] = self.endpoint
        env["ACTIVITY_AGENT_TOKEN"] = get_or_create_gui_access_token()
        env.setdefault("ACTIVITY_AGENT_DEVICE_ID", "desktop-main")
        env.setdefault("ACTIVITY_AGENT_NAME", "live2d-rust-agent")
        try:
            import config

            env.setdefault(
                "ACTIVITY_AGENT_SEDENTARY_WINDOW_MINUTES",
                str(max(1, int(getattr(config, "SEDENTARY_REMINDER_MINUTES", 60)))),
            )
            env.setdefault(
                "ACTIVITY_AGENT_SEDENTARY_COOLDOWN_MINUTES",
                str(max(1, int(getattr(config, "SEDENTARY_REMINDER_COOLDOWN_MINUTES", 60)))),
            )
            env.setdefault(
                "ACTIVITY_AGENT_SEDENTARY_BREAK_MINUTES",
                str(max(1, int(getattr(config, "ACTIVITY_AGENT_SEDENTARY_BREAK_MINUTES", 5)))),
            )
        except Exception:
            env.setdefault("ACTIVITY_AGENT_SEDENTARY_WINDOW_MINUTES", "60")
            env.setdefault("ACTIVITY_AGENT_SEDENTARY_COOLDOWN_MINUTES", "60")
            env.setdefault("ACTIVITY_AGENT_SEDENTARY_BREAK_MINUTES", "5")
        log_file = None
        try:
            log_path = (
                Path(__file__).resolve().parent.parent / "logs" / "activity_sidecar.log"
            )
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_file = log_path.open("a", encoding="utf-8")
            self._log_file = log_file
            self.process = subprocess.Popen(
                [str(binary)],
                env=env,
                stdout=log_file,
                stderr=log_file,
            )
            if self.logger:
                self.logger.info(
                    f"Rust activity agent 已启动: {binary} endpoint={env.get('ACTIVITY_AGENT_ENDPOINT')}"
                )
            return True
        except Exception as e:
            if self.logger:
                self.logger.warning(f"Rust activity agent 启动失败: {e}")
            self.process = None
            if log_file:
                try:
                    log_file.close()
                except Exception:
                    pass
                self._log_file = None
            return False

    def is_running(self) -> bool:
        return bool(self.process and self.process.poll() is None)

    def _close_log_file(self):
        if not self._log_file:
            return
        try:
            self._log_file.close()
        except Exception:
            pass
        self._log_file = None

    def stop(self):
        proc = self.process
        self.process = None
        if not proc:
            self._close_log_file()
            return
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        self._close_log_file()
        if self.logger:
            self.logger.info("Rust activity agent 已停止")
