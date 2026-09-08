# 视觉感知

`plugins/vision/` · 类型 `observe` · 触发词 `vision`

能够截图或拍照，并分析当前屏幕或摄像头画面

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `vision` |
| LLM 指令 | `screenshot` |
| 插件类型 | `observe` |
| 别名 | `截图`、`截屏`、`看屏幕`、`看看屏幕`、`屏幕分析`、`分析屏幕`、`拍照`、`看摄像头`、`看看摄像头`、`screenshot`、`capture`、`screen`、`camera`、`photo` |

示例：

```
[CMD: screenshot | 看看屏幕]
```

## 权限

`config.json` 未声明 `access_control`，由 `PluginManager._normalize_access_control` 套用默认：本地 ✅ / QQ 远程 ✅ / QQ 主人 ✅ / QQ 他人 ❌ / 群聊未 @ ❌。

## 动作

取自源码里的 action/alias 映射表：

`camera`

## 依赖与数据

- 宿主模块：`modules`、`services`

## 源码

约 120 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
