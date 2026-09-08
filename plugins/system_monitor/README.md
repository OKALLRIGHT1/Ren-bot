# 系统运维

`plugins/system_monitor/` · 类型 `react` · 触发词 `monitor`

系统监控与控制中枢。支持：check / cpu / memory / disk / temperature / gpu / all / start / stop / status / set_volume / mute / set_brightness / lock_screen。

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `monitor` |
| LLM 指令 | `check` |
| 插件类型 | `react` |
| 别名 | `硬件`、`监控`、`状态`、`cpu`、`memory`、`内存`、`温度`、`temperature`、`sys`、`check`、`检查`、`音量`、`亮度`、`静音`、`锁屏` |

示例：

```
[CMD: check | set_volume ||| +20]
```

## 权限

`config.json` 未声明 `access_control`，由 `PluginManager._normalize_access_control` 套用默认：本地 ✅ / QQ 远程 ✅ / QQ 主人 ✅ / QQ 他人 ❌ / 群聊未 @ ❌。

## 可配置项

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `cpu_threshold` | number | 80 | CPU使用率阈值 (%) |
| `memory_threshold` | number | 85 | 内存使用率阈值 (%) |
| `disk_threshold` | number | 90 | 磁盘使用率阈值 (%) |
| `cpu_temp_threshold` | number | 75 | CPU温度阈值 (℃) |
| `gpu_threshold` | number | 85 | GPU使用率阈值 (%) |
| `gpu_temp_threshold` | number | 80 | GPU温度阈值 (℃) |
| `check_interval` | number | 60 | 检查间隔 (秒) |

在 GUI「插件管理」里改，或直接编辑本目录的 `config.json`。

## 动作

取自 `description` 声明：

`check` `cpu` `memory` `disk` `temperature` `gpu` `all` `start` `stop` `status` `set_volume` `mute` `set_brightness` `lock_screen`

## 运行时

- 会 `asyncio.create_task` 起后台任务。
- 生命周期钩子：`start` 无、`stop` 有。

## 依赖与数据

- 第三方包：`comtypes`、`psutil`、`pycaw`、`pynvml`、`screen_brightness_control`
- 宿主模块：`core`、`plugins`
- 涉及的数据路径字面量：`config.json`

## 源码

约 755 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
## 两个 `stop` 不是一回事

- action map 里的 `"stop"` → `_stop_monitoring()`：**用户指令**，返回给用户看的字符串。
- 类方法 `stop()`：**生命周期钩子**，宿主 `stop_all_plugins()` 和热重载路径
  （`modules/plugin_manager.py` 里 `hasattr(old_plugin, "stop")`）调用它。

生命周期 `stop()` 原先缺失，导致用户 `start` 过监控后，每次热重载都残留一条旧循环并累积。
现在补上了，它在未运行时是空操作。改这个插件时别把两者搞混。

`config.json` 未声明 `access_control`，套用宿主默认 —— 注意它有 `set_volume` /
`set_brightness` / `lock_screen`，这些在默认画像下主人可从 QQ 远程触发。
