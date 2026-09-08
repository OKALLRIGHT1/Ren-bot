# 记忆与画像查询

`plugins/memory_tools/` · 类型 `delegate` · 触发词 `memory_tools`

查询长期记忆、人物画像和本地活动统计。参数：query/profile/activity ||| 自然语言问题

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `memory_tools` |
| LLM 指令 | `memory_tools` |
| 插件类型 | `delegate` |
| 别名 | `/memory`、`/记忆` |
| 超时 | 30 秒 |

示例：

```
[CMD: memory_tools | query ||| 我上次提过的项目是什么]
```

## 权限

| 渠道 | 允许 |
| --- | --- |
| 本地桌面 | ✅ |
| QQ 远程 | ✅ |
| QQ 主人 | ✅ |
| QQ 他人 | ❌ |
| 群聊未 @ | ❌ |

## 动作

取自源码里的 action/alias 映射表：

`activity` `profile` `query`

## 依赖与数据

- 宿主模块：`modules`、`services`

## 源码

约 164 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
