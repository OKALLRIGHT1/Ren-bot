# QQ点歌

`plugins/qq_music/` · 类型 `direct` · 触发词 `qq_music`

通过多平台音乐网关搜索歌曲，默认先搜 QQ 音乐，搜不到再回退网易云，并直接以 QQ 语音形式回发。

> ⚠️ **安全审计风险标记**：`paid_api`  
> 见 `modules/plugin_security_audit.py`。 开放给 QQ 远程时会计入高风险清单。

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `qq_music` |
| LLM 指令 | `qq_music` |
| 插件类型 | `direct` |
| 别名 | `点歌`、`qq点歌`、`QQ点歌`、`来首歌`、`放首歌`、`播首歌`、`听歌` |
| 超时 | 90 秒 |

示例：

```
[CMD: qq_music | 点歌 晴天]
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
| `model_queue` | model_queue | 空 | 点歌点评模型（按顺序尝试） |
| `base_url` | text | https://gateway.karpov.cn | API Base URL |
| `api_key` | secret | （密钥，存 data/plugin_secrets.db） | API Key |
| `session_cookie` | secret | （密钥，存 data/plugin_secrets.db） | Session Cookie |
| `default_quality` | text | MP3_320 | 默认音质 |
| `provider_order` | text | qqmusic,netease | 搜索源顺序 |
| `fallback_to_netease` | bool | True | QQ 失败回退网易云 |
| `search_page_size` | number | 8 | 搜索返回数量 |
| `request_timeout_sec` | number | 12 | 请求超时(秒) |
| `cache_subdir` | text | audio_cache/qq_music | 缓存目录 |
| `cache_ttl_hours` | number | 23 | 缓存保留小时数 |
| `comment_after_send` | bool | True | 语音后补一句点评 |
| `comment_defer_until_voice` | bool | True | 先放歌后点评 |
| `comment_dispatch_delay_sec` | number | 2 | 点评发送延迟(秒) |
| `comment_use_llm` | bool | True | 点评使用 LLM |
| `comment_timeout_sec` | number | 30 | 点评超时(秒) |
| `comment_lyric_lines` | number | 2 | 点评用歌词行数 |

在 GUI「插件管理」里改，或直接编辑本目录的 `config.json`。

## 运行时

- 会 `asyncio.create_task` 起后台任务。
- 生命周期钩子：`start` 无、`stop` 无（宿主关闭/热重载时无法回收后台任务）。

## 依赖与数据

- 宿主模块：`config`、`core`、`modules`、`plugins`、`services`
- 涉及的数据路径字面量：`data/cache`

## 源码

约 1044 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
