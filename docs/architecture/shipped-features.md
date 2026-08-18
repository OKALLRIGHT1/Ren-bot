# Shipped Feature Notes

已落地、不再维护长篇 plan/spec 的行为摘要。需要实现细节时查代码与测试。

## 联网搜索：即时确认 + 确定性执行

- 搜索插件类型必须是 `delegate`（config 与 class 一致）。
- 路由识别为搜索后：先发一条**短暂**、带主题的确认语（本地模板，不再次调聊天模型），再执行 delegate。
- 确认语展示/播报，**不写入**聊天 transcript 与长期记忆。
- 搜索成功或失败都必须再发**第二条**明确结果；不能只停在「我去查一下」。
- 实现入口：`services/chat_support/search_flow_service.py` 与 ChatService 搜索路由。

## 助手徽章（Assistant Badge）

- 元数据在 `data/characters.json`；图片副本在 `data/assistant_badges/`（不存用户本机绝对路径）。
- 角色默认徽章；服装可选覆盖。解析顺序：服装 → 角色 → 无托管徽章（前端可回退缓存/内置脸）。
- 接口：`/characters/badge/current|import|update|clear` 等（见 `services/gui_api/assistant_badge_service.py`）。
- 限制：图片 ≤ 10 MiB；scale `0.5..3.0`；offset `-1.0..1.0`。Data URL 仅响应、不持久化。

## 嵌入模型目录

- 模型目录统一管理嵌入模型；运行时只选 `embedding_model_id`。
- 同模型同维度可复用索引；换模型/维度需重建，不自动清空知识数据。
- 详见 `docs/architecture/memory.md`。

## 知识库可用化 + 人设 current（2026-08-13）

已落地计划 `docs/superpowers/plans/2026-08-13-knowledge-and-persona-timeline.md` 的 **B0 + A0–A4**。未做 A5/A6 与 B1–B4。

- 闲聊默认不查知识库；明确说「设定 / 资料 / 知识库 / 文档里 / 词条」才自动注入，并带来源和资料约束。
- 普通文档按段落分块；未改文件且 `chunker_version` 一致才 skip；升级后的旧按行清单会自动重导。改过会替换旧块。
- 人设只取当前有效；纠正原子 supersede；人设不带会话号；没有通用 `valid_days` TTL。
- 关系 / 经历时间线 / 「以前怎么样」历史召回还没做。
- 契约见 `docs/architecture/memory.md`。

## 角色自然聊 + 接线收敛（2026-08-18）

已按 `docs/architecture/README.md` 落地 P0–P4a。

- 桌面 / QQ 私聊：Character Thought → 词法表情（默认 1 条）→ 主回复 → 禁词守卫。Thought 超时走 `chat_with_ai(..., timeout_sec=)`，`max_tokens` 已接线。
- 作用域内且要出声或出气泡时走非流式，守卫发生在 catchphrase / share 之前。
- 闲聊不再二次 polish；`qq_polish_mode` 与未接线的 `modules/memory/prompt_builder.py` 已删。传感器观察报告改写、工具直出软化仍在。
- `VISION_MODE` 默认 `separate`；一次视觉+说话用环境变量 `VISION_MODE=direct`。
- 设置 → 记忆 / 知识走 `MemoryGuiService` / `KnowledgeGuiService`，与聊天共用进程内 Memory Core；导入走 `ingest_knowledge_paths`。
- `ctx["knowledge"]` = `BrainKnowledgePort`。`ctx["brain"]`、`ctx["chat_service"]` 仍在。未做 P4b / P5。

## 运行健康 + Rust 纯文本屏幕

- 进程内 `RuntimeHealthCenter` 只观测；Qt 顶栏与详情窗读同一 `snapshot()`，不经 HTTP 回环。
- 屏幕吐槽走 Tauri 文本事件，不在 Python 里截图/视觉升级。
- 同页挂机不得说「打开了 N 次」：会话去抖（默认离开 ≥ 90s 再回才 +1），见 `docs/TROUBLESHOOTING.md` 4.x 对应节。
