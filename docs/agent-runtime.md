# Agent Runtime

主程序使用轻量 `AgentRuntime` 提供受控 agent 工具能力。

## 边界

- `ChatService` 仍负责对话主流程、UI、TTS、QQ 回发和记忆。
- `PluginManager` 仍负责加载插件、权限、超时和旧工具执行。
- `AgentRuntime` 负责 direct 工具入口、确认状态和统一工具目录。
- `ActionGate` 负责安装、写配置、发邮件、删除、代码执行等高风险操作的确认策略。
- `PendingConfirmStore`（`services/security/pending_confirm.py`）保存待确认动作；用户回复「确认」后复跑并注入 `action_confirmed=true`。
- **本地**高风险确认优先走 Qt 弹窗（`PluginManager.local_confirm_handler` → `QtChatTrayApp.request_action_confirm`）；弹窗确认后当场放行，无需再打字「确认」。
- **远程 QQ** 仍用聊天「确认/取消」，不弹本机窗。
- `CapabilityManager` 负责自检和能力安装计划；第一版不直接安装。
- `code_executor` / `code_agent` 均委托本机 Codex CLI / Claude Code，并经 ActionGate（`system.exec_code` / `system.code_agent`）。

## 工具来源

- 插件工具来自 `PluginToolProvider`。
- MCP 工具来自 `McpToolProvider`，底层仍使用现有 `MCPToolBridge`。
- `mcp_tools` 插件的 allowlist 和 delegate-only 策略继续保留。

## Agent Mail

`plugins/agently_mail` 通过本机 `agently-cli` 操作邮箱。

默认 CLI：

```text
C:\Users\Gengar\AppData\Roaming\npm\agently-cli.cmd
```

可用说法：

```text
我最近收到了哪些邮件？
搜索邮件 账单
读邮件 msg_xxx
发邮件 to=a@example.com subject=主题 body=正文
回邮件 id=msg_xxx body=回复正文
转发邮件 id=msg_xxx to=a@example.com body=供参考
删除邮件 id=msg_xxx
```

读操作可以直接执行。发、回、转、删必须先返回确认摘要，再由用户回复 `确认` 执行，回复 `取消` 放弃。

## 自检和自扩展

自检可以直接运行，例如检查工具目录、MCP server 状态、插件状态和 CLI 授权状态。

安装新功能、写配置、启用能力、发邮件、删除数据都必须先返回计划和确认摘要。用户回复 `确认` 后才允许执行。

本地主程序可用入口：

```text
/agent 自检
/agent 工具列表
/agent 安装计划 weather
```

`/agent 自检` 和 `/agent 工具列表` 可由本地入口或 QQ 主人触发；QQ 群聊仍需要 @ 机器人。
`/agent 安装计划 <name>` 只生成计划，不执行命令、不安装依赖、不写配置。
除 Agent Mail 这种明确自然语言意图插件外，direct 插件统一使用 `/命令` 触发，避免普通聊天误触。

## 验证

目标测试：

```powershell
python -m pytest tests/test_agent_runtime.py tests/test_agently_mail_plugin.py tests/test_agent_runtime_plugin.py tests/test_plugin_tool_metadata.py tests/test_agent_tool_providers.py tests/test_action_gate.py tests/test_capability_manager.py tests/test_agent_adapters.py tests/test_plugin_access_control.py -q
```

只读 CLI 冒烟：

```powershell
& 'C:\Users\Gengar\AppData\Roaming\npm\agently-cli.cmd' +me
& 'C:\Users\Gengar\AppData\Roaming\npm\agently-cli.cmd' message +list --limit 3
```

## 未来外部框架

如果以后接 OpenAI Agents SDK 或其他外部 agent 框架，只新增 `AgentAdapter` 实现，输入为 `AgentRuntime.list_tools()`，输出为 `{handled, reply, meta}`。不要让外部框架直接控制 Live2D、TTS、QQ 网关或久坐提醒。
