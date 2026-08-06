# Memory System

当前记忆系统的长期事实说明。实现细节以代码为准；本文件只固定边界与用户可见组织方式。

## 事实源

- SQLite `memory_records`（经 Memory Core / `memory_sqlite`）是**语义记忆的唯一写入真相源**（偏好、事实、画像、证据等）。
- `transcript`：原始对话。
- `memory_items`：**仅任务存储**，产品允许的 type 默认只有 `todo`。不要把偏好/事实写入此表；HTTP `/memory/items*` 已按此门禁，非 todo 写入失败。
- Chroma 等向量库只保存**派生索引**，损坏后必须能从 SQLite 重建。
- 旧 `waifu_memory_advanced` 集合仅只读查看，不参与当前 Memory Core 召回。
- 日记使用独立窗口与调度，不混入语义分类树的「经历与事件」。
- 长期方向：`todo` 可迁到独立 tasks 表；在此之前**禁止扩大** `memory_items` 业务语义。

## 语义分类树（GUI）

记忆记录页按用户可理解的语义树组织，不改变召回 / 画像 / 证据逻辑。

固定分类（空分类数量可为 0）：

- 全部记忆
- 称呼与身份
- 喜欢（音乐 / 动漫与角色 / 游戏 / 食物 / 绘画与审美 / 其他）
- 不喜欢
- 习惯
- 近期状态
- 互动与回复规则
- 经历与事件
- 待办与承诺
- 未分类

自动分类为确定性规则（`modules/memory_core/categories.py`），优先级：

1. `metadata.category_override` 手动覆盖（稳定 ID，如 `likes.music`）
2. 稳定键前缀
3. `kind` / 来源标签
4. 未分类

不新增独立 `category` 列，避免与稳定键双事实源。手动覆盖只影响 GUI 组织。

## 记忆窗口页

1. **档案概览**：按人物投影称呼、喜欢、不喜欢、习惯、状态、互动规则。
2. **记忆记录**：分类树 + 搜索 / 筛选 / 编辑 / 归档 / 删除。
3. **原始对话**：Transcript。
4. **向量与检索**：索引健康、任务队列、模型与维度；旧向量只读折叠区。

档案概览与记忆记录是同一批 SQLite 记录的两种投影。

## Embedding 与向量索引

- 共享 `modules/embeddings`：`EmbeddingService` 负责调用；`catalog` 从模型目录解析配置。
- `data/custom_models.json` 存模型接口与嵌入字段；`data/runtime_settings.json` 只存当前 `embedding_model_id`。
- 未选择目录模型时，才回退 `EMBEDDING_*` 环境兼容配置。
- embedding 失败保留 pending，召回退回文本候选；**禁止零向量伪成功**。
- 同一集合禁止混用不同模型或维度；模型/维度变化标记 `rebuild_required`，用户确认前不混写、不静默删旧数据。
- `memory_vector_jobs` 与记录变更同事务入队；后台同步写 indexed / failed。

## 混合召回

1. 文本候选
2. 向量候选（可用时）
3. 按 `record_id` 合并后重排
4. 交给既有 `memory_selector`

人物 / 会话范围必须先过滤再重排。向量不可用时只走文本，并在 diagnostics 说明原因。

## 近史与中期会话连续性

