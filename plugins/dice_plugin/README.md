# 骰子

`plugins/dice_plugin/` · 类型 `direct` · 触发词 `dice_roll`

掷骰指令（D&D/CoC/选项表）

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `dice_roll` |
| LLM 指令 | `dice_roll` |
| 插件类型 | `direct` |
| 别名 | `.r`、`.ra`、`.rc`、`.sc`、`dice` |
| 超时 | 6 秒 |

示例：

```
[CMD: dice_roll | .r 2d100]
```

## 权限

| 渠道 | 允许 |
| --- | --- |
| 本地桌面 | ✅ |
| QQ 远程 | ✅ |
| QQ 主人 | ✅ |
| QQ 他人 | ✅ |
| 群聊未 @ | ✅ |

## 依赖与数据

- 宿主模块：`core`、`plugins`

## 源码

约 248 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
