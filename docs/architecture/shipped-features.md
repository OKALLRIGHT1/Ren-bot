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
