# Live2D-Suzu

Live2D-Suzu 是一个本地运行的 AI 桌面助手项目。它以 Live2D 桌面形象为前端，把对话、语音、长期记忆、QQ/NapCat 接入、插件工具、信息源查询、邮件、音乐、截图、桌面活动感知和久坐提醒整合到一个可配置的本地程序里。

这个项目的目标不是做一个简单聊天窗口，而是让一个本地角色能够在桌面上长期陪伴、响应消息、调用工具、记住上下文，并通过 Live2D 形象、语音和动作表现出来。

## 主要功能

- Live2D 桌面形象：气泡、表情、动作、口型同步、换装和模型预览。
- 角色系统：角色提示词、服装、默认动作映射、TTS、QQ 昵称和头像配置。
- 情绪动作映射：根据回复情绪触发 Live2D 表情和动作，支持一个情绪配置多个动作随机播放。
- 空闲动作：空闲一段时间后随机播放动作，并自动回到 idle 状态。
- 语音合成：支持 GPT-SoVITS、Edge TTS 等路由方式。
- QQ / NapCat 接入：支持私聊、群聊、图片理解、语音回复和主人远程指令。
- 插件系统：邮件、音乐、信息源、截图、应用控制、网页读取、技能运行等能力可通过插件扩展。
- 统一能力层：插件可以声明自然语言能力、命令能力和可用性检查，天气、文件、Codex、绘图等工具调用会先统一判定再执行。
- Agent 工具调用：主程序可以按权限调用本地工具、用户目录、MCP、Codex/Claude 等能力，并对高风险操作做确认。
- Memory Core 2.0：以 SQLite 为单一事实源，支持聊天印象、候选精排、人物画像、证据追踪、表达学习、回复反馈和活动统计查询。
- 桌面活动感知：由 Live2D/Tauri 端作为唯一活动来源上报状态，主程序只消费 `/gui/activity-ingest` 数据，用于久坐提醒、桌面状态判断和日报统计。
- 久坐提醒：支持提醒间隔、休息重置、冷却时间、弹窗文案、表情包和顶部状态显示；设置变更后通过认证活动配置接口通知增强版采集端热刷新。
- 设置中心：常用配置可在 GUI 中调整，减少手动改配置文件。

## 项目结构

```text
core/          应用启动、生命周期、GUI/QQ/MCP/传感器初始化
services/      对话主流程、工具调用、回复输出和辅助服务
modules/       Live2D、TTS、记忆、GUI、插件管理等基础模块
plugins/       插件目录
integrations/  QQ 网关、GUI HTTP/WS、外部系统适配
data/          运行时配置、角色数据、状态数据和本地缓存
tests/         自动化测试
```

## 快速开始

推荐使用 Python 3.10+。

```bash
conda create -n live2d-llm python=3.10
conda activate live2d-llm
pip install -r requirements.txt
python main.py
```

`main.py` 是推荐启动入口，会拉起 `boot.py` 并接管远程重启和异常恢复。`boot.py` 只适合开发时临时调试核心进程。

```bash
python boot.py
```

Windows 下也可以使用根目录中的启动脚本或打包后的可执行文件启动。

## Live2D 桌面端

Live2D 桌面端可以作为独立程序运行，也可以和主程序通信。兼容链路下，主程序仍可通过本地 WebSocket 连接桌面端，发送气泡文本、表情、动作、TTS 状态和久坐提醒等事件。

桌面端会根据模型版本选择合适的 Live2D 运行时，兼容 Cubism 2/3/4 模型；不同模型的缩放和位置可以在前端手动调整。

常见使用方式：

1. 先启动 Live2D 桌面端。
2. 再启动主程序。
3. 在设置中心选择角色、模型、服装、动作映射和语音配置。

如果只需要桌面形象和基础久坐提醒，Live2D 桌面端也可以独立运行。

## 与增强版前端分离连接

Python 主程序可以独立运行：聊天、插件、记忆、旧 Qt GUI 都不依赖增强版前端。增强版（Tauri）通过连接档案主动连接本机或远程 Python 后端，不再要求远程服务器回调桌面 `127.0.0.1:10086`。

后端暴露的 GUI HTTP / WebSocket 端点：

- 本机 loopback 可使用明文 `http://127.0.0.1:<port>/gui` 与 `ws://127.0.0.1:<port>/gui`。
- 非 loopback 地址必须经 TLS 反向代理，使用 `https://...` 与 `wss://...`。
- 访问令牌通过 `X-GUI-Token` 请求头认证；令牌保存在运行时设置 / 环境配置中，不要写入普通配置仓库或日志。

