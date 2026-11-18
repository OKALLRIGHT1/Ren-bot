"""QQ 群免 @ 之后，决定这条消息要不要进入主模型。

和麦麦的差别：
- @、回复她、点名、插件口令直接开口，不再花一次判断。
- 不掷骰子。talk_value 只写成一句倾向，交给小模型。
- 规划器不看完整人设，拿不准就沉默。
- 连续沉默后按退避跳过小模型；堆到一定条数再查一次。
- 不改「允许群聊 / 仅响应 @我」。那些开关仍然决定消息进不进这里。
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Optional, Sequence


_AT_KEYS = (
    "targets_self",
    "mentioned",
    "is_mentioned",
    "is_at",
    "at_me",
    "to_me",
)
_QQ_SOURCES = {"qq_gateway", "napcat_qq"}


@dataclass
class GroupReplyPlannerConfig:
    enabled: bool = True
    talk_value: float = 0.35
    context_lines: int = 8
    store_lines: int = 12
    line_chars: int = 80
    max_tokens: int = 24
    timeout_sec: float = 12.0
    backoff_base_sec: float = 15.0
    backoff_cap_sec: float = 120.0
    backoff_start_count: int = 2
    backoff_bypass_count: int = 6


@dataclass
class GroupLine:
    sender: str
    text: str
    role: str


@dataclass
class GroupReplyDecision:
    applicable: bool
    reply: bool
    reason: str
    context_block: str = ""
    used_model: bool = False


@dataclass
class _SessionState:
    lines: Deque[GroupLine] = field(default_factory=deque)
    silence_streak: int = 0
    last_model_check_at: float = 0.0
    messages_since_check: int = 0
    checking: bool = False


def is_group_context(ctx: Optional[dict]) -> bool:
    data = ctx if isinstance(ctx, dict) else {}
    source = str(data.get("source") or "").strip().lower()
    if source not in _QQ_SOURCES:
        return False
    meta = data.get("channel_meta") if isinstance(data.get("channel_meta"), dict) else {}
    message_type = str(meta.get("message_type") or "").strip().lower()
    return message_type == "group"


def session_key(ctx: Optional[dict]) -> str:
    data = ctx if isinstance(ctx, dict) else {}
    meta = data.get("channel_meta") if isinstance(data.get("channel_meta"), dict) else {}
    session_id = str(meta.get("session_id") or "").strip()
    if session_id:
        return session_id
    group_id = str(meta.get("group_id") or "").strip()
    return f"group:{group_id}" if group_id else ""


def mention_names(character: Optional[dict]) -> list[str]:
    data = character if isinstance(character, dict) else {}
    found: list[str] = []
    for key in ("name", "nickname", "display_name"):
        _push_name(found, data.get(key))
    aliases = data.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [part.strip() for part in aliases.splitlines() if part.strip()]
    if isinstance(aliases, (list, tuple)):
        for alias in aliases:
            _push_name(found, alias)
    return found


def talk_stance(talk_value: float) -> str:
    value = _clamp_unit(talk_value)
    if value >= 0.8:
        return (
            f"发言倾向 {value:.2f}：可以正常接话。"
            "这句话跟她有关，或她接一句不突兀，就可以回。"
        )
    if value >= 0.45:
        return (
            f"发言倾向 {value:.2f}：偏安静。"
            "只有在问她、接她刚说的话、或明显等她表态时才回。"
        )
    return (
        f"发言倾向 {value:.2f}：很安静。"
        "别人互相聊天、闲扯、发表情，都不要回。只有明显是在对她说话时才回。"
    )


def uncertainty_line(talk_value: float) -> str:
    if _clamp_unit(talk_value) >= 0.8:
        return "拿不准、但这句话像在等一个人接时，输出 YES。"
    return "拿不准就输出 NO。"


def parse_yes_no(text: str) -> Optional[bool]:
    raw = str(text or "").strip()
    if not raw:
        return None
    token = raw.split()[0].strip(".,;:：\"'`*_<>[](){}")
    upper = token.upper()
    if upper.startswith("YES") or token in {"是", "要", "回"}:
        return True
    if upper.startswith("NO") or token in {"否", "不", "不回", "沉默", "别回"}:
        return False
    folded = raw.upper()
    has_yes = "YES" in folded
    has_no = "NO" in folded
    if has_yes and not has_no:
        return True
    if has_no and not has_yes:
        return False
    return None


def is_low_content(text: str) -> bool:
    raw = str(text or "").strip()
    if len(raw) < 2:
        return True
    core = re.sub(r"[\s\W_]+", "", raw, flags=re.UNICODE)
    return len(core) < 2


def is_at_self(meta: Optional[dict]) -> bool:
    data = meta if isinstance(meta, dict) else {}
    return any(bool(data.get(key)) for key in _AT_KEYS)


def replies_to_self(meta: Optional[dict]) -> bool:
    data = meta if isinstance(meta, dict) else {}
    reply = data.get("reply") if isinstance(data.get("reply"), dict) else {}
    user_id = str(reply.get("user_id") or "").strip()
    self_id = str(data.get("self_id") or "").strip()
    return bool(user_id and self_id and user_id == self_id)


def hits_any_keyword(text: str, keywords: Sequence[str]) -> bool:
    raw = str(text or "")
    low = raw.lower()
    for item in keywords or ():
        key = str(item or "").strip()
        if not key:
            continue
        if key.lower().startswith("re:"):
            pattern = key[3:]
            try:
                if re.search(pattern, raw, flags=re.IGNORECASE | re.DOTALL):
                    return True
            except re.error:
                continue
        elif key.lower() in low:
            return True
    return False


def looks_like_command(text: str) -> bool:
    return str(text or "").lstrip().startswith(("/", "!", "！", "#"))


def backoff_seconds(streak: int, config: GroupReplyPlannerConfig) -> float:
    start = max(1, int(config.backoff_start_count or 1))
    if int(streak or 0) < start:
        return 0.0
    exponent = int(streak) - start
    base = max(0.0, float(config.backoff_base_sec or 0))
    cap = max(base, float(config.backoff_cap_sec or base))
    return min(cap, base * (2 ** max(0, exponent)))


def format_group_context_block(
    lines: Sequence[GroupLine],
    *,
    limit: int = 8,
) -> str:
    shown = list(lines or [])[-max(1, int(limit or 1)) :]
    if not shown:
        return ""
    rows = []
    for line in shown:
        who = "她" if line.role == "assistant" else (line.sender or "有人")
        rows.append(f"- {who}：{line.text}")
    return (
        "【最近群聊】\n"
        + "\n".join(rows)
        + "\n这些是她没接或刚接过的近几句，只用来看语境。"
        "顺着当前这一句回，不要逐条点评，也不要说自己刚才没说话。"
    )


def build_planner_prompt(
    *,
    names: Sequence[str],
    talk_value: float,
    prior: Sequence[GroupLine],
    sender: str,
    text: str,
    limit: int = 8,
) -> str:
    called = "、".join(str(name).strip() for name in names if str(name).strip())
    if not called:
        called = "当前角色"
    history = list(prior or [])[-max(1, int(limit or 1)) :]
    if history:
        rows = []
        for line in history:
            who = "她" if line.role == "assistant" else (line.sender or "有人")
            rows.append(f"- {who}：{line.text}")
        history_block = "\n".join(rows)
    else:
        history_block = "- （还没有更早的群消息）"
    return (
        "你在判断一个桌面陪伴角色现在要不要在 QQ 群里开口。\n"
        "只输出一个词：YES 或 NO。\n"
        f"她的名字：{called}\n"
        f"{talk_stance(talk_value)}\n"
        f"{uncertainty_line(talk_value)}\n"
        "不要因为群里很热闹就接话。不要评价你自己的判断。\n"
        "最近群消息：\n"
        f"{history_block}\n"
        "当前这句：\n"
        f"- {sender or '有人'}：{text}"
    )


def default_group_reply_config() -> GroupReplyPlannerConfig:
    try:
        from config import (
            GROUP_REPLY_BACKOFF_BASE_SEC,
            GROUP_REPLY_BACKOFF_BYPASS_COUNT,
            GROUP_REPLY_BACKOFF_CAP_SEC,
            GROUP_REPLY_BACKOFF_START_COUNT,
            GROUP_REPLY_CONTEXT_LINES,
            GROUP_REPLY_PLANNER_ENABLED,
            GROUP_REPLY_PLANNER_MAX_TOKENS,
            GROUP_REPLY_PLANNER_TIMEOUT_SEC,
            GROUP_REPLY_TALK_VALUE,
        )
    except Exception:
        return GroupReplyPlannerConfig()
    return GroupReplyPlannerConfig(
        enabled=bool(GROUP_REPLY_PLANNER_ENABLED),
        talk_value=float(GROUP_REPLY_TALK_VALUE),
        context_lines=int(GROUP_REPLY_CONTEXT_LINES),
        max_tokens=int(GROUP_REPLY_PLANNER_MAX_TOKENS),
        timeout_sec=float(GROUP_REPLY_PLANNER_TIMEOUT_SEC),
        backoff_base_sec=float(GROUP_REPLY_BACKOFF_BASE_SEC),
        backoff_cap_sec=float(GROUP_REPLY_BACKOFF_CAP_SEC),
        backoff_start_count=int(GROUP_REPLY_BACKOFF_START_COUNT),
        backoff_bypass_count=int(GROUP_REPLY_BACKOFF_BYPASS_COUNT),
    )


def config_from_mapping(raw: Optional[dict] = None) -> GroupReplyPlannerConfig:
    """环境默认值之上，只覆盖调用方真正写下的键。"""
    base = default_group_reply_config()
    data = raw if isinstance(raw, dict) else {}
    enabled = base.enabled
    if "group_reply_planner_enabled" in data:
        enabled = _as_bool(data.get("group_reply_planner_enabled"), base.enabled)
    talk_value = base.talk_value
    if "group_reply_talk_value" in data:
        talk_value = _clamp_unit(data.get("group_reply_talk_value"))
    return GroupReplyPlannerConfig(
        enabled=enabled,
        talk_value=talk_value,
        context_lines=_mapped_int(
            data, "group_reply_context_lines", base.context_lines, 1, 20
        ),
        max_tokens=base.max_tokens,
        timeout_sec=base.timeout_sec,
        backoff_base_sec=_mapped_float(
            data, "group_reply_backoff_base_sec", base.backoff_base_sec, 0.0, 120.0
        ),
        backoff_cap_sec=_mapped_float(
            data, "group_reply_backoff_cap_sec", base.backoff_cap_sec, 15.0, 300.0
        ),
        backoff_start_count=_mapped_int(
            data, "group_reply_backoff_start_count", base.backoff_start_count, 1, 8
        ),
        backoff_bypass_count=_mapped_int(
            data,
            "group_reply_backoff_bypass_count",
            base.backoff_bypass_count,
            1,
            30,
        ),
        store_lines=base.store_lines,
        line_chars=base.line_chars,
    )


def group_reply_settings(config: Optional[GroupReplyPlannerConfig] = None) -> dict:
    current = config or default_group_reply_config()
    return {
        "group_reply_planner_enabled": bool(current.enabled),
        "group_reply_talk_value": float(current.talk_value),
        "group_reply_context_lines": int(current.context_lines),
        "group_reply_backoff_base_sec": float(current.backoff_base_sec),
        "group_reply_backoff_cap_sec": float(current.backoff_cap_sec),
        "group_reply_backoff_start_count": int(current.backoff_start_count),
        "group_reply_backoff_bypass_count": int(current.backoff_bypass_count),
    }


def load_group_reply_config(runtime: Optional[dict] = None) -> GroupReplyPlannerConfig:
    if runtime is None:
        try:
            from modules.runtime_settings import load_runtime_settings

            runtime = load_runtime_settings()
        except Exception:
            runtime = {}
    return config_from_mapping(runtime)


class GroupReplyPlanner:
    def __init__(
        self,
        config: Optional[GroupReplyPlannerConfig] = None,
        *,
        load_config: Optional[Callable[[], GroupReplyPlannerConfig]] = None,
    ):
        self.config = config or GroupReplyPlannerConfig()
        self._load_config = load_config
        self._sessions: dict[str, _SessionState] = {}

    @classmethod
    def from_config(cls) -> "GroupReplyPlanner":
        loader = load_group_reply_config
        return cls(loader(), load_config=loader)

    def _use_config(self) -> GroupReplyPlannerConfig:
        if self._load_config is None:
            return self.config
        try:
            self.config = self._load_config()
        except Exception:
            pass
        return self.config

    def note_assistant(self, ctx: Optional[dict], text: str) -> None:
        if not self._use_config().enabled or not is_group_context(ctx):
            return
        key = session_key(ctx)
        clean = self._clip(text)
        if not key or not clean:
            return
        state = self._state(key)
        if (
            state.lines
            and state.lines[-1].role == "assistant"
            and state.lines[-1].text == clean
        ):
            return
        state.lines.append(GroupLine("她", clean, "assistant"))
        self._clear_backoff(state)

    async def decide(
        self,
        user_text: str,
        ctx: Optional[dict],
        *,
        mention_names: Sequence[str] = (),
        keywords: Sequence[str] = (),
        chat_with_ai: Optional[Callable[..., Any]] = None,
        logger: Any = None,
        now: Callable[[], float] = time.time,
    ) -> GroupReplyDecision:
        self._use_config()
        if not self.config.enabled:
            return GroupReplyDecision(False, True, "disabled")
        if not is_group_context(ctx):
            return GroupReplyDecision(False, True, "not_group")
        key = session_key(ctx)
        if not key:
            return GroupReplyDecision(False, True, "no_session")

        meta = (ctx or {}).get("channel_meta") if isinstance(ctx, dict) else {}
        if not isinstance(meta, dict):
            meta = {}
        text = str(user_text or "").strip()
        sender = str(meta.get("sender_name") or meta.get("user_id") or "有人").strip() or "有人"
        state = self._state(key)
        prior = list(state.lines)

        if is_at_self(meta):
            return self._force_reply(state, sender, text, prior, "at")
        if replies_to_self(meta):
            return self._force_reply(state, sender, text, prior, "reply_to_self")
        if self._mentions(text, mention_names):
            return self._force_reply(state, sender, text, prior, "name")
        if looks_like_command(text) or hits_any_keyword(text, keywords):
            return self._force_reply(state, sender, text, prior, "command")
        if is_low_content(text):
            self._append_user(state, sender, text)
            return GroupReplyDecision(True, False, "low_content")
        if state.checking:
            self._append_user(state, sender, text)
            return GroupReplyDecision(True, False, "in_flight")

        now_ts = float(now())
        if self._in_backoff(state, now_ts):
            state.messages_since_check += 1
            if state.messages_since_check < max(1, int(self.config.backoff_bypass_count or 1)):
                self._append_user(state, sender, text)
                return GroupReplyDecision(True, False, "backoff")

        state.checking = True
        used_model = False
        try:
            prompt = build_planner_prompt(
                names=mention_names,
                talk_value=self.config.talk_value,
                prior=prior,
                sender=sender,
                text=text,
                limit=self.config.context_lines,
            )
            raw = await self._ask_model(chat_with_ai, prompt)
            used_model = True
            parsed = parse_yes_no(raw)
            if parsed is None:
                reply = False
                reason = "uncertain"
            else:
                reply = parsed
                reason = "model_yes" if reply else "model_no"
        except Exception as exc:
            reply = False
            reason = "model_error"
            used_model = False
            if logger is not None:
                try:
                    logger.warning(f"[GroupReply] 判断失败，保持沉默: {exc}")
                except Exception:
                    pass
        finally:
            state.checking = False

        self._append_user(state, sender, text)
        state.last_model_check_at = now_ts
        state.messages_since_check = 0
        if reply:
            state.silence_streak = 0
            return GroupReplyDecision(
                True,
                True,
                reason,
                format_group_context_block(prior, limit=self.config.context_lines),
                used_model,
            )
        state.silence_streak += 1
        return GroupReplyDecision(True, False, reason, "", used_model)

    def _force_reply(
        self,
        state: _SessionState,
        sender: str,
        text: str,
        prior: Sequence[GroupLine],
        reason: str,
    ) -> GroupReplyDecision:
        self._append_user(state, sender, text)
        self._clear_backoff(state)
        return GroupReplyDecision(
            True,
            True,
            reason,
            format_group_context_block(prior, limit=self.config.context_lines),
        )

    def _state(self, key: str) -> _SessionState:
        state = self._sessions.get(key)
        if state is None:
            state = _SessionState(
                lines=deque(maxlen=max(1, int(self.config.store_lines or 1)))
            )
            self._sessions[key] = state
        return state

    def _append_user(self, state: _SessionState, sender: str, text: str) -> None:
        clean = self._clip(text)
        if not clean:
            return
        who = str(sender or "有人").strip() or "有人"
        state.lines.append(GroupLine(who[:24], clean, "user"))

    def _clip(self, text: str) -> str:
        clean = " ".join(str(text or "").split())
        limit = max(8, int(self.config.line_chars or 8))
        if len(clean) > limit:
            return clean[: limit - 1] + "…"
        return clean

    def _clear_backoff(self, state: _SessionState) -> None:
        state.silence_streak = 0
        state.messages_since_check = 0
        state.last_model_check_at = 0.0

    def _in_backoff(self, state: _SessionState, now_ts: float) -> bool:
        wait = backoff_seconds(state.silence_streak, self.config)
        if wait <= 0 or state.last_model_check_at <= 0:
            return False
        return (now_ts - state.last_model_check_at) < wait

    def _mentions(self, text: str, names: Sequence[str]) -> bool:
        folded = str(text or "").casefold()
        if not folded:
            return False
        for name in names or ():
            token = str(name or "").strip()
            if len(token) < 2:
                continue
            if token.casefold() in folded:
                return True
        return False

    async def _ask_model(self, chat_with_ai: Optional[Callable[..., Any]], prompt: str) -> str:
        if not callable(chat_with_ai):
            raise RuntimeError("group reply planner has no model")
        messages = [{"role": "user", "content": prompt}]
        kwargs = {
            "task_type": "gatekeeper",
            "caller": "group_reply_planner",
            "max_tokens": int(self.config.max_tokens or 24),
            "timeout_sec": float(self.config.timeout_sec or 12),
        }
        if asyncio.iscoroutinefunction(chat_with_ai):
            result = await chat_with_ai(messages, **kwargs)
        else:
            result = await asyncio.to_thread(chat_with_ai, messages, **kwargs)
        return str(result or "")


def _push_name(found: list[str], value: Any) -> None:
    text = str(value or "").strip()
    if len(text) < 2 or text in found:
        return
    found.append(text)


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    if value is None:
        return bool(default)
    return bool(value)


def _mapped_int(
    data: dict, key: str, default: int, minimum: int, maximum: int
) -> int:
    if key not in data:
        return int(default)
    try:
        value = int(float(data.get(key)))
    except (TypeError, ValueError):
        value = int(default)
    return min(maximum, max(minimum, value))


def _mapped_float(
    data: dict, key: str, default: float, minimum: float, maximum: float
) -> float:
    if key not in data:
        return float(default)
    try:
        value = float(data.get(key))
    except (TypeError, ValueError):
        value = float(default)
    return min(maximum, max(minimum, value))


def _clamp_unit(value: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.35
    return min(1.0, max(0.0, number))
