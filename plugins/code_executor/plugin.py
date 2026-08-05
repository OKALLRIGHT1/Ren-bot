"""Code executor via local Codex CLI / Claude Code (no in-process sandbox)."""

from __future__ import annotations

import re
import uuid
from typing import Any, Callable, Dict, Optional, Tuple

from core.logger import get_logger
from modules.code_agent import CodeAgentRequest, discover_agent_command, run_code_agent
from modules.security_redaction import redact_sensitive_text
from plugins.plugin_utils import handle_plugin_errors

try:
    from config import CODE_EXECUTOR_ENABLED, CODE_EXECUTOR_MAX_TIME
except ImportError:
    CODE_EXECUTOR_ENABLED = False
    CODE_EXECUTOR_MAX_TIME = 300


def _get_logger():
    try:
        return get_logger()
    except Exception:
        return None


class Plugin:
    """
    代码执行 / 代码任务插件。

    不再在进程内跑 Python 沙箱，而是委托本机 Codex CLI / Claude Code。
    高风险：需 ActionGate 确认（local 或管理员 QQ 私聊）。
    """

    name = "代码执行器"
    type = "react"
    gated_action = "system.exec_code"
    plugin_trigger = "code"

    def __init__(
        self,
        *,
        runner: Callable[[CodeAgentRequest], Any] | None = None,
        discoverer: Callable[[str], str] | None = None,
    ) -> None:
        self._runner = runner or run_code_agent
        self._discoverer = discoverer or discover_agent_command
        self.settings: Dict[str, Any] = {}
        self.ENABLED = bool(CODE_EXECUTOR_ENABLED)

    @handle_plugin_errors("代码执行器")
    async def run(self, args: str, ctx: Dict[str, Any]) -> Any:
        if not args or not str(args).strip():
            return "❌ 请提供要执行/分析的代码或任务描述"

        if not self.ENABLED and not self._setting_bool("force_enabled", False):
            return (
                "❌ 代码执行器未启用。请在环境变量设置 CODE_EXECUTOR_ENABLED=1，"
                "并安装本机 Codex CLI 或 Claude Code。"
            )

        # Defense in depth: ActionGate should already require confirm; keep explicit check.
        if not bool((ctx or {}).get("action_confirmed") or (ctx or {}).get("gate_confirmed")):
            preview = self._preview(args)
            return {
                "__agent_result__": "confirmation_required",
                "trigger": str(
                    getattr(self, "plugin_trigger", None) or "code"
                ),
                "summary": (
                    "⚠️ 将通过本机 Codex/Claude Code 处理代码任务（高风险）。\n"
                    f"预览: {preview}"
                ),
                "payload": {
                    "mode": "gate_rerun",
                    "args": args,
                    "gated_action": "system.exec_code",
                    "provider": self._default_provider(),
                },
                "expires_in": 300,
            }

        provider, prompt, cwd = self._parse_args(args, ctx)
        command_template = self._command_template(provider)
        if not command_template:
            return (
                f"❌ 未找到 {self._provider_label(provider)} 命令。"
                "请安装 Codex CLI / Claude Code，或在插件设置里配置命令模板。"
            )

        request = CodeAgentRequest(
            provider=provider,
            prompt=self._build_prompt(prompt),
            cwd=cwd,
            command_template=command_template,
            timeout_sec=self._timeout_sec(),
            allow_write=self._looks_like_modify(prompt),
            allow_exec=True,  # already gate-confirmed
            task_id=str((ctx or {}).get("codex_task_id") or uuid.uuid4().hex[:8]),
        )

        log = _get_logger()
        if log:
            log.info(
                "code_executor external CLI provider=%s cwd=%s chars=%s",
                provider,
                cwd,
                len(request.prompt),
            )

        try:
            result = await self._runner(request)
            return self._format_result(result, provider)
        except PermissionError as exc:
            return f"❌ {redact_sensitive_text(exc)}"
        except Exception as exc:
            if log:
                log.error("code_executor CLI failed: %s", redact_sensitive_text(exc))
            return f"❌ 外部代码代理执行失败: {redact_sensitive_text(exc)}"

    async def confirm_agent_action(self, payload: Dict[str, Any], ctx: Dict[str, Any]) -> str:
        runtime = dict(ctx or {})
        runtime["action_confirmed"] = True
        runtime["gate_confirmed"] = True
        args = str(payload.get("args") or "").strip()
        if not args:
            # allow payload-built prompt
            provider = str(payload.get("provider") or self._default_provider())
            prompt = str(payload.get("prompt") or "").strip()
            cwd = str(payload.get("cwd") or self._default_cwd(ctx)).strip()
            if not prompt:
                return "确认载荷缺少任务内容，已取消。"
            args = f"{provider} ||| {cwd} ||| {prompt}"
        result = await self.run(args, runtime)
        if isinstance(result, dict):
            return str(result.get("summary") or result)
        return str(result)

    def _parse_args(self, args: str, ctx: Dict[str, Any]) -> Tuple[str, str, str]:
        text = str(args or "").strip()
        # Strip common react wrappers
        for prefix in ("execute_code", "code", "python", "run"):
            if text.lower().startswith(prefix + " "):
                text = text[len(prefix) :].strip()
                break

        parts = [p.strip() for p in text.split("|||")]
        if len(parts) >= 3 and self._normalize_provider(parts[0]):
            provider = self._normalize_provider(parts[0]) or self._default_provider()
            cwd = parts[1] or self._default_cwd(ctx)
            prompt = parts[2]
            return provider, prompt, cwd
        if len(parts) == 2 and self._normalize_provider(parts[0]):
            provider = self._normalize_provider(parts[0]) or self._default_provider()
            return provider, parts[1], self._default_cwd(ctx)

        provider, stripped = self._detect_provider(text)
        provider = provider or self._default_provider()
        code = self._extract_code(stripped or text)
        return provider, code, self._default_cwd(ctx)

    def _extract_code(self, text: str) -> str:
        code_block = re.search(r"```(?:python)?\s*?\n(.*?)```", text, re.DOTALL)
        if code_block:
            return code_block.group(1).strip()
        return str(text or "").strip()

    def _build_prompt(self, prompt: str) -> str:
        body = str(prompt or "").strip()
        # If it looks like raw code, ask CLI to run/analyze and return output
        if "\n" in body or re.search(r"\b(def|import|print|class)\b", body):
            return (
                "请在当前工作目录用合适方式处理以下 Python/代码任务，"
                "返回关键结果或错误，不要多余寒暄：\n\n"
                f"{body}"
            )
        return body

    def _detect_provider(self, text: str) -> Tuple[str, str]:
        raw = str(text or "")
        lowered = raw.lower()
        if "claude code" in lowered or re.search(r"(^|[^\w])claude([^\w]|$)", lowered):
            cleaned = re.sub(r"claude(?:\s*code)?", "", raw, flags=re.IGNORECASE).strip()
            return "claude_code", cleaned
        if "codex" in lowered:
            cleaned = re.sub("codex", "", raw, flags=re.IGNORECASE).strip()
            return "codex_cli", cleaned
        return "", raw.strip()

    def _normalize_provider(self, provider: str) -> str:
        raw = str(provider or "").strip().lower().replace("-", "_").replace(" ", "_")
        if raw in {"codex", "codex_cli"}:
            return "codex_cli"
        if raw in {"claude", "claude_code", "cc"}:
            return "claude_code"
        return ""

    def _looks_like_modify(self, text: str) -> bool:
        return bool(
            re.search(
                r"(修改|修复|重构|改一下|写入|生成|新增|删除|移动|安装|打包)",
                str(text or ""),
                flags=re.IGNORECASE,
            )
        )

    def _command_template(self, provider: str) -> str:
        setting_key = f"{provider}_command_template"
        configured = str(self._setting(setting_key, "") or "").strip()
        return configured or self._discoverer(provider)

    def _format_result(self, result: Any, provider: str) -> str:
        ok = bool(getattr(result, "ok", False))
        status = "完成" if ok else "失败"
        stdout = str(getattr(result, "stdout", "") or "").strip()
        stderr = str(getattr(result, "stderr", "") or "").strip()
        exit_code = getattr(result, "exit_code", "")
        command_preview = str(getattr(result, "command_preview", "") or "").strip()
        duration = getattr(result, "duration_sec", None)
        lines = [
            f"✅ 外部代码代理{status}"
            if ok
            else f"❌ 外部代码代理{status}",
            f"provider={self._provider_label(provider)} exit={exit_code}",
        ]
        if duration is not None:
            try:
                lines.append(f"耗时: {float(duration):.2f}s")
            except (TypeError, ValueError):
                pass
        if command_preview:
            lines.append(f"命令: {command_preview}")
        if stdout:
            lines.append(redact_sensitive_text(stdout))
        if stderr:
            lines.append("stderr:\n" + redact_sensitive_text(stderr))
        return "\n".join(lines)

    def _provider_label(self, provider: str) -> str:
        if provider == "claude_code":
            return "Claude Code"
        return "Codex"

    def _preview(self, args: str) -> str:
        preview = str(args or "").strip().replace("\n", " ")
        if len(preview) > 120:
            preview = preview[:120] + "..."
        return preview

    def _default_provider(self) -> str:
        return (
            self._normalize_provider(str(self._setting("default_provider", "codex_cli")))
            or "codex_cli"
        )

    def _default_cwd(self, ctx: Optional[Dict[str, Any]]) -> str:
        for key in ("code_path", "app_root", "cwd"):
            value = str((ctx or {}).get(key) or "").strip()
            if value:
                return value
        return str(self._setting("default_cwd", ".") or ".").strip() or "."

    def _timeout_sec(self) -> int:
        try:
            configured = int(self._setting("timeout_sec", CODE_EXECUTOR_MAX_TIME or 300))
        except (TypeError, ValueError):
            configured = 300
        # CLI tasks are slower than in-process scripts
        return max(30, min(3600, configured if configured > 0 else 300))

    def _setting(self, key: str, default: Any) -> Any:
        value = (self.settings or {}).get(key, default)
        if isinstance(value, dict):
            return value.get("default", default)
        return value

    def _setting_bool(self, key: str, default: bool = False) -> bool:
        value = self._setting(key, default)
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}
