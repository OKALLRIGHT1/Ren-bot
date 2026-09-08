# 灾害预警

`plugins/disaster_warning/` · 类型 `react` · 触发词 `disaster_warning`

按关注地区查询地震/台风/气象预警。后台自动推送默认关闭，需要时再打开。

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `disaster_warning` |
| LLM 指令 | `disaster_warning` |
| 插件类型 | `react` |
| 别名 | `/灾害预警`、`/地震预警`、`/灾害预警状态` |
| 超时 | 30 秒 |

示例：

```
[CMD: disaster_warning | /灾害预警 最近地震]
```

## 权限

| 渠道 | 允许 |
| --- | --- |
| 本地桌面 | ✅ |
| QQ 远程 | ✅ |
| QQ 主人 | ✅ |
| QQ 他人 | ❌ |
| 群聊未 @ | ❌ |

## 可配置项

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `enabled` | bool | False | 启用后台预警 |
| `status_ui_url` | link | http://127.0.0.1:8765/ | 本机 WebUI |
| `owner_session` | text | 1132824061 | 主人 QQ 会话 |
| `group_sessions` | text |  | 推送群号 |
| `watch_regions_json` | text | [{"id": "上海", "label": "上海", "keyword… | 关注地区 JSON |
| `min_magnitude` | number | 4 | 最低震级 |
| `major_override_mag` | number | 7 | 特大地震仍推门槛 |
| `silence_sec` | number | 60 | 启动静默秒数 |
| `fanstudio_app_id` | text | 97b68b51-ec96-42c3-80d7-83d2bff70d99 | Fan Studio App ID |
| `fanstudio_api_key` | secret | （密钥，存 data/plugin_secrets.db） | Fan Studio API Key |
| `wolfx_eew_enabled` | bool | False | 启用 Wolfx 秒级预警 |
| `wolfx_ws_url` | text | wss://ws-api.wolfx.jp/all_eew | Wolfx EEW 地址 |
| `wolfx_ws_backup_url` | text |  | Wolfx EEW 备份地址 |
| `alert_poll_enabled` | bool | False | 轮询台风/气象预警 |
| `alert_poll_sec` | number | 300 | 气象预警轮询秒 |
| `ws_url` | text | wss://ws.fanstudio.tech/all | Fan WS 主地址 |
| `ws_backup_url` | text | wss://ws.fanstudio.hk/all | Fan WS 备份地址 |
| `state_path` | text | data/disaster_warning_state.json | 状态文件 |
| `status_ui_enabled` | bool | True | 本机设置页 |
| `status_ui_port` | number | 8765 | 本机设置页端口 |

在 GUI「插件管理」里改，或直接编辑本目录的 `config.json`。

## 动作

取自源码里的 action/alias 映射表：

`eq_list`

## 运行时

- 会 `asyncio.create_task` 起后台任务。
- 生命周期钩子：`start` 有、`stop` 有。

## 依赖与数据

- 第三方包：`aiohttp`
- 宿主模块：`modules`、`services`
- 涉及的数据路径字面量：`config.json`、`data/disaster_warning_state.json`、`https://api.wolfx.jp/cenc_eqlist.json`

## 源码

约 2945 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
