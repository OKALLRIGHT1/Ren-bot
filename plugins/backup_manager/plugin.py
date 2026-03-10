import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


class Plugin:
    name = "backup_manager"
    type = "react"

    def __init__(self):
        self.workspace = Path.cwd().resolve()
        self.backup_root = (self.workspace / "data" / "backups").resolve()
        self.backup_root.mkdir(parents=True, exist_ok=True)
        self.targets = [
            Path("userdata.db"),
            Path("data/runtime_settings.json"),
            Path("config.py"),
        ]

    async def run(self, args: str, ctx: Dict[str, Any]) -> str:
        parts = [x.strip() for x in (args or "").split("|||")]
        action = (parts[0].lower() if parts and parts[0] else "list")
        arg = parts[1] if len(parts) > 1 else ""

        if action == "create":
            return self._create_snapshot(arg)
        if action == "list":
            return self._list_snapshots()
        if action == "restore":
            if not arg:
                return "请提供快照ID，例如: restore ||| 20260101-120000_before_xxx"
            return self._restore_snapshot(arg)
        return "不支持的 action，可用: create/list/restore"

    def _create_snapshot(self, name: str) -> str:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_name = (name or "manual").replace(" ", "_")
        snap_id = f"{ts}_{safe_name}"
        snap_dir = self.backup_root / snap_id
        snap_dir.mkdir(parents=True, exist_ok=True)

        copied: List[str] = []
        for rel in self.targets:
            src = (self.workspace / rel).resolve()
            if not src.exists():
                continue
            dst = (snap_dir / rel).resolve()
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(rel.as_posix())

        if not copied:
            return f"快照已创建: {snap_id}，但未找到可备份文件。"
        return f"快照已创建: {snap_id}\n" + "\n".join(f"- {x}" for x in copied)

    def _list_snapshots(self) -> str:
        items = [p for p in self.backup_root.iterdir() if p.is_dir()]
        items.sort(key=lambda p: p.name, reverse=True)
        if not items:
            return "暂无快照。"
        lines = ["快照列表："]
        for p in items[:80]:
            lines.append(f"- {p.name}")
        return "\n".join(lines)

    def _restore_snapshot(self, snap_id: str) -> str:
        snap_dir = (self.backup_root / snap_id).resolve()
        if not snap_dir.exists() or not snap_dir.is_dir():
            return f"快照不存在: {snap_id}"

        restored: List[str] = []
        for rel in self.targets:
            src = (snap_dir / rel).resolve()
            if not src.exists():
                continue
            dst = (self.workspace / rel).resolve()
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            restored.append(rel.as_posix())

        if not restored:
            return f"快照 {snap_id} 内没有可恢复文件。"
        return f"已从快照恢复: {snap_id}\n" + "\n".join(f"- {x}" for x in restored)
