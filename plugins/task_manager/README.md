# 任务中枢

`plugins/task_manager/` · 类型 `react` · 触发词 `task`

任务与主动建议中枢。支持：add_task / list_tasks / complete_task / delete_task / today_schedule / check_tasks / daily_summary / suggest / check_overdue / check_urgent / start / stop / status。

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `task` |
| LLM 指令 | `task_manager` |
| 插件类型 | `react` |
| 别名 | `任务`、`待办`、`todo`、`任务提醒`、`schedule`、`计划`、`agenda`、`任务建议`、`任务摘要`、`逾期任务`、`紧急任务` |

示例：

```
[CMD: task_manager | check_tasks]
```

## 权限

`config.json` 未声明 `access_control`，由 `PluginManager._normalize_access_control` 套用默认：本地 ✅ / QQ 远程 ✅ / QQ 主人 ✅ / QQ 他人 ❌ / 群聊未 @ ❌。

## 动作

取自 `description` 声明：

`add_task` `list_tasks` `complete_task` `delete_task` `today_schedule` `check_tasks` `daily_summary` `suggest` `check_overdue` `check_urgent` `start` `stop` `status`

## 运行时

- 使用 `modules/plugin_scheduler.py` 的 `PeriodicTask` 跑后台循环（循环/退避/取消由 helper 统一负责）。
- 生命周期钩子：`start` 有、`stop` 有。
- 以下 async 方法内直接使用 `sqlite3.connect`（阻塞调用）：`_add_task`、`_check_overdue`、`_check_tasks`、`_complete_task`、`_daily_summary`、`_delete_task`、`_list_tasks`、`_suggest_schedule`。长查询会卡事件循环，改动时建议走 `asyncio.to_thread`。

## 依赖与数据

- 第三方包：`dateparser`
- 宿主模块：`core`、`modules`、`plugins`
- 涉及的数据路径字面量：`../../userdata.db`

## 源码

约 670 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
## 还有 8 个 async 方法在阻塞事件循环

后台循环调的 `_check_urgent()` 已改为走 `asyncio.to_thread`（见 `_check_urgent_sync`），
因为它无人值守、每 `_check_interval` 秒跑一次，卡住会连带卡 GUI 和对话。

但用户触发的那 8 个方法（`_add_task` / `_list_tasks` / `_complete_task` / `_delete_task` /
`_check_tasks` / `_daily_summary` / `_suggest_schedule` / `_check_overdue`）仍在 async 函数里
直接 `sqlite3.connect`。用户在等回复，阻塞可见且有界，所以暂未改。
参考 `plugins/life_manager/plugin.py` 的写法：同步函数 + `asyncio.to_thread` 包一层。

## 后台循环

`PeriodicTask` 配 `tick_first=False`，保留原行为「启动后先睡一个间隔再首次巡检」。
`_running` 仍是状态真相（通过 `should_continue` 传给 helper），`start`/`stop`
返回的用户可见字符串未变。
