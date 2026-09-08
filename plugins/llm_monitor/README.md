# 模型监控

`plugins/llm_monitor/` · 类型 `react` · 触发词 `llm_monitor`

查看最近模型调用记录与成功率。参数格式：action ||| limit；action可用 recent/summary。

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `llm_monitor` |
| LLM 指令 | `llm_monitor` |
| 插件类型 | `react` |
| 别名 | `模型监控`、`模型状态`、`llm监控`、`成本监控` |

示例：

```
[CMD: llm_monitor | summary ||| 50]
```

## 权限

`config.json` 未声明 `access_control`，由 `PluginManager._normalize_access_control` 套用默认：本地 ✅ / QQ 远程 ✅ / QQ 主人 ✅ / QQ 他人 ❌ / 群聊未 @ ❌。

## 动作

取自源码里的 action/alias 映射表：

`recent`

## 依赖与数据

- 宿主模块：`modules`

## 源码

约 58 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
