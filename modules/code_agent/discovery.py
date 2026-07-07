from __future__ import annotations

import shutil
from pathlib import Path
from typing import Iterable, Optional


AGENT_EXECUTABLES = {
    "codex_cli": ("codex", "codex.cmd", "codex.exe"),
    "claude_code": ("claude", "claude.cmd", "claude.exe"),
}


def _quote_if_needed(path: str) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    if any(ch.isspace() for ch in text):
        return f'"{text}"'
    return text


def build_command_template(executable: str, provider: str) -> str:
    exe = _quote_if_needed(executable)
    if not exe:
        return ""
    if provider == "codex_cli":
        return f"{exe} exec {{prompt_stdin}}"
    if provider == "claude_code":
        return f"{exe} -p {{prompt_stdin}}"
    if provider == "custom_cli":
        return f"{exe} {{prompt}}"
    return exe


def find_executable(candidates: Iterable[str]) -> Optional[str]:
    for name in candidates:
        found = shutil.which(str(name or "").strip())
        if found:
            return str(Path(found))
    return None


def discover_agent_command(provider: str) -> str:
    candidates = AGENT_EXECUTABLES.get(str(provider or "").strip())
    if not candidates:
        return ""
    executable = find_executable(candidates)
    return build_command_template(executable, provider) if executable else ""
