from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from modules.code_agent import CodeAgentRequest, discover_agent_command, run_code_agent
from services.capability_manager import ToolCapability, ToolCapabilityMatch


class Plugin:
    name = "代码代理"
    type = "direct"
    description = "把代码项目分析或修改任务委托给本机 Codex CLI / Claude Code。"
    aliases = ["code_agent", "codex", "Codex", "claude code", "Claude Code", "cc"]
    allow_natural_language_direct = True
    tool_examples = [
        "让 Codex 分析这个项目为什么启动失败",
        "用 Claude Code 修改 README",
        "code_agent analyze ||| codex_cli ||| . ||| 检查报错",
    ]

    def __init__(
        self,
        *,
        runner: Callable[[CodeAgentRequest], Any] | None = None,
        discoverer: Callable[[str], str] | None = None,
    ) -> None:
        self._runner = runner or run_code_agent
        self._discoverer = discoverer or discover_agent_command
        self.settings: Dict[str, Any] = {}

    def get_capabilities(self):
        return [
            ToolCapability(
                id="code_agent.codex_task",
                plugin="code_agent",
                trigger_mode="natural",
                match=lambda text, ctx: self._match_agent_task(
                    text, ctx, provider="codex_cli"
                ),
                description="委托 Codex CLI 分析或修改代码项目",
                examples=["让 Codex 分析这个项目为什么启动失败"],
            ),
            ToolCapability(
                id="code_agent.claude_task",
                plugin="code_agent",
                trigger_mode="natural",
                match=lambda text, ctx: self._match_agent_task(
                    text, ctx, provider="claude_code"
                ),
                description="委托 Claude Code 分析或修改代码项目",
                examples=["用 Claude Code 修改 README"],
            ),
        ]

    def should_handle_direct(self, text: str, context: Dict[str, Any], key: str) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return False
        if raw.lower().startswith(("code_agent ", "code_agent\n")):
            return True
        provider, _ = self._detect_provider(raw)
        if not provider:
            return False
        return bool(
            re.search(
                r"(分析|检查|查看|看一下|看看|读一下|审查|排查|修复|修改|重构|改一下|处理|接手|帮我|画|画图|绘图|生图|生成图片)",
                raw,
                flags=re.IGNORECASE,
            )
        )

    def _match_agent_task(
        self,
        text: str,
        ctx: Dict[str, Any],
        *,
        provider: str,
    ) -> Optional[ToolCapabilityMatch]:
        raw = str(text or "").strip()
        if not raw:
            return None
        detected_provider, _ = self._detect_provider(raw)
        if detected_provider != provider:
            return None
        if not self.should_handle_direct(raw, ctx, "code_agent"):
            return None
        action = "modify" if self._looks_like_modify(raw) else "analyze"
        return ToolCapabilityMatch(
            capability_id=(
                "code_agent.claude_task"
                if provider == "claude_code"
                else "code_agent.codex_task"
            ),
            plugin="code_agent",
            score=0.9,
            args={"provider": provider, "action": action},
            raw_text=raw,
            reason="code_agent_provider_task",
        )

    async def run(self, args: str, ctx: Dict[str, Any]) -> Any:
        action, provider, cwd, prompt = self._parse_request(args)
        if action in {"", "help"}:
            return self._help_text()
        if action == "status":
            return self._status_text()
        if action not in {"analyze", "modify"}:
            return f"不支持的 action: {action}\n\n{self._help_text()}"

        command_template = self._command_template(provider)
        if not command_template:
            return f"未找到 {self._provider_label(provider)} 命令，请先安装或在设置里配置命令模板。"
        request = self._build_request(
            provider=provider,
            cwd=cwd,
            prompt=prompt,
            command_template=command_template,
            action=action,
            ctx=ctx,
        )

        if action == "modify":
            if not request.allow_exec:
                return self._exec_permission_message()
            return {
                "__agent_result__": "confirmation_required",
                "trigger": "code_agent",
                "summary": (
                    f"将使用 {self._provider_label(provider)} 修改 {request.cwd}。\n"
                    "确认后会启动外部代码代理执行。"
                ),
                "payload": {
                    "provider": provider,
                    "cwd": request.cwd,
                    "prompt": prompt,
                    "command_template": command_template,
                    "timeout_sec": request.timeout_sec,
                    "allow_write": True,
                    "task_id": request.task_id,
                },
                "expires_in": 300,
            }

        if not request.allow_exec:
            return self._exec_permission_message()
        result = await self._runner(request)
        return self._format_result(result)

    async def confirm_agent_action(self, payload: Dict[str, Any], ctx: Dict[str, Any]) -> str:
        provider = str(payload.get("provider") or "codex_cli").strip()
        request = CodeAgentRequest(
            provider=provider,
            prompt=str(payload.get("prompt") or "").strip(),
            cwd=str(payload.get("cwd") or ".").strip(),
            command_template=str(payload.get("command_template") or "").strip(),
            timeout_sec=int(payload.get("timeout_sec") or self._setting_int("timeout_sec", 300)),
            allow_write=bool(payload.get("allow_write", True)),
            allow_exec=bool(ctx.get("allow_exec", False)),
            task_id=str(payload.get("task_id") or uuid.uuid4().hex[:8]),
        )
        result = await self._runner(request)
        return self._format_result(result)

    def _parse_request(self, raw: str) -> Tuple[str, str, str, str]:
        text = str(raw or "").strip()
        if text.lower().startswith("code_agent"):
            text = text[len("code_agent") :].strip()
        parts = [p.strip() for p in text.split("|||")]
        action = parts[0].strip().lower() if parts else ""
        if action in {"help", "status"}:
            return action, self._default_provider(), self._default_cwd(), ""
        if action in {"analyze", "modify"}:
            provider = self._normalize_provider(parts[1] if len(parts) >= 2 else "")
            cwd = parts[2] if len(parts) >= 3 else self._default_cwd()
            prompt = parts[3] if len(parts) >= 4 else ""
            return action, provider or self._default_provider(), cwd or self._default_cwd(), prompt

        provider, stripped = self._detect_provider(text)
        provider = provider or self._default_provider()
        action = "modify" if self._looks_like_modify(text) else "analyze"
        cwd = self._extract_cwd(text) or self._default_cwd()
        prompt = stripped or text
        return action, provider, cwd, prompt

    def _detect_provider(self, text: str) -> Tuple[str, str]:
        raw = str(text or "")
        lowered = raw.lower()
        if "claude code" in lowered:
            return "claude_code", re.sub("claude code", "", raw, flags=re.IGNORECASE).strip()
        if re.search(r"(^|[^\w])cc([^\w]|$)", lowered):
            return "claude_code", re.sub(r"(^|[^\w])cc([^\w]|$)", " ", raw, flags=re.IGNORECASE).strip()
        if "codex" in lowered:
            return "codex_cli", re.sub("codex", "", raw, flags=re.IGNORECASE).strip()
        return "", raw.strip()

    def _looks_like_modify(self, text: str) -> bool:
        return bool(
            re.search(
                r"(修改|修复|重构|改一下|写入|生成|新增|删除|移动|安装|打包)",
                str(text or ""),
                flags=re.IGNORECASE,
            )
        )

    def _looks_like_image_generation(self, text: str) -> bool:
        return bool(
            re.search(
                r"(画图|画画|画一张|绘图|生图|生成图|生成图片|图片生成|发图)",
                str(text or ""),
                flags=re.IGNORECASE,
            )
        )

    def _extract_cwd(self, text: str) -> str:
        match = re.search(r"([A-Za-z]:\\[^\s，。]+|[./][^\s，。]+)", str(text or ""))
        return match.group(1).strip() if match else ""

    def _normalize_provider(self, provider: str) -> str:
        raw = str(provider or "").strip().lower().replace("-", "_").replace(" ", "_")
        if raw in {"codex", "codex_cli"}:
            return "codex_cli"
        if raw in {"claude", "claude_code", "cc"}:
            return "claude_code"
        return raw

    def _build_request(
        self,
        *,
        provider: str,
        cwd: str,
        prompt: str,
        command_template: str,
        action: str,
        ctx: Dict[str, Any],
    ) -> CodeAgentRequest:
        return CodeAgentRequest(
            provider=provider,
            prompt=str(prompt or "").strip(),
            cwd=self._resolve_cwd(cwd, ctx),
            command_template=command_template,
            timeout_sec=self._setting_int("timeout_sec", 300),
            allow_write=action == "modify",
            allow_exec=bool(ctx.get("allow_exec", False)),
            task_id=str(ctx.get("codex_task_id") or uuid.uuid4().hex[:8]),
        )

    def _command_template(self, provider: str) -> str:
        setting_key = f"{provider}_command_template"
        configured = str(self._setting(setting_key, "") or "").strip()
        return configured or self._discoverer(provider)

    def _format_result(self, result: Any) -> str:
        ok = bool(getattr(result, "ok", False))
        status = "完成" if ok else "失败"
        stdout = str(getattr(result, "stdout", "") or "").strip()
        stderr = str(getattr(result, "stderr", "") or "").strip()
        exit_code = getattr(result, "exit_code", "")
        command_preview = str(getattr(result, "command_preview", "") or "").strip()
        lines = [f"代码代理{status} (exit={exit_code})"]
        if command_preview:
            lines.append(f"命令: {command_preview}")
        if stdout:
            lines.append(stdout)
        if stderr:
            lines.append("stderr:\n" + stderr)
        return "\n".join(lines)

    def _status_text(self) -> str:
        rows = []
        for provider in ("codex_cli", "claude_code"):
            template = self._command_template(provider)
            rows.append(f"- {self._provider_label(provider)}: {'可用' if template else '未找到'}")
        return "代码代理状态\n" + "\n".join(rows)

    def _help_text(self) -> str:
        return (
            "code_agent 用法：\n"
            "- code_agent status\n"
            "- code_agent analyze ||| codex_cli ||| . ||| 检查启动失败\n"
            "- code_agent modify ||| claude_code ||| . ||| 修改 README\n"
            "自然语言示例：让 Codex 分析这个项目为什么启动失败"
        )

    def _exec_permission_message(self) -> str:
        return (
            "代码代理需要允许执行命令后才能启动外部 Codex/Claude。"
            "请在代码助手里开启“允许执行命令”，或从代码助手面板发起。"
        )

    def _provider_label(self, provider: str) -> str:
        if provider == "claude_code":
            return "Claude Code"
        return "Codex"

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

    def _default_provider(self) -> str:
        return self._normalize_provider(str(self._setting("default_provider", "codex_cli"))) or "codex_cli"

    def _default_cwd(self) -> str:
        return str(self._setting("default_cwd", ".") or ".").strip() or "."

    def _resolve_cwd(self, requested: str, ctx: Dict[str, Any]) -> str:
        raw = str(requested or "").strip()
        if raw and raw != ".":
            return raw
        for key in ("code_path", "app_root", "cwd"):
            value = str((ctx or {}).get(key) or "").strip()
            if value:
                return value
        return raw or self._default_cwd()
