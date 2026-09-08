"""Unified natural-chat orchestration for desktop + QQ (group optional)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Set

from services.chat_support.character_thought import (
    format_thought_for_prompt,
    generate_character_thought,
)
from services.chat_support.flow_result import (
    STATUS_CONTINUE,
    STATUS_REPLIED,
    ChatFlowResult,
)
from services.chat_support.forbidden_phrase_guard import (
    DEFAULT_FORBIDDEN_PHRASES,
    build_retry_constraint,
    find_forbidden_phrases,
    should_retry_after_forbidden,
    strip_forbidden_spans,
)
from services.chat_support.reply_flow_service import (
    finalize_model_reply,
    prepare_final_reply,
)
from services.chat_support.search_flow_service import choose_forced_search_query
from services.chat_support.tool_flow_service import (
    ReactFirstPassResult,
    run_react_first_pass,
)
from services.chat_support.prompt_assembly import make_chat_prompt_parts

QQ_REMOTE_SOURCES: Set[str] = {"qq_gateway", "napcat_qq"}
DESKTOP_SOURCES: Set[str] = {"text_input", "desktop", "voice"}


def _int_config(raw: Dict[str, Any], key: str, default: int) -> int:
    if not isinstance(raw, dict) or key not in raw or raw.get(key) in {None, ""}:
        return int(default)
    try:
        return int(raw.get(key))
    except (TypeError, ValueError):
        return int(default)


# 只补系统规则没写的本轮禁令，避免再念一遍「短句/别客服腔」。
SCENE_CORE_LINES = (
    "禁止「切换得好硬/节奏很怪/状态不对/落差好大」等抽象标签。",
)


@dataclass
class NaturalChatConfig:
    character_thought_enabled: bool = True
    character_thought_scope: str = "desktop_and_qq_private"
    group_chat_natural_enabled: bool = False
    character_thought_timeout_ms: int = 4000,
    character_thought_max_tokens: int = 220
    expression_inject_in_main_reply: bool = True
    expression_inject_max_items: int = 1
    character_thought_on_error: str = "skip_thought"
    forbidden_phrase_max_retries: int = 1
    detail_intent_bypass_short_shell: bool = True

    @classmethod
    def from_mapping(cls, data: Optional[Dict[str, Any]]) -> "NaturalChatConfig":
        raw = data if isinstance(data, dict) else {}
        return cls(
            character_thought_enabled=bool(
                raw.get("character_thought_enabled", True)
            ),
            character_thought_scope=str(
                raw.get("character_thought_scope") or "desktop_and_qq_private"
            ).strip()
            or "desktop_and_qq_private",
            group_chat_natural_enabled=bool(
                raw.get("group_chat_natural_enabled", False)
            ),
            character_thought_timeout_ms=int(
                raw.get("character_thought_timeout_ms") or 4000
            ),
            character_thought_max_tokens=int(
                raw.get("character_thought_max_tokens") or 220
            ),
            expression_inject_in_main_reply=bool(
                raw.get("expression_inject_in_main_reply", True)
            ),
            expression_inject_max_items=_int_config(
                raw, "expression_inject_max_items", 1
            ),
            character_thought_on_error=str(
                raw.get("character_thought_on_error") or "skip_thought"
            ).strip()
            or "skip_thought",
            forbidden_phrase_max_retries=int(
                raw.get("forbidden_phrase_max_retries") or 1
            ),
            detail_intent_bypass_short_shell=bool(
                raw.get("detail_intent_bypass_short_shell", True)
            ),
        )


@dataclass
class ThoughtGateDecision:
    should_run: bool
    scope_matched: bool
    reason: str = ""
    short_shell: bool = True
    detail_intent: bool = False


def chat_message_type(ctx: Optional[Dict[str, Any]]) -> str:
    if not isinstance(ctx, dict):
        return "private"
    meta = ctx.get("channel_meta") or {}
    if not isinstance(meta, dict):
        return "private"
    return str(meta.get("message_type") or "private").strip().lower() or "private"


def _message_type(ctx: Optional[Dict[str, Any]]) -> str:
    return chat_message_type(ctx)


def looks_like_chat_command(user_text: str) -> bool:
    raw = str(user_text or "").strip()
    if not raw:
        return False
    return raw.startswith(("/", "!", "！", "#"))


def is_qq_source(source: str) -> bool:
    return str(source or "").strip().lower() in QQ_REMOTE_SOURCES


def is_desktop_source(source: str) -> bool:
    return str(source or "").strip().lower() in DESKTOP_SOURCES


def is_group_message(source: str, message_type: str) -> bool:
    return is_qq_source(source) and str(message_type or "").strip().lower() == "group"


def is_qq_private(source: str, message_type: str) -> bool:
    if not is_qq_source(source):
        return False
    mt = str(message_type or "private").strip().lower() or "private"
    return mt != "group"


def scope_matches(
    source: str,
    message_type: str,
    config: NaturalChatConfig,
) -> bool:
    scope = str(config.character_thought_scope or "desktop_and_qq_private").strip().lower()
    if scope in {"off", "none", "disabled"}:
        return False
    src = str(source or "").strip().lower()
    mt = str(message_type or "private").strip().lower() or "private"
    group = is_group_message(src, mt)

    # 群聊：产品未开放时默认关；打开后与桌面/私聊同源（不要求 scope 再开 qq_all）
    if group:
        return bool(config.group_chat_natural_enabled) and scope not in {
            "desktop_only",
            "qq_private_only",
            "off",
        }

    if scope == "all_chat_sources":
        return is_desktop_source(src) or is_qq_source(src)
    if scope == "desktop_only":
        return is_desktop_source(src)
    if scope == "qq_private_only":
        return is_qq_private(src, mt)
    if scope == "qq_all":
        return is_qq_source(src)
    # default: desktop_and_qq_private
    if is_desktop_source(src):
        return True
    if is_qq_private(src, mt):
        return True
    return False


def decide_thought_gate(
    *,
    user_text: str,
    source: str,
    message_type: str = "private",
    config: NaturalChatConfig,
    need_tools: bool = False,
    codex_mode: bool = False,
    wants_detailed_answer: bool = False,
    is_command: bool = False,
) -> ThoughtGateDecision:
    if not config.character_thought_enabled:
        return ThoughtGateDecision(
            should_run=False,
            scope_matched=False,
            reason="disabled",
            short_shell=not wants_detailed_answer,
            detail_intent=wants_detailed_answer,
        )

    matched = scope_matches(source, message_type, config)
    detail = bool(wants_detailed_answer)
    short_shell = True
    if detail and config.detail_intent_bypass_short_shell:
        short_shell = False

    if not matched:
        return ThoughtGateDecision(
            should_run=False,
            scope_matched=False,
            reason="scope_miss",
            short_shell=short_shell,
            detail_intent=detail,
        )
    if need_tools or codex_mode:
        return ThoughtGateDecision(
            should_run=False,
            scope_matched=True,
            reason="tools_or_codex",
            short_shell=short_shell,
            detail_intent=detail,
        )
    if is_command:
        return ThoughtGateDecision(
            should_run=False,
            scope_matched=True,
            reason="command",
            short_shell=short_shell,
            detail_intent=detail,
        )

    text = str(user_text or "").strip()
    if not text:
        return ThoughtGateDecision(
            should_run=False,
            scope_matched=True,
            reason="empty",
            short_shell=short_shell,
            detail_intent=detail,
        )
    if len(text) > 200:
        return ThoughtGateDecision(
            should_run=False,
            scope_matched=True,
            reason="too_long",
            short_shell=short_shell,
            detail_intent=detail,
        )

    # 详细意图仍可跑 Thought（want 倾向 direct_answer），不强制 False
    return ThoughtGateDecision(
        should_run=True,
        scope_matched=True,
        reason="ok",
        short_shell=short_shell,
        detail_intent=detail,
    )


def build_scene_prompt(
    source: str,
    message_type: str = "private",
    *,
    detail_intent: bool = False,
    bypass_short_shell: bool = False,
) -> str:
    """极简本轮约束：系统规则已有短句/聊天默认，这里只补禁令 + 渠道壳。"""
    parts = ["【本轮】"]
    for line in SCENE_CORE_LINES:
        parts.append(f"- {line}")

    src = str(source or "").strip().lower()
    mt = str(message_type or "private").strip().lower() or "private"
    use_short = not (detail_intent and bypass_short_shell)

    if detail_intent:
        parts.append("- 用户要展开：先结论，再必要说明；仍像聊天。")
    elif use_short:
        parts.append("- 默认 1～2 句短接话。")

    if is_qq_source(src):
        if is_group_message(src, mt):
            parts.append("- QQ 群：别刷屏，短回。")
        else:
            parts.append("- QQ 私聊：像真人消息；短句少用句号收尾。")
    elif is_desktop_source(src):
        parts.append("- 本地闲聊：自然短促即可。")

    return "\n".join(parts)


def format_expression_block(hints: list[str], max_items: int = 1) -> str:
    items = [str(h).strip() for h in (hints or []) if str(h).strip()]
    if not items:
        return ""
    cap = max(1, int(max_items or 1))
    lines = ["【说法参考·可忽略】", *[f"- {item}" for item in items[:cap]]]
    return "\n".join(lines)


def evaluate_forbidden_reply(
    text: str,
    *,
    retries_done: int,
    max_retries: int,
) -> Dict[str, Any]:
    hits = find_forbidden_phrases(text)
    retry = should_retry_after_forbidden(
        hits=hits,
        retries_done=retries_done,
        max_retries=max_retries,
    )
    return {
        "hits": hits,
        "should_retry": retry,
        "retry_constraint": build_retry_constraint(hits) if hits else "",
        "stripped": strip_forbidden_spans(text) if hits and not retry else text,
    }


async def build_character_thought_block(
    *,
    user_text: str,
    ctx: Optional[Dict[str, Any]],
    config: NaturalChatConfig,
    need_tools: bool = False,
    codex_mode: bool = False,
    recent: Optional[List[Dict[str, Any]]] = None,
    get_active_character_context: Callable[[], tuple],
    consume_character_switch: Callable[[], bool],
    wants_detailed_answer: Callable[[str], bool],
    looks_like_command: Callable[[str], bool],
    chat_fn: Callable[..., Any],
    get_short_term_messages: Optional[Callable[..., List[Dict[str, Any]]]] = None,
    logger: Any = None,
) -> tuple[str, Dict[str, Any]]:
    """Run Character Thought when the gate allows; returns (prompt_block, trace)."""
    source = str((ctx or {}).get("source") or "").strip().lower()
    message_type = chat_message_type(ctx)
    detail = wants_detailed_answer(user_text)
    gate = decide_thought_gate(
        user_text=user_text,
        source=source,
        message_type=message_type,
        config=config,
        need_tools=need_tools,
        codex_mode=codex_mode,
        wants_detailed_answer=detail,
        is_command=looks_like_command(user_text),
    )
    just_switched = bool(consume_character_switch())
    trace: Dict[str, Any] = {
        "character_thought_used": False,
        "scope_matched": gate.scope_matched,
        "thought_skipped_reason": gate.reason if not gate.should_run else "",
        "thought_latency_ms": 0.0,
        "emotion_level": "",
        "want": "",
        "angle": "",
        "just_switched_character": just_switched,
        "short_shell": gate.short_shell,
        "detail_intent": gate.detail_intent,
        "source": source,
        "message_type": message_type,
    }
    if not gate.should_run:
        return "", trace

    char_name, _char_id, base_prompt = get_active_character_context()
    if recent is not None:
        recent_window = list(recent)[-6:]
    elif get_short_term_messages is not None:
        recent_window = list(get_short_term_messages(ctx))[-6:]
    else:
        recent_window = []
    try:
        thought, skip_reason, latency_ms = await asyncio.to_thread(
            generate_character_thought,
            chat_fn=chat_fn,
            character_name=char_name,
            character_prompt_excerpt=str(base_prompt or ""),
            recent=recent_window,
            user_text=user_text,
            just_switched_character=just_switched,
            timeout_ms=config.character_thought_timeout_ms,
            max_tokens=config.character_thought_max_tokens,
            on_error=config.character_thought_on_error,
        )
    except TimeoutError:
        trace["thought_skipped_reason"] = "timeout"
        return "", trace
    except Exception as exc:
        if logger:
            try:
                logger.warning("character thought failed: %s", exc)
            except Exception:
                pass
        trace["thought_skipped_reason"] = "error"
        return "", trace

    trace["thought_latency_ms"] = round(float(latency_ms or 0.0), 1)
    if thought is None:
        trace["thought_skipped_reason"] = skip_reason or "error"
        return "", trace

    if gate.detail_intent and thought.want == "light_ack":
        thought.want = "direct_answer"
        thought.angle = "direct"

    block = format_thought_for_prompt(
        thought,
        short_shell=gate.short_shell,
        just_switched_character=just_switched,
    )
    trace.update(
        {
            "character_thought_used": True,
            "thought_skipped_reason": "",
            "emotion_level": thought.emotion_level,
            "want": thought.want,
            "angle": thought.angle,
            "avoid": list(thought.avoid or []),
        }
    )
    return block, trace


async def retry_reply_without_forbidden(
    *,
    user_text: str,
    draft_text: str,
    constraint: str,
    ctx: Optional[Dict[str, Any]],
    get_active_character_context: Callable[[], tuple],
    chat_fn: Callable[..., Any],
    clean_retry_text: Callable[[str], str],
    normalize_qq_reply: Callable[[str], str],
) -> str:
    """One-shot constrained rewrite only when forbidden phrases hit."""
    char_name, _cid, _prompt = get_active_character_context()
    system = (
        f"你正在修正角色「{char_name}」的一句回复草稿。"
        "只去掉点评腔/抽象标签，保持原意与短句，不新增事实。"
        "只输出修正后的回复正文。"
    )
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                f"{constraint}\n\n"
                f"【用户原话】\n{str(user_text or '').strip()}\n\n"
                f"【回复草稿】\n{str(draft_text or '').strip()}"
            ),
        },
    ]
    try:
        reply = await asyncio.to_thread(
            chat_fn,
            messages,
            task_type="gatekeeper",
            caller="forbidden_phrase_retry",
        )
        polished = clean_retry_text(reply or "")
        source = str((ctx or {}).get("source") or "").strip().lower()
        if polished and source in QQ_REMOTE_SOURCES:
            polished = normalize_qq_reply(polished)
        return polished or ""
    except Exception:
        return ""


async def apply_forbidden_phrase_guard(
    *,
    text: str,
    user_text: str,
    ctx: Optional[Dict[str, Any]],
    config: NaturalChatConfig,
    retry_reply: Callable[..., Awaitable[str]],
    logger: Any = None,
    regenerate: Optional[Callable[..., Awaitable[str]]] = None,
) -> tuple[str, Dict[str, Any]]:
    max_retries = int(config.forbidden_phrase_max_retries or 0)
    current = str(text or "")
    retries_done = 0
    last_hits: List[str] = []
    source = str((ctx or {}).get("source") or "").strip().lower()
    message_type = chat_message_type(ctx)
    if not scope_matches(source, message_type, config):
        return current, {
            "forbidden_phrase_hit": False,
            "forbidden_phrases": [],
            "forbidden_retries": 0,
            "forbidden_skipped": "scope_miss",
        }

    async def _default_regen(constraint: str) -> str:
        return await retry_reply(
            user_text=user_text,
            draft_text=current,
            constraint=constraint,
            ctx=ctx,
        )

    regen = regenerate or _default_regen

    while True:
        result = evaluate_forbidden_reply(
            current,
            retries_done=retries_done,
            max_retries=max_retries,
        )
        hits = list(result.get("hits") or [])
        last_hits = hits
        if not hits:
            return current, {
                "forbidden_phrase_hit": False,
                "forbidden_phrases": [],
                "forbidden_retries": retries_done,
            }
        if result.get("should_retry"):
            constraint = str(result.get("retry_constraint") or "")
            try:
                retried = await regen(constraint=constraint)
                if retried:
                    current = str(retried)
                    retries_done += 1
                    continue
            except Exception as exc:
                if logger:
                    try:
                        logger.warning("forbidden phrase retry failed: %s", exc)
                    except Exception:
                        pass
        stripped = str(result.get("stripped") or current)
        if not stripped.strip():
            stripped = "嗯……这样啊"
        return stripped, {
            "forbidden_phrase_hit": True,
            "forbidden_phrases": last_hits,
            "forbidden_retries": retries_done,
        }


def _trace_guard(trace_process: Optional[Callable[..., None]], stage: str, trace: Dict[str, Any]) -> None:
    if trace_process is None:
        return
    if not (trace.get("forbidden_phrase_hit") or trace.get("forbidden_retries")):
        return
    try:
        trace_process(stage, **trace)
    except Exception:
        pass


@dataclass
class PlainChatHooks:
    brain: Any
    plugin_manager: Any
    chat_with_ai: Callable[..., Any]
    thought_chat_fn: Callable[..., Any]
    natural_config: NaturalChatConfig
    get_active_character_context: Callable[[], tuple]
    consume_character_switch: Callable[[], bool]
    wants_detailed_answer: Callable[[str], bool]
    looks_like_command: Callable[[str], bool]
    clean_retry_text: Callable[[str], str]
    normalize_qq_reply: Callable[[str], str]
    build_expression: Callable[..., tuple[str, List[str]]]
    get_short_term_messages: Callable[..., List[Dict[str, Any]]]
    build_user_address_context: Callable[..., str]
    build_external_sender_context: Callable[..., str]
    build_live2d_self_awareness_hint: Callable[..., str]
    build_current_emotion_context: Callable[..., str]
    build_reply_style_context: Callable[..., str]
    get_memory_person_id: Callable[..., Any]
    extract_emo_tag: Callable[[str], tuple[str, str]]
    clean_text_for_tts: Callable[[str], str]
    strip_internal_tags: Callable[[str], str]
    strip_cmd_anywhere: Callable[[str], str]
    strip_emo_tags_anywhere: Callable[[str], str]
    should_suppress_followup_preface: Callable[[str], bool]
    merge_preface_texts: Callable[..., str]
    apply_character_catchphrase: Callable[[str], str]
    prepare_reply_for_output: Callable[..., str]
    infer_reply_emotion_with_llm: Callable[..., Awaitable[Optional[str]]]
    contains_cmd: Callable[[str], bool]
    looks_like_uncertain_answer: Callable[[str], bool]
    is_searchworthy_question: Callable[[str], bool]
    skill_manager: Any = None
    personality: Any = None
    logger: Any = None
    record_tool_execution: Optional[Callable[..., Any]] = None
    trace_process: Optional[Callable[..., None]] = None


@dataclass
class PlainChatOutcome:
    flow: ChatFlowResult
    react_first_pass: Optional[ReactFirstPassResult] = None
    context_messages: list = field(default_factory=list)
    tool_event_state: Dict[str, str] = field(default_factory=dict)


def should_run_plain_chat(
    *,
    plain_direct_chat_candidate: bool,
    deferred_tool_flow: bool,
    delegate_triggers: Sequence[str],
    search_delegate_requested: bool,
) -> bool:
    has_delegate = any(str(item or "").strip() for item in (delegate_triggers or []))
    return (
        bool(plain_direct_chat_candidate)
        and not bool(deferred_tool_flow)
        and not has_delegate
        and not bool(search_delegate_requested)
    )


def _capability_unavailable_context(route_reason: str, match_reason: str) -> str:
    reason = str(route_reason or "")
    if not reason.startswith("capability_unavailable:"):
        return ""
    unavailable = str(match_reason or "capability unavailable")
    return (
        "\n\n【工具状态】用户请求命中了可用能力声明，但当前工具不可执行："
        f"{reason}；原因：{unavailable}。"
        "请不要假装已经查询成功，直接用自然语言说明该工具当前不可用。"
    )


async def _clean_prompt_messages(messages: list) -> list:
    cleaned = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "")
        content = message.get("content")
        if role != "system":
            cleaned.append(message)
            continue
        try:
            from modules.memory import clean_injected_context

            sanitized = dict(message)
            sanitized["content"] = clean_injected_context(str(content or ""))
            cleaned.append(sanitized)
        except Exception:
            cleaned.append(message)
    return cleaned


@dataclass
class AssembledChatPrompt:
    context_messages: list = field(default_factory=list)
    thought_trace: Dict[str, Any] = field(default_factory=dict)
    expression_hints: List[str] = field(default_factory=list)


async def assemble_chat_context_messages(
    *,
    user_text: str,
    ctx: Dict[str, Any],
    hooks: PlainChatHooks,
    conversation_session_id: str,
    memory_session_id: str,
    route_reason: str = "",
    capability_match_reason: str = "",
    invocation_mode: str = "cmd",
    need_tools: bool = False,
    codex_mode: bool = False,
    extra_runtime_sections: Sequence[Optional[str]] = (),
    tool_sections: Sequence[Optional[str]] = (),
    tool_intent: Sequence[str] = (),
    trace_extra: Optional[Dict[str, Any]] = None,
) -> AssembledChatPrompt:
    """Thought → PromptParts → brain.build_prompt → clean.

    Plain chat and the remaining tool/image/preface path share this.
    Callers pass tool/codex extras; this function does not own routing.
    """
    special_context = _capability_unavailable_context(
        route_reason, capability_match_reason
    )
    user_address_context = hooks.build_user_address_context(ctx)
    if user_address_context:
        special_context += f"\n\n{user_address_context}"
    external_sender_context = hooks.build_external_sender_context(ctx)
    if external_sender_context:
        special_context += f"\n\n{external_sender_context}"

    short_term_messages = hooks.get_short_term_messages(ctx)
    thought_block, thought_trace = await build_character_thought_block(
        user_text=user_text,
        ctx=ctx,
        config=hooks.natural_config,
        need_tools=need_tools,
        codex_mode=codex_mode,
        recent=short_term_messages,
        get_active_character_context=hooks.get_active_character_context,
        consume_character_switch=hooks.consume_character_switch,
        wants_detailed_answer=hooks.wants_detailed_answer,
        looks_like_command=hooks.looks_like_command,
        chat_fn=hooks.thought_chat_fn,
        get_short_term_messages=hooks.get_short_term_messages,
        logger=hooks.logger,
    )
    expression_block, expression_hints = hooks.build_expression(
        user_text,
        ctx,
        config=hooks.natural_config,
        recent=short_term_messages,
    )
    if hooks.trace_process:
        try:
            hooks.trace_process(
                "character_natural_chat",
                **{
                    **(thought_trace or {}),
                    "expression_count": len(expression_hints or []),
                    "expression_ids_or_hints": (expression_hints or [])[:3],
                    **dict(trace_extra or {}),
                },
            )
        except Exception:
            pass
    skill_prompt = ""
    if hooks.skill_manager is not None:
        try:
            skill_prompt = hooks.skill_manager.build_prompt_addition()
        except Exception as exc:
            if hooks.logger:
                hooks.logger.warning(f"Skill prompt build failed: {exc}")
    prompt_parts = make_chat_prompt_parts(
        invocation_mode=invocation_mode,
        runtime_sections=(
            hooks.build_current_emotion_context(ctx),
            hooks.build_reply_style_context(user_text, ctx),
            special_context,
            thought_block,
            expression_block,
            hooks.build_live2d_self_awareness_hint(ctx),
            skill_prompt,
            *tuple(extra_runtime_sections or ()),
        ),
        tool_sections=tool_sections,
    )

    conversation_scope = ctx.get("conversation_scope")
    context_messages = await asyncio.to_thread(
        hooks.brain.build_prompt,
        user_text,
        system_persona=prompt_parts.to_legacy_system_text(),
        prompt_parts=prompt_parts,
        tool_intent=list(tool_intent or []),
        session_id=conversation_session_id,
        memory_session_id=memory_session_id,
        person_id=hooks.get_memory_person_id(ctx) or "owner",
        conversation_scope=conversation_scope,
    )
    context_messages = await _clean_prompt_messages(list(context_messages or []))
    return AssembledChatPrompt(
        context_messages=context_messages,
        thought_trace=dict(thought_trace or {}),
        expression_hints=list(expression_hints or []),
    )


async def run_plain_chat(
    *,
    user_text: str,
    ctx: Dict[str, Any],
    hooks: PlainChatHooks,
    conversation_session_id: str,
    memory_session_id: str,
    source_key: str,
    route_reason: str = "",
    capability_match_reason: str = "",
    persona_prompt: str = "",
    preface_text: str = "",
    followup_search_query: str = "",
    invocation_mode: str = "cmd",
    task_default: str = "default",
    task_reasoning: str = "tool_reasoning",
    character_sharing_enabled: bool = False,
    start_emo: str = "neutral",
) -> PlainChatOutcome:
    """Own the plain-chat order: thought → prompt → model → guard.

    Returns status=replied when the turn stays natural chat. Returns
    status=continue (without a second model call) when the first pass
    executed tools or forced search is required.
    """
    assembled = await assemble_chat_context_messages(
        user_text=user_text,
        ctx=ctx,
        hooks=hooks,
        conversation_session_id=conversation_session_id,
        memory_session_id=memory_session_id,
        route_reason=route_reason,
        capability_match_reason=capability_match_reason,
        invocation_mode=invocation_mode,
        need_tools=False,
        codex_mode=False,
        tool_intent=[],
        trace_extra={"plain_chat_pipeline": True},
    )
    context_messages = list(assembled.context_messages)

    tool_event_state: Dict[str, str] = {}

    def _record_tool_execution(**event: Any) -> Any:
        if hooks.record_tool_execution is None:
            return None
        return hooks.record_tool_execution(event_state=tool_event_state, **event)

    react_first_pass = await run_react_first_pass(
        context_messages=context_messages,
        ctx=ctx,
        need_tools=False,
        deferred_tool_flow=False,
        task_reasoning=task_reasoning,
        task_default=task_default,
        plugin_manager=hooks.plugin_manager,
        chat_with_ai=hooks.chat_with_ai,
        contains_cmd=hooks.contains_cmd,
        strip_cmd_anywhere=hooks.strip_cmd_anywhere,
        record_tool_execution=_record_tool_execution,
        invocation_mode=invocation_mode,
        tool_triggers=[],
    )
    context_messages = list(react_first_pass.context_messages or context_messages)
    continue_outcome = PlainChatOutcome(
        flow=ChatFlowResult(status=STATUS_CONTINUE, source=source_key),
        react_first_pass=react_first_pass,
        context_messages=context_messages,
        tool_event_state=tool_event_state,
    )
    if react_first_pass.triggered or react_first_pass.tool_results:
        return continue_outcome

    forced_search_query = choose_forced_search_query(
        user_text=user_text,
        first_reply=react_first_pass.reply or "",
        followup_query=followup_search_query,
        triggered=bool(react_first_pass.triggered),
        tool_results=react_first_pass.tool_results,
        delegate_triggers=[],
        looks_like_uncertain_answer=hooks.looks_like_uncertain_answer,
        is_searchworthy_question=hooks.is_searchworthy_question,
    )
    if forced_search_query:
        return continue_outcome

    try_share = getattr(hooks.personality, "try_share", None)
    model_finalize = finalize_model_reply(
        reply=react_first_pass.reply,
        start_emo=start_emo,
        extract_emo_tag=hooks.extract_emo_tag,
        character_sharing_enabled=character_sharing_enabled,
        try_share=try_share if callable(try_share) else (lambda: ""),
    )
    prepared_reply = await prepare_final_reply(
        final_reply=model_finalize.final_reply,
        final_emo=model_finalize.final_emo,
        model_emo_seen=model_finalize.model_emo_seen,
        user_text=user_text,
        ctx=ctx,
        preface_text=preface_text,
        clean_text_for_tts=hooks.clean_text_for_tts,
        strip_internal_tags=hooks.strip_internal_tags,
        strip_cmd_anywhere=hooks.strip_cmd_anywhere,
        strip_emo_tags_anywhere=hooks.strip_emo_tags_anywhere,
        should_suppress_followup_preface=hooks.should_suppress_followup_preface,
        merge_preface_texts=hooks.merge_preface_texts,
        apply_character_catchphrase=hooks.apply_character_catchphrase,
        prepare_reply_for_output=hooks.prepare_reply_for_output,
        infer_reply_emotion_with_llm=hooks.infer_reply_emotion_with_llm,
    )
    async def _retry_forbidden(**kwargs: Any) -> str:
        return await retry_reply_without_forbidden(
            get_active_character_context=hooks.get_active_character_context,
            chat_fn=hooks.thought_chat_fn,
            clean_retry_text=hooks.clean_retry_text,
            normalize_qq_reply=hooks.normalize_qq_reply,
            **kwargs,
        )

    final_reply, guard_trace = await apply_forbidden_phrase_guard(
        text=prepared_reply.text,
        user_text=user_text,
        ctx=ctx,
        config=hooks.natural_config,
        retry_reply=_retry_forbidden,
        logger=hooks.logger,
    )
    _trace_guard(hooks.trace_process, "forbidden_phrase_guard", guard_trace)
    return PlainChatOutcome(
        flow=ChatFlowResult(
            status=STATUS_REPLIED,
            reply_text=str(final_reply or ""),
            emotion=prepared_reply.emotion,
            source=source_key,
        ),
        react_first_pass=react_first_pass,
        context_messages=context_messages,
        tool_event_state=tool_event_state,
    )
