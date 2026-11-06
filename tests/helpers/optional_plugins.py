"""Skip tests only when an explicitly requested external plugin is absent."""

from pathlib import Path

import pytest


PLUGIN_ROOT = Path(__file__).resolve().parents[2] / "plugins"
CORE_PLUGINS = {"search", "web_reader", "memory_tools", "vision", "meme_pack"}


def require_optional_plugins(*names: str) -> None:
    for name in names:
        if name in CORE_PLUGINS:
            raise ValueError(f"Core plugin must not be skipped: {name}")
    missing = [name for name in names if not (PLUGIN_ROOT / name).exists()]
    if missing:
        pytest.skip(
            "Optional plugins not installed: " + ", ".join(missing),
            allow_module_level=True,
        )
