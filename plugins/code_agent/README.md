# 代码代理

`plugins/code_agent/` · 类型 `direct` · 触发词 `code_agent`

把代码项目分析或修改任务委托给本机 Codex CLI / Claude Code。修改模式必须确认。

> ⚠️ **安全审计风险标记**：`process_control`、`code_or_skill`  
> 见 `modules/plugin_security_audit.py`。 开放给 QQ 远程时会计入高风险清单。

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `code_agent` |
| LLM 指令 | `code_agent` |
| 插件类型 | `direct` |
| 别名 | `code_agent`、`codex`、`Codex`、`claude code`、`Claude Code`、`cc` |
| 超时 | 300 秒 |

示例：

```
[CMD: code_agent | analyze ||| codex_cli ||| . ||| 检查项目为什么启动失败]
```

## 权限

| 渠道 | 允许 |
| --- | --- |
| 本地桌面 | ✅ |
| QQ 远程 | ✅ |
| QQ 主人 | ✅ |
| QQ 他人 | ❌ |
| 群聊未 @ | ❌ |

## 可配置项

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `default_provider` | — | codex_cli | 未明确指定 Codex/Claude 时使用的外部代码代理。 |
| `default_cwd` | — | . | 自然语言未包含路径时使用的工作目录。 |
| `timeout_sec` | — | 300 | 外部代码代理单次执行超时秒数。 |

在 GUI「插件管理」里改，或直接编辑本目录的 `config.json`。

## 动作

取自源码里的 action/alias 映射表：

`analyze` `help` `modify` `status`

## 依赖与数据

- 宿主模块：`modules`、`services`

## 源码

约 348 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
