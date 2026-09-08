# 洛克王国世界查蛋器

`plugins/egg_search/` · 类型 `direct` · 触发词 `egg_search`

输入尺寸和重量，返回匹配的精灵种类。

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `egg_search` |
| LLM 指令 | `egg_search` |
| 插件类型 | `direct` |
| 别名 | `/egg`、`/egg_reload` |
| 超时 | 10 秒 |

示例：

```
[CMD: egg_search | /egg 0.04 0.057]
```

## 权限

| 渠道 | 允许 |
| --- | --- |
| 本地桌面 | ✅ |
| QQ 远程 | ✅ |
| QQ 主人 | ✅ |
| QQ 他人 | ✅ |
| 群聊未 @ | ✅ |

## 可配置项

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `data_path` | text | data/egg_data.json | 数据文件路径 |
| `result_limit` | number | 50 | 最多显示条数 |

在 GUI「插件管理」里改，或直接编辑本目录的 `config.json`。

## 依赖与数据

- 涉及的数据路径字面量：`config.json`、`data/egg_data.json`

## 源码

约 129 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
