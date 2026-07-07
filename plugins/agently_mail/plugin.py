from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import tempfile
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from PIL import Image, ImageDraw, ImageFont


CliRunner = Callable[[List[str], float], Awaitable[Dict[str, Any]]]
IntentResolver = Callable[[str, Dict[str, Any]], Awaitable[Dict[str, Any]]]


@dataclass(frozen=True)
class MailIntent:
    action: str
    params: Dict[str, Any]


class Plugin:
    name = "Agent Mail"
    type = "direct"
    allow_natural_language_direct = True
    aliases = [
        "agently_mail",
        "邮件",
        "邮箱",
        "收件箱",
        "最近邮件",
        "查邮件",
        "读邮件",
        "发邮件",
        "回邮件",
        "转发邮件",
    ]
    description = "通过本机 agently-cli 查询、读取、搜索和按确认流程发送邮件。"
    example_arg = "最近邮件"
    _FONT_CANDIDATES = (
        "msyh.ttc",
        "msyhbd.ttc",
        "simhei.ttf",
        "simsun.ttc",
        "NotoSansCJK-Regular.ttc",
    )

    def __init__(
        self,
        cli_runner: Optional[CliRunner] = None,
        intent_resolver: Optional[IntentResolver] = None,
        persona_resolver: Any = None,
    ):
        self._cli_runner = cli_runner or self._run_cli
        self._intent_resolver = intent_resolver or self._resolve_intent_with_llm
        self._persona_resolver = persona_resolver

    def should_handle_direct(self, text: str, context: Dict[str, Any], key: str) -> bool:
        raw = str(text or "").strip()
        lowered = raw.lower()
        if not raw:
            return False
        if any(word in lowered for word in ("agently_mail", "agent mail")):
            return True
        if re.search(r"\b(?:mail|email)\b", lowered) and any(
            word in lowered
            for word in (
                "list",
                "inbox",
                "search",
                "read",
                "send",
                "reply",
                "forward",
                "trash",
            )
        ):
            return True
        mail_intent_patterns = (
            r"最近.*(?:邮件|邮箱|收件箱)",
            r"(?:收到了哪些|有哪些).*邮件",
            r"(?:查|看|打开|读|搜索|搜|查找).*(?:邮件|邮箱|收件箱)",
            r"(?:邮件|邮箱|收件箱).*(?:列表|账号|授权|状态)",
            r"(?:发|发送|回|回复|转发|删除)邮件",
            r"发一封.*邮件.*(?:到|给)[A-Za-z0-9_.+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+",
            r"(?:给|到)?[A-Za-z0-9_.+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+.*(?:邮箱|邮件).*(?:发|发送)",
            r"\bmsg_[A-Za-z0-9_\-]+\b",
        )
        return any(re.search(pattern, raw, flags=re.IGNORECASE) for pattern in mail_intent_patterns)

    async def run(self, args: str, ctx: Dict[str, Any]) -> str:
        ctx = dict(ctx or {})
        args, actor_or_error = self._extract_tool_actor(args, ctx)
        if isinstance(actor_or_error, str):
            return actor_or_error
        intent = self._parse_intent(args)
        timeout_sec = float(self._setting("request_timeout_sec", 20))
        cli_path = str(self._setting("cli_path", "agently-cli") or "agently-cli").strip()
        if not cli_path:
            cli_path = "agently-cli"

        if intent.action == "me":
            payload = await self._call([cli_path, "+me"], timeout_sec)
            return self._format_me(payload)
        if intent.action == "list":
            limit = self._limit(intent.params.get("limit"))
            payload = await self._call(
                [
                    cli_path,
                    "message",
                    "+list",
                    "--dir",
                    "inbox",
                    "--limit",
                    str(limit),
                ],
                timeout_sec,
            )
            if self._is_qq_context(ctx):
                return self._format_list_card_result(payload, title="最近邮件")
            return self._format_list(payload, title="最近邮件")
        if intent.action == "search":
            query = str(intent.params.get("q") or "").strip()
            if not query:
                return "要搜索邮件的话，请告诉我要搜什么关键词。"
            limit = self._limit(intent.params.get("limit"))
            payload = await self._call(
                [
                    cli_path,
                    "message",
                    "+search",
                    "--q",
                    query,
                    "--limit",
                    str(limit),
                ],
                timeout_sec,
            )
            if self._is_qq_context(ctx):
                return self._format_list_card_result(payload, title=f"搜索结果：{query}")
            return self._format_list(payload, title=f"搜索结果：{query}")
        if intent.action == "read":
            message_id = str(intent.params.get("id") or "").strip()
            if not message_id:
                return "要读哪封邮件？请给我 msg_xxx 这样的邮件 ID。"
            payload = await self._call(
                [cli_path, "message", "+read", "--id", message_id],
                timeout_sec,
            )
            return self._format_read(payload)
        if intent.action in {"send", "reply", "forward", "trash"}:
            params = await self._prepare_write_params(
                args,
                ctx,
                intent.action,
                intent.params,
                cli_path,
                timeout_sec,
            )
            return await self._start_write(intent.action, params, cli_path, timeout_sec)
        return self._help_text()

    def _extract_tool_actor(self, args: str, ctx: Dict[str, Any]) -> tuple[str, Optional[str]]:
        resolver = self._persona_resolver
        if resolver is None:
            try:
                from modules.persona_resolver import PersonaResolver

                resolver = PersonaResolver()
                self._persona_resolver = resolver
            except Exception:
                return str(args or ""), None
        try:
            actor, remaining = resolver.extract_leading_actor(str(args or ""))
        except Exception:
            return str(args or ""), None
        if not actor:
            return str(args or ""), None
        if isinstance(actor, dict):
            actor_ctx = dict(actor)
        elif getattr(actor, "ambiguous", False):
            names = "、".join(
                str(item.get("name") or item.get("character_id") or "")
                for item in getattr(actor, "candidates", [])
                if isinstance(item, dict)
            )
            return str(args or ""), f"角色称呼不明确：{names}。请使用完整角色名。"
        else:
            actor_ctx = actor.to_context()
        if actor_ctx.get("ambiguous"):
            candidates = actor_ctx.get("candidates") if isinstance(actor_ctx.get("candidates"), list) else []
            names = "、".join(str(item.get("name") or "") for item in candidates if isinstance(item, dict))
            return str(args or ""), f"角色称呼不明确：{names}。请使用完整角色名。"
        ctx["tool_actor"] = actor_ctx
        return str(remaining or "").strip(), None

    async def _prepare_write_params(
        self,
        raw_text: str,
        ctx: Dict[str, Any],
        action: str,
        params: Dict[str, Any],
        cli_path: str,
        timeout_sec: float,
    ) -> Dict[str, Any]:
        params = dict(params or {})
        actor = ctx.get("tool_actor") if isinstance(ctx, dict) else None
        if isinstance(actor, dict):
            params["_actor"] = actor
        if action == "reply":
            params = await self._prepare_reply_source(raw_text, ctx, params, cli_path, timeout_sec)
        return await self._maybe_resolve_intent(raw_text, ctx, action, params)

    async def _prepare_reply_source(
        self,
        raw_text: str,
        ctx: Dict[str, Any],
        params: Dict[str, Any],
        cli_path: str,
        timeout_sec: float,
    ) -> Dict[str, Any]:
        message_id = str(params.get("id") or self._extract_message_id(raw_text) or "").strip()
        if message_id and not str(params.get("id") or "").strip():
            params["id"] = message_id
        if not message_id or str(params.get("body") or "").strip():
            return params
        payload = await self._call([cli_path, "message", "+read", "--id", message_id], timeout_sec)
        if not payload.get("ok", True):
            params["_source_error"] = str(payload.get("error") or "读取原邮件失败")
            return params
        source_mail = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        if source_mail:
            ctx["source_mail"] = self._source_mail_context(source_mail)
        return params

    async def _maybe_resolve_intent(
        self,
        raw_text: str,
        ctx: Dict[str, Any],
        action: str,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not bool(self._setting("llm_intent_enabled", True)):
            return params
        if action not in {"send", "reply", "forward", "trash"}:
            return params
        if params.get("_source_error"):
            return params
        should_resolve = self._looks_natural_mail_request(raw_text) or any(
            not str(params.get(key) or "").strip()
            for key in self._required_fields_for_action(action)
        )
        if not should_resolve:
            return params
        try:
            resolved = await asyncio.wait_for(
                self._intent_resolver(raw_text, ctx or {}),
                timeout=float(self._setting("intent_timeout_sec", 18)),
            )
        except asyncio.TimeoutError:
            merged = dict(params)
            merged["_intent_error"] = "草稿生成超时，请稍后重试，或直接提供 subject= 和 body=。"
            return merged
        except Exception:
            merged = dict(params)
            merged["_intent_error"] = "草稿生成失败，请稍后重试，或直接提供 subject= 和 body=。"
            return merged
        normalized = self._normalize_llm_intent(resolved)
        if normalized.get("action") and normalized["action"] != action:
            return params
        merged = dict(params)
        allow_llm_override = (
            self._looks_natural_mail_request(raw_text)
            or bool((ctx or {}).get("tool_actor"))
            or bool((ctx or {}).get("source_mail"))
        )
        for key in ("to", "subject", "body", "id"):
            value = str(normalized.get(key) or "").strip()
            if value and (allow_llm_override or not str(merged.get(key) or "").strip()):
                merged[key] = value
        return merged

    def _required_fields_for_action(self, action: str) -> tuple[str, ...]:
        if action == "send":
            return ("to", "subject", "body")
        if action == "reply":
            return ("id", "body")
        if action == "forward":
            return ("id", "to")
        if action == "trash":
            return ("id",)
        return ()

    def _looks_natural_mail_request(self, text: str) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return False
        if re.search(r"\b(?:to|subject|body|id)=", raw, flags=re.IGNORECASE):
            return False
        if re.search(r"(?:发|发送|回|回复|转发|删除).*邮件", raw):
            return True
        return bool(
            re.search(
                r"[A-Za-z0-9_.+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+.*(?:邮箱|邮件).*(?:发|发送)",
                raw,
                flags=re.IGNORECASE,
            )
        )

    def _extract_message_id(self, text: str) -> str:
        match = re.search(r"\bmsg_[A-Za-z0-9_\-]+\b", str(text or ""))
        return match.group(0) if match else ""

    def _source_mail_context(self, data: Dict[str, Any]) -> Dict[str, Any]:
        sender = data.get("from") if isinstance(data.get("from"), dict) else {}
        body = str(data.get("body") or "")
        return {
            "id": str(data.get("message_id") or ""),
            "subject": str(data.get("subject") or ""),
            "from_name": str(sender.get("name") or ""),
            "from_email": str(sender.get("email") or ""),
            "body": body[:4000],
        }

    def _normalize_llm_intent(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        try:
            confidence = float(payload.get("confidence", 0) or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence and confidence < 0.55:
            return {}
        action = str(payload.get("action") or "").strip().lower()
        if action not in {"send", "reply", "forward", "trash"}:
            action = ""
        result = {"action": action}
        for key in ("to", "subject", "body", "id"):
            value = payload.get(key)
            if isinstance(value, list):
                value = ", ".join(str(item) for item in value if str(item).strip())
            result[key] = str(value or "").strip()
        return result

    async def _resolve_intent_with_llm(self, text: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
        def run_blocking() -> str:
            from modules.llm import chat_with_ai

            persona_prompt = self._current_persona_prompt(ctx)
            user_content = str(text or "")
            source_mail = ctx.get("source_mail") if isinstance(ctx, dict) else None
            if isinstance(source_mail, dict) and source_mail:
                user_content += (
                    "\n\n[原邮件信息]\n"
                    + json.dumps(source_mail, ensure_ascii=False, indent=2)
                )
            messages = [
                {
                    "role": "system",
                    "content": (
                        "你是邮件意图解析器。只输出 JSON，不要解释。\n"
                        "字段: action(send/reply/forward/trash/list/search/read/me/unknown), "
                        "to, subject, body, id, confidence。\n"
                        "如果用户要发邮件但正文需要你根据请求生成，可以按照当前角色人设生成简短正文草稿；"
                        "邮件正文只写邮件内容，不要添加 Agent/AI 署名或系统说明。\n"
                        f"当前角色人设:\n{persona_prompt}\n"
                        "不要执行发送，不要编造收件人。"
                    ),
                },
                {"role": "user", "content": user_content},
            ]
            return str(
                chat_with_ai(
                    messages,
                    task_type="gatekeeper",
                    caller="agently_mail_intent",
                    timeout_sec=12,
                )
                or ""
            )

        raw = await asyncio.to_thread(run_blocking)
        return self._parse_llm_json(raw)

    def _current_persona_prompt(self, ctx: Optional[Dict[str, Any]] = None) -> str:
        actor = (ctx or {}).get("tool_actor") if isinstance(ctx, dict) else None
        if isinstance(actor, dict):
            name = str(actor.get("name") or "").strip()
            prompt = str(actor.get("prompt") or "").strip()
            parts = []
            if name:
                parts.append(f"角色名: {name}")
            if prompt:
                parts.append(prompt)
            if parts:
                return "\n".join(parts)
        try:
            from modules.character_manager import character_manager

            active_char = character_manager.get_active_character()
            if isinstance(active_char, dict):
                name = str(active_char.get("name") or "").strip()
                prompt = str(active_char.get("prompt") or "").strip()
                parts = []
                if name:
                    parts.append(f"角色名: {name}")
                if prompt:
                    parts.append(prompt)
                if parts:
                    return "\n".join(parts)
        except Exception:
            pass
        try:
            from config import DEFAULT_PERSONA

            return str(DEFAULT_PERSONA or "").strip()
        except Exception:
            return ""

    def _parse_llm_json(self, text: str) -> Dict[str, Any]:
        raw = str(text or "").strip()
        if not raw:
            return {}
        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, flags=re.IGNORECASE)
        if fenced:
            raw = fenced.group(1).strip()
        if not raw.startswith("{"):
            match = re.search(r"\{[\s\S]*\}", raw)
            raw = match.group(0) if match else raw
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    async def confirm_agent_action(self, payload: Dict[str, Any], ctx: Dict[str, Any]) -> str:
        argv = payload.get("argv") if isinstance(payload, dict) else None
        if not isinstance(argv, list) or not argv:
            return "邮件确认失败：缺少原始命令。"
        token = str(payload.get("confirmation_token") or "").strip()
        if not token:
            return "邮件确认失败：缺少 confirmation token。"
        action = str(payload.get("action") or "").strip() or "send"
        timeout_sec = float(self._setting("request_timeout_sec", 20))
        result = await self._call(
            [str(item) for item in argv] + ["--confirmation-token", token],
            timeout_sec,
        )
        return self._format_write_success(result, action)

    async def _call(self, argv: List[str], timeout_sec: float) -> Dict[str, Any]:
        result = await self._cli_runner(argv, timeout_sec)
        code = int(result.get("returncode", 1))
        stdout = str(result.get("stdout") or "")
        stderr = str(result.get("stderr") or "")
        if code != 0:
            message = self._extract_error_message(stdout) or stderr.strip()
            return {
                "ok": False,
                "error": message or f"agently-cli 退出码 {code}",
                "returncode": code,
            }
        try:
            data = json.loads(stdout or "{}")
        except json.JSONDecodeError:
            return {"ok": True, "data": stdout.strip()}
        if isinstance(data, dict):
            return data
        return {"ok": True, "data": data}

    async def _run_cli(self, argv: List[str], timeout_sec: float) -> Dict[str, Any]:
        def run_blocking() -> Dict[str, Any]:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_sec,
                shell=False,
            )
            return {
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }

        try:
            return await asyncio.to_thread(run_blocking)
        except subprocess.TimeoutExpired:
            return {"returncode": 124, "stdout": "", "stderr": "agently-cli 调用超时"}
        except FileNotFoundError:
            return {
                "returncode": 127,
                "stdout": "",
                "stderr": "找不到 agently-cli，请检查插件里的 cli_path 设置。",
            }

    def _parse_intent(self, text: str) -> MailIntent:
        raw = str(text or "").strip()
        lowered = raw.lower()
        if any(word in raw for word in ("当前邮箱", "邮箱账号", "邮箱授权")) or "+me" in lowered:
            return MailIntent("me", {})
        if any(word in raw for word in ("发邮件", "发送邮件", "发送一封邮件", "发一封邮件")):
            params = self._parse_key_values(raw)
            params.update({k: v for k, v in self._parse_natural_send(raw).items() if k not in params or not params[k]})
            return MailIntent("send", params)
        if self._looks_like_send(raw, lowered):
            params = self._parse_key_values(raw)
            params.update({k: v for k, v in self._parse_natural_send(raw).items() if k not in params or not params[k]})
            return MailIntent("send", params)
        if any(word in raw for word in ("回复邮件", "回复")) or lowered.startswith("reply "):
            params = self._parse_key_values(raw)
            message_id = self._extract_message_id(raw)
            if message_id and not params.get("id"):
                params["id"] = message_id
            return MailIntent("reply", params)
        if any(word in raw for word in ("回邮件", "回复邮件")) or lowered.startswith("reply "):
            return MailIntent("reply", self._parse_key_values(raw))
        if any(word in raw for word in ("转发邮件", "转邮件")) or lowered.startswith("forward "):
            return MailIntent("forward", self._parse_key_values(raw))
        if any(word in raw for word in ("删除邮件", "邮件移到回收站", "移到回收站")) or lowered.startswith("trash "):
            return MailIntent("trash", self._parse_key_values(raw))
        msg_match = re.search(r"\bmsg_[A-Za-z0-9_\-]+\b", raw)
        if msg_match and any(word in raw for word in ("读", "看", "打开", "详情")):
            return MailIntent("read", {"id": msg_match.group(0)})
        if any(word in raw for word in ("搜索邮件", "搜邮件", "查找邮件")):
            query = re.sub(r"^(搜索邮件|搜邮件|查找邮件)[:：\s]*", "", raw).strip()
            return MailIntent("search", {"q": query, "limit": self._extract_limit(raw)})
        if "search" in lowered:
            return MailIntent(
                "search",
                {
                    "q": raw.split("search", 1)[-1].strip(),
                    "limit": self._extract_limit(raw),
                },
            )
        if any(word in raw for word in ("最近", "收到了哪些", "收件箱", "查邮件", "邮件列表")):
            return MailIntent("list", {"limit": self._extract_limit(raw)})
        if any(word in lowered for word in ("list", "inbox")):
            return MailIntent("list", {"limit": self._extract_limit(raw)})
        return MailIntent("list", {"limit": self._extract_limit(raw)})

    def _looks_like_send(self, raw: str, lowered: str) -> bool:
        if any(word in raw for word in ("发邮件", "发送邮件")) or lowered.startswith("send "):
            return True
        return bool(
            re.search(
                r"(?:发|发送).*邮件.*(?:到|给)\s*[A-Za-z0-9_.+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+",
                raw,
                flags=re.IGNORECASE,
            )
            or re.search(
                r"(?:给|到)?\s*[A-Za-z0-9_.+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+.*(?:邮箱|邮件).*(?:发|发送)",
                raw,
                flags=re.IGNORECASE,
            )
        )

    def _parse_natural_send(self, text: str) -> Dict[str, str]:
        raw = str(text or "").strip()
        result: Dict[str, str] = {}
        email_match = re.search(
            r"[A-Za-z0-9_.+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+",
            raw,
        )
        if email_match:
            result["to"] = email_match.group(0)

        subject_match = re.search(
            r"(?:主题|标题)\s*(?:是|为|:|：)?\s*([\s\S]*?)(?=\s*(?:正文|内容)\s*(?:是|为|:|：)|$)",
            raw,
        )
        if subject_match:
            subject = subject_match.group(1).strip()
        else:
            subject = re.sub(r"^(?:帮我)?(?:发|发送)一?封?", "", raw).strip()
            subject = re.sub(
                r"(?:邮件)?(?:到|给)\s*[A-Za-z0-9_.+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+.*$",
                "",
                subject,
                flags=re.IGNORECASE,
            ).strip()
            subject = re.sub(r"邮件$", "", subject).strip()
        if subject:
            result["subject"] = subject

        if email_match and "subject" not in result:
            after_email = raw[email_match.end() :].strip()
            after_email = re.sub(r"^(?:邮箱|邮件)?\s*(?:发送|发)(?:一封)?", "", after_email).strip()
            if after_email:
                result["subject"] = after_email

        body_match = re.search(
            r"(?:正文|内容)\s*(?:是|为|:|：)?\s*([\s\S]+)$",
            raw,
        )
        if body_match:
            result["body"] = body_match.group(1).strip()
        return result

    async def _start_write(
        self,
        action: str,
        params: Dict[str, Any],
        cli_path: str,
        timeout_sec: float,
    ) -> str | Dict[str, Any]:
        argv_or_error = self._build_write_argv(action, params, cli_path)
        if isinstance(argv_or_error, str):
            return argv_or_error
        argv = argv_or_error
        payload = await self._call(argv, timeout_sec)
        token = self._confirmation_token(payload)
        if token:
            summary = self._confirmation_summary(payload)
            actor = params.get("_actor") if isinstance(params.get("_actor"), dict) else None
            return {
                "__agent_result__": "confirmation_required",
                "trigger": "agently_mail",
                "summary": self._format_confirmation_summary(summary, action, actor),
                "payload": {
                    "action": action,
                    "argv": argv,
                    "confirmation_token": token,
                    "actor": actor,
                },
                "expires_in": self._confirmation_expires_in(payload),
            }
        return self._format_write_success(payload, action)

    def _build_write_argv(
        self,
        action: str,
        params: Dict[str, Any],
        cli_path: str,
    ) -> List[str] | str:
        if params.get("_intent_error"):
            return str(params.get("_intent_error"))
        if action == "send":
            to = str(params.get("to") or "").strip()
            subject = str(params.get("subject") or "").strip()
            body = str(params.get("body") or "").strip()
            missing = [
                label
                for label, value in (
                    ("收件人(to=)", to),
                    ("主题(subject=)", subject),
                    ("正文(body=)", body),
                )
                if not value
            ]
            if missing:
                hint_parts = []
                if to:
                    hint_parts.append(f"to={to}")
                if subject:
                    hint_parts.append(f"subject={subject}")
                hint = " ".join(hint_parts + ["body=正文"])
                return (
                    "发邮件还缺少："
                    + "、".join(missing)
                    + "。格式：发邮件 to=a@example.com subject=主题 body=正文"
                    + (f"\n你这次可以补成：发邮件 {hint}" if hint_parts else "")
                )
            return [
                cli_path,
                "message",
                "+send",
                "--to",
                to,
                "--subject",
                subject,
                "--body",
                body,
            ]
        if action == "reply":
            if params.get("_source_error"):
                return f"回信前读取原邮件失败：{params.get('_source_error')}"
            message_id = str(params.get("id") or "").strip()
            body = str(params.get("body") or "").strip()
            if not message_id or not body:
                return "回邮件需要 id=msg_xxx 和 body=回复正文。"
            return [cli_path, "message", "+reply", "--id", message_id, "--body", body]
        if action == "forward":
            message_id = str(params.get("id") or "").strip()
            to = str(params.get("to") or "").strip()
            body = str(params.get("body") or "").strip()
            if not message_id or not to:
                return "转发邮件需要 id=msg_xxx 和 to=收件人。可选 body=说明。"
            argv = [cli_path, "message", "+forward", "--id", message_id, "--to", to]
            if body:
                argv.extend(["--body", body])
            return argv
        if action == "trash":
            message_id = str(params.get("id") or "").strip()
            if not message_id:
                return "删除邮件需要 id=msg_xxx。"
            return [cli_path, "message", "+trash", "--id", message_id]
        return f"不支持的邮件写操作：{action}"

    def _parse_key_values(self, text: str) -> Dict[str, str]:
        result: Dict[str, str] = {}
        pattern = re.compile(
            r"\b(to|subject|body|id)=([\s\S]*?)(?=\s+\b(?:to|subject|body|id)=|$)",
            re.IGNORECASE,
        )
        for key, value in pattern.findall(str(text or "")):
            result[key.lower()] = value.strip().strip('"').strip("'")
        return result

    def _format_me(self, payload: Dict[str, Any]) -> str:
        if not payload.get("ok", True):
            return f"邮箱状态获取失败：{payload.get('error')}"
        return "当前 Agent Mail 账号：\n" + json.dumps(
            payload.get("data", payload),
            ensure_ascii=False,
            indent=2,
        )

    def _format_list(self, payload: Dict[str, Any], title: str) -> str:
        if not payload.get("ok", True):
            return f"{title}获取失败：{payload.get('error')}"
        envelope = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        items = envelope.get("data") if isinstance(envelope, dict) else []
        if not items:
            return f"{title}：没有找到邮件。"
        lines = [f"{title}："]
        for index, item in enumerate(items, 1):
            if not isinstance(item, dict):
                continue
            sender = item.get("from") if isinstance(item.get("from"), dict) else {}
            sender_text = sender.get("name") or sender.get("email") or "未知发件人"
            unread = "未读" if item.get("is_read") is False else "已读"
            lines.append(
                f"{index}. {item.get('subject') or '(无主题)'} | {sender_text} | "
                f"{item.get('created_at') or ''} | {unread}\n"
                f"   ID: {item.get('message_id') or ''}\n"
                f"   摘要: {item.get('snippet') or ''}"
            )
        pagination = envelope.get("pagination") if isinstance(envelope, dict) else {}
        if isinstance(pagination, dict) and pagination.get("has_more"):
            lines.append(f"还有更多邮件，next_cursor={pagination.get('next_cursor')}")
        return "\n".join(lines)

    def _format_list_card_result(self, payload: Dict[str, Any], title: str) -> Any:
        if not payload.get("ok", True):
            return f"{title}获取失败：{payload.get('error')}"
        items, has_more = self._extract_list_items(payload)
        if not items:
            return f"{title}：没有找到邮件。"
        image_path = self._render_mail_list_card(title, items, has_more=has_more)
        unread_count = sum(
            1 for item in items if isinstance(item, dict) and item.get("is_read") is False
        )
        more_text = "，还有更多" if has_more else ""
        summary = f"{title}：共 {len(items)} 封，未读 {unread_count} 封{more_text}。详情见图片。"
        return {
            "__type__": "gateway_image",
            "image_path": image_path,
            "caption": summary,
            "post_send_text": summary,
            "success_text": summary,
            "fallback_text": self._format_list(payload, title=title),
            "cleanup": True,
            "suppress_fallback_reply": False,
        }

    def _extract_list_items(self, payload: Dict[str, Any]) -> tuple[List[Dict[str, Any]], bool]:
        envelope = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        raw_items = envelope.get("data") if isinstance(envelope, dict) else []
        items = [item for item in raw_items if isinstance(item, dict)]
        pagination = envelope.get("pagination") if isinstance(envelope, dict) else {}
        has_more = bool(isinstance(pagination, dict) and pagination.get("has_more"))
        return items, has_more

    def _is_qq_context(self, ctx: Dict[str, Any]) -> bool:
        source = str((ctx or {}).get("source") or "").strip().lower()
        return source in {"qq_gateway", "napcat_qq"}

    def _render_mail_list_card(
        self, title: str, items: List[Dict[str, Any]], *, has_more: bool = False
    ) -> str:
        output_dir = Path(tempfile.gettempdir()) / "live2d_llm_agently_mail"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"mail_card_{int(time.time() * 1000)}.png"

        width = 920
        row_height = 142
        visible_items = items[:10]
        height = 150 + max(1, len(visible_items)) * row_height + 44
        image = Image.new("RGB", (width, height), (248, 250, 252))
        draw = ImageDraw.Draw(image)
        title_font = self._load_font(34, bold=True)
        meta_font = self._load_font(20)
        subject_font = self._load_font(24, bold=True)
        body_font = self._load_font(19)
        small_font = self._load_font(17)

        draw.rounded_rectangle(
            (24, 24, width - 24, height - 24),
            radius=18,
            fill=(255, 255, 255),
            outline=(226, 232, 240),
            width=2,
        )
        unread_count = sum(1 for item in visible_items if item.get("is_read") is False)
        draw.text((48, 42), title, fill=(15, 23, 42), font=title_font)
        summary = f"{len(items)} 封邮件 · {unread_count} 封未读"
        if has_more:
            summary += " · 还有更多"
        draw.text((48, 90), summary, fill=(71, 85, 105), font=meta_font)

        y = 132
        for index, item in enumerate(visible_items, 1):
            top = y
            bottom = y + row_height - 16
            is_unread = item.get("is_read") is False
            accent = (37, 99, 235) if is_unread else (148, 163, 184)
            draw.rounded_rectangle(
                (48, top, width - 48, bottom),
                radius=12,
                fill=(248, 250, 252),
                outline=(226, 232, 240),
                width=1,
            )
            draw.rounded_rectangle((48, top, 55, bottom), radius=4, fill=accent)
            sender = item.get("from") if isinstance(item.get("from"), dict) else {}
            sender_text = str(sender.get("name") or sender.get("email") or "未知发件人")
            subject = str(item.get("subject") or "(无主题)")
            created_at = str(item.get("created_at") or "")
            snippet = str(item.get("snippet") or "")
            message_id = str(item.get("message_id") or "")
            state = "未读" if is_unread else "已读"

            draw.text((72, top + 14), f"{index}. {self._fit_text(subject, 30)}", fill=(15, 23, 42), font=subject_font)
            draw.text(
                (72, top + 50),
                self._fit_text(f"{sender_text} · {created_at} · {state}", 64),
                fill=(71, 85, 105),
                font=small_font,
            )
            draw.text(
                (72, top + 78),
                self._fit_text(snippet, 76),
                fill=(51, 65, 85),
                font=body_font,
            )
            draw.text(
                (72, top + 106),
                self._fit_text(f"ID: {message_id}", 82),
                fill=(100, 116, 139),
                font=small_font,
            )
            y += row_height

        footer = "回复“读邮件 msg_xxx”可查看正文"
        draw.text((48, height - 52), footer, fill=(100, 116, 139), font=small_font)
        image.save(output_path, "PNG", optimize=True)
        return str(output_path)

    def _load_font(self, size: int, *, bold: bool = False) -> ImageFont.ImageFont:
        windir = os.environ.get("WINDIR", r"C:\Windows")
        candidates = (("msyhbd.ttc",) if bold else ()) + self._FONT_CANDIDATES
        for font_name in candidates:
            font_path = Path(windir) / "Fonts" / font_name
            if not font_path.exists():
                continue
            try:
                return ImageFont.truetype(str(font_path), size=size)
            except Exception:
                continue
        return ImageFont.load_default()

    def _fit_text(self, text: str, width: int) -> str:
        clean = " ".join(str(text or "").replace("\r", " ").replace("\n", " ").split())
        if len(clean) <= width:
            return clean
        return textwrap.shorten(clean, width=width, placeholder="...")

    def _format_read(self, payload: Dict[str, Any]) -> str:
        if not payload.get("ok", True):
            return f"读取邮件失败：{payload.get('error')}"
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        sender = data.get("from") if isinstance(data.get("from"), dict) else {}
        body = str(data.get("body") or "")
        limit = int(self._setting("max_body_chars", 2400))
        truncated = ""
        if len(body) > limit:
            body = body[:limit].rstrip()
            truncated = "\n\n（正文已截断）"
        attachments = data.get("attachments") if isinstance(data.get("attachments"), list) else []
        attachment_lines = []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            label = str(attachment.get("filename") or attachment.get("attachment_id") or "附件")
            if attachment.get("attachment_id"):
                label += f" ({attachment.get('attachment_id')})"
            elif attachment.get("download_url"):
                label += f" ({attachment.get('download_url')})"
            attachment_lines.append(f"- {label}")
        return (
            f"邮件详情：{data.get('subject') or '(无主题)'}\n"
            f"ID: {data.get('message_id') or ''}\n"
            f"发件人: {sender.get('name') or ''} <{sender.get('email') or ''}>\n"
            f"时间: {data.get('created_at') or ''}\n"
            f"附件: {len(attachments)} 个"
            + (("\n" + "\n".join(attachment_lines)) if attachment_lines else "")
            + f"\n\n正文:\n{body}{truncated}"
        )

    def _extract_error_message(self, stdout: str) -> str:
        try:
            data = json.loads(stdout or "{}")
        except json.JSONDecodeError:
            return ""
        if not isinstance(data, dict):
            return ""
        error = data.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or "")
        return str(error or "")

    def _confirmation_token(self, payload: Dict[str, Any]) -> str:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        if data.get("confirmation_required"):
            return str(data.get("confirmation_token") or "").strip()
        return ""

    def _confirmation_summary(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        summary = data.get("summary")
        return summary if isinstance(summary, dict) else {}

    def _confirmation_expires_in(self, payload: Dict[str, Any]) -> int:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        try:
            return max(30, int(data.get("expires_in") or 300))
        except (TypeError, ValueError):
            return 300

    def _format_confirmation_summary(
        self,
        summary: Dict[str, Any],
        action: str,
        actor: Optional[Dict[str, Any]] = None,
    ) -> str:
        to_value = summary.get("to")
        to = ", ".join(str(item) for item in to_value if str(item).strip()) if isinstance(to_value, list) else ""
        subject = str(summary.get("subject") or "")
        action_name = str(summary.get("action") or action)
        lines = [f"这次邮件操作需要确认。", f"操作: {action_name}"]
        if isinstance(actor, dict) and str(actor.get("name") or "").strip():
            lines.append(f"执行角色: {str(actor.get('name')).strip()}")
        if to:
            lines.append(f"收件人: {to}")
        if subject:
            lines.append(f"主题: {subject}")
        return "\n".join(lines)

    def _format_write_success(self, payload: Dict[str, Any], action: str) -> str:
        if not payload.get("ok", True):
            return f"邮件操作失败：{payload.get('error')}"
        labels = {
            "send": "邮件已提交发送。",
            "reply": "回复已提交发送。",
            "forward": "转发已提交发送。",
            "trash": "邮件已移到回收站。",
        }
        return labels.get(action, "邮件操作已完成。")

    def _extract_limit(self, text: str) -> Optional[int]:
        match = re.search(r"(?:limit=|前|最近)?\s*(\d{1,2})\s*(?:封|条|个)?", str(text or ""))
        if not match:
            return None
        return int(match.group(1))

    def _limit(self, value: Any) -> int:
        try:
            number = int(value or self._setting("default_limit", 10))
        except (TypeError, ValueError):
            number = int(self._setting("default_limit", 10))
        return max(1, min(20, number))

    def _setting(self, key: str, default: Any) -> Any:
        settings = getattr(self, "settings", {}) or {}
        raw = settings.get(key, default) if isinstance(settings, dict) else default
        if isinstance(raw, dict):
            return raw.get("value", raw.get("default", default))
        return raw

    def _help_text(self) -> str:
        return (
            "Agent Mail 用法：\n"
            "- 最近邮件\n"
            "- 搜索邮件 关键词\n"
            "- 读邮件 msg_xxx\n"
            "- 当前邮箱"
        )
