# 架构文档

实现以代码和测试为准。这里只固定边界、入口和「已经做完 / 还没做」的状态，避免和审查稿里的当时快照混在一起。

| 文档 | 用途 |
| --- | --- |
| [memory.md](memory.md) | 记忆 / 知识库契约：谁是事实源、GUI 页、召回、写回、知识门控 |
| [shipped-features.md](shipped-features.md) | 已落地行为摘要（搜索确认、徽章、知识门控、自然聊、GUI 接线） |
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

### 插件 ctx

`process()` 现在同时写入：

| 键 | 含义 | 可否删除 |
| --- | --- | --- |
| `chat_service` | 整颗 ChatService | **否**。QQ 发信、TTS、点歌、邮件、MCP、技能、换角仍靠它 |
| `brain` | AdvancedMemory 门面 | **否**。`search` 跟进话题、`memory_tools`、跨通道回退仍靠它 |
| `knowledge` | `BrainKnowledgePort` | 窄口：导入 / 检索 / 统计。`local_knowledge` 优先用它，没有则回退 `brain` |

`services/plugin_ports/knowledge.py` 只转发，不改导入语义。Gateway / Presenter / App 端口还没做，**禁止**先撤 `chat_service`。

---

## 收敛批次

| 批次 | 状态 | 做什么 |
| --- | --- | --- |
| P0 | 已做 | Thought 真超时与 max tokens；禁词先于展示；`VISION_MODE=separate`；表情条数=1 |
| P1 | 已做 | 闲聊表情改词法；短期记忆窗口取一次下传 Thought/表情；禁词重试独立 caller |
| P2 | 已做 | 记忆/知识对话框接 `gui_api`；共用 ingest |
| P3 | 已做 | 删闲聊 polish 双轨；删未接线的 `modules/memory/prompt_builder.py` |
| P4a | 已做 | 加 `ctx["knowledge"]`，不删旧键 |
| P4b | 未做 | 发消息 / TTS / app 窄端口齐了，再撤 `ctx["chat_service"]` |
| P5 | 未做 | 仅直聊委托给拥有完整顺序的编排对象；不要再抽空头 pipeline |

未做部分的风险说明见审查稿第 7 节（尤其 7.5：按字面收窄插件 ctx 会破发）。

仍进行中的大计划只留：`2026-07-16` 前后端分离、`2026-08-05` 连续性 Task 12、`2026-08-13` 知识/人设未做批次。做完的实施勾选表不要再加回 `superpowers/plans/`。
