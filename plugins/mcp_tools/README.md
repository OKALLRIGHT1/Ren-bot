# MCP 工具桥

`plugins/mcp_tools/` · 类型 `delegate` · 触发词 `mcp_tools`

调用已接入的本地/远程 MCP 工具。参数格式：action ||| tool_name ||| JSON参数；action 可用：help, list_tools, server_status, call_tool

> ⚠️ **安全审计风险标记**：`code_or_skill`  
> 见 `modules/plugin_security_audit.py`。 开放给 QQ 远程时会计入高风险清单。

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `mcp_tools` |
| LLM 指令 | `mcp_tools` |
| 插件类型 | `delegate` |
| 别名 | `mcp`、`mcp工具`、`远程工具`、`外部工具桥` |
| 超时 | 6 秒 |

示例：

```
[CMD: mcp_tools | list_tools]
```

## 权限

| 渠道 | 允许 |
| --- | --- |
| 本地桌面 | ✅ |
| QQ 远程 | ❌ |
| QQ 主人 | ❌ |
| QQ 他人 | ❌ |
| 群聊未 @ | ❌ |

## 可配置项

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `allow_all_local_tools` | bool | True | 允许全部本地 MCP 工具 |
| `allow_all_remote_tools` | bool | False | 允许全部远程 MCP 工具 |
| `allowed_server_names` | list | 空 | 允许的远程 MCP server |
| `allowed_tool_names` | list | 空 | 允许的 MCP 工具名 |
| `intent_route_enabled` | bool | True | 自然语言路由开关 |
| `intent_route_brand_keywords` | list | 4 项 | 品牌/领域关键词 |
| `intent_route_action_keywords` | list | 14 项 | 动作关键词 |
| `intent_route_web_search_override_keywords` | list | 7 项 | 显式联网搜索关键词 |

在 GUI「插件管理」里改，或直接编辑本目录的 `config.json`。

## 动作

取自 `description` 声明：

`help` `list_tools` `server_status` `call_tool`

## 依赖与数据

- 宿主模块：`services`

## 源码

约 290 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
