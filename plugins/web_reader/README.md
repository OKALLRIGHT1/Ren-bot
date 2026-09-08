# 网页解析

`plugins/web_reader/` · 类型 `delegate` · 触发词 `web_reader`

读取指定网页链接，提取标题、域名与正文摘要。参数直接传 URL。

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `web_reader` |
| LLM 指令 | `web_reader` |
| 插件类型 | `delegate` |
| 别名 | `网页解析`、`网页阅读`、`读网页`、`解析链接`、`打开链接`、`网页内容` |
| 超时 | 15 秒 |

示例：

```
[CMD: web_reader | https://example.com/article]
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
| `request_timeout_sec` | number | 12 | 请求超时(秒) |
| `max_chars` | number | 2400 | 正文最大字符数 |

在 GUI「插件管理」里改，或直接编辑本目录的 `config.json`。

## 依赖与数据

- 宿主模块：`core`、`plugins`、`services`

## 源码

约 331 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
