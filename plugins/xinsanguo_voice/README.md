# 新三国语音

`plugins/xinsanguo_voice/` · 类型 `direct` · 触发词 `xinsanguo_voice`

识别新三国经典台词关键词并回发对应音频文件。

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `xinsanguo_voice` |
| LLM 指令 | `xinsanguo_voice` |
| 插件类型 | `direct` |
| 别名 | `新三国`、`三国语音`、`三国玩梗` |
| 超时 | 6 秒 |

示例：

```
[CMD: xinsanguo_voice | 不可能]
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
| `enable_group_only` | bool | True | 仅群聊触发 |
| `random_select` | bool | True | 随机选择语音 |
| `session_cooldown_enabled` | bool | True | 启用会话冷却 |
| `session_cooldown_seconds` | number | 600 | 会话冷却秒数 |
| `audio_subdir` | text | data/sound | 音频目录名 |
| `rules_file` | text | rules.json | 规则文件名 |

在 GUI「插件管理」里改，或直接编辑本目录的 `config.json`。

## 动作

取自源码里的 action/alias 映射表：

`/sanguo` `/三国语音`

## 依赖与数据

- 涉及的数据路径字面量：`data/sound`、`rules.json`

## 源码

约 232 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
