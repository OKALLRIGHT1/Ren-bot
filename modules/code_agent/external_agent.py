from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple


MAX_OUTPUT_CHARS = 6000
FORBIDDEN_TEMPLATE_PARTS = ("|", "&", ";", ">", "<", "`", "$(", "\n", "\r")


@dataclass(frozen=True)
class CodeAgentRequest:
    provider: str
    prompt: str
    cwd: str
    command_template: str
    timeout_sec: int
    allow_write: bool
    allow_exec: bool
    task_id: str


@dataclass(frozen=True)
class CodeAgentResult:
    ok: bool
    stdout: str
    stderr: str
    exit_code: int
    duration_sec: float
    command_preview: str


def _trim_output(text: str, max_chars: int = MAX_OUTPUT_CHARS) -> str:
    text = str(text or "")
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[truncated]"


def _resolve_cwd(raw_cwd: str) -> str:
    raw = str(raw_cwd or "").strip()
    path = Path(raw).expanduser() if raw else Path.cwd()
    if path.is_file():
        path = path.parent
    path = path.resolve()
    if not path.exists() or not path.is_dir():
        raise ValueError(f"工作目录不存在或不是目录: {path}")
    return str(path)


def _split_template(command_template: str) -> List[str]:
    template = str(command_template or "").strip()
    if not template:
        raise ValueError("外部代码代理命令模板不能为空")
    for token in FORBIDDEN_TEMPLATE_PARTS:
        if token in template:
            raise ValueError(f"命令模板包含不允许的 shell 元字符: {token}")
    try:
        parts = shlex.split(template, posix=(os.name != "nt"))
    except ValueError as exc:
        raise ValueError(f"命令模板解析失败: {exc}") from exc
    if os.name == "nt":
        parts = [
            item[1:-1] if len(item) >= 2 and item[0] == item[-1] and item[0] in ("'", '"') else item
            for item in parts
        ]
    return parts


def _build_argv(request: CodeAgentRequest, cwd: str) -> Tuple[List[str], bool]:
    argv = _split_template(request.command_template)
    built: List[str] = []
    prompt_stdin = False
    for item in argv:
        if "{prompt}" in item and item != "{prompt}":
            raise ValueError("{prompt} 必须作为独立参数使用，不能拼在其他参数里")
        if "{prompt_stdin}" in item and item != "{prompt_stdin}":
            raise ValueError("{prompt_stdin} 必须作为独立参数使用，不能拼在其他参数里")
        if item == "{prompt_stdin}":
            prompt_stdin = True
            continue
        built.append(
            item.replace("{prompt}", request.prompt).replace("{cwd}", cwd)
        )
    if not built:
        raise ValueError("外部代码代理命令为空")
    return built, prompt_stdin


def _command_preview(argv: List[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


async def run_code_agent(request: CodeAgentRequest) -> CodeAgentResult:
    if not request.allow_exec:
        raise PermissionError("外部代码代理需要先勾选“允许执行命令”")

    cwd = _resolve_cwd(request.cwd)
    argv, prompt_stdin = _build_argv(request, cwd)
    preview = _command_preview(argv)
    timeout_sec = max(5, min(3600, int(request.timeout_sec or 300)))
    started = time.monotonic()

    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        stdin=asyncio.subprocess.PIPE if prompt_stdin else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        prompt_b = request.prompt.encode("utf-8", errors="replace") if prompt_stdin else None
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(prompt_b), timeout=timeout_sec)
        exit_code = int(proc.returncode or 0)
    except asyncio.TimeoutError:
        proc.kill()
        stdout_b, stderr_b = await proc.communicate()
        exit_code = -1
        stderr_b = (stderr_b or b"") + f"\n执行超时: {timeout_sec}s".encode("utf-8", errors="ignore")

    duration_sec = time.monotonic() - started
    stdout = _trim_output(stdout_b.decode("utf-8", errors="replace"))
    stderr = _trim_output(stderr_b.decode("utf-8", errors="replace"))
    return CodeAgentResult(
        ok=exit_code == 0,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        duration_sec=duration_sec,
        command_preview=preview,
    )
