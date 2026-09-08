# 生活管家

`plugins/life_manager/` · 类型 `react` · 触发词 `database`

记账与提醒。指令：add_bill 金额 事项 / list_bills / add_reminder 时间 事项

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `database` |
| LLM 指令 | `life_manager` |
| 插件类型 | `react` |
| 别名 | `记账`、`生活提醒`、`账单`、`生活`、`支出`、`收入`、`消费`、`bill`、`expense`、`life` |

示例：

```
[CMD: life_manager | add_bill 15 奶茶]
```

## 权限

`config.json` 未声明 `access_control`，由 `PluginManager._normalize_access_control` 套用默认：本地 ✅ / QQ 远程 ✅ / QQ 主人 ✅ / QQ 他人 ❌ / 群聊未 @ ❌。

## 动作

取自源码里的 action/alias 映射表：

`add_bill` `add_record` `add_reminder` `list_bills` `提醒` `查账` `记账` `账单` `闹钟`

## 运行时

- 使用 `modules/plugin_scheduler.py` 的 `PeriodicTask` 跑后台循环（循环/退避/取消由 helper 统一负责）。
- 生命周期钩子：`start` 有、`stop` 有。
- 以下 async 方法内直接使用 `sqlite3.connect`（阻塞调用）：`_check_due_reminders_once`。长查询会卡事件循环，改动时建议走 `asyncio.to_thread`。

## 依赖与数据

- 第三方包：`dateparser`
- 宿主模块：`core`、`modules`、`plugins`
- 涉及的数据路径字面量：`../../userdata.db`、`../userdata.db`

## 源码

约 221 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
