"""ASR / microphone input settings and wake-word resolution.

Runtime keys live in data/runtime_settings.json. Defaults come from config.py.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

try:
    from modules.runtime_settings import load_runtime_settings, update_runtime_settings
except Exception:  # pragma: no cover

    def load_runtime_settings() -> Dict[str, Any]:
        return {}

    def update_runtime_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
        return dict(patch or {})


try:
    from config import (
        ASR_MIN_CHARS,
        GATEKEEPER_ACTIVE_SESSION_WINDOW,
        WAKE_KEYWORDS,
    )
except Exception:  # pragma: no cover
    WAKE_KEYWORDS = ["五十铃", "怜", "Suzu", "助手"]
    GATEKEEPER_ACTIVE_SESSION_WINDOW = 20
    ASR_MIN_CHARS = 2


# Runtime setting keys
ASR_REQUIRE_WAKE_WORD_KEY = "asr_require_wake_word"
ASR_USE_CHARACTER_WAKE_WORDS_KEY = "asr_use_character_wake_words"
ASR_INCLUDE_GLOBAL_WAKE_WORDS_KEY = "asr_include_global_wake_words"
ASR_GLOBAL_WAKE_WORDS_KEY = "asr_global_wake_words"
ASR_EXTRA_WAKE_WORDS_KEY = "asr_extra_wake_words"
ASR_ACTIVE_WINDOW_SEC_KEY = "asr_active_window_sec"
ASR_MIN_CHARS_KEY = "asr_min_chars"

ASR_SETTING_KEYS = (
    ASR_REQUIRE_WAKE_WORD_KEY,
    ASR_USE_CHARACTER_WAKE_WORDS_KEY,
    ASR_INCLUDE_GLOBAL_WAKE_WORDS_KEY,
    ASR_GLOBAL_WAKE_WORDS_KEY,
    ASR_EXTRA_WAKE_WORDS_KEY,
    ASR_ACTIVE_WINDOW_SEC_KEY,
    ASR_MIN_CHARS_KEY,
)


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    return bool(default)


def _as_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except Exception:
        number = int(default)
    return max(minimum, min(maximum, number))


def normalize_wake_word_list(value: Any) -> List[str]:
    """Normalize free-form wake word config into a de-duplicated list."""
    items: List[str] = []
    if value is None:
        return items
    if isinstance(value, str):
        raw = value.replace("\r", "\n").replace(";", "\n").replace(",", "\n")
        parts = [part.strip() for part in raw.splitlines()]
        items = [part for part in parts if part]
    elif isinstance(value, (list, tuple, set)):
        for entry in value:
            text = str(entry or "").strip()
            if text:
                items.append(text)
    else:
        text = str(value or "").strip()
        if text:
            items.append(text)

    seen: set[str] = set()
    result: List[str] = []
    for item in items:
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _looks_cjk_name(text: str) -> bool:
    if not text or any(ch.isspace() for ch in text):
        return False
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk >= max(2, len(text) // 2)


def _name_variants(name: str) -> List[str]:
    """Expand a display name into practical wake phrases."""
    text = str(name or "").strip()
    if not text:
        return []
    variants = [text]
    # Latin / mixed: also keep first token ("Ren Isuzu" -> Ren)
    if " " in text or "·" in text or "・" in text:
        for sep in (" ", "·", "・"):
            if sep in text:
                head = text.split(sep, 1)[0].strip()
                if head:
                    variants.append(head)
                break
    # CJK full name heuristic: 五十铃怜 -> 五十铃 + 怜
    if _looks_cjk_name(text) and len(text) >= 2:
        given = text[-1]
        family = text[:-1]
        if given:
            variants.append(given)
        if len(family) >= 2:
            variants.append(family)
    return variants


def collect_character_wake_words(character: Optional[Dict[str, Any]]) -> List[str]:
    if not isinstance(character, dict):
        return []
    words: List[str] = []
    words.extend(_name_variants(str(character.get("name") or "")))

    aliases = character.get("aliases") or []
    if isinstance(aliases, str):
        aliases = normalize_wake_word_list(aliases)
    if isinstance(aliases, (list, tuple)):
        for alias in aliases:
            words.extend(_name_variants(str(alias or "")))

    qq_profile = character.get("qq_profile")
    if isinstance(qq_profile, dict):
        for key in ("nickname", "card", "name", "display_name"):
            words.extend(_name_variants(str(qq_profile.get(key) or "")))

    return normalize_wake_word_list(words)


def default_global_wake_words() -> List[str]:
    """Factory defaults from config.WAKE_KEYWORDS (editable at runtime)."""
    return normalize_wake_word_list(WAKE_KEYWORDS)


def default_asr_settings() -> Dict[str, Any]:
    return {
        ASR_REQUIRE_WAKE_WORD_KEY: True,
        ASR_USE_CHARACTER_WAKE_WORDS_KEY: True,
        ASR_INCLUDE_GLOBAL_WAKE_WORDS_KEY: True,
        ASR_GLOBAL_WAKE_WORDS_KEY: default_global_wake_words(),
        ASR_EXTRA_WAKE_WORDS_KEY: [],
        ASR_ACTIVE_WINDOW_SEC_KEY: int(GATEKEEPER_ACTIVE_SESSION_WINDOW or 20),
        ASR_MIN_CHARS_KEY: int(ASR_MIN_CHARS or 2),
    }


def load_asr_settings(runtime: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = runtime if isinstance(runtime, dict) else load_runtime_settings()
    defaults = default_asr_settings()
    return {
        ASR_REQUIRE_WAKE_WORD_KEY: _as_bool(
            data.get(ASR_REQUIRE_WAKE_WORD_KEY, defaults[ASR_REQUIRE_WAKE_WORD_KEY]),
            defaults[ASR_REQUIRE_WAKE_WORD_KEY],
        ),
        ASR_USE_CHARACTER_WAKE_WORDS_KEY: _as_bool(
            data.get(
                ASR_USE_CHARACTER_WAKE_WORDS_KEY,
                defaults[ASR_USE_CHARACTER_WAKE_WORDS_KEY],
            ),
            defaults[ASR_USE_CHARACTER_WAKE_WORDS_KEY],
        ),
        ASR_INCLUDE_GLOBAL_WAKE_WORDS_KEY: _as_bool(
            data.get(
                ASR_INCLUDE_GLOBAL_WAKE_WORDS_KEY,
                defaults[ASR_INCLUDE_GLOBAL_WAKE_WORDS_KEY],
            ),
            defaults[ASR_INCLUDE_GLOBAL_WAKE_WORDS_KEY],
        ),
        ASR_GLOBAL_WAKE_WORDS_KEY: normalize_wake_word_list(
            data.get(ASR_GLOBAL_WAKE_WORDS_KEY, defaults[ASR_GLOBAL_WAKE_WORDS_KEY])
        ),
        ASR_EXTRA_WAKE_WORDS_KEY: normalize_wake_word_list(
            data.get(ASR_EXTRA_WAKE_WORDS_KEY, defaults[ASR_EXTRA_WAKE_WORDS_KEY])
        ),
        ASR_ACTIVE_WINDOW_SEC_KEY: _as_int(
            data.get(ASR_ACTIVE_WINDOW_SEC_KEY, defaults[ASR_ACTIVE_WINDOW_SEC_KEY]),
            defaults[ASR_ACTIVE_WINDOW_SEC_KEY],
            minimum=0,
            maximum=600,
        ),
        ASR_MIN_CHARS_KEY: _as_int(
            data.get(ASR_MIN_CHARS_KEY, defaults[ASR_MIN_CHARS_KEY]),
            defaults[ASR_MIN_CHARS_KEY],
            minimum=1,
            maximum=20,
        ),
    }


def save_asr_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    current = load_asr_settings()
    incoming = dict(patch or {})
    for list_key in (ASR_EXTRA_WAKE_WORDS_KEY, ASR_GLOBAL_WAKE_WORDS_KEY):
        if list_key in incoming:
            incoming[list_key] = normalize_wake_word_list(incoming.get(list_key))
    current.update({k: incoming[k] for k in ASR_SETTING_KEYS if k in incoming})
    # Re-normalize via loader rules
    normalized = load_asr_settings(current)
    update_runtime_settings(normalized)
    return normalized


def resolve_wake_words(
    *,
    settings: Optional[Dict[str, Any]] = None,
    character: Optional[Dict[str, Any]] = None,
    global_keywords: Optional[Sequence[str]] = None,
) -> List[str]:
    cfg = load_asr_settings(settings)
    words: List[str] = []

    if cfg[ASR_USE_CHARACTER_WAKE_WORDS_KEY]:
        active = character
        if active is None:
            try:
                from modules.character_manager import character_manager

                active = character_manager.get_active_character()
            except Exception:
                active = None
        words.extend(collect_character_wake_words(active if isinstance(active, dict) else None))

    if cfg[ASR_INCLUDE_GLOBAL_WAKE_WORDS_KEY]:
        # Explicit override > runtime-editable list > config factory default.
        if global_keywords is not None:
            source = global_keywords
        else:
            source = cfg.get(ASR_GLOBAL_WAKE_WORDS_KEY) or default_global_wake_words()
        words.extend(normalize_wake_word_list(source))

    words.extend(normalize_wake_word_list(cfg.get(ASR_EXTRA_WAKE_WORDS_KEY)))
    return normalize_wake_word_list(words)


def text_contains_wake_word(text: str, wake_words: Iterable[str]) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    lowered = raw.casefold()
    for word in wake_words:
        token = str(word or "").strip()
        if not token:
            continue
        if token.casefold() in lowered:
            return True
    return False


def should_accept_voice_utterance(
    text: str,
    *,
    settings: Optional[Dict[str, Any]] = None,
    wake_words: Optional[Sequence[str]] = None,
    is_woken: bool = False,
    last_active_time: float = 0.0,
    now: Optional[float] = None,
    blacklist: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Pure decision helper for VoiceSensor (also used by tests)."""
    import time as _time

    cfg = load_asr_settings(settings)
    utterance = str(text or "").strip()
    min_chars = int(cfg[ASR_MIN_CHARS_KEY])
    if len(utterance) < min_chars:
        return {"accept": False, "reason": "too_short", "wake": False, "woken": is_woken}

    blocked = blacklist or ()
    if any(str(b) and str(b) in utterance for b in blocked):
        return {"accept": False, "reason": "blacklist", "wake": False, "woken": is_woken}

    words = list(wake_words) if wake_words is not None else resolve_wake_words(settings=cfg)
    has_wake = text_contains_wake_word(utterance, words)
    require_wake = bool(cfg[ASR_REQUIRE_WAKE_WORD_KEY])
    window = float(cfg[ASR_ACTIVE_WINDOW_SEC_KEY] or 0)
    ts = float(now if now is not None else _time.time())
    in_window = bool(is_woken and window > 0 and (ts - float(last_active_time or 0)) < window)

    if not require_wake:
        return {
            "accept": True,
            "reason": "free_listen",
            "wake": has_wake,
            "woken": True,
        }

    if has_wake:
        return {"accept": True, "reason": "wake_word", "wake": True, "woken": True}
    if in_window:
        return {"accept": True, "reason": "active_window", "wake": False, "woken": True}
    return {"accept": False, "reason": "not_woken", "wake": False, "woken": False}
