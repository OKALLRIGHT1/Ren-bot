# 主程序控制

`plugins/app_control/` · 类型 `direct` · 触发词 `app_control`

通过受权限控制的指令重启主程序。

> ⚠️ **安全审计风险标记**：`process_control`  
> 见 `modules/plugin_security_audit.py`。 开放给 QQ 远程时会计入高风险清单。

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `app_control` |
| LLM 指令 | `app_control` |
| 插件类型 | `direct` |
| 别名 | `/重启`、`/远程重启`、`/restart` |
| 超时 | 3 秒 |

示例：

```
[CMD: app_control | /重启]
```

## 权限

| 渠道 | 允许 |
| --- | --- |
| 本地桌面 | ✅ |
| QQ 远程 | ✅ |
| QQ 主人 | ✅ |
| QQ 他人 | ❌ |
| 群聊未 @ | ❌ |

## 源码

约 24 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
## 不是应用启动器

这个插件**只做一件事：重启主程序**。想打开本地软件看 `plugins/open_app/`。
两个名字容易混，改动前先确认你在哪个。

它带 `process_control` 风险标记（`modules/plugin_security_audit.py`），
且有自己的 `access_control`。**不要把它折进 `system_monitor`** ——
后者既无风险标记也无 `access_control` 声明，合并会让重启能力落进更宽松的权限画像。
