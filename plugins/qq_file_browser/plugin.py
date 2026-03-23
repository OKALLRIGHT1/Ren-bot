import asyncio
import json
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

try:
    import aiohttp
except Exception:  # pragma: no cover - optional dependency
    aiohttp = None


VFS_MAP = {
    "/bot": r"E:\\QQ_Bot",
}
DEFAULT_VFS = "/bot"
INBOX_VPATH = "/bot/inbox"
SESSION_TTL_SEC = 1800
LIST_LIMIT = 200


@dataclass
class SessionState:
    current_vfs_path: str = DEFAULT_VFS
    cached_dir_list: List[Dict[str, Any]] = field(default_factory=list)
    last_active_time: float = 0.0


_SESSIONS: Dict[str, SessionState] = {}


def _now() -> float:
    return time.time()


def _normalize_virtual_path(path: str) -> str:
    text = str(path or "").strip()
    if not text:
        return "/"
    if not text.startswith("/"):
        text = "/" + text
    parts: List[str] = []
    for part in PurePosixPath(text).parts:
        if part in ("", "/", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/" + "/".join(parts) if parts else "/"


def _join_virtual(base: str, target: str) -> str:
    if str(target or "").strip().startswith("/"):
        return _normalize_virtual_path(target)
    combined = str(PurePosixPath(base) / str(target or ""))
    return _normalize_virtual_path(combined)


def _safe_resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except Exception:
        return path.absolute()


def _find_mount(vpath: str) -> Optional[str]:
    norm = _normalize_virtual_path(vpath)
    for mount in sorted(VFS_MAP.keys(), key=len, reverse=True):
        if norm == mount or norm.startswith(mount + "/"):
            return mount
    return None


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        return path.is_relative_to(base)
    except AttributeError:
        return path == base or base in path.parents


def _resolve_vfs_path(vpath: str) -> Tuple[Path, Path]:
    mount = _find_mount(vpath)
    if not mount:
        raise ValueError("VFS path not allowed")
    base = Path(VFS_MAP[mount]).expanduser()
    base_resolved = _safe_resolve(base)
    rel = _normalize_virtual_path(vpath)[len(mount) :].lstrip("/")
    candidate = _safe_resolve(base_resolved / rel)
    if not _is_relative_to(candidate, base_resolved):
        raise ValueError("VFS escape blocked")
    return candidate, base_resolved


def _cleanup_sessions() -> None:
    now = _now()
    expired = [
        key
        for key, session in _SESSIONS.items()
        if now - session.last_active_time > SESSION_TTL_SEC
    ]
    for key in expired:
        _SESSIONS.pop(key, None)


def _get_session(user_id: str) -> SessionState:
    _cleanup_sessions()
    key = str(user_id or "unknown")
    session = _SESSIONS.get(key)
    if session is None:
        session = SessionState(current_vfs_path=DEFAULT_VFS, last_active_time=_now())
        _SESSIONS[key] = session
    session.last_active_time = _now()
    if not session.current_vfs_path:
        session.current_vfs_path = DEFAULT_VFS
    return session


def _parse_command(text: str) -> Tuple[str, str]:
    raw = str(text or "").strip()
    if not raw:
        return "", ""
    tokens = [tok for tok in re.split(r"\s+", raw) if tok]
    command_tokens = {
        "/ls": "ls",
        "ls": "ls",
        "/dir": "ls",
        "dir": "ls",
        "/cd": "cd",
        "cd": "cd",
        "/get": "get",
        "get": "get",
        "/pwd": "pwd",
        "pwd": "pwd",
        "/help": "help",
        "help": "help",
    }
    for idx, tok in enumerate(tokens):
        key = tok.lower()
        if key in command_tokens:
            return command_tokens[key], " ".join(tokens[idx + 1 :]).strip()
    if any(tok in {"/文件", "文件"} for tok in tokens) or "/文件" in raw:
        return "help", ""
    return "help", ""


def _format_help(session: SessionState) -> str:
    return "\n".join(
        [
            "📁 QQ 文件浏览 (VFS)",
            f"当前目录: {session.current_vfs_path}",
            "用法:",
            "- /ls   列出当前目录",
            "- /cd <目录|序号> 切换目录",
            "- /get <文件|序号> 发送文件",
            "- /pwd  显示当前目录",
            "- /help 帮助",
        ]
    )


def _format_listing(vpath: str, base: Path, entries: List[Dict[str, str]]) -> str:
    header = f"📂 当前目录: {vpath} -> {base}"
    if not entries:
        return "\n".join([header, "(空目录)"])
    lines = [header]
    for item in entries:
        icon = "📁" if item.get("is_dir") else "📄"
        lines.append(f"{item['index']}. {icon} {item['name']}")
    if len(entries) >= LIST_LIMIT:
        lines.append(f"...仅显示前 {LIST_LIMIT} 项")
    lines.append("提示: /cd <序号|目录>  /get <序号|文件>  /pwd")
    return "\n".join(lines)


def _scan_dir(physical: Path) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    try:
        for item in physical.iterdir():
            try:
                is_dir = item.is_dir()
            except Exception:
                is_dir = False
            items.append({"name": item.name, "is_dir": is_dir})
    except Exception:
        return []
    items.sort(key=lambda x: (not x.get("is_dir"), str(x.get("name") or "").lower()))
    return items


def _parse_local_path(value: str) -> Optional[Path]:
    text = str(value or "").strip()
    if not text:
        return None
    low = text.lower()
    if low.startswith("file://"):
        parsed = urlparse(text)
        path = unquote(parsed.path or "")
        if re.match(r"^/[A-Za-z]:", path):
            path = path.lstrip("/")
        return Path(path)
    if re.match(r"^[A-Za-z]:[\\/]", text) or text.startswith("\\\\"):
        return Path(text)
    if re.match(r"^/[A-Za-z]:", text):
        return Path(text.lstrip("/"))
    return None


def _unique_path(dest_dir: Path, name: str) -> Path:
    safe_name = Path(name).name if name else "file"
    candidate = dest_dir / safe_name
    if not candidate.exists():
        return candidate
    stem = candidate.stem or "file"
    suffix = candidate.suffix
    for idx in range(1, 1000):
        test = dest_dir / f"{stem}_{idx}{suffix}"
        if not test.exists():
            return test
    return dest_dir / f"{stem}_{int(time.time())}{suffix}"


class Plugin:
    def __init__(self):
        self._config_path = Path(__file__).with_name("config.json")
        self.reload_config()

    def reload_config(self):
        global VFS_MAP, INBOX_VPATH
        try:
            config = json.loads(self._config_path.read_text(encoding="utf-8"))
        except Exception:
            config = {}
        settings = config.get("settings") or {}
        mounts = {"/bot": r"E:\\QQ_Bot"}
        extra_mounts = self._read_setting(settings, "extra_mounts", [])
        if isinstance(extra_mounts, list):
            for item in extra_mounts:
                alias = ""
                path = ""
                text = str(item or "").strip()
                if "|" in text:
                    alias, path = text.split("|", 1)
                else:
                    continue
                alias = alias.strip().strip("/")
                path = path.strip()
                if not alias or not path:
                    continue
                mounts[f"/{alias}"] = path
        VFS_MAP = mounts
        inbox_mount = str(
            self._read_setting(settings, "inbox_mount", "/bot/inbox") or "/bot/inbox"
        ).strip()
        INBOX_VPATH = inbox_mount if inbox_mount.startswith("/") else f"/{inbox_mount}"

    def _read_setting(self, settings: dict, key: str, default):
        value = settings.get(key, default)
        if isinstance(value, dict):
            return value.get("default", default)
        return value

    async def _list_dir(self, session: SessionState) -> str:
        vpath = session.current_vfs_path
        try:
            physical, base = _resolve_vfs_path(vpath)
        except Exception:
            return "⚠️ 路径越界或未挂载，已拦截。"
        if not physical.exists():
            return "⚠️ 目录不存在。"
        if not physical.is_dir():
            return "⚠️ 目标不是目录。"
        items = await asyncio.to_thread(_scan_dir, physical)
        entries: List[Dict[str, str]] = []
        session.cached_dir_list = []
        for idx, item in enumerate(items[:LIST_LIMIT], 1):
            vchild = _join_virtual(vpath, item["name"])
            entry = {
                "index": str(idx),
                "name": item["name"],
                "is_dir": item["is_dir"],
                "vpath": vchild,
            }
            entries.append(entry)
            session.cached_dir_list.append(entry)
        return _format_listing(vpath, base, entries)

    async def _change_dir(self, session: SessionState, target: str) -> str:
        arg = str(target or "").strip()
        if not arg:
            return f"当前目录: {session.current_vfs_path}"
        if arg.isdigit() and session.cached_dir_list:
            idx = int(arg)
            if idx < 1 or idx > len(session.cached_dir_list):
                return "⚠️ 序号超出范围，请先 /ls。"
            entry = session.cached_dir_list[idx - 1]
            if not entry.get("is_dir"):
                return "⚠️ 该序号不是目录。"
            session.current_vfs_path = _normalize_virtual_path(
                entry.get("vpath", session.current_vfs_path)
            )
            session.cached_dir_list = []
            return f"✅ 已切换到: {session.current_vfs_path}"
        new_vpath = _join_virtual(session.current_vfs_path, arg)
        try:
            physical, _ = _resolve_vfs_path(new_vpath)
        except Exception:
            return "⚠️ 目录越界或未挂载，已拦截。"
        if not physical.exists() or not physical.is_dir():
            return "⚠️ 目录不存在。"
        session.current_vfs_path = _normalize_virtual_path(new_vpath)
        session.cached_dir_list = []
        return f"✅ 已切换到: {session.current_vfs_path}"

    async def _get_file(self, session: SessionState, target: str):
        arg = str(target or "").strip()
        if not arg:
            return "⚠️ 请提供文件名或序号。"
        vpath = ""
        if arg.isdigit() and session.cached_dir_list:
            idx = int(arg)
            if idx < 1 or idx > len(session.cached_dir_list):
                return "⚠️ 序号超出范围，请先 /ls。"
            entry = session.cached_dir_list[idx - 1]
            if entry.get("is_dir"):
                return "⚠️ 该序号是目录，请选择文件。"
            vpath = entry.get("vpath", "")
        else:
            for entry in session.cached_dir_list:
                if entry.get("name") == arg:
                    if entry.get("is_dir"):
                        return "⚠️ 这是目录，请选择文件。"
                    vpath = entry.get("vpath", "")
                    break
            if not vpath:
                vpath = _join_virtual(session.current_vfs_path, arg)
        try:
            physical, _ = _resolve_vfs_path(vpath)
        except Exception:
            return "⚠️ 文件路径越界，已拦截。"
        is_file = await asyncio.to_thread(
            lambda: physical.exists() and physical.is_file()
        )
        if not is_file:
            return "⚠️ 文件不存在。"
        file_name = physical.name
        return {
            "__type__": "gateway_file",
            "file_path": str(physical),
            "file_name": file_name,
            "success_text": f"📎 已发送文件：{file_name}",
            "fallback_text": f"⚠️ 文件已准备好，但回发失败：{file_name}",
        }

    async def _handle_incoming_files(self, files: List[Dict[str, str]]) -> str:
        if not files:
            return ""
        try:
            inbox_path, _ = _resolve_vfs_path(INBOX_VPATH)
        except Exception:
            return "⚠️ 接收目录未挂载，已拦截。"
        await asyncio.to_thread(inbox_path.mkdir, parents=True, exist_ok=True)

        results: List[str] = []
        for item in files:
            name = str(item.get("name") or "").strip() or "file"
            local_path = _parse_local_path(str(item.get("file") or ""))
            url = str(item.get("url") or "").strip()
            try:
                if local_path and local_path.exists() and local_path.is_file():
                    dest = _unique_path(inbox_path, name or local_path.name)
                    await asyncio.to_thread(shutil.move, str(local_path), str(dest))
                    results.append(f"✅ 已保存: {dest.name}")
                    continue
                if url and aiohttp is not None:
                    dest = _unique_path(inbox_path, name)
                    ok = await self._download_file(url, dest)
                    results.append(
                        f"✅ 已下载: {dest.name}" if ok else f"⚠️ 下载失败: {name}"
                    )
                    continue
                results.append(f"⚠️ 无法获取文件: {name}")
            except Exception:
                results.append(f"⚠️ 处理失败: {name}")
        return "\n".join(["📥 已接收文件:"] + results)

    async def _download_file(self, url: str, dest: Path) -> bool:
        if aiohttp is None:
            return False
        timeout = aiohttp.ClientTimeout(total=30)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return False
                    with open(dest, "wb") as f:
                        async for chunk in resp.content.iter_chunked(65536):
                            if not chunk:
                                continue
                            f.write(chunk)
            return True
        except Exception:
            return False

    async def run(self, args: str, context: dict):
        source = str((context or {}).get("source") or "").strip().lower()
        if source not in {"qq_gateway", "napcat_qq"}:
            return "⚠️ 此功能仅支持 QQ 使用。"

        channel_meta = (context or {}).get("channel_meta") or {}
        user_id = str(channel_meta.get("user_id") or "unknown")
        session = _get_session(user_id)
        text = str(args or "").strip()
        files = channel_meta.get("files") or []

        cmd, arg = _parse_command(text)
        if files and cmd in {"", "help"}:
            incoming = await self._handle_incoming_files(files)
            return incoming or "⚠️ 未检测到可接收的文件。"

        if cmd == "ls":
            return await self._list_dir(session)
        if cmd == "cd":
            return await self._change_dir(session, arg)
        if cmd == "pwd":
            return f"当前目录: {session.current_vfs_path}"
        if cmd == "get":
            return await self._get_file(session, arg)
        if cmd == "help" or not cmd:
            return _format_help(session)
        return _format_help(session)
