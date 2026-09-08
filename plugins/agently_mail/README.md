# Agent Mail

`plugins/agently_mail/` · 类型 `direct` · 触发词 `agently_mail`

通过本机 agently-cli 查询、读取、搜索和按确认流程发送邮件；支持新邮件 QQ 私聊通知。

> ⚠️ **安全审计风险标记**：`mail`  
> 见 `modules/plugin_security_audit.py`。 开放给 QQ 远程时会计入高风险清单。

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `agently_mail` |
| LLM 指令 | `agently_mail` |
| 插件类型 | `direct` |
| 别名 | `agently_mail`、`邮件`、`邮箱`、`收件箱`、`最近邮件`、`查邮件`、`读邮件`、`发邮件`、`回邮件`、`转发邮件`、`测试邮件通知`、`邮件通知测试` |
| 超时 | 150 秒 |

示例：

```
[CMD: agently_mail | 最近邮件]
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
| `model_queue` | model_queue | 空 | 邮件意图模型（按顺序尝试） |
| `cli_path` | text | C:\Users\Gengar\AppData\Roaming\npm\a… | agently-cli 路径 |
| `default_limit` | number | 10 | 默认邮件数量 |
| `max_body_chars` | number | 2400 | 正文最大展示字符 |
| `request_timeout_sec` | number | 20 | CLI 超时秒数 |
| `intent_timeout_sec` | number | 120 | 草稿生成超时秒数 |
| `enable_notifications` | bool | True | 启用新邮件通知 |
| `notification_check_interval_sec` | number | 120 | 通知检查间隔(秒) |
| `notification_target_sessions` | list | 1 项 | 通知推送目标会话 |
| `notification_character_name` | choice | 丰川祥子 | 通知角色 |
| `notification_poll_limit` | number | 10 | 轮询检查数量 |
| `notification_body_chars` | number | 1600 | 通知正文图片字数 |
| `seen_state_path` | text | data/agently_mail_seen.json | 已读通知状态文件 |

在 GUI「插件管理」里改，或直接编辑本目录的 `config.json`。

## 动作

取自源码里的 action/alias 映射表：

`forward` `list` `me` `read` `reply` `search` `send` `trash`

## 运行时

- 会 `asyncio.create_task` 起后台任务。
- 生命周期钩子：`start` 有、`stop` 有。

## 依赖与数据

- 第三方包：`PIL`
- 宿主模块：`config`、`core`、`modules`、`services`
- 涉及的数据路径字面量：`data/agently_mail_seen.json`

## 源码

约 1797 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
