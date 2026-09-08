"""Dependency-injection ports for the standalone ASR package.

These Protocols let :mod:`modules.asr` stay free of any project imports
(``config``, ``core.logger``, ``modules.runtime_settings``,
``modules.character_manager``, ``services.runtime_health``).  The host
project wires concrete implementations through :mod:`modules.asr.adapter`;
in standalone mode callers pass ``None`` and sensible in-memory defaults
take over.

The shape mirrors the project's existing ``services/plugin_ports`` pattern:
inject optional callables/providers, guard against ``None`` at use sites.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Protocol, runtime_checkable

__all__ = [
    "LoggerPort",
    "SettingsStore",
    "CharacterProvider",
    "HealthHook",
    "NullHealthHook",
    "default_logger",
]


@runtime_checkable
class LoggerPort(Protocol):
    """Minimal logger surface.

    Both stdlib :class:`logging.Logger` and the project's ``AppLogger``
    satisfy this contract (methods accept ``*args, **kwargs``).
    """

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None: ...

    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None: ...

    def error(self, msg: str, *args: Any, **kwargs: Any) -> None: ...

    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None: ...


@runtime_checkable
class SettingsStore(Protocol):
    """Persistent key/value settings backend.

    Wraps ``modules.runtime_settings`` on the project side; in standalone
    mode an in-memory dict-backed store is used so the package works
    out-of-the-box without any disk path.
    """

    def load(self) -> Dict[str, Any]: ...

    def save(self, patch: Dict[str, Any]) -> Dict[str, Any]: ...


@runtime_checkable
class CharacterProvider(Protocol):
    """Active-character source for character-derived wake words.

    Wraps ``modules.character_manager`` on the project side.  The
    returned dict only needs ``name`` (and optionally ``aliases``,
    ``qq_profile``) for wake-word expansion; see
    :func:`modules.asr.settings.collect_character_wake_words`.
    """

    def get_active_character(self) -> Optional[Dict[str, Any]]: ...


@runtime_checkable
class HealthHook(Protocol):
    """Best-effort runtime-health reporter; fire-and-forget, never raises."""

    def report(self, component: str, state: str, summary: str) -> None: ...


class NullHealthHook:
    """No-op health hook used when the host does not provide one."""

    def report(self, component: str, state: str, summary: str) -> None:
        return None


def default_logger() -> logging.Logger:
    """Stdlib logger used when the host does not inject one."""
    return logging.getLogger("asr")
