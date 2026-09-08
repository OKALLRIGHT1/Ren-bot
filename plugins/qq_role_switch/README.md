# QQ角色切换

`plugins/qq_role_switch/` · 类型 `direct` · 触发词 `qq角色`

在 QQ 中查看角色列表，并用斜杠命令快速切换角色。也支持直接用 `/角色名` 切换。

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `qq角色` |
| LLM 指令 | `qq_role_switch` |
| 插件类型 | `direct` |
| 别名 | `/角色`、`/角色列表` |
| 超时 | 10 秒 |

示例：

```
[CMD: qq_role_switch | /角色 丰川祥子]
```

## 权限

| 渠道 | 允许 |
| --- | --- |
| 本地桌面 | ❌ |
| QQ 远程 | ✅ |
| QQ 主人 | ✅ |
| QQ 他人 | ❌ |
| 群聊未 @ | ✅ |

## 依赖与数据

- 宿主模块：`modules`、`services`

## 源码

约 86 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
