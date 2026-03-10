import difflib
import fnmatch
import json
import re
import shlex
import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    from config import (
        CODEX_AUTORUN_ENABLED,
        CODEX_AUTORUN_COMMANDS,
        CODEX_AUTORUN_TIMEOUT_SEC,
        CODEX_AUTOROLLBACK_ON_FAIL,
    )
except Exception:
    CODEX_AUTORUN_ENABLED = False
    CODEX_AUTORUN_COMMANDS = []
    CODEX_AUTORUN_TIMEOUT_SEC = 120
    CODEX_AUTOROLLBACK_ON_FAIL = False


class Plugin:
    """
    工作区代码助手插件（安全版）
    - 受限在项目工作区
    - 写入默认走“预览 diff -> apply_change 确认”流程
    - 支持会话级回滚
    """

    name = "workspace_ops"
    type = "react"

    MAX_READ_CHARS = 12000
    MAX_RESULTS = 200
    MAX_HITS = 120
    MAX_DIFF_LINES = 240
    MAX_OUTPUT_CHARS = 6000

    ALLOWED_EXEC_PREFIX = {"python", "pytest", "ruff", "mypy", "uv"}

    TEXT_SUFFIXES = {
        ".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini",
        ".js", ".ts", ".tsx", ".jsx", ".css", ".html", ".xml",
        ".cpp", ".c", ".h", ".hpp", ".java", ".go", ".rs", ".sh", ".bat",
    }

    def __init__(self):
        self.workspace_root = Path.cwd().resolve()
        self.backup_root = (self.workspace_root / "data" / "codex_backups").resolve()
        self.backup_root.mkdir(parents=True, exist_ok=True)
        self.auto_rollback_on_fail = bool(CODEX_AUTOROLLBACK_ON_FAIL)

        self.pending_changes: Dict[str, Dict[str, Any]] = {}
        self.session_backups: Dict[str, Dict[str, Any]] = {}
        self.session_changed_files: List[str] = []

    async def run(self, args: str, ctx: Dict[str, Any]) -> str:
        action, parts = self._parse_args(args or "")
        action = action.lower().strip()

        if action in {"", "help"}:
            return self._help_text()

        try:
            if action == "pwd":
                base = self._resolve_base(ctx)
                return f"workspace_root={self.workspace_root}\nactive_base={base}"

            if action == "list_files":
                self._assert_permission(ctx, need_read=True)
                subpath = parts[0] if len(parts) >= 1 else "."
                pattern = parts[1] if len(parts) >= 2 else "*"
                return self._list_files(subpath, pattern, ctx)

            if action == "read_file":
                self._assert_permission(ctx, need_read=True)
                self._require(parts, 1, "read_file 需要 path")
                return self._read_file(parts[0], ctx)

            if action == "read_range":
                self._assert_permission(ctx, need_read=True)
                self._require(parts, 3, "read_range 需要 path/start/end")
                return self._read_range(parts[0], parts[1], parts[2], ctx)

            if action == "search_code":
                self._assert_permission(ctx, need_read=True)
                self._require(parts, 1, "search_code 需要 pattern")
                pattern = parts[0]
                subpath = parts[1] if len(parts) >= 2 else "."
                glob = parts[2] if len(parts) >= 3 else "*"
                return self._search_code(pattern, subpath, glob, ctx)

            if action == "write_file":
                self._assert_permission(ctx, need_write=True)
                self._require(parts, 2, "write_file 需要 path/content")
                return self._stage_write(parts[0], parts[1], ctx, mode="write")

            if action == "append_file":
                self._assert_permission(ctx, need_write=True)
                self._require(parts, 2, "append_file 需要 path/content")
                return self._stage_write(parts[0], parts[1], ctx, mode="append")

            if action == "replace_text":
                self._assert_permission(ctx, need_write=True)
                self._require(parts, 3, "replace_text 需要 path/old/new")
                return self._stage_replace(parts[0], parts[1], parts[2], ctx)

            if action == "list_changes":
                return self._list_changes()

            if action == "apply_change":
                self._assert_permission(ctx, need_write=True)
                self._require(parts, 2, "apply_change 需要 change_id/confirm_token")
                return self._apply_change(parts[0], parts[1], ctx)

            if action == "discard_change":
                self._require(parts, 1, "discard_change 需要 change_id")
                return self._discard_change(parts[0])

            if action == "session_changes":
                return self._session_changes()

            if action == "rollback_session":
                self._assert_permission(ctx, need_write=True)
                return self._rollback_session()

            if action == "git_status":
                return self._run_git(["status", "--short"], ctx)

            if action == "git_diff":
                path = parts[0] if parts else ""
                cmd = ["diff"]
                if path:
                    target = self._resolve_target(path, ctx, allow_create=False)
                    cmd += ["--", str(target.relative_to(self.workspace_root))]
                return self._run_git(cmd, ctx)

            if action == "git_summary":
                return self._git_summary(ctx)

            if action == "run_cmd":
                self._assert_permission(ctx, need_exec=True)
                self._require(parts, 1, "run_cmd 需要命令")
                return self._run_command(parts[0], ctx)

            if action == "session_context":
                return self._session_context(ctx)

            if action == "session_events":
                limit = int(parts[0]) if parts and str(parts[0]).isdigit() else 20
                return self._session_events(limit)

            if action == "task_status":
                task_id = parts[0].strip() if parts else str(ctx.get("codex_task_id", "")).strip()
                return self._task_status(task_id)

            if action == "task_list":
                limit = int(parts[0]) if parts and str(parts[0]).isdigit() else 20
                return self._task_list(limit)

            return f"未支持的 action: {action}\n\n{self._help_text()}"
        except Exception as e:
            return f"workspace_ops 错误: {e}"

    def _help_text(self) -> str:
        return (
            "workspace_ops 用法:\n"
            "- [CMD: workspace_ops | pwd]\n"
            "- [CMD: workspace_ops | list_files ||| 子路径 ||| 通配符]\n"
            "- [CMD: workspace_ops | read_file ||| 相对路径]\n"
            "- [CMD: workspace_ops | read_range ||| 相对路径 ||| 起始行 ||| 结束行]\n"
            "- [CMD: workspace_ops | search_code ||| 关键词/正则 ||| 子路径 ||| 通配符]\n"
            "- [CMD: workspace_ops | write_file ||| 路径 ||| 新内容] (仅预览)\n"
            "- [CMD: workspace_ops | append_file ||| 路径 ||| 追加内容] (仅预览)\n"
            "- [CMD: workspace_ops | replace_text ||| 路径 ||| old ||| new] (仅预览)\n"
            "- [CMD: workspace_ops | list_changes]\n"
            "- [CMD: workspace_ops | apply_change ||| change_id ||| confirm_token]\n"
            "- [CMD: workspace_ops | discard_change ||| change_id]\n"
            "- [CMD: workspace_ops | session_changes]\n"
            "- [CMD: workspace_ops | rollback_session]\n"
            "- [CMD: workspace_ops | session_events ||| 20]\n"
            "- [CMD: workspace_ops | task_status ||| task_id]\n"
            "- [CMD: workspace_ops | task_list ||| 20]\n"
            "- [CMD: workspace_ops | git_status|git_diff|git_summary]\n"
            "- [CMD: workspace_ops | run_cmd ||| python -m py_compile a.py]\n"
            "限制: 仅允许访问工作区内路径；写入/执行受权限开关控制。"
        )

    def _parse_args(self, raw: str) -> Tuple[str, List[str]]:
        parts = [p.strip() for p in raw.split("|||")]
        if not parts:
            return "", []
        return parts[0], parts[1:]

    def _require(self, parts: List[str], n: int, msg: str):
        if len(parts) < n:
            raise ValueError(msg)

    def _assert_permission(self, ctx: Dict[str, Any], *, need_read=False, need_write=False, need_exec=False):
        allow_read = bool(ctx.get("allow_read", False))
        allow_write = bool(ctx.get("allow_write", False))
        allow_exec = bool(ctx.get("allow_exec", False))

        if need_read and not allow_read:
            raise PermissionError("读取权限已关闭")
        if need_write and not allow_write:
            raise PermissionError("写入权限已关闭")
        if need_exec and not allow_exec:
            raise PermissionError("执行权限已关闭")

    def _is_within(self, path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _resolve_base(self, ctx: Dict[str, Any]) -> Path:
        base = self.workspace_root
        code_path_raw = str((ctx or {}).get("code_path", "")).strip()
        if not code_path_raw:
            return base

        candidate = Path(code_path_raw).expanduser()
        if not candidate.is_absolute():
            candidate = (self.workspace_root / candidate).resolve()
        else:
            candidate = candidate.resolve()

        if candidate.exists():
            candidate = candidate if candidate.is_dir() else candidate.parent
        else:
            candidate = candidate.parent.resolve()

        if candidate.exists() and self._is_within(candidate, self.workspace_root):
            return candidate
        return base

    def _resolve_target(self, path_str: str, ctx: Dict[str, Any], *, allow_create: bool = False) -> Path:
        path_str = (path_str or "").strip()
        if not path_str:
            raise ValueError("路径不能为空")
        base = self._resolve_base(ctx)
        p = Path(path_str).expanduser()
        target = p.resolve() if p.is_absolute() else (base / p).resolve()
        if not self._is_within(target, self.workspace_root):
            raise PermissionError(f"路径越界: {target}")
        if not allow_create and not target.exists():
            raise FileNotFoundError(f"文件不存在: {target}")
        return target

    def _list_files(self, subpath: str, pattern: str, ctx: Dict[str, Any]) -> str:
        root = self._resolve_target(subpath, ctx, allow_create=False)
        if not root.is_dir():
            raise NotADirectoryError(f"不是目录: {root}")
        items: List[str] = []
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(self.workspace_root).as_posix()
            if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(p.name, pattern):
                items.append(rel)
                if len(items) >= self.MAX_RESULTS:
                    break
        return "\n".join(items) if items else "未找到匹配文件"

    def _read_file(self, path_str: str, ctx: Dict[str, Any]) -> str:
        target = self._resolve_target(path_str, ctx, allow_create=False)
        text = target.read_text(encoding="utf-8", errors="replace")
        truncated = text[: self.MAX_READ_CHARS]
        rel = target.relative_to(self.workspace_root).as_posix()
        suffix = "\n...[truncated]" if len(text) > self.MAX_READ_CHARS else ""
        return f"# {rel}\n{truncated}{suffix}"

    def _read_range(self, path_str: str, start_s: str, end_s: str, ctx: Dict[str, Any]) -> str:
        target = self._resolve_target(path_str, ctx, allow_create=False)
        start = int(start_s)
        end = int(end_s)
        if start <= 0 or end < start:
            raise ValueError("行号范围非法")
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        if start > len(lines):
            return f"起始行超出文件总行数({len(lines)})"
        end = min(end, len(lines))
        rel = target.relative_to(self.workspace_root).as_posix()
        out = [f"# {rel}:{start}-{end}"]
        for i in range(start, end + 1):
            out.append(f"{i}: {lines[i - 1]}")
        return "\n".join(out)

    def _search_code(self, pattern: str, subpath: str, glob: str, ctx: Dict[str, Any]) -> str:
        root = self._resolve_target(subpath, ctx, allow_create=False)
        if not root.is_dir():
            raise NotADirectoryError(f"不是目录: {root}")
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error:
            regex = None
        hits: List[str] = []
        for p in root.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in self.TEXT_SUFFIXES:
                continue
            rel = p.relative_to(self.workspace_root).as_posix()
            if glob and glob != "*" and not (fnmatch.fnmatch(rel, glob) or fnmatch.fnmatch(p.name, glob)):
                continue
            try:
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                continue
            for idx, line in enumerate(lines, 1):
                ok = bool(regex.search(line)) if regex else (pattern.lower() in line.lower())
                if not ok:
                    continue
                hits.append(f"{rel}:{idx}: {line.strip()}")
                if len(hits) >= self.MAX_HITS:
                    break
            if len(hits) >= self.MAX_HITS:
                break
        return "\n".join(hits) if hits else "未找到匹配内容"

    def _build_new_content(self, old: str, content: str, mode: str) -> str:
        if mode == "append":
            return old + content
        return content

    def _build_diff(self, rel: str, old: str, new: str) -> str:
        lines = list(
            difflib.unified_diff(
                old.splitlines(),
                new.splitlines(),
                fromfile=f"a/{rel}",
                tofile=f"b/{rel}",
                lineterm="",
            )
        )
        if len(lines) > self.MAX_DIFF_LINES:
            lines = lines[: self.MAX_DIFF_LINES] + ["... (diff truncated)"]
        return "\n".join(lines) if lines else "(no changes)"

    def _stage_write(self, path_str: str, content: str, ctx: Dict[str, Any], mode: str) -> str:
        target = self._resolve_target(path_str, ctx, allow_create=True)
        old = ""
        if target.exists():
            old = target.read_text(encoding="utf-8", errors="replace")
        new = self._build_new_content(old, content, mode)
        return self._stage_change(target, old, new, ctx)

    def _stage_replace(self, path_str: str, old_text: str, new_text: str, ctx: Dict[str, Any]) -> str:
        target = self._resolve_target(path_str, ctx, allow_create=False)
        old = target.read_text(encoding="utf-8", errors="replace")
        if old_text not in old:
            return "未找到要替换的文本"
        new = old.replace(old_text, new_text, 1)
        return self._stage_change(target, old, new, ctx)

    def _stage_change(self, target: Path, old: str, new: str, ctx: Dict[str, Any]) -> str:
        rel = target.relative_to(self.workspace_root).as_posix()
        change_id = uuid.uuid4().hex[:10]
        confirm_token = uuid.uuid4().hex[:8]
        diff_text = self._build_diff(rel, old, new)
        task_id = str((ctx or {}).get("codex_task_id", "")).strip()
        self.pending_changes[change_id] = {
            "target": target,
            "old": old,
            "new": new,
            "diff": diff_text,
            "time": datetime.now().isoformat(timespec="seconds"),
            "confirm_token": confirm_token,
            "task_id": task_id,
        }
        if task_id:
            try:
                from modules.codex_task_state import set_task_state
                set_task_state(
                    task_id,
                    "proposed_change",
                    summary=f"待确认变更: {rel}",
                    meta={"change_id": change_id, "confirm_token": confirm_token, "file": rel, "preview": diff_text[:1500]},
                )
            except Exception:
                pass
        task_tip = f"\n任务ID: {task_id}" if task_id else ""
        return (
            f"已生成变更预览 change_id={change_id}\n"
            f"确认令牌 confirm_token={confirm_token}{task_tip}\n"
            f"目标文件: {rel}\n"
            "请确认后执行：\n"
            f"[CMD: workspace_ops | apply_change ||| {change_id} ||| {confirm_token}]\n"
            "或取消：\n"
            f"[CMD: workspace_ops | discard_change ||| {change_id}]\n\n"
            "=== DIFF ===\n"
            f"{diff_text}"
        )

    def _list_changes(self) -> str:
        if not self.pending_changes:
            return "当前没有待确认变更"
        out = ["待确认变更："]
        for cid, item in self.pending_changes.items():
            target = item["target"].relative_to(self.workspace_root).as_posix()
            token = str(item.get("confirm_token", ""))
            task_id = str(item.get("task_id", ""))
            extra = f" | token={token}"
            if task_id:
                extra += f" | task={task_id}"
            out.append(f"- {cid} | {target} | {item.get('time','')}{extra}")
        return "\n".join(out)

    def _snapshot_before_write(self, target: Path):
        key = str(target.resolve())
        if key in self.session_backups:
            return
        rel = target.relative_to(self.workspace_root).as_posix()
        if target.exists():
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            safe_rel = rel.replace("/", "__")
            backup = self.backup_root / f"{ts}__{safe_rel}"
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
            self.session_backups[key] = {"mode": "file", "backup": str(backup), "rel": rel}
        else:
            self.session_backups[key] = {"mode": "created", "backup": "", "rel": rel}

    def _apply_change(self, change_id: str, confirm_token: str, ctx: Dict[str, Any]) -> str:
        item = self.pending_changes.get(change_id)
        if not item:
            return f"未找到变更: {change_id}"
        if bool(ctx.get("codex_mode", False)):
            user_confirmed = bool(ctx.get("codex_user_confirmed_apply", False))
            confirmed_change_id = str(ctx.get("codex_confirm_change_id", "")).strip()
            confirmed_token = str(ctx.get("codex_confirm_token", "")).strip()
            if not user_confirmed or confirmed_change_id != change_id or confirmed_token != str(confirm_token).strip():
                return "拒绝应用变更：需要用户在本轮消息中显式确认 change_id 与 confirm_token"
        expected = str(item.get("confirm_token", "")).strip()
        if not confirm_token or str(confirm_token).strip() != expected:
            return "确认令牌不匹配，拒绝应用变更"

        target: Path = item["target"]
        target.parent.mkdir(parents=True, exist_ok=True)
        self._snapshot_before_write(target)
        target.write_text(item["new"], encoding="utf-8")

        rel = target.relative_to(self.workspace_root).as_posix()
        if rel not in self.session_changed_files:
            self.session_changed_files.append(rel)
        self.pending_changes.pop(change_id, None)
        task_id = str(ctx.get("codex_task_id", "")).strip() or str(item.get("task_id", "")).strip()
        if task_id:
            try:
                from modules.codex_task_state import set_task_state
                set_task_state(
                    task_id,
                    "applied",
                    summary=f"已应用变更: {rel}",
                    meta={"change_id": change_id, "file": rel},
                )
            except Exception:
                pass

        auto = bool(ctx.get("codex_autorun", False)) or bool(CODEX_AUTORUN_ENABLED)
        auto_report = ""
        if auto:
            if bool(ctx.get("allow_exec", False)):
                try:
                    auto_report = "\n\n" + self._autorun(ctx, changed_paths=[target])
                except Exception as e:
                    auto_report = f"\n\n自动回归执行失败: {e}"
            else:
                auto_report = "\n\n自动回归已跳过：执行权限已关闭 (allow_exec=False)"

        try:
            from modules.codex_session import add_event as codex_add_event
            codex_add_event(
                "apply_change",
                code_path=str(ctx.get("code_path", "")),
                files=[rel],
                meta={
                    "change_id": change_id,
                    "task_id": str(ctx.get("codex_task_id", "")),
                },
            )
        except Exception:
            pass

        return f"已应用变更: {rel} (change_id={change_id}){auto_report}"

    def _discard_change(self, change_id: str) -> str:
        if change_id not in self.pending_changes:
            return f"未找到变更: {change_id}"
        self.pending_changes.pop(change_id, None)
        return f"已丢弃变更: {change_id}"

    def _session_changes(self) -> str:
        if not self.session_changed_files:
            return "本会话暂无已应用文件变更"
        return "本会话变更文件:\n" + "\n".join(f"- {x}" for x in self.session_changed_files)

    def _rollback_session(self) -> str:
        if not self.session_backups:
            return "没有可回滚的会话变更"

        restored = []
        for key, info in list(self.session_backups.items()):
            target = Path(key)
            mode = info.get("mode")
            if mode == "file":
                backup = Path(info.get("backup", ""))
                if backup.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup, target)
                    restored.append(info.get("rel", target.name))
            elif mode == "created":
                if target.exists():
                    target.unlink(missing_ok=True)
                restored.append(info.get("rel", target.name))

        self.pending_changes.clear()
        self.session_backups.clear()
        self.session_changed_files.clear()
        return "已回滚本会话变更:\n" + "\n".join(f"- {x}" for x in restored)

    def _run_git(self, args: List[str], ctx: Dict[str, Any]) -> str:
        cmd = ["git"] + args
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self.workspace_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=40,
            )
            out = (proc.stdout or "").strip()
            err = (proc.stderr or "").strip()
            if proc.returncode != 0:
                return f"git 执行失败(code={proc.returncode}):\n{err or out}"
            return out or "(empty)"
        except Exception as e:
            return f"git 执行异常: {e}"

    def _git_summary(self, ctx: Dict[str, Any]) -> str:
        raw = self._run_git(["diff", "--numstat"], ctx)
        if raw.startswith("git 执行"):
            return raw
        if not raw.strip() or raw.strip() == "(empty)":
            return "当前无未提交代码差异"
        files = 0
        add_lines = 0
        del_lines = 0
        details = []
        for line in raw.splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            a, d, f = parts[0], parts[1], parts[2]
            try:
                add_lines += int(a)
            except Exception:
                pass
            try:
                del_lines += int(d)
            except Exception:
                pass
            files += 1
            details.append(f"- {f}: +{a} -{d}")
        return f"git diff 摘要: files={files}, +{add_lines}, -{del_lines}\n" + "\n".join(details[:80])

    def _run_command_result(self, command: str) -> Dict[str, Any]:
        command = (command or "").strip()
        if not command:
            return {"ok": False, "code": -1, "report": "命令为空"}
        try:
            parts = shlex.split(command, posix=True)
        except Exception:
            parts = shlex.split(command, posix=False)
        if not parts:
            return {"ok": False, "code": -1, "report": "命令解析失败"}
        prefix = parts[0].lower()
        if prefix not in self.ALLOWED_EXEC_PREFIX:
            return {"ok": False, "code": -1, "report": f"命令前缀不在白名单: {prefix}"}
        try:
            proc = subprocess.run(
                parts,
                cwd=str(self.workspace_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=int(CODEX_AUTORUN_TIMEOUT_SEC),
            )
            out = (proc.stdout or "")[-self.MAX_OUTPUT_CHARS :]
            err = (proc.stderr or "")[-self.MAX_OUTPUT_CHARS :]
            return {
                "ok": proc.returncode == 0,
                "code": proc.returncode,
                "report": f"$ {command}\nexit={proc.returncode}\n{out}\n{err}".strip(),
            }
        except Exception as e:
            return {"ok": False, "code": -1, "report": f"命令执行异常: {e}"}

    def _run_command(self, command: str, ctx: Dict[str, Any]) -> str:
        return self._run_command_result(command).get("report", "")

    def _rollback_paths(self, paths: List[Path]) -> List[str]:
        restored: List[str] = []
        for p in paths:
            key = str(p.resolve())
            info = self.session_backups.get(key)
            if not info:
                continue
            mode = info.get("mode")
            rel = info.get("rel", p.name)
            if mode == "file":
                backup = Path(info.get("backup", ""))
                if backup.exists():
                    p.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup, p)
                    restored.append(rel)
            elif mode == "created":
                if p.exists():
                    p.unlink(missing_ok=True)
                restored.append(rel)
            if rel in self.session_changed_files:
                self.session_changed_files.remove(rel)
        return restored

    def _autorun(self, ctx: Dict[str, Any], changed_paths: List[Path] | None = None) -> str:
        self._assert_permission(ctx, need_exec=True)
        cmds = CODEX_AUTORUN_COMMANDS or []
        if not isinstance(cmds, list) or not cmds:
            return "自动回归已启用，但未配置 CODEX_AUTORUN_COMMANDS"
        reports = ["自动回归结果："]
        failed = []
        task_id = str((ctx or {}).get("codex_task_id", "")).strip()
        for cmd in cmds:
            result = self._run_command_result(str(cmd))
            reports.append(result.get("report", ""))
            if not result.get("ok", False):
                failed.append({"cmd": str(cmd), "code": result.get("code", -1)})
        if failed:
            failed_line = ", ".join([f"{x['cmd']} (exit={x['code']})" for x in failed[:5]])
            reports.append(f"自动回归失败: {failed_line}")
            if task_id:
                try:
                    from modules.codex_task_state import set_task_state
                    set_task_state(task_id, "verify_failed", summary="自动回归失败", meta={"failed": failed[:5]})
                except Exception:
                    pass
            if self.auto_rollback_on_fail and changed_paths:
                restored = self._rollback_paths(changed_paths)
                if restored:
                    reports.append("已自动回滚失败变更: " + ", ".join(restored))
                    if task_id:
                        try:
                            from modules.codex_task_state import set_task_state
                            set_task_state(task_id, "rollback_done", summary="自动回滚已执行", meta={"files": restored})
                        except Exception:
                            pass
                else:
                    reports.append("自动回滚已启用，但未找到可回滚快照")
            else:
                reports.append("未自动回滚。可手动执行 [CMD: workspace_ops | rollback_session]")
        else:
            if task_id:
                try:
                    from modules.codex_task_state import set_task_state
                    set_task_state(task_id, "verify_passed", summary="自动回归通过")
                except Exception:
                    pass
        return "\n\n".join(reports)

    def _session_context(self, ctx: Dict[str, Any]) -> str:
        base = self._resolve_base(ctx)
        pending = len(self.pending_changes)
        changed = len(self.session_changed_files)
        task_id = str((ctx or {}).get("codex_task_id", "")).strip()
        return (
            f"active_base={base}\n"
            f"active_task_id={task_id}\n"
            f"pending_changes={pending}\n"
            f"session_changed_files={changed}\n"
            + ("\n".join(f"- {x}" for x in self.session_changed_files) if self.session_changed_files else "")
        )

    def _session_events(self, limit: int = 20) -> str:
        try:
            from modules.codex_session import get_recent as codex_get_recent
            items = codex_get_recent(max(1, min(100, int(limit))))
        except Exception as e:
            return f"读取 codex 会话事件失败: {e}"

        if not items:
            return "暂无 codex 会话事件"

        out = [f"最近 {len(items)} 条 codex 会话事件："]
        for item in items:
            t = item.get("time", "")
            tp = item.get("type", "")
            path = item.get("code_path", "")
            files = item.get("files", []) or []
            text = (item.get("user_text", "") or "").strip()
            meta = item.get("meta", {}) or {}
            task_id = str(meta.get("task_id", "")).strip()
            line = f"- [{t}] type={tp}"
            if task_id:
                line += f" task={task_id}"
            if path:
                line += f" path={path}"
            if files:
                line += f" files={','.join(map(str, files[:5]))}"
            if text:
                line += f" text={text[:80]}"
            out.append(line)
        return "\n".join(out)

    def _task_status(self, task_id: str) -> str:
        task_id = str(task_id or "").strip()
        if not task_id:
            return "请提供 task_id"
        try:
            from modules.codex_task_state import get_task
            task = get_task(task_id)
        except Exception as e:
            return f"读取任务状态失败: {e}"
        if not task:
            return f"未找到任务: {task_id}"
        state = task.get("state", "")
        code_path = task.get("code_path", "")
        summary = task.get("summary", "")
        updated = task.get("updated_at", "")
        return (
            f"task_id={task_id}\n"
            f"state={state}\n"
            f"code_path={code_path}\n"
            f"updated_at={updated}\n"
            f"summary={summary}"
        )

    def _task_list(self, limit: int = 20) -> str:
        try:
            from modules.codex_task_state import get_recent_tasks
            items = get_recent_tasks(max(1, min(100, int(limit))))
        except Exception as e:
            return f"读取任务列表失败: {e}"
        if not items:
            return "暂无任务记录"
        out = [f"最近 {len(items)} 个任务："]
        for it in items:
            out.append(
                f"- {it.get('task_id','')} | {it.get('state','')} | {it.get('updated_at','')} | {it.get('code_path','')}"
            )
        return "\n".join(out)
