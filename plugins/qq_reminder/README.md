# QQ定时提醒

`plugins/qq_reminder/` · 类型 `direct` · 触发词 `qq提醒`

在 QQ 中创建仅发给自己的定时提醒，支持工作日、每天等周期提醒。

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `qq提醒` |
| LLM 指令 | `qq_reminder` |
| 插件类型 | `direct` |
| 别名 | `/提醒`、`/提醒列表`、`/提醒删除`、`/提醒测试`、`提醒我`、`打卡提醒`、`定时提醒`、`提醒` |
| 超时 | 20 秒 |

示例：

```
[CMD: qq_reminder | /提醒 每周1到周5 17:20 提醒我打卡]
```

## 权限

| 渠道 | 允许 |
| --- | --- |
| 本地桌面 | ❌ |
| QQ 远程 | ✅ |
| QQ 主人 | ✅ |
| QQ 他人 | ❌ |
| 群聊未 @ | ❌ |

## 可配置项

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `storage_path` | text | data/qq_reminders.json | 提醒存储文件 |
| `scan_interval_sec` | number | 20 | 轮询间隔(秒) |
| `default_reminder_prefix` | text | ⏰ 提醒时间到： | 提醒前缀 |

在 GUI「插件管理」里改，或直接编辑本目录的 `config.json`。

## 运行时

- 使用 `modules/plugin_scheduler.py` 的 `PeriodicTask` 跑后台循环（循环/退避/取消由 helper 统一负责）。
- 生命周期钩子：`start` 有、`stop` 有。

## 依赖与数据

- 宿主模块：`modules`、`services`
- 涉及的数据路径字面量：`config.json`、`data/qq_reminders.json`

## 源码

约 465 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
## reload_config 会无条件重读磁盘

`reload_config()` 直接读本目录的 `config.json`。而宿主的加载顺序是：
先 `_apply_secret_overrides` 注入 secret → 设 `inst.settings` → 再调 `reload_config()`
（见 `modules/plugin_manager.py` 的 474 / 515 行）。

也就是说 `reload_config` 会覆盖宿主注入的值。**目前无害**，因为本插件三个设置
（`storage_path` / `scan_interval_sec` / `default_reminder_prefix`）都不是 `secret` 类型，
磁盘上就是真值。但**一旦给这个插件加 `type: secret` 的设置，就会读到空值** ——
届时必须改成优先读已注入的 `self.settings`。

## 后台循环

用 `modules/plugin_scheduler.py` 的 `PeriodicTask`。`scan_interval_sec` 有 `>= 5` 秒下限
（原实现的 `max(5, ...)` 保留在 interval 回调里）。错误退避固定 30 秒。