增强版会主动建立认证 WebSocket，完成 hello / 能力协商，并在同一连接上承载控制中心事件与 Live2D 协议信封。桌面活动与久坐配置由增强版通过认证 API 获取与上报：

- `GET /gui/activity-config`：仅返回客户端安全字段。
- `POST /gui/activity-ingest`：仅接受 `source=live2d-tauri` 的活动事件。

Rust/Tauri 是桌面活动的唯一采集源；Python 只消费上报结果。旧 Python Qt GUI 继续保留，不会被默认关闭或删除。

## 常用配置

- `.env`：本机私密配置，例如 API Key、端口、模型服务地址。
- `data/runtime_settings.json`：GUI 保存的运行时设置。
- `data/characters.json`：角色、服装、动作映射、TTS、QQ 档案。
- `plugins/*/config.json`：各插件自己的配置。
- `config.py`：默认值和旧配置兼容层。

不要把真实密钥提交到仓库。发布前请检查 `.env`、日志、缓存和本地数据库是否被误加入版本控制。

## 角色与动作

角色编辑器中可以为每个角色和服装配置 Live2D 动作映射。动作映射支持：

- 每个情绪选择一个或多个动作。
- 多动作随机播放。
- 停止动作。
- 使用模型刚加载时的默认姿态。
- 为不同服装设置不同动作覆盖。

动作优先级为：

```text
服装映射 > 角色默认映射 > 模型自动推导 > 全局默认映射
```

## QQ 与插件

QQ 接入主要通过 NapCat / OneBot 网关。插件可以提供命令、自然语言工具调用、观察事件和后台委托能力。

常见插件能力包括：

- 查询和发送邮件
- 点歌和音乐播放
- 天气、日报和其他信息源查询
- 截图、文件浏览和网页读取
- 应用控制和远程重启
- 本地技能运行
- Codex / Claude 任务委托

插件内部需要语言模型时，统一从“设置中心 → 模型与路由”读取模型，不再要求每个插件重复填写模型地址、密钥和上游模型名。模型可勾选聊天、推理/工具、总结、联网搜索、视觉、画图、代码、翻译或向量等用途；插件设置只显示用途匹配的模型，并允许按顺序组成故障转移队列。模型队列留空时跟随对应任务路由；联网搜索在新路由未配置时仍兼容原 Grok/Exa 设置。

涉及发送邮件、安装能力、修改配置、删除数据、远程重启等高风险操作时，建议保持确认流程开启。

## Memory Core 2.0

长期记忆统一保存在 `memory/memory.sqlite`。主对话不再把旧 Notes、Episodes、Profile、Graph 或 Chroma 聊天记录整批注入提示词，而是按下面的流程读取：

```text
当前问题 + 最近对话
  -> 记忆意图识别
  -> 聊天印象
  -> SQLite 文本候选 + 当前 Chroma 语义候选
  -> 按 record_id 合并并执行人物/会话硬过滤
  -> 轻量 LLM 精排（允许返回空）
  -> 有长度上限的记忆上下文
```

Memory Core 同时维护：

- `memory_records`：事实、偏好、规则、画像、事件和摘要。
- `memory_evidence`：每条学习结果对应的原始消息证据。
- `persons` 与 `person_profile_snapshots`：按本地 owner 或 QQ 用户隔离的人物画像。
- `reply_feedback`：回复后的正负反馈，用于降低不合适表达的权重。
- `memory_query_log`：召回候选和最终选择记录，便于排查无关记忆。
- `memory_vector_jobs`：当前向量索引的待处理、已索引和失败状态；索引可从 SQLite 全量重建。
- `expression_patterns`：按角色和语境选择的表达模式。

首次升级会在 SQLite 同目录创建一次性备份：

```text
memory/memory.pre-memory-core-v1.bak.sqlite
```

旧 `profile.json`、`learning.db` 和 reply effect 数据只会幂等迁移，不再作为运行时写入目标。记忆管理中心分为“档案概览 / 记忆记录 / 原始对话 / 向量与检索”；概览按人物及“喜欢 / 音乐 / 游戏 / 食物 / 习惯 / 近期状态”等语义分区展示，记录页负责完整编辑。手动调整分类仅写入记录的 `metadata.category_override`，不会产生第二套分类事实源。用户与 QQ 人物使用独立人物档案，角色补充档案使用稳定 `character:<角色ID>` 隔离，并只注入当前角色的自我认知上下文。

