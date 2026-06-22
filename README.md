# Live2D-LLM

一个可本地运行、可接 QQ、可挂插件、可连 MCP 的 Live2D 桌面助手主干。

当前项目重点不是单纯“接一个聊天模型”，而是把桌面陪伴、长期记忆、知识库、表达学习、表情包、TTS、屏幕感知、QQ 网关和插件工具统一到一个可配置的本地应用里。

## 快速开始

```bash
conda create -n live2d-llm python=3.10
conda activate live2d-llm
pip install -r requirements.txt
python boot.py
```

如果要走守护进程模式，改用：

```bash
python main.py
```

Rust 活动采集器是可选增强：

```bash
cargo build --release --manifest-path rust-activity-agent/Cargo.toml
```

如果不想看到命令行窗口，可以使用根目录的启动器：

- `Live2D-Suzu.exe`
- `启动-无窗口.vbs`

## 主要能力

- Live2D 桌面陪伴、气泡、动作、表情和口型同步
- GPT-SoVITS / Edge TTS 路由，支持关闭 TTS 后的文字口型兜底
- QQ / NapCat 接入，支持私聊、群聊、图片识别、语音回复和远程截图
- 切换角色时可同步 QQ 昵称和头像；不会主动修改签名、说说或在线状态
- 插件系统，支持 `react` / `direct` / `observe` / `delegate`
- ClawEmail 邮件插件走 `direct` 命令，可直接处理 `/查邮件`、`/邮件诊断` 等邮件请求
- 硬件状态问法会优先走系统监控插件，再由 LLM 润色并按 QQ / 本地气泡分段输出
- 本地记忆、每日总结、屏幕活动日记和 QQ 会话隔离
- 知识库导入、检索、慢速导入、Ollama `bge-m3` embedding 兼容
- 聊天学习 / 表达学习库，用于降低总结腔和客服腔
- 数据库版表情包系统，支持标签、情绪、描述、批量启停和 LLM 语义选择
- 模型供应商、模型路由、任务路由和 GUI 设置中心
- 可选 Rust sidecar 活动采集
- 兼容轻量 `SKILL.md` 运行时 Skill

## 常用目录

- `core/`：应用启动、事件总线接线、GUI/QQ/MCP/传感器初始化
- `services/`：主对话、屏幕感知、总结、网关输出等服务逻辑；`services/chat_support/` 放已拆出的对话辅助服务
- `modules/`：Live2D、TTS、记忆、GUI、插件管理、知识库等基础模块
- `plugins/`：插件目录，每个插件通常包含 `config.json` 和 `plugin.py`
- `integrations/`：QQ 网关、GUI HTTP/WS、外部系统适配
- `data/`：运行时配置、角色、学习库、部分索引和状态文件
- `docs/`：专题文档与历史归档

## 文档入口

- 当前状态与最近改动：[PROJECT_STATUS.md](./PROJECT_STATUS.md)
- 插件结构、权限和插件清单：[PLUGINS_GUIDE.md](./PLUGINS_GUIDE.md)
- QQ / NapCat / MCP 接入：[MCP_QQ_SETUP_GUIDE.md](./MCP_QQ_SETUP_GUIDE.md)
- 常见问题与排障：[docs/TROUBLESHOOTING.md](./docs/TROUBLESHOOTING.md)
- Skill 目录约定与命令：[skills/README.md](./skills/README.md)
- 远程后端 + 本地 Live2D/Tauri GUI 方案：[docs/REMOTE_LIVE2D_GUI_PLAN.md](./docs/REMOTE_LIVE2D_GUI_PLAN.md)
- 其它专题与历史归档：[docs/README.md](./docs/README.md)

## 当前建议阅读顺序

1. 先看 [PROJECT_STATUS.md](./PROJECT_STATUS.md)，了解当前项目在做什么。
2. 再看 [MCP_QQ_SETUP_GUIDE.md](./MCP_QQ_SETUP_GUIDE.md)，处理 QQ 和 MCP 接入。
3. 需要改插件时，看 [PLUGINS_GUIDE.md](./PLUGINS_GUIDE.md)。
4. 需要改 Skill 时，看 [skills/README.md](./skills/README.md)。

## 维护原则

- 当前真实状态优先看 `PROJECT_STATUS.md`，历史长文只作为上下文。
- 配置优先通过 GUI 或 `data/runtime_settings.json` 管理，`config.py` 主要保留默认值和兜底值。
- 对话主链路现在仍集中在 `services/chat_service.py`，该文件偏长；后续重构应优先做“拆服务、不改行为”，优先复用已拆出的 `services/chat_support/gateway_sender.py`，不要直接删逻辑。
