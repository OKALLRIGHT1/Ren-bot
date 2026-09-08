# QQ生图

`plugins/qq_draw/` · 类型 `direct` · 触发词 `qq生图`

仅在 QQ 中用固定斜杠命令触发生图。模型唯一来自「模型与路由」：先给模型勾选画图用途，再在任务路由「画图」里手动选择要用的模型与顺序（不会自动全选）。

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `qq生图` |
| LLM 指令 | `qq_draw` |
| 插件类型 | `direct` |
| 别名 | `/画图`、`/画画` |
| 超时 | 300 秒 |

示例：

```
[CMD: qq_draw | /画图 绘制一个丰川祥子在雨后的街道]
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
| `model_queue` | model_queue | 2 项 | 画图模型（按顺序尝试） |
| `size_value` | text | 1024x1024 | 默认尺寸 |
| `quality` | text |  | 质量 |
| `style` | text |  | 风格 |
| `negative_prompt` | text |  | 默认负面提示词 |
| `extra_body_json` | text | {} | 额外请求体(JSON) |
| `request_timeout_sec` | number | 300 | 请求超时(秒) |
| `caption_template` | text | 已按你的要求画好了：{prompt} | 回发说明模板 |
| `image_to_image_enabled` | bool | True | 启用图生图 |
| `max_input_images` | number | 8 | 最多参考图数量 |
| `input_image_field` | text | image | 输入图字段名 |
| `input_image_format` | text | data_url | 输入图格式 |
| `include_chat_image_part` | bool | True | 附带聊天图片片段 |
| `debug_logging` | bool | False | 调试日志 |

在 GUI「插件管理」里改，或直接编辑本目录的 `config.json`。

## 依赖与数据

- 第三方包：`aiohttp`
- 宿主模块：`config`、`integrations`、`modules`、`services`
- 涉及的数据路径字面量：`config.json`、`data/custom_models.json`

## 源码

约 1345 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
