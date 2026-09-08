# Agent 运行时

`plugins/agent_runtime/` · 类型 `direct` · 触发词 `agent_runtime`

查看轻量 AgentRuntime 的工具目录、MCP 状态，并生成受控能力安装计划。不会直接执行安装或写配置。

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `agent_runtime` |
| LLM 指令 | `agent_runtime` |
| 插件类型 | `direct` |
| 别名 | `agent 自检`、`agent 工具列表`、`agent 能力列表`、`agent 安装计划`、`agent 位置`、`代码在哪`、`项目目录在哪`、`agent runtime`、`agent tools`、`agent health` |
| 超时 | 6 秒 |

示例：

```
[CMD: agent_runtime | /agent 自检]
```

## 权限

| 渠道 | 允许 |
| --- | --- |
| 本地桌面 | ✅ |
| QQ 远程 | ✅ |
| QQ 主人 | ✅ |
| QQ 他人 | ❌ |
| 群聊未 @ | ❌ |

## 依赖与数据

- 宿主模块：`services`

## 源码

约 186 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
