from __future__ import annotations

import shutil
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from services.capability_manager import ToolCapability, ToolCapabilityMatch


class Plugin:
    name = "用户文件助手"
    type = "direct"
    description = "读取和受控写入白名单用户目录。"
    aliases = ["用户文件", "文件助手", "下载目录", "文档目录", "桌面文件", "user_files"]
    allow_natural_language_direct = True
    tool_examples = [
        "帮我看看下载目录里的 a.txt",
        "user_files list ||| downloads ||| .",
        "user_files read ||| documents ||| notes.txt",
    ]

    def __init__(self) -> None:
        self.settings: Dict[str, Any] = {}

    def get_capabilities(self):
        return [
            ToolCapability(
                id="user_files.command",
                plugin="user_files",
                trigger_mode="command_only",
                match=self._match_user_files_command,
                description="通过 user_files 指令访问用户文件白名单目录",
                examples=["user_files read ||| downloads ||| notes.txt"],
            ),
            ToolCapability(
                id="user_files.read",
                plugin="user_files",
                trigger_mode="natural",
                match=lambda text, ctx: self._match_natural_user_file(
                    text, ctx, expected_action="read"
                ),
                description="读取用户白名单目录中的文件",
                examples=["帮我看看下载目录里的 a.txt"],
            ),
            ToolCapability(
                id="user_files.list",
                plugin="user_files",
                trigger_mode="natural",
                match=lambda text, ctx: self._match_natural_user_file(
                    text, ctx, expected_action="list"
                ),
                description="列出用户白名单目录",
                examples=["列出 downloads 目录里的文件"],
            ),
        ]

    def should_handle_direct(self, text: str, context: Dict[str, Any], key: str) -> bool:
        raw = str(text or "").strip().lower()
        if not raw:
            return False
        if raw.startswith("user_files "):
            return True
        has_file_place = any(k in raw for k in ["下载目录", "文档目录", "桌面", "documents", "downloads", "desktop"])
        has_file_action = any(k in raw for k in ["看看", "查看", "读取", "列出", "整理", "移动", "写入", "保存"])
        has_file_name = any(k in raw for k in [".txt", ".md", ".json", ".py", ".log", ".csv", ".zip", ".png", ".jpg"])
        return bool(has_file_place and (has_file_action or has_file_name))

    def _match_user_files_command(
        self, text: str, ctx: Dict[str, Any]
    ) -> Optional[ToolCapabilityMatch]:
        raw = str(text or "").strip()
        if not raw.lower().startswith("user_files "):
            return None
        return ToolCapabilityMatch(
            capability_id="user_files.command",
            plugin="user_files",
            score=1.0,
            raw_text=raw,
            reason="user_files_command",
        )

    def _match_natural_user_file(
        self,
        text: str,
        ctx: Dict[str, Any],
        *,
        expected_action: str,
    ) -> Optional[ToolCapabilityMatch]:
        raw = str(text or "").strip()
        if not raw:
            return None
        action, parts = self._parse_natural_language(raw)
        if action != expected_action:
            return None
        args = {"action": action}
        if len(parts) >= 1:
            args["root"] = parts[0]
        if len(parts) >= 2:
            args["path"] = parts[1]
        return ToolCapabilityMatch(
            capability_id=f"user_files.{expected_action}",
            plugin="user_files",
            score=0.9,
            args=args,
            raw_text=raw,
            reason=f"user_files_{expected_action}_intent",
        )

    async def run(self, args: str, ctx: Dict[str, Any]) -> Any:
        try:
            action, parts = self._parse_args(args)
            if action == "natural":
                action, parts = self._parse_natural_language(parts[0])
            if action in {"", "help"}:
                return self._help_text()
            if action == "roots":
                return self._format_roots()
            if action == "list":
                self._require_permission(ctx, read=True)
                root, subpath = self._require_parts(parts, 2, "list 需要 root 和 subpath")
                return self._list(root, subpath)
            if action == "read":
                self._require_permission(ctx, read=True)
                root, path = self._require_parts(parts, 2, "read 需要 root 和 path")
                return self._read(root, path)
            if action == "write":
                self._require_permission(ctx, write=True)
                root, path, content = self._require_parts(parts, 3, "write 需要 root、path 和 content")
                target = self._resolve(root, path, allow_missing=True)
                return self._confirmation(
                    summary=f"将写入用户文件：{target}",
                    payload={
                        "action": "write",
                        "root": root,
                        "path": path,
                        "content": content,
                    },
                )
            if action == "move":
                self._require_permission(ctx, write=True)
                root, src, dst = self._require_parts(parts, 3, "move 需要 root、from 和 to")
                source = self._resolve(root, src, allow_missing=False)
                target = self._resolve(root, dst, allow_missing=True)
                return self._confirmation(
                    summary=f"将移动用户文件：{source} -> {target}",
                    payload={
                        "action": "move",
                        "root": root,
                        "src": src,
                        "dst": dst,
                    },
                )
            return f"不支持的 action: {action}\n\n{self._help_text()}"
        except Exception as exc:
            return str(exc)

    async def confirm_agent_action(self, payload: Dict[str, Any], ctx: Dict[str, Any]) -> str:
        self._require_permission(ctx, write=True)
        action = str(payload.get("action") or "").strip()
        if action == "write":
            root = str(payload.get("root") or "").strip()
            path = str(payload.get("path") or "").strip()
            content = str(payload.get("content") or "")
            target = self._resolve(root, path, allow_missing=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"已写入：{target}"
        if action == "move":
            root = str(payload.get("root") or "").strip()
            source = self._resolve(root, str(payload.get("src") or ""), allow_missing=False)
            target = self._resolve(root, str(payload.get("dst") or ""), allow_missing=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
            return f"已移动：{source} -> {target}"
        return "未知确认操作，已取消。"

    def _parse_args(self, raw: str) -> Tuple[str, list[str]]:
        text = str(raw or "").strip()
        if text.lower().startswith("user_files"):
            text = text[len("user_files") :].strip()
        if "|||" not in text:
            return "natural", [text]
        parts = [p.strip() for p in text.split("|||")]
        action = parts[0].lower() if parts else ""
        return action, parts[1:]

    def _parse_natural_language(self, text: str) -> Tuple[str, list[str]]:
        raw = str(text or "").strip()
        root = self._detect_root(raw)
        if not root:
            return raw.lower(), []
        file_name = self._extract_file_name(raw)
        if file_name:
            return "read", [root, file_name]
        if any(token in raw for token in ("列出", "有哪些", "目录里", "文件夹里")):
            return "list", [root, "."]
        return raw.lower(), []

    def _detect_root(self, text: str) -> str:
        lowered = str(text or "").lower()
        if "下载目录" in text or "downloads" in lowered:
            return "downloads"
        if "文档目录" in text or "documents" in lowered:
            return "documents"
        if "桌面" in text or "desktop" in lowered:
            return "desktop"
        for name in self._roots():
            if name and name in lowered:
                return name
        return ""

    def _extract_file_name(self, text: str) -> str:
        raw = str(text or "")
        match = re.search(
            r"([A-Za-z0-9._-]+\.(?:txt|md|json|py|log|csv|zip|png|jpe?g|pdf|docx?))",
            raw,
            flags=re.IGNORECASE,
        )
        if match:
            return match.group(1).strip()
        match = re.search(
            r"(?:里的?|文件[:：]?)\s*([^\s\\/:*?\"<>|，。]+?\.(?:txt|md|json|py|log|csv|zip|png|jpe?g|pdf|docx?))",
            raw,
            flags=re.IGNORECASE,
        )
        return match.group(1).strip() if match else ""

    def _require_parts(self, parts: list[str], count: int, message: str) -> tuple[str, ...]:
        if len(parts) < count:
            raise ValueError(message)
        return tuple(parts[:count])

    def _require_permission(self, ctx: Dict[str, Any], *, read: bool = False, write: bool = False) -> None:
        if read and not bool((ctx or {}).get("allow_read", False)):
            raise PermissionError("读取权限已关闭")
        if write and not bool((ctx or {}).get("allow_write", False)):
            raise PermissionError("写入权限已关闭")

    def _roots(self) -> Dict[str, Path]:
        home = self._home_dir()
        roots = {
            "desktop": home / "Desktop",
            "documents": home / "Documents",
            "downloads": self._downloads_root(home),
        }
        custom = self._setting("custom_roots", [])
        if isinstance(custom, list):
            for item in custom:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip().lower()
                path = str(item.get("path") or "").strip()
                if name and path:
                    roots[name] = Path(path).expanduser()
        return {name: path.resolve() for name, path in roots.items()}

    def _home_dir(self) -> Path:
        return Path.home()

    def _downloads_root(self, home: Path) -> Path:
        default = home / "Downloads"
        if default.exists():
            return default
        for candidate in self._drive_download_candidates():
            if candidate.exists() and candidate.is_dir():
                return candidate
        return default

    def _drive_download_candidates(self) -> list[Path]:
        candidates: list[Path] = []
        for drive in "DEFGHIJKLMNOPQRSTUVWXYZ":
            candidates.append(Path(f"{drive}:/Downloads"))
            candidates.append(Path(f"{drive}:/下载"))
        return candidates

    def _resolve(self, root_name: str, subpath: str, *, allow_missing: bool) -> Path:
        roots = self._roots()
        root_key = str(root_name or "").strip().lower()
        root = roots.get(root_key)
        if root is None:
            raise ValueError(f"未知用户文件根目录: {root_name}")
        raw = str(subpath or "").strip()
        if not raw:
            raise ValueError("路径不能为空")
        candidate = Path(raw).expanduser()
        target = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise PermissionError(f"路径越界: {target}") from exc
        if not allow_missing and not target.exists():
            raise FileNotFoundError(f"文件不存在: {target}")
        return target

    def _list(self, root_name: str, subpath: str) -> str:
        target = self._resolve(root_name, subpath, allow_missing=False)
        if not target.is_dir():
            raise NotADirectoryError(f"不是目录: {target}")
        max_items = self._setting_int("max_list_items", 80)
        rows = []
        for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:max_items]:
            kind = "dir" if item.is_dir() else "file"
            rows.append(f"{kind}\t{item.name}")
        return "\n".join(rows) if rows else "目录为空"

    def _read(self, root_name: str, path: str) -> str:
        target = self._resolve(root_name, path, allow_missing=False)
        if not target.is_file():
            raise IsADirectoryError(f"不是文件: {target}")
        text = target.read_text(encoding="utf-8", errors="replace")
        max_chars = self._setting_int("max_read_chars", 8000)
        suffix = "\n...[truncated]" if len(text) > max_chars else ""
        return f"# {target}\n{text[:max_chars]}{suffix}"

    def _format_roots(self) -> str:
        lines = ["用户文件根目录"]
        for name, path in sorted(self._roots().items()):
            lines.append(f"- {name}: {path}")
        return "\n".join(lines)

    def _confirmation(self, *, summary: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(payload)
        payload["task_id"] = uuid.uuid4().hex[:8]
        return {
            "__agent_result__": "confirmation_required",
            "trigger": "user_files",
            "summary": summary,
            "payload": payload,
            "expires_in": 300,
        }

    def _setting(self, key: str, default: Any) -> Any:
        value = (self.settings or {}).get(key, default)
        if isinstance(value, dict):
            return value.get("default", default)
        return value

    def _setting_int(self, key: str, default: int) -> int:
        try:
            return int(self._setting(key, default))
        except (TypeError, ValueError):
            return int(default)

    def _help_text(self) -> str:
        return (
            "user_files 用法：\n"
            "- user_files roots\n"
            "- user_files list ||| downloads ||| .\n"
            "- user_files read ||| documents ||| notes.txt\n"
            "- user_files write ||| desktop ||| note.txt ||| 内容\n"
            "- user_files move ||| downloads ||| a.txt ||| archive/a.txt"
        )
