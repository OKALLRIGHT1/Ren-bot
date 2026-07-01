# 配置收束与 Live2D 默认姿态执行记录

## 目标

把分散配置逐步收束到更清晰的事实源，同时把 Live2D 情绪映射里的“刚打开模型时的状态”做成可选择的特殊动作候选。

本次执行只做低风险落地项：

- 记录配置收束路线。
- 增加 `__model_default__` 特殊动作，用于复用模型加载后的默认启动动作。
- 在角色动作映射 GUI 中暴露“模型默认姿态 / 刚打开状态”。
- 重写根目录 `README.md`，让 GitHub 用户只看一个精炼入口。

## 配置收束路线

### 阶段 1：保留兼容，新增运行时入口

`config.py` 暂时保留旧常量和默认值，避免大量旧 import 失效。后续新增的 GUI 可调项优先进入 `data/runtime_settings.json`，通过应用启动时的 `apply_external_settings()` 合并到运行时。

适合迁移的配置：

- 久坐提醒时间、冷却、弹窗文案。
- QQ/NapCat/MCP 开关和端口。
- TTS 开关、文本分段、空闲回 idle。
- 空闲随机动作时间：`IDLE_RANDOM_*`。

### 阶段 2：角色与 Live2D 映射归角色数据

动作映射的运行时优先级固定为：

1. 服装 `emotion_map`
2. 角色 `default_emotion_map`
3. 当前模型自动推导
4. `config.EMO_TO_LIVE2D` 全局 fallback

`config.EMO_TO_LIVE2D` 后续只保留兜底值，不再作为主要编辑入口。

### 阶段 3：Prompt 和模型池迁出 config.py

长 prompt、persona、模型池配置不适合长期放在 `config.py`。后续应迁到：

- `data/characters.json`：角色 persona、口癖、TTS、QQ 档案。
- `data/prompts/`：通用系统规则和结构化输出模板。
- `data/model_routes.json` 或现有运行时设置：模型路由和任务模型分类。

### 阶段 4：插件/API 配置归插件或服务

插件配置继续放在插件目录的 `config.json`。信息源、ALAPI、天气等接口统一放到信息源服务的 provider JSON 中，避免把 API 参数塞回 `config.py`。

### 阶段 5：清理 config.py

在上面迁移完成后，再做一次小步清理：

- 删除重复 import 和废弃变量。
- 删除已经迁到角色/运行时设置的长文本。
- 保留 env helper、启动默认值和旧代码兼容导出。

## 默认姿态方案

Live2D 前端当前加载模型后会自动播放一个默认启动动作：

1. 优先 `idle` 动作组第 0 个。
2. 否则使用 `Motion` 动作组第 0 个。
3. 否则使用模型里的第一个动作组第 0 个。

因此新增特殊动作值：

```text
__model_default__
```

含义：

- GUI 中显示为“模型默认姿态 / 刚打开状态”。
- 保存到角色或服装映射时作为普通 `mtn` 字段保存。
- 播放时不把它当普通动作名，而是解析当前模型文件，复用前端加载模型后的默认动作选择规则。

这样用户可以把 `idle`、`neutral` 或任意情绪映射到刚切模型时看到的状态。

## 本次执行结果

- 已记录配置收束路线，后续迁移按阶段推进，不在一次改动里大规模移动 `config.py`。
- 已在后端新增 `MODEL_DEFAULT_MOTION = "__model_default__"`。
- 已在 `modules.live2d.play_motion()` 中识别 `__model_default__`，并按当前模型 JSON 解析为实际默认启动动作。
- 已在角色编辑器动作下拉框第一项加入“模型默认姿态 / 刚打开状态”。
- 已重写根目录 `README.md`，作为 GitHub 用户面向入口。

## 验证记录

已运行：

```bash
python -m pytest tests\test_live2d_motion_candidates.py tests\test_character_editor_preview.py tests\test_character_manager_motion_candidates.py tests\test_emotion_controller_idle_random.py tests\test_emotion_defaults.py
python -m py_compile modules\live2d.py modules\emotion_controller.py modules\character_manager.py modules\gui\dialogs\character_editor.py config.py
```

结果：

- 19 个相关测试通过。
- 语法检查通过。
