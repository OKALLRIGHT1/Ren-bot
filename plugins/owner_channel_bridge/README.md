# 主人跨通道近史

`plugins/owner_channel_bridge/` · 类型 `delegate` · 触发词 `owner_channel_bridge`

主人按需查询桌面或 QQ 另一侧的近史摘要。默认会话仍隔离，不会自动混进 prompt。参数：desktop|qq|other ||| 可选关键词

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `owner_channel_bridge` |
| LLM 指令 | `owner_channel_bridge` |
| 插件类型 | `delegate` |
| 别名 | `/桥接`、`/跨通道`、`/channel_bridge` |
| 超时 | 20 秒 |

示例：

```
[CMD: owner_channel_bridge | desktop ||| 刚才在干嘛]
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
| `max_items` | int | 8 | — |
| `max_chars` | int | 1200 | — |

在 GUI「插件管理」里改，或直接编辑本目录的 `config.json`。

## 依赖与数据

- 宿主模块：`services`

## 源码

约 328 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