当前 Memory Core 向量集合是 `memory_records` 的派生索引，SQLite 始终是唯一事实源。人设默认只取当前有效记录（`is_current`）；纠正会废止旧条而不是并列两条当前习惯。人设事实不带 desktop / QQ 会话号。向量模型也在“设置中心 → 模型与路由”统一维护：给模型勾选“向量”，填写嵌入路径和维度，再到记忆管理中心的“向量与检索”页单选、测试并保存；保存后重启生效。向量模型不进入普通 LLM 多模型回退链。模型名或维度变化时，程序会阻止新旧向量混用并要求显式重建；相同 `bge-m3/1024` 的 Memory Core 派生索引迁移不会触发重建。资料知识库与人设分开：普通文档按段落导入，未改文件再学会跳过；闲聊默认不查知识库，只有明确问设定 / 资料时才自动注入，并带来源文件名。非空且没有模型元数据的旧知识库无法证明向量来源，会要求清空后从原知识文件重新导入。旧 `waifu_memory_advanced` 继续作为按需加载的只读历史查看器。Embedding 失败时保留索引任务错误并退回 SQLite 文本召回，不会写入零向量，也不会切换到不同维度的本地模型伪装成功。知识库导入清单在 `data/knowledge_import_manifest.json`。

角色日记使用 `episodes` 中的 `daily_log` 记录，并在“设置中心 → 高级 → 日记”单独管理。日记窗口支持搜索、编辑、删除、Markdown 导出和独立窗口打开；原始 Transcript 页面默认不再重复显示日记归档消息。

可通过环境变量调整核心限制，修改后需要重启。`EMBEDDING_*` 仅在“向量与检索”页没有选择目录模型时作为兼容配置生效：

```text
MEMORY_CORE_ENABLED=1
MEMORY_CORE_PROFILE_MAX_ITEMS=6
MEMORY_CORE_CANDIDATE_LIMIT=12
MEMORY_CORE_FINAL_LIMIT=3
MEMORY_CORE_CONTEXT_MAX_CHARS=1200
MEMORY_CORE_IMPRESSION_WINDOW=8
MEMORY_CORE_PROFILE_LEARNING_ENABLED=1
MEMORY_CORE_EXPRESSION_LEARNING_ENABLED=1
MEMORY_CORE_LEARNING_BATCH_MESSAGES=10
KNOWLEDGE_AUTO_RETRIEVAL_ENABLED=1
EMBEDDING_ENABLED=1
EMBEDDING_PROVIDER=ollama
EMBEDDING_API_URL=http://127.0.0.1:11434/v1/embeddings
EMBEDDING_MODEL_NAME=bge-m3
EMBEDDING_EXPECTED_DIMENSION=1024
```

使用本地 Ollama 向量模型时，`ollama serve` 必须保持运行。知识库管理和知识插件会明确显示连接错误；普通聊天会跳过本轮知识召回继续回复，不会把连接失败伪装成“知识库没有内容”。把 `KNOWLEDGE_AUTO_RETRIEVAL_ENABLED` 设为 `0` 只关闭闲聊自动查库，插件和知识库管理窗的手动搜索仍可用。升级后第一次请在知识库管理里「重建索引库」再「一键学习」；旧清单没有分块版本时，再点学习也会按新段落重导。步骤见 `docs/TROUBLESHOOTING.md` 第 4.1 节。记忆与知识库边界见 `docs/architecture/memory.md`，当前接线与收敛批次见 `docs/architecture/README.md`，人设/知识时间线计划见 `docs/superpowers/plans/2026-08-13-knowledge-and-persona-timeline.md`。

统一能力插件 `memory_tools` 提供 `memory.query`、`memory.person_profile` 和 `activity.query`，自然语言请求会通过现有能力层交给副脑执行。远程 QQ 仅 owner 可调用，其他 QQ 不能读取本机活动或长期记忆。

## 开发验证

常用检查：

```bash
python -m pytest
python -m py_compile main.py boot.py
```

只验证 Live2D 动作和角色编辑相关逻辑：

```bash
python -m pytest tests\test_live2d_motion_candidates.py tests\test_character_editor_preview.py
```

如果修改了 Live2D 桌面端前端，请在对应前端目录运行：

```bash
npm run build
```

## 说明

这是一个偏个人化、本地化的桌面助手项目。部分功能依赖本机环境、第三方模型服务、NapCat、TTS 服务或外部 API。首次运行前建议先完成基础模型、语音、QQ 和插件配置，再逐步开启远程控制、邮件和 MCP 等高权限能力。
