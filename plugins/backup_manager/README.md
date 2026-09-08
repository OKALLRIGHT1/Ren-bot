# 备份恢复

`plugins/backup_manager/` · 类型 `react` · 触发词 `backup_manager`

管理关键文件快照。参数格式：action ||| arg；action可用 create/list/restore。restore 需确认；仅本地或管理员私聊。

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `backup_manager` |
| LLM 指令 | `backup_manager` |
| 插件类型 | `react` |
| 别名 | `备份`、`恢复`、`快照`、`回滚快照` |

示例：

```
[CMD: backup_manager | create ||| before_codex_change]
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

`create` `list` `restore`

## 依赖与数据

- 涉及的数据路径字面量：`data/runtime_settings.json`、`userdata.db`

## 源码

约 145 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
