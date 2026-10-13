"""ASR / microphone input settings and wake-word resolution.

Migrated from ``modules/asr_settings.py``.  External dependencies are now
injected: settings persistence goes through an optional
:class:`~modules.asr.ports.SettingsStore` (defaults to an in-memory dict),
and character wake-word source through an optional
:class:`~modules.asr.ports.CharacterProvider` (defaults to ``None``).
This keeps the module free of host-project imports while preserving every
function signature and runtime key the callers rely on.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

from .config import AsrConfig
from .ports import CharacterProvider, LoggerPort, SettingsStore

# Runtime setting keys (unchanged from the legacy module)
ASR_REQUIRE_WAKE_WORD_KEY = "asr_require_wake_word"
ASR_USE_CHARACTER_WAKE_WORDS_KEY = "asr_use_character_wake_words"
ASR_INCLUDE_GLOBAL_WAKE_WORDS_KEY = "asr_include_global_wake_words"
ASR_GLOBAL_WAKE_WORDS_KEY = "asr_global_wake_words"
ASR_EXTRA_WAKE_WORDS_KEY = "asr_extra_wake_words"
ASR_ACTIVE_WINDOW_SEC_KEY = "asr_active_window_sec"
ASR_MIN_CHARS_KEY = "asr_min_chars"
ASR_ENGINE_KEY = "asr_engine"
ASR_HOTWORDS_FROM_WAKE_KEY = "asr_hotwords_from_wake"

ASR_ENGINE_QWEN3 = "qwen3"
ASR_ENGINE_ZIPFORMER = "zipformer"
ASR_ENGINE_CHOICES = (ASR_ENGINE_QWEN3, ASR_ENGINE_ZIPFORMER)

ASR_SETTING_KEYS = (
    ASR_REQUIRE_WAKE_WORD_KEY,
    ASR_USE_CHARACTER_WAKE_WORDS_KEY,
    ASR_INCLUDE_GLOBAL_WAKE_WORDS_KEY,
    ASR_GLOBAL_WAKE_WORDS_KEY,
    ASR_EXTRA_WAKE_WORDS_KEY,
    ASR_ACTIVE_WINDOW_SEC_KEY,
    ASR_MIN_CHARS_KEY,
    ASR_ENGINE_KEY,
    ASR_HOTWORDS_FROM_WAKE_KEY,
)

# Process-wide singletons injected by the host (adapter) or built lazily.
# A module-level store keeps the legacy ``load_asr_settings()`` / ``resolve_*``
# call sites working without threading the store through every signature.
_SETTINGS_STORE: Optional[SettingsStore] = None
_CHARACTER_PROVIDER: Optional[CharacterProvider] = None
_LOGGER: Optional[LoggerPort] = None


def configure(
    *,
    settings_store: Optional[SettingsStore] = None,
    character_provider: Optional[CharacterProvider] = None,
    logger: Optional[LoggerPort] = None,
) -> None:
    """Inject host-side dependencies.  Idempotent; safe to call repeatedly."""
    global _SETTINGS_STORE, _CHARACTER_PROVIDER, _LOGGER
    if settings_store is not None:
        _SETTINGS_STORE = settings_store
    if character_provider is not None:
        _CHARACTER_PROVIDER = character_provider
    if logger is not None:
        _LOGGER = logger


class _MemorySettingsStore:
    """In-memory fallback when no host store is injected (standalone mode)."""

    def __init__(self) -> None:
        self._data: Dict[str, Any] = {}

    def load(self) -> Dict[str, Any]:
        return dict(self._data)

    def save(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        self._data.update(patch or {})
        return dict(self._data)


def _resolve_store() -> SettingsStore:
    global _SETTINGS_STORE
    if _SETTINGS_STORE is None:
        try:
            from .adapter import project_settings_store

            _SETTINGS_STORE = project_settings_store()
        except Exception:
            _SETTINGS_STORE = _MemorySettingsStore()
    return _SETTINGS_STORE


def _resolve_character_provider(
    character_provider: Optional[CharacterProvider] = None,
) -> Optional[CharacterProvider]:
    global _CHARACTER_PROVIDER
    if character_provider is not None:
        return character_provider
    if _CHARACTER_PROVIDER is not None:
        return _CHARACTER_PROVIDER
    try:
        from .adapter import project_character_provider

        _CHARACTER_PROVIDER = project_character_provider()
    except Exception:
        pass
    return _CHARACTER_PROVIDER


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


def _as_engine(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in ASR_ENGINE_CHOICES:
        return text
    return ASR_ENGINE_ZIPFORMER


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
    if " " in text or "·" in text or "・" in text:
        for sep in (" ", "·", "・"):
            if sep in text:
                head = text.split(sep, 1)[0].strip()
                if head:
                    variants.append(head)
                break
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


def default_global_wake_words(config: Optional[AsrConfig] = None) -> List[str]:
    cfg = config
    if cfg is None:
        try:
            from .adapter import build_asr_config

            cfg = build_asr_config()
        except Exception:
            pass
    return normalize_wake_word_list(cfg.wake_keywords if cfg else ["五十铃", "怜", "Suzu", "助手"])


def default_asr_settings(config: Optional[AsrConfig] = None) -> Dict[str, Any]:
    cfg = config
    if cfg is None:
        try:
            from .adapter import build_asr_config

            cfg = build_asr_config()
        except Exception:
            cfg = AsrConfig()
    return {
        ASR_REQUIRE_WAKE_WORD_KEY: cfg.require_wake_word,
        ASR_USE_CHARACTER_WAKE_WORDS_KEY: cfg.use_character_wake_words,
        ASR_INCLUDE_GLOBAL_WAKE_WORDS_KEY: cfg.include_global_wake_words,
        ASR_GLOBAL_WAKE_WORDS_KEY: normalize_wake_word_list(cfg.wake_keywords),
        ASR_EXTRA_WAKE_WORDS_KEY: [],
        ASR_ACTIVE_WINDOW_SEC_KEY: int(cfg.active_window_sec or 20),
        ASR_MIN_CHARS_KEY: int(cfg.min_chars or 2),
        ASR_ENGINE_KEY: cfg.engine,
        ASR_HOTWORDS_FROM_WAKE_KEY: cfg.hotwords_from_wake,
    }


def load_asr_settings(
    runtime: Optional[Dict[str, Any]] = None,
    *,
    config: Optional[AsrConfig] = None,
) -> Dict[str, Any]:
    data = runtime if isinstance(runtime, dict) else _resolve_store().load()
    defaults = default_asr_settings(config)
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
        ASR_ENGINE_KEY: _as_engine(
            data.get(ASR_ENGINE_KEY, defaults[ASR_ENGINE_KEY])
        ),
        ASR_HOTWORDS_FROM_WAKE_KEY: _as_bool(
            data.get(
                ASR_HOTWORDS_FROM_WAKE_KEY,
                defaults[ASR_HOTWORDS_FROM_WAKE_KEY],
            ),
            defaults[ASR_HOTWORDS_FROM_WAKE_KEY],
        ),
    }


def save_asr_settings(
    patch: Dict[str, Any],
    *,
    config: Optional[AsrConfig] = None,
) -> Dict[str, Any]:
    current = load_asr_settings(config=config)
    incoming = dict(patch or {})
    for list_key in (ASR_EXTRA_WAKE_WORDS_KEY, ASR_GLOBAL_WAKE_WORDS_KEY):
        if list_key in incoming:
            incoming[list_key] = normalize_wake_word_list(incoming.get(list_key))
    current.update({k: incoming[k] for k in ASR_SETTING_KEYS if k in incoming})
    normalized = load_asr_settings(current, config=config)
    _resolve_store().save(normalized)
    return normalized


def resolve_wake_words(
    *,
    settings: Optional[Dict[str, Any]] = None,
    character: Optional[Dict[str, Any]] = None,
    global_keywords: Optional[Sequence[str]] = None,
    config: Optional[AsrConfig] = None,
    character_provider: Optional[CharacterProvider] = None,
) -> List[str]:
    cfg = load_asr_settings(settings, config=config)
    words: List[str] = []

    if cfg[ASR_USE_CHARACTER_WAKE_WORDS_KEY]:
        active = character
        if active is None:
            provider = _resolve_character_provider(character_provider)
            if provider is not None:
                try:
                    active = provider.get_active_character()
                except Exception:
                    active = None
        words.extend(collect_character_wake_words(active if isinstance(active, dict) else None))

    if cfg[ASR_INCLUDE_GLOBAL_WAKE_WORDS_KEY]:
        if global_keywords is not None:
            source = global_keywords
        else:
            source = cfg.get(ASR_GLOBAL_WAKE_WORDS_KEY) or default_global_wake_words(config)
        words.extend(normalize_wake_word_list(source))

    words.extend(normalize_wake_word_list(cfg.get(ASR_EXTRA_WAKE_WORDS_KEY)))
    return normalize_wake_word_list(words)


def format_asr_hotwords(words: Iterable[str]) -> str:
    """Sherpa Qwen3 hotwords: '五十铃/怜/Suzu'."""
    from .engine import format_hotwords

    return format_hotwords(list(words or []))


def resolve_asr_hotwords(
    *,
    settings: Optional[Dict[str, Any]] = None,
    wake_words: Optional[Sequence[str]] = None,
    config: Optional[AsrConfig] = None,
    character_provider: Optional[CharacterProvider] = None,
) -> str:
    cfg = load_asr_settings(settings, config=config)
    if not cfg.get(ASR_HOTWORDS_FROM_WAKE_KEY, True):
        return ""
    provider = _resolve_character_provider(character_provider)
    words = (
        list(wake_words)
        if wake_words is not None
        else resolve_wake_words(settings=cfg, config=config, character_provider=provider)
    )
    return format_asr_hotwords(words)


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
    config: Optional[AsrConfig] = None,
) -> Dict[str, Any]:
    """Pure decision helper for VoiceSensor (also used by tests)."""
    import time as _time

    cfg = load_asr_settings(settings, config=config)
    utterance = str(text or "").strip()
    min_chars = int(cfg[ASR_MIN_CHARS_KEY])
    if len(utterance) < min_chars:
        return {"accept": False, "reason": "too_short", "wake": False, "woken": is_woken}

    blocked = blacklist or ()
    if any(str(b) and str(b) in utterance for b in blocked):
        return {"accept": False, "reason": "blacklist", "wake": False, "woken": is_woken}

    words = list(wake_words) if wake_words is not None else resolve_wake_words(settings=cfg, config=config)
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
