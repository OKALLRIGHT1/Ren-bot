from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional


class ActivitySidecarManager:
    def __init__(self, logger=None):
        self.logger = logger
        self.process: Optional[subprocess.Popen] = None

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

        env = os.environ.copy()
        env.setdefault(
            "ACTIVITY_AGENT_ENDPOINT", "http://127.0.0.1:8097/gui/activity-ingest"
        )
        env.setdefault("ACTIVITY_AGENT_DEVICE_ID", "desktop-main")
        env.setdefault("ACTIVITY_AGENT_NAME", "live2d-rust-agent")
        try:
            self.process = subprocess.Popen(
                [str(binary)],
                env=env,
                stdout=None,
                stderr=None,
            )
            if self.logger:
                self.logger.info(f"Rust activity agent 已启动: {binary}")
            return True
        except Exception as e:
            if self.logger:
                self.logger.warning(f"Rust activity agent 启动失败: {e}")
            self.process = None
            return False

    def is_running(self) -> bool:
        return bool(self.process and self.process.poll() is None)

    def stop(self):
        proc = self.process
        self.process = None
        if not proc:
            return
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        if self.logger:
            self.logger.info("Rust activity agent 已停止")
