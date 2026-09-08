# 副脑任务状态

`plugins/delegate_status/` · 类型 `direct` · 触发词 `delegate_status`

查看最近的副脑委托任务，或根据任务ID查看详情。

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `delegate_status` |
| LLM 指令 | `delegate_status` |
| 插件类型 | `direct` |
| 别名 | `副脑任务`、`副脑状态`、`委托任务`、`委托状态`、`最近副脑任务`、`最近委托任务` |
| 超时 | 6 秒 |

示例：

```
[CMD: delegate_status | 副脑任务]
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

- 宿主模块：`modules`

## 源码

约 101 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
