"""External code-agent adapters."""

from .discovery import build_command_template, discover_agent_command
from .external_agent import CodeAgentRequest, CodeAgentResult, run_code_agent

__all__ = [
    "CodeAgentRequest",
    "CodeAgentResult",
    "build_command_template",
    "discover_agent_command",
    "run_code_agent",
]
