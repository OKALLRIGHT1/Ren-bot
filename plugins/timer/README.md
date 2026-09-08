# 番茄钟/倒计时

`plugins/timer/` · 类型 `react` · 触发词 `timer`

当用户要求倒计时、专注、番茄钟时使用。参数为分钟数(数字)。

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `timer` |
| LLM 指令 | `timer` |
| 插件类型 | `react` |
| 别名 | `倒计时`、`番茄钟`、`番茄`、`闹钟`、`定时`、`计时` |
| 超时 | 360 秒 |
| 默认启用 | 是 |

示例：

```
[CMD: timer | 25]
```

## 权限

`config.json` 未声明 `access_control`，由 `PluginManager._normalize_access_control` 套用默认：本地 ✅ / QQ 远程 ✅ / QQ 主人 ✅ / QQ 他人 ❌ / 群聊未 @ ❌。

## 运行时

- 会 `asyncio.create_task` 起后台任务。
- 生命周期钩子：`start` 无、`stop` 有。

## 依赖与数据

- 宿主模块：`core`、`plugins`、`services`

## 源码

约 128 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
## 倒计时任务必须被持有引用

`run()` 里 `asyncio.create_task` 起的倒计时是 fire-and-forget。
如果不保留强引用，任务可能在 `await asyncio.sleep()` 期间被 GC 回收，倒计时静默失效。
所以有 `self._pending` 这个集合 + `add_done_callback(self._pending.discard)` 自动清理。

`stop()` 是生命周期钩子，宿主关闭/热重载时取消未完成的倒计时。
加倒计时相关功能时，新起的 task 也要进 `_pending`。
