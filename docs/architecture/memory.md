# Memory System

当前记忆系统的长期事实说明。实现细节以代码为准；本文件只固定边界与用户可见组织方式。

## 事实源

- SQLite `memory_records`（经 Memory Core / `memory_sqlite`）是**唯一写入真相源**。
- Chroma 等向量库只保存**派生索引**，损坏后必须能从 SQLite 重建。
- 旧 `waifu_memory_advanced` 集合仅只读查看，不参与当前 Memory Core 召回。
- 日记使用独立窗口与调度，不混入语义分类树的「经历与事件」。

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

## 过渡态提醒

代码侧仍可能并存 `memory_sqlite`、`advanced_memory` facade 与 `memory_core/*`。新增功能应经单一 facade 写入 SQLite，避免再开旁路存储。
