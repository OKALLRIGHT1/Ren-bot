# 萌娘百科查询

`plugins/moegirl_wiki/` · 类型 `delegate` · 触发词 `moegirl_wiki`

查询萌娘百科词条、角色、作品、术语，并返回主词条与相关语境。

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `moegirl_wiki` |
| LLM 指令 | `moegirl_lookup` |
| 插件类型 | `delegate` |
| 别名 | `萌百`、`萌娘百科`、`moegirl`、`查萌百`、`萌娘百科查询` |
| 超时 | 15 秒 |

示例：

```
[CMD: moegirl_lookup | 初音未来是谁]
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
| `timeout_seconds` | number | 10 | 请求超时(秒) |
| `cache_ttl_seconds` | number | 300 | 缓存秒数 |
| `prefer_exact_title` | bool | True | 优先精确标题 |
| `prefer_generator_search` | bool | True | 优先 generator 搜索 |
| `max_candidates` | number | 5 | 候选词条上限 |
| `cookie_string` | secret | （密钥，存 data/plugin_secrets.db） | 萌娘百科 Cookie |

在 GUI「插件管理」里改，或直接编辑本目录的 `config.json`。

## 动作

取自源码里的 action/alias 映射表：

`candidates`

## 依赖与数据

- 第三方包：`aiohttp`
- 宿主模块：`plugins`、`services`

## 源码

约 606 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
