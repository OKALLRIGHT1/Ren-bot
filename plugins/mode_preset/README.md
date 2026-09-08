# 模式预设

`plugins/mode_preset/` · 类型 `react` · 触发词 `mode_preset`

切换助手工作模式。参数格式：action ||| name；action可用 list/status/apply。name可用 companion/codex/quiet/eco。

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `mode_preset` |
| LLM 指令 | `mode_preset` |
| 插件类型 | `react` |
| 别名 | `模式`、`预设`、`切换模式`、`助手模式` |

示例：

```
[CMD: mode_preset | apply ||| codex]
```

## 权限

`config.json` 未声明 `access_control`，由 `PluginManager._normalize_access_control` 套用默认：本地 ✅ / QQ 远程 ✅ / QQ 主人 ✅ / QQ 他人 ❌ / 群聊未 @ ❌。

## 动作

取自源码里的 action/alias 映射表：

`apply` `list` `status`

## 依赖与数据

- 宿主模块：`config`、`modules`、`services`

## 源码

约 151 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