- SQLite `conversation_events` 是“刚才发生了什么”的唯一权威源；普通消息、主动行为、屏幕观察、关怀和工具事件共用同一事件模型。
- `short_term` 是携带 `event_id` 的热窗投影。裁切后只把事件 ID 交给中期分段构建器；缓存失效时可从 events 重建对话热窗。
- `mid_term_segments` 是 events 的不可变压缩投影，必须保留 `source_event_ids`，不写回长期 Memory Core。
- `ContextAssembler` 是近因与中期上下文的唯一读出口。注入优先级固定为：当前消息 > short-term / recent 原始事件 > Active Session State > 历史中期片段 > 长期 Memory Core。
- 最新 segment 常驻为 Active Session State，并确定性补齐该段之后的原始事件；已经出现在热窗或 recent block 的事件按 `event_id` 去重。
- 更老 segment 只通过 embedding 相关度按需召回，默认最多 1 条；embedding 不可用或失败时跳过历史中期召回，不使用字符串包含 fallback，也不影响 Active Session 和近因主链。
- desktop、QQ 私聊、QQ群按 `persona_id + person_id + channel + conversation_id` 硬隔离。长期语义记忆的共享桶 `owner_shared` 不得代替中期会话的 `conversation_id`。
- LLM 摘要只允许引用其声明的来源事件；数字、日期、路径、URL、实体、助手承诺和工具未决状态必须经来源校验。非法输出降级为只复制来源原文的低置信 `stub`。
- `ContextAssembler` 是最终预算边界：recent 900 字符 / 3 项、Active Session 500 字符、历史中期 1800 字符 / 默认 1 段、长期 1200 字符。先跨层去重，再按完整行或完整 segment 裁切；Active Session 优先保留最新段后原始事件。
- 每轮 trace 只记录 conversation ID、候选 / 入选 event ID、选择理由、segment ID、各层字符数和去重位置；不得复制 QQ 正文。显式传入的 candidates 仍必须在 Assembler 内重新按完整 scope 过滤。
- 旧 `_looks_like_sensor_source_followup()` / `_build_sensor_source_followup_context()` 关键词与观察拼接路径已删除；`_recent_sensor_replies` 只用于屏幕生成防复读，不参与聊天 prompt 近史读取。
- `short_term_from_events=1` 与 `mid_term_enabled=1` 现已默认开启：对话热窗从 events 投影，窗口滑出后生成中期 segment，并在后续轮次注入 Active Session / 按需召回历史段。出现运行问题时可分别设置对应环境变量为 `0` 并重启回滚；旧 transcript 热窗仅作为关闭投影或缺少有效 scope 时的兼容路径。

`mid_term_segments` 与长期写回的 `memory_records(kind=summary)` 不是同一层：前者只承托当前 conversation 的连续性，后者保存可跨会话使用的长期语义摘要。两者不得互相回写或重复入库。

## 长期记忆写回（Person Fact / Chat Summary）

实现：`services/memory_writeback.py`，由 `MemoryCoreService.record_message` 在写入 transcript 后触发。

- 生产：后台线程队列（不阻塞聊天主路径）。
- 测试 / 调试：`memory_writeback_inline=1` 同步处理。
- 与 profile / expression 学习开关**解耦**：关闭画像学习时写回仍可运行（只要 `memory_core_enabled` 与 `memory_writeback_enabled` 开启）。
- 进程退出：`Application.cleanup` → `MemoryCoreService.stop_writeback()` 停 worker。

原则（对齐 MaiBot PersonFactWriteback，不整包搬 A_memorix）：

1. **transcript 总是保留**（原始证据）。
2. **`memory_records` 仅在模型抽出稳定、可被用户原话支持的事实时写入**；空结果 `items:[]` = 不记。
3. 事实值不得只来自助手回复；邻近上下文只补指代/短答；`evidence_ids` 必须能落到窗口内用户消息（或 `trigger:` 合成 id）。
4. 用户纠正优先：同一 `subject_id + kind + key` 内容变化时旧记录 `superseded`；`supersede_keys` 可主动废止其它 key。
5. 触发：
   - `assistant_reply`：助手非客套回复后，用最近用户证据抽取；
   - `explicit_user`：用户明确习惯/纠正/「记住」类信号时立刻抽取（含「其实是周四」）；
   - `chat_summary_window`：会话消息达到阈值后写中期摘要（`kind=summary`）。
6. 模型任务：`memory_writeback`（默认与看门链一致：云端 flash 优先，`local` 垫底）；摘要用 `summary`。
7. 调用方：`memory_writeback_extract` / `memory_writeback_summary`（见 `task_registry`）。
8. 配置键：`MEMORY_SETTINGS` 中 `memory_writeback_*`（可用环境变量 `MEMORY_WRITEBACK_*` 覆盖）。
9. 来源门禁：QQ / NapCat 仅 `is_owner=true` 写回；隐藏/工具/传感器 path 不入写回。

禁止：

- 把口头「我记住了」当成写入成功；
- 把用户问句本身写成事实；
- 用日记长文噪声覆盖短习惯事实（召回侧已对 habit 查询加权）；
- 把写回再做成「以后再补」的旁路存储。

## 过渡态提醒

代码侧仍可能并存 `memory_sqlite`、`advanced_memory` facade 与 `memory_core/*`。新增功能应经单一 facade 写入 SQLite，避免再开旁路存储。

迁移/修复脚本如需写入历史 `memory_items` 类型，必须显式 `allow_legacy_write=True`，不得用于产品路径。
