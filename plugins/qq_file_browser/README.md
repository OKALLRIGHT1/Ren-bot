# QQ文件浏览

`plugins/qq_file_browser/` · 类型 `direct` · 触发词 `qq文件`

在 QQ 中安全浏览 /bot 虚拟目录并发送文件（零拷贝）。

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `qq文件` |
| LLM 指令 | `qq_file_browser` |
| 插件类型 | `direct` |
| 别名 | `/文件`、`/ls`、`/dir`、`/cd`、`/get`、`/pwd`、`/help`、`[文件]` |
| 超时 | 12 秒 |

示例：

```
[CMD: qq_file_browser | /文件 /ls]
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
| `extra_mounts` | list | 空 | 额外挂载目录 |
| `inbox_mount` | text | /bot/inbox | 接收文件目录 |

在 GUI「插件管理」里改，或直接编辑本目录的 `config.json`。

## 动作

取自源码里的 action/alias 映射表：

`/cd` `/dir` `/get` `/help` `/ls` `/pwd` `cd` `dir` `get` `help` `ls` `pwd`

## 依赖与数据

- 第三方包：`aiohttp`
- 涉及的数据路径字面量：`./data/qq_file_browser`、`config.json`

## 源码

约 549 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
