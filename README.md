<p align="center">
  <img src="assets/avatar.png" width="128" alt="Live2D-Suzu">
</p>

<h1 align="center">Live2D-Suzu</h1>

<p align="center">
  本地运行的 AI 桌面角色助手。<br>
  不是一个聊天窗口，而是能长期陪伴、记住上下文、调用工具，并用 Live2D、语音和动作表现出来的本机程序。
</p>

<p align="center">
  <img src="assets/readme/desktop.png" width="320" alt="Live2D 桌面形象">
</p>

## 它能做什么

- **桌面形象**：Live2D 气泡、表情、动作、口型同步、换装和模型预览；空闲时会随机动作并回到 idle。
- **角色系统**：提示词、服装、情绪动作映射、TTS、QQ 昵称和头像。一个情绪可以配多个动作随机播放。
- **语音**：GPT-SoVITS、Edge TTS 等路由；可开本机语音输入。
- **QQ / NapCat**：私聊、群聊、图片理解、语音回复和主人远程指令。QQ 来源默认不驱动本地 Live2D / TTS。
- **插件与工具**：邮件、音乐、天气/日报、截图、文件、网页、应用控制、技能运行、Codex / Claude 委托等。高风险操作会先确认。
- **长期记忆**：SQLite 为唯一事实源，支持向量检索、人物画像、知识库和日记。
- **桌面感知**：Live2D / Tauri 采集本机活动并上报。连上主程序后，久坐弹窗由桌面端显示后端模板（话术和表情包），角色口头提醒走气泡和语音；顶部显示「工作了多久」。没连主程序时，桌面端仍可独立弹窗。
- **设置中心**：模型、角色、插件、QQ、记忆等常用项可以在 GUI 里改，不必先翻配置文件。

## 快速开始

Windows、Python 3.10+。克隆仓库后直接双击：

1. `安装环境.bat`：没有 Python 时会尝试用 winget 安装 3.12，然后创建 `.venv` 并安装依赖。首次可能需要几分钟。
2. `启动.bat`：环境未就绪时会先安装，再启动主程序。
3. `启动-无窗口.vbs`：环境就绪后不弹出命令行窗口。

安装成功后会写入 `.suzu-ready`。如果还没有 `.env`，会从 `.env.example` 复制一份，**请自行填入 API Key**。失败时看 `logs/setup_env.log`。

已经有 Conda 环境 `live2d-llm` 时，安装脚本会往这个环境装依赖，不再另建 `.venv`。

手动安装（开发 / Conda）：

```bash
conda create -n live2d-llm python=3.10
conda activate live2d-llm
pip install -r requirements.txt
python main.py
```

只检查或只安装、不启动：

```bash
python setup_env.py --ensure --console
python setup_env.py --check
```

`main.py` 是推荐入口，会拉起 `boot.py` 并接管远程重启和异常恢复。`boot.py` 只适合临时调试核心进程。

## Live2D 桌面端

桌面形象是独立程序，可以单独运行，也可以连上本仓库的 Python 主程序。

1. 先启动 Live2D 桌面端。
2. 再启动本仓库主程序（`启动.bat` 或 `python main.py`）。
3. 在设置中心选择角色、模型、服装、动作映射和语音。

只想看形象时，桌面端可以独立运行；没连主程序时，基础久坐弹窗也在桌面端本地生效。聊天、插件、记忆和 Qt 设置中心都不依赖它。

增强版（Tauri）通过连接档案主动连本机或远程后端：

- 本机可用 `http://127.0.0.1:<port>/gui` 与 `ws://127.0.0.1:<port>/gui`。
- 非本机地址必须走 TLS（`https` / `wss`）。
- 访问令牌放在运行时设置或环境变量里，用 `X-GUI-Token` 认证，不要写进仓库或日志。

桌面活动由 Live2D / Tauri 采集并上报 `/gui/activity-ingest`；Python 只消费 `source=live2d-tauri`。连上之后：

- 久坐判定、冷却、免打扰、弹窗模板和表情包由主程序决定
- 桌面端只负责弹出窗口；角色说话仍走 Live2D 气泡 / TTS
- 稍后提醒或关闭弹窗会回告主程序，两边冷却保持一致
- 顶部工作时长显示「工作了多久」，不是「坐了多久」

## 常用配置

| 位置 | 用途 |
| --- | --- |
| `.env` | API Key、端口、模型地址等本机私密配置 |
| `data/runtime_settings.json` | GUI 保存的运行时设置 |
| `data/characters.json` | 角色、服装、动作、TTS、QQ 档案 |
| `plugins/*/config.json` | 各插件自己的配置 |
| `config.py` | 默认值和旧配置兼容层 |

不要把真实密钥提交到 Git。发布前检查 `.env`、日志、缓存和本地数据库有没有被误加入版本控制。

## 角色与动作

角色编辑器里可以为每个角色和服装配置 Live2D 动作：

- 每个情绪选一个或多个动作，多个则随机播放
- 可以停止动作，或使用模型刚加载时的默认姿态
- 不同服装可以覆盖角色默认映射

优先级：`服装映射 > 角色默认映射 > 模型自动推导 > 全局默认映射`。

## QQ 与插件

QQ 走 NapCat / OneBot。插件可提供命令、自然语言工具、观察事件和后台委托。

插件要用模型时，统一读「设置中心 → 模型与路由」，不用在每个插件里再填地址和密钥。模型可勾选聊天、推理/工具、总结、联网搜索、视觉、画图、代码、翻译或向量等用途；插件设置只显示匹配的模型，并可排故障转移队列。

接入步骤见 [`MCP_QQ_SETUP_GUIDE.md`](MCP_QQ_SETUP_GUIDE.md)，插件说明见 [`PLUGINS_GUIDE.md`](PLUGINS_GUIDE.md)。

## 记忆与知识库

长期记忆在 `memory/memory.sqlite`。主对话不会把全部历史塞进提示词，而是按当前问题召回少量相关记忆。向量索引可从 SQLite 重建；人设只取当前有效记录。闲聊默认不查知识库，明确问设定 / 资料时才会注入，并带来源文件名。

本地 Ollama 向量模型需要 `ollama serve` 一直开着。升级后第一次建议在知识库管理里「重建索引库」再「一键学习」。

## 项目结构

```text
core/          应用启动、生命周期、GUI/QQ/MCP/传感器初始化
services/      对话主流程、工具调用、回复输出和辅助服务
modules/       Live2D、TTS、记忆、GUI、插件管理等基础模块
plugins/       插件目录
integrations/  QQ 网关、GUI HTTP/WS、外部系统适配
assets/        图标、Live2D 模型和 README 预览图
tests/         自动化测试
```

## 开发验证

```bash
python -m pytest
python -m py_compile main.py boot.py setup_env.py launcher.py
```

只验证 Live2D 动作和角色编辑：

```bash
python -m pytest tests/test_live2d_motion_candidates.py tests/test_character_editor_preview.py
```

改了 Live2D 桌面端前端时，在对应前端目录运行 `npm run build`；打安装包用 `npm run tauri build` 或 `build.bat`。

启动失败、环境安装失败先看 `logs/setup_env.log` 和 `logs/launcher.log`。

## 说明

这是一个偏个人化、本地化的桌面助手。部分功能依赖本机环境、第三方模型、NapCat、TTS 或外部 API。第一次建议先配好模型、语音和 QQ，再逐步打开远程控制、邮件和 MCP 等高权限能力。
