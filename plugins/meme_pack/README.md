# 表情包库

`plugins/meme_pack/` · 类型 `direct` · 触发词 `meme_pack`

数据库版表情包系统：支持目录导入、按情绪/关键词选图、QQ 手动发送，并提供自动发送钩子。

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `meme_pack` |
| LLM 指令 | `meme_pack` |
| 插件类型 | `direct` |
| 别名 | `/表情包`、`/发表情`、`/随机表情`、`/导入表情包`、`/表情包统计` |
| 超时 | 20 秒 |

示例：

```
[CMD: meme_pack | /表情包 狡猾 / /导入表情包 D:\memes / /表情包统计]
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
| `model_queue` | model_queue | 空 | 表情包选择模型（按顺序尝试） |
| `auto_enabled` | bool | True | 启用自动表情包 |
| `auto_probability` | number | 0.12 | 自动发送概率 |
| `auto_private_only` | bool | True | 仅 QQ 私聊自动发送 |
| `llm_selector_enabled` | bool | True | 启用 LLM 表情包选择器 |
| `session_cooldown_seconds` | number | 120 | 会话冷却秒数 |
| `max_candidates` | number | 8 | 候选数量 |
| `assets_dir` | text | plugins/meme_pack/assets | 素材目录 |
| `database_path` | text | plugins/meme_pack/data/memes.sqlite | 数据库路径 |

在 GUI「插件管理」里改，或直接编辑本目录的 `config.json`。

## 动作

取自源码里的 action/alias 映射表：

`亲近` `安慰` `尴尬` `日常` `疑问` `调侃`

## 依赖与数据

- 第三方包：`meme_store`
- 宿主模块：`modules`、`services`
- 涉及的数据路径字面量：`plugins/meme_pack/data/memes.sqlite`

## 源码

约 965 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
