# 本地第二大脑

`plugins/local_knowledge/` · 类型 `react` · 触发词 `knowledge_base`

用于管理本地知识库。支持指令：learn (扫描文件夹并学习), search (搜索知识库)。参数：指令 ||| 内容(文件夹路径或查询词)

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `knowledge_base` |
| LLM 指令 | 不暴露给 LLM |
| 插件类型 | `react` |

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
| `knowledge_source_dirs` | list | 2 项 | 知识目录 |
| `session_kb_enabled` | bool | True | 启用会话知识域 |
| `slow_ingest_enabled` | bool | True | 慢速导入模式 |
| `slow_ingest_batch` | number | 20 | 每批文件数 |
| `slow_ingest_sleep_ms` | number | 1200 | 每批暂停毫秒 |
| `adaptive_slow_ingest` | bool | True | 自适应慢速模式 |

在 GUI「插件管理」里改，或直接编辑本目录的 `config.json`。

## 动作

取自源码里的 action/alias 映射表：

`learn` `search`

## 依赖与数据

- 宿主模块：`modules`、`services`
- 涉及的数据路径字面量：`*.json`

## 源码

约 270 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
