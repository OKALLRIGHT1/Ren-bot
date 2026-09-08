# 用户文件助手

`plugins/user_files/` · 类型 `direct` · 触发词 `user_files`

读取和受控写入白名单用户目录。支持 roots/list/read/write/move，不支持删除。

> ⚠️ **安全审计风险标记**：`file_access`  
> 见 `modules/plugin_security_audit.py`。 开放给 QQ 远程时会计入高风险清单。

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `user_files` |
| LLM 指令 | `user_files` |
| 插件类型 | `direct` |
| 别名 | `用户文件`、`文件助手`、`下载目录`、`文档目录`、`桌面文件`、`user_files` |
| 超时 | 20 秒 |

示例：

```
[CMD: user_files | read ||| downloads ||| note.txt]
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
| `custom_roots` | — | 空 | 额外允许的目录，例如 [{"name":"work","path":"D:/Work"}]。 |
| `max_read_chars` | — | 8000 | 单次读取最大字符数。 |
| `max_list_items` | — | 80 | 单次列目录最大条目数。 |

在 GUI「插件管理」里改，或直接编辑本目录的 `config.json`。

## 动作

取自源码里的 action/alias 映射表：

`help` `list` `move` `natural` `read` `roots` `write`

## 依赖与数据

- 宿主模块：`services`
- 涉及的数据路径字面量：`.json`

## 源码

约 354 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
