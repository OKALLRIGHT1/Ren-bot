# 技能运行时

`plugins/skill_runtime/` · 类型 `direct` · 触发词 `skill_runtime`

管理运行时 Skill 系统，支持列出、启用、停用、重载兼容 SKILL.md 的技能目录。

> ⚠️ **安全审计风险标记**：`code_or_skill`  
> 见 `modules/plugin_security_audit.py`。 开放给 QQ 远程时会计入高风险清单。

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `skill_runtime` |
| LLM 指令 | `skill_runtime` |
| 插件类型 | `direct` |
| 别名 | `技能列表`、`当前技能`、`重载技能`、`启用技能`、`停用技能`、`禁用技能`、`关闭技能系统`、`开启技能系统`、`skill list`、`skill current`、`skill reload`、`skill enable`、`skill disable`、`skill on`、`skill off` |
| 超时 | 8 秒 |

示例：

```
[CMD: skill_runtime | 启用技能 imagegen]
```

## 权限

| 渠道 | 允许 |
| --- | --- |
| 本地桌面 | ✅ |
| QQ 远程 | ✅ |
| QQ 主人 | ✅ |
| QQ 他人 | ❌ |
| 群聊未 @ | ❌ |

## 依赖与数据

- 第三方包：`__main__`
- 宿主模块：`services`

## 源码

约 214 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
