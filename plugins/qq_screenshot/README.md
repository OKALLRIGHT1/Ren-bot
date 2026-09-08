# QQ远程截图

`plugins/qq_screenshot/` · 类型 `direct` · 触发词 `qq截图`

在 QQ 中直接把当前电脑屏幕截图回发给主人。

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `qq截图` |
| LLM 指令 | `qq_screenshot` |
| 插件类型 | `direct` |
| 别名 | `截图发我`、`截图发给我`、`截个图发我`、`截个图发给我`、`发个截图`、`发张截图`、`发截图给我`、`把截图发我`、`把截图发给我`、`把屏幕发我`、`发屏幕给我`、`把屏幕截图发我`、`把屏幕截图发给我`、`给我看下屏幕`、`给我看看屏幕`、`截屏给我`、`截屏发我`、`截屏发给我`、`屏幕截图` |
| 超时 | 15 秒 |

示例：

```
[CMD: qq_screenshot | 截图发我]
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
| `capture_target` | choice | primary | 默认截图范围 |
| `monitor_index` | number | 1 | 默认屏幕序号 |
| `include_window_title` | bool | True | 附带当前窗口标题 |

在 GUI「插件管理」里改，或直接编辑本目录的 `config.json`。

## 依赖与数据

- 宿主模块：`modules`
- 涉及的数据路径字面量：`config.json`

## 源码

约 143 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
