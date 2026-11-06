"""Compatibility import; install the optional mcp_tools plugin for MCP support.

The protocol implementation and its dependency now live in that plugin.
"""
import importlib
import sys

try:
    _implementation = importlib.import_module("plugins.mcp_tools.bridge")
except ModuleNotFoundError as exc:
    if exc.name in {"plugins.mcp_tools", "plugins.mcp_tools.bridge"}:
        raise ModuleNotFoundError("MCP 已迁移到可选 mcp_tools 插件，请从 Ren-bot-plugin 安装。") from exc
    raise

sys.modules[__name__] = _implementation
