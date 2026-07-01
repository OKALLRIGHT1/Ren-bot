# Live2D-Suzu

Live2D-Suzu 是一个本地运行的 Live2D 桌面助手。它把 Live2D 形象、语音合成、长期记忆、QQ/NapCat 接入、插件工具、MCP 工具和屏幕/活动感知整合到一个可配置的桌面程序里。

项目偏向个人桌面陪伴与自动化助手，不只是简单聊天壳。

## 功能概览

- Live2D 桌面形象、气泡、表情、动作、口型同步。
- 角色系统：角色提示词、服装、动作映射、TTS 配置、QQ 昵称和头像。
- 情绪到 Live2D 动作映射，支持一个情绪配置多个动作随机播放。
- 空闲随机动作：空闲一段时间后自动播放 `idle_random`，再回到 `idle`。
- 模型默认姿态：动作映射里可以选择“模型默认姿态 / 刚打开状态”。
- GPT-SoVITS / Edge TTS 路由。
- QQ / NapCat 网关：私聊、群聊、图片理解、语音回复、远程命令。
- 插件系统：支持 direct / react / observe / delegate 类型插件。
- Agent Mail、音乐、信息源、截图、应用控制、技能运行等插件能力。
- 本地记忆、每日总结、表达学习、知识库检索。
- 可选 Rust/Live2D sidecar 活动采集，用于久坐提醒和桌面状态判断。

## 快速开始

推荐 Python 3.10+。

```bash
conda create -n live2d-llm python=3.10
conda activate live2d-llm
pip install -r requirements.txt
python boot.py
```

如果希望程序异常退出后自动拉起，用守护模式：

```bash
python main.py
```

Windows 下也可以使用根目录里的启动器或打包后的 `Live2D-Suzu.exe`。

## Live2D 前端

桌面 Live2D 前端在相邻目录：

```text
D:\Desktop\live2d-suzu\live2d-only
```

主程序通过 WebSocket 和 Live2D 前端通信。启动 Live2D 前端后，主程序会自动扫描常用端口并连接。

## 常用配置

- `.env`：放 API Key、端口、模型服务地址等本机私密配置。
- `data/runtime_settings.json`：GUI 保存的运行时设置。
- `data/characters.json`：角色、服装、动作映射、TTS、QQ 档案。
- `plugins/*/config.json`：插件配置。
- `config.py`：默认值和旧代码兼容层，后续新增配置优先不要再塞进这里。

空闲随机动作可用 `.env` 覆盖：

```env
IDLE_RANDOM_MOTION_ENABLED=1
IDLE_RANDOM_MIN_SECONDS=90
IDLE_RANDOM_MAX_SECONDS=240
IDLE_RANDOM_MIN_IDLE_SECONDS=30
IDLE_RANDOM_RETURN_IDLE_SECONDS=4
```

## 动作映射说明

角色编辑器里可以把情绪映射到 Live2D 动作。

优先级：

```text
服装 emotion_map > 角色 default_emotion_map > 模型自动推导 > config.EMO_TO_LIVE2D
```

特殊动作：

```text
__model_default__
```

GUI 中显示为“模型默认姿态 / 刚打开状态”。它会复用模型加载后默认播放的启动动作，适合用作 `idle` 或 `neutral`。

## QQ / 插件 / MCP

QQ 接入主要通过 NapCat / OneBot 网关。插件可以提供命令、自然语言工具调用、观察型事件和委托任务。MCP 可作为外部工具入口接入。

常见插件包括：

- 邮件收发与回复
- 音乐播放和点歌
- 信息源查询
- 应用控制和远程重启
- 截图与文件浏览
- 技能运行时

## 开发验证

常用检查：

```bash
python -m py_compile modules\live2d.py modules\emotion_controller.py
python -m pytest tests\test_live2d_motion_candidates.py tests\test_character_editor_preview.py
```

如果修改前端：

```bash
cd D:\Desktop\live2d-suzu\live2d-only
npm run build
```

## 目录结构

```text
core/          应用启动、事件总线、GUI/QQ/MCP/传感器初始化
services/      对话主链路和拆分后的辅助服务
modules/       Live2D、TTS、记忆、GUI、插件管理等基础模块
plugins/       插件目录
integrations/  QQ 网关、GUI HTTP/WS、外部系统适配
data/          运行时配置、角色、状态和本地数据
docs/          维护记录和专题文档
tests/         自动化测试
```

## 维护说明

- 不要硬编码密钥，使用 `.env` 或 GUI 配置。
- 新增 GUI 可调项优先写入 `data/runtime_settings.json`。
- 角色、服装和动作映射优先写入 `data/characters.json`。
- 插件配置留在插件自己的 `config.json`。
- `config.py` 只保留默认值和兼容导出。

配置收束和默认姿态方案记录见：

```text
docs/config-consolidation-and-live2d-default-pose.md
```
