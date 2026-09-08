# 联网搜索

`plugins/search/` · 类型 `delegate` · 触发词 `search_web`

联网搜索插件，优先使用 Grok 代理做提示式联网检索；也兼容 Exa，并可在失败时回退到 DDG。

> ⚠️ **安全审计风险标记**：`paid_api`  
> 见 `modules/plugin_security_audit.py`。 开放给 QQ 远程时会计入高风险清单。

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `search_web` |
| LLM 指令 | `search` |
| 插件类型 | `delegate` |
| 别名 | `搜索`、`查`、`百度`、`google`、`搜`、`找资料`、`查询`、`查一下`、`联网`、`网络`、`在线`、`提供链接`、`来源链接`、`给我提供链接`、`给我链接`、`web`、`query` |
| 超时 | 60 秒 |

示例：

```
[CMD: search | 今天有什么新闻]
```

## 权限

| 渠道 | 允许 |
| --- | --- |
| 本地桌面 | ✅ |
| QQ 远程 | ✅ |
| QQ 主人 | ✅ |
| QQ 他人 | ✅ |
| 群聊未 @ | ❌ |

## 可配置项

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `model_queue` | model_queue | 1 项 | 联网搜索模型（按顺序尝试） |
| `provider` | text | grok | 兼容搜索源 |
| `grok_base_url` | text | http://20.214.141.16:8317/v1 | Grok Base URL |
| `grok_api_key` | secret | （密钥，存 data/plugin_secrets.db） | Grok API Key |
| `grok_model` | text | grok-4.20-0309-non-reasoning | Grok 模型 |
| `grok_use_active_character` | bool | True | 自动使用当前角色人设 |
| `grok_force_web_hint` | bool | True | 强制提示联网 |
| `base_url` | text | https://exa.chengtx.vip | Exa 基础地址 |
| `remote_base_url` | text | https://exa.chengtx.vip | Exa 远程地址 |
| `local_base_url` | text |  | Exa 本地地址 |
| `prefer_local_first` | bool | True | Exa 优先本地 |
| `api_key` | secret | （密钥，存 data/plugin_secrets.db） | Exa API Key |
| `num_results` | number | 5 | 默认返回条数 |
| `use_answer` | bool | True | Exa 问题优先 /answer |
| `use_contents` | bool | True | Exa 自动拉正文 |
| `contents_max` | number | 3 | Exa /contents 条数 |
| `request_timeout_sec` | number | 18 | 请求超时(秒) |
| `fallback_ddg` | bool | True | 失败回退 DDG |

在 GUI「插件管理」里改，或直接编辑本目录的 `config.json`。

## 依赖与数据

- 第三方包：`ddgs`、`duckduckgo_search`
- 宿主模块：`core`、`modules`、`plugins`、`services`

## 源码

约 1252 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
