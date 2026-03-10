import asyncio
import json
from typing import Any, Dict, List


class Plugin:
    name = "MCP 工具桥"
    type = "react"
    description = "调用已接入的本地/远程 MCP 工具。参数格式：action ||| tool_name ||| JSON参数。"
    example_arg = "list_tools"

    async def run(self, args: str, ctx: Dict[str, Any]) -> str:
        action, parts = self._parse_args(args or "")
        if action in {"", "help"}:
            return self._help_text()

        bridge = self._get_bridge(ctx)
        if bridge is None:
            return "MCP bridge 未初始化。"

        action = action.lower().strip()
        if action == "list_tools":
            return self._format_tools(bridge.list_tools())
        if action == "server_status":
            return self._format_server_status(bridge.list_server_status())
        if action == "call_tool":
            if not parts:
                return "call_tool ?????? tool_name?\n\n" + self._help_text()
            tool_name = str(parts[0] or "").strip()
            if not tool_name:
                return "tool_name ?????"
            arguments = self._parse_json_arguments(parts[1] if len(parts) >= 2 else "")
            try:
                result = await bridge.call_tool(tool_name, arguments=arguments)
            except asyncio.CancelledError:
                return "MCP ??????????????? server_status ???????"
            except Exception as exc:
                return f"MCP ???????{exc}"
            return self._format_call_result(tool_name, result)

        return f"不支持的 action: {action}\n\n{self._help_text()}"

    def _get_bridge(self, ctx: Dict[str, Any]):
        bridge = ctx.get("mcp_bridge") if isinstance(ctx, dict) else None
        if bridge is not None:
            return bridge
        chat_service = ctx.get("chat_service") if isinstance(ctx, dict) else None
        return getattr(chat_service, "mcp_bridge", None)

    def _parse_args(self, raw: str):
        parts = [p.strip() for p in str(raw or "").split("|||")]
        if not parts:
            return "", []
        return parts[0].strip(), parts[1:]

    def _parse_json_arguments(self, raw: str) -> Dict[str, Any]:
        text = str(raw or "").strip()
        if not text:
            return {}
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("JSON 参数必须是对象，例如 {\"path\": \"README.md\"}")
        return data

    def _format_tools(self, specs: List[Any]) -> str:
        if not specs:
            return "当前没有可用的 MCP 工具。"
        lines = []
        for spec in specs:
            name = str(getattr(spec, "name", "") or "")
            provider = str(getattr(spec, "provider", "") or "")
            desc = str(getattr(spec, "description", "") or "").replace("\n", " ").strip()
            prefix = f"[{provider}] " if provider else ""
            lines.append(f"- {prefix}{name}: {desc or '无描述'}")
        return "可用 MCP 工具：\n" + "\n".join(lines)

    def _format_server_status(self, items: List[Dict[str, Any]]) -> str:
        if not items:
            return "当前没有配置远程 MCP 服务器。"
        lines = []
        for item in items:
            name = str(item.get("name") or "")
            transport = str(item.get("transport") or "")
            enabled = bool(item.get("enabled", False))
            connected = bool(item.get("connected", False))
            tool_count = int(item.get("tool_count") or 0)
            error = str(item.get("error") or "").strip()
            state = "已连接" if connected else ("已禁用" if not enabled else "未连接")
            line = f"- {name} ({transport}): {state}，工具 {tool_count} 个"
            if error:
                line += f"，错误：{error}"
            lines.append(line)
        return "MCP 服务器状态：\n" + "\n".join(lines)

    def _format_call_result(self, tool_name: str, result: Any) -> str:
        if isinstance(result, list):
            return f"MCP 工具 `{tool_name}` 返回：\n{json.dumps(result, ensure_ascii=False, indent=2)}"
        if isinstance(result, dict):
            is_error = bool(result.get("isError", False))
            content = result.get("content") or []
            text_parts = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type") or "").strip().lower()
                if item_type == "text":
                    text = str(item.get("text") or "").strip()
                    if text:
                        text_parts.append(text)
                elif item_type == "image":
                    text_parts.append("[图片结果]")
                elif item_type == "embeddedresource":
                    text_parts.append("[资源结果]")
                elif item_type:
                    text_parts.append(f"[{item_type}]")
            structured = result.get("structuredContent")
            if structured not in (None, {}, []):
                text_parts.append(json.dumps(structured, ensure_ascii=False, indent=2))
            if not text_parts:
                text_parts.append(json.dumps(result, ensure_ascii=False, indent=2))
            prefix = "MCP 工具返回错误：" if is_error else f"MCP 工具 `{tool_name}` 返回："
            return prefix + "\n" + "\n".join(text_parts)
        return f"MCP 工具 `{tool_name}` 返回：\n{result}"

    def _help_text(self) -> str:
        return (
            "mcp_tools 用法：\n"
            "- [CMD: mcp_tools | list_tools]\n"
            "- [CMD: mcp_tools | server_status]\n"
            "- [CMD: mcp_tools | call_tool ||| mcp.server.tool ||| {\"key\": \"value\"}]\n"
            "说明：远程工具名统一使用 mcp.<server>.<tool> 格式。"
        )
