# 架构文档

实现以代码和测试为准。这里只固定边界、入口和「已经做完 / 还没做」的状态，避免和审查稿里的当时快照混在一起。

| 文档 | 用途 |
| --- | --- |
| [memory.md](memory.md) | 记忆 / 知识库契约：谁是事实源、GUI 页、召回、写回、知识门控 |
| [shipped-features.md](shipped-features.md) | 已落地行为摘要（搜索确认、徽章、知识门控、自然聊、GUI 接线、久坐弹窗） |
| [project_slimdown.md](project_slimdown.md) | 瘦身规则；不要一次重写 `ChatService` / Settings |
| [coupling-and-overdesign-review.md](coupling-and-overdesign-review.md) | 2026-08-18 审查原文 + 后续批次落地状态 |

---

## 当前接线（2026-08-18 之后）

### 闲聊主路径

一次桌面 / QQ 私聊大致是：

1. `ChatService.process()` 装配 ctx、路由工具 / 日记 / 传感器
2. 普通直聊：Character Thought（HTTP `timeout_sec` + `max_tokens`）→ 词法表情（默认 1 条）→ `AdvancedMemorySystem.build_prompt`（知识门控）→ 主回复 → 禁词守卫 → 展示
3. 闲聊**不再**二次 polish（`qq_polish_mode` 已删）。传感器草稿像观察报告时仍可改写；工具直出文本的软化还在

入口：`services/chat_service.py`、`services/chat_support/character_thought.py`、`natural_chat_pipeline.py`、`forbidden_phrase_guard.py`、`modules/memory/knowledge_gate.py`。

`VISION_MODE` 默认 `separate`（先描述再说话）。一次看图就说：环境变量 `VISION_MODE=direct`。

### 记忆 / 知识 GUI

- 设置 → 记忆：注入进程内 `brain.memory_core`，经 `MemoryGuiService` 读转写 / 向量；已初始化的 Core 不再 `initialize()` 第二套。
- 设置 → 知识：`KnowledgeManagerDialog` 走 `KnowledgeGuiService`（统计、搜索、重建、删除、导入）。
- 文件导入共用 `ingest_knowledge_paths()`：GUI worker、插件 `learn` / `gui_ingest`、门面 `learn_configured_dirs`。

独立打开记忆窗口（无 live core）仍可自己建一套，仅给测试 / 脱离主进程的场景。

### 设置壳

`modules/gui/dialogs/settings.py` 只负责导航、叠页和 `showEvent` 刷新目录。已经外提的页在 `modules/gui/settings_pages/`：久坐、ASR、信息源、颜色、依赖、提供商、模型路由。提供商 / 模型共用 `catalog_io.py` 读写 `data/custom_models.json`。模型编辑弹窗仍留在设置壳里（测试从那里 import）。QQ / MCP 仍和壳缠在一起，这轮没动。

### 插件 ctx

`process()` 现在同时写入：

| 键 | 含义 | 可否删除 |
| --- | --- | --- |
| `gateway` | `ChatGatewayPort` | 窄口：QQ 发文本 / 发图 / 拉回复消息。插件优先用它 |
| `tts` | `TtsPort` | 窄口：开关 TTS |
| `app` | `AppPort` | 窄口：换角、本机根目录、点歌事件 |
| `plugin_manager` | 已加载插件表 | 帮助页等只列插件时用 |
| `agent_runtime` | AgentRuntime | `/agent` 自检 |
| `mcp_bridge` | MCP 桥 | `mcp_tools` 优先用它 |
| `knowledge` | `BrainKnowledgePort` | 窄口：导入 / 检索 / 统计 |
| `skills` | `SkillManager` 或 `None` | **可选**。只给 `SKILL.md` 提示词注入；不是插件执行层。不用 Skill 可忽略 |
| `brain` | AdvancedMemory 门面 | **否**。`search` 跟进话题、`memory_tools`、跨通道回退仍靠它 |
| `chat_service` | 整颗 ChatService | **已撤。** 插件不得再读此键 |

入口：`services/plugin_ports/`。生产 `process()` 注入 `gateway` / `tts` / `app`。`resolve_*` 仍可从测试里的 `chat_service` 拼端口，主路径不注入该键。

**Skill 不是插件。** 执行能力一律 `plugins/`。`skills/` 只注入提示词；没有 `SKILL.md` 时 `skill_runtime` 显示未初始化即可，不为它开第二套端口。

---

## 收敛批次

| 批次 | 状态 | 做什么 |
| --- | --- | --- |
| P0 | 已做 | Thought 真超时与 max tokens；禁词先于展示；`VISION_MODE=separate`；表情条数=1 |
| P1 | 已做 | 闲聊表情改词法；短期记忆窗口取一次下传 Thought/表情；禁词重试独立 caller |
| P2 | 已做 | 记忆/知识对话框接 `gui_api`；共用 ingest |
| P3 | 已做 | 删闲聊 polish 双轨；删未接线的 `modules/memory/prompt_builder.py` |
| P4a | 已做 | 加 `ctx["knowledge"]`，不删旧键 |
| P4b | 已做 | 窄口齐；`process()` **不再**写入 `ctx["chat_service"]`。Skill 可选，不为此开端口 |
| P5 | 已做 | 普通直聊 / Direct / 非流式工具 / 流式残留已有完整编排入口；Prompt 走 `PromptParts`；插件端口与 `AgentRuntime` 不再包整颗 `ChatService`。计划：`docs/superpowers/plans/2026-09-07-chat-service-p5-natural-chat.md` |

审查稿 7.5 的「按字面立刻删键会破发」是当时的风险；P4b 已经先补端口再撤键。`brain` 仍在。P5 已做：普通直聊走 `run_plain_chat()`；Direct 走 `run_direct_turn()`；非流式工具走 `run_non_stream_tool_turn()`；流式残留走 `run_stream_turn()`；闲聊与工具路径 Prompt 共用 `assemble_chat_context_messages()`；任务跟进 / 主动关心走 `FollowupService`；后台委托走 `run_background_delegate_task()`；屏幕/点歌走 `SensorEventService` / `run_music_event`；QQ 发送者/资料走 `GatewayContextService`；日记回填走 `DiaryService`；回复门控走 `reply_gatekeeper.should_reply`；插件端口不再保存整颗 `ChatService`；`AgentRuntime` 不再持有 `ChatService`。

**工具轮下一步（不是拆 ChatService）：** 第三波 schema 已在 `music_player` / `mode_preset` / `local_knowledge` / `diary_export`。direct（邮件、QQ 点歌、`user_files`）不改 type。HIGH（锁屏 / 执行代码 / 备份）没有确认测试不加 schema。计划：`docs/superpowers/plans/2026-08-21-tool-calling-function-call.md` §0.4 / PR8。

仍进行中的大计划只留：`2026-07-16` 前后端分离、`2026-08-05` 连续性 Task 12、`2026-08-13` 知识/人设未做批次、`2026-08-21` 工具轮部分 function call（默认 `dual`，可回 `cmd`）、`2026-08-27` 本机 ASR 换 Qwen3-0.6B（代码已接，待下载模型）。做完的实施勾选表不要再加回 `superpowers/plans/`。
