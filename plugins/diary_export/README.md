# 日记管家

`plugins/diary_export/` · 类型 `react` · 触发词 `diary_manager`

管理每日总结日记。功能：1.查询特定日期的日记(如:昨天干了什么)；2.导出所有日记为Markdown文件(如:导出日记)。

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `diary_manager` |
| LLM 指令 | 不暴露给 LLM |
| 插件类型 | `react` |
| 别名 | `check_history`、`check_diary`、`review_yesterday`、`export_diary`、`save_diary` |
| 超时 | 15.0 秒 |

## 权限

`config.json` 未声明 `access_control`，由 `PluginManager._normalize_access_control` 套用默认：本地 ✅ / QQ 远程 ✅ / QQ 主人 ✅ / QQ 他人 ❌ / 群聊未 @ ❌。

## 依赖与数据

- 宿主模块：`modules`

## 源码

约 111 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
