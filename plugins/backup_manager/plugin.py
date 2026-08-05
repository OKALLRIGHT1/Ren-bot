import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


class Plugin:
    name = "备份恢复"
    type = "react"
    plugin_trigger = "backup_manager"

    def __init__(self):
        self.workspace = Path.cwd().resolve()
        self.backup_root = (self.workspace / "data" / "backups").resolve()
        self.backup_root.mkdir(parents=True, exist_ok=True)
        self.targets = [
            Path("userdata.db"),
            Path("data/runtime_settings.json"),
            Path("config.py"),
        ]

    def resolve_gated_action(self, args: str, ctx: Optional[Dict[str, Any]] = None) -> str:
        parts = [x.strip() for x in (args or "").split("|||")]
        action = (parts[0].lower() if parts and parts[0] else "list")
        if action == "restore":
            return "system.backup_restore"
        if action == "create":
            return "backup.create"
        if action == "list":
            return "backup.list"
        return "system.backup_restore"

    async def run(self, args: str, ctx: Dict[str, Any]) -> Any:
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
            # Require explicit confirmation for restore (ActionGate also enforces)
            if not bool((ctx or {}).get("action_confirmed") or (ctx or {}).get("gate_confirmed")):
                return {
                    "__agent_result__": "confirmation_required",
                    "trigger": str(getattr(self, "plugin_trigger", None) or "backup_manager"),
                    "summary": (
                        f"⚠️ 恢复快照会覆盖: {', '.join(p.as_posix() for p in self.targets)}\n"
                        f"目标快照: {arg}"
                    ),
                    "payload": {
                        "mode": "gate_rerun",
                        "args": args,
                        "gated_action": "system.backup_restore",
                    },
                    "expires_in": 300,
                }
            return self._restore_snapshot(arg)
        return "不支持的 action，可用: create/list/restore"

    async def confirm_agent_action(self, payload: Dict[str, Any], ctx: Dict[str, Any]) -> str:
        runtime = dict(ctx or {})
        runtime["action_confirmed"] = True
        runtime["gate_confirmed"] = True
        args = str((payload or {}).get("args") or "").strip()
        if not args:
            return "确认载荷缺少参数，已取消。"
        result = await self.run(args, runtime)
        if isinstance(result, dict):
            return str(result.get("summary") or result)
        return str(result)

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
            # ensure src under workspace
            try:
                src.relative_to(self.workspace)
            except ValueError:
                continue
            dst = (snap_dir / rel).resolve()
            try:
                dst.relative_to(self.backup_root)
            except ValueError:
                continue
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
        try:
            snap_dir.relative_to(self.backup_root)
        except ValueError:
            return f"非法快照路径: {snap_id}"
        if not snap_dir.exists() or not snap_dir.is_dir():
            return f"快照不存在: {snap_id}"

        restored: List[str] = []
        for rel in self.targets:
            src = (snap_dir / rel).resolve()
            if not src.exists():
                continue
            try:
                src.relative_to(self.backup_root)
            except ValueError:
                continue
            dst = (self.workspace / rel).resolve()
            try:
                dst.relative_to(self.workspace)
            except ValueError:
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            restored.append(rel.as_posix())

        if not restored:
            return f"快照 {snap_id} 内没有可恢复文件。"
        return f"已从快照恢复: {snap_id}\n" + "\n".join(f"- {x}" for x in restored)
