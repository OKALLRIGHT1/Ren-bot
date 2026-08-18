# 架构审查：上帝模块、高度耦合、重复处理、过度设计

- **审查范围**：当时工作区未提交变更（75 个已跟踪文件 + 20 个未跟踪文件，约 +4892 / -1207 行），并对照生产主路径上的既有模块。
- **审查日期**：2026-08-18
- **焦点**：上帝模块 / 插件、模块间高耦合、一轮对话里的重复劳动、多余抽象。
- **结论先行**：知识门控（`knowledge_gate`）和「当前人设」读取是真正在减负。其余大多是在尚未拆开的 `ChatService` 前面又加了一层规划器。`plugins/local_knowledge` 本身不是上帝插件；真正的中枢是被整颗塞进插件 `ctx` 的 `ChatService` + `brain`。
- **当前接线与批次状态**：见 [README.md](README.md)。下文是审查原文；P0–P4a 已落地，P4b / P5 未做。不要把已修条目当成仍待办。

### 批次落地（对照第 7.9 节）

| 批次 | 状态 | 对应代码入口 |
| --- | --- | --- |
| P0 止血 | 已做 | Thought 走 `chat_with_ai(..., timeout_sec=, max_tokens=)`；`speak`/`show_bubble` 且在作用域内非流式；守卫先于 catchphrase；`VISION_MODE` 默认 `separate`；表情默认 1 条 |
| P1 减负 | 已做 | 闲聊 `select_expressions(use_llm=False)`；`process()` 取一次短期窗口下传；`caller=forbidden_phrase_retry` |
| P2 GUI | 已做 | 设置注入 live `memory_core` + `MemoryGuiService` / `KnowledgeGuiService`；`ingest_knowledge_paths` |
| P3 删双轨 | 已做 | 闲聊不再 polish；已删 `prompt_builder.py` 与 `qq_polish_mode` |
| P4a 知识端口 | 已做 | `ctx["knowledge"]=BrainKnowledgePort`；`local_knowledge` 优先端口、回退 `brain`。**未删** `chat_service` / `brain` |
| P4b / P5 | 未做 | 见 README |

---

---

## 1. 上帝模块 / 上帝插件

「上帝」指：知道太多、做太多、或处在所有路径中间。按体量和职责宽度排序。

| 模块 | 体量（当前工作区） | 为什么算上帝 |
| --- | --- | --- |
| `services/chat_service.py` `ChatService` | 5672 行 / 124 个方法 | 系统重心。`process()` 仍是真实流水线；本轮又塞进 Thought / 表情注入 / 禁词守卫 / 重试。同时握着 gateway、日记、传感器、工具、情绪、风格、记忆写回、QQ 缓冲、展示。 |
| `modules/gui/dialogs/settings.py` | 5746 行 / 143 个方法 | 设置对话框本身就是一台应用：页面拼装 + 运行时 `brain` 下发 + 记忆/知识入口。 |
| `core/application.py` `Live2DApplication` | 3540 行 / 81 个方法 | 进程装配、TTS 静音气泡、GUI WS、NapCat、日记调度、角色 QQ 资料同步。本轮还加了「无声口型 + append_ui」输出导演。 |
| `modules/advanced_memory.py` `AdvancedMemorySystem.build_prompt` | 1888 行 | 生产环境的 prompt 上帝：人设拼接、Memory Core、知识门控、SQLite 任务、近历史、工具史。文件头还警告别的层不要直接碰 Memory Core / Chroma，但 GUI 和插件正好在这么做。 |
| `modules/memory_core/repository.py` | 1966 行 | 持久化 + schema 修复 + `is_current` 产品策略。仓库同时是存储层和业务规则。 |
| `modules/memory_core/service.py` `MemoryCoreService` | 1579 行 | 转写、档案学习、写回触发、回复召回、向量合并、表情选择、意图识别。聊天风格的表情挑选不该住在记忆核里。 |
| `modules/gui/app.py` | 1500 行 / 83 个方法 | GUI 壳层，和 Application / Settings 叠在同一条控制面上。 |
| `modules/gui/dialogs/memory_editor.py` | 1333 行 | 对话框兼持久化客户端：自己 `initialize()` 一套 Memory Core。 |
| `modules/memory/knowledge_store.py` | 983 行 | 知识「仓库」同时知道 Pokemon / OpenIE 特殊切块、v2 段落切块、导入清单、锁、检索。 |
| `modules/gui/dialogs/knowledge_manager.py` | 970 行 | 对话框直接调 `brain.import_knowledge_from_file` / `search_knowledge` / `rebuild_knowledge_collection`。 |

### 插件结论

- **`plugins/local_knowledge/plugin.py`（342 行）不是上帝插件。** 它是薄命令包装，真正的上帝是它拿到的 `context["brain"]`。
- 未在本轮 diff 里深挖、但体积已经像小型应用的插件（供后续拆分参考，不当作本轮已核实缺陷）：
  - `plugins/agently_mail/plugin.py` ~77 KB
  - `plugins/what_to_eat/plugin.py` ~57 KB
  - `plugins/qq_draw/plugin.py` ~55 KB
  - `plugins/search/plugin.py` ~44 KB
  - `plugins/qq_music/plugin.py` ~42 KB
- 上帝症状不在「插件目录」，而在 **每个插件都能拿到整颗 `ChatService` + `brain`**。

### 关键锚点

- `ChatService` 定义：`services/chat_service.py:160`
- 真实流水线仍是 `process()`：`services/chat_service.py:3900`（约 1480 行）
- 新自然语言层是加方法，不是替换：`_get_natural_chat_config` `:1020`、`_maybe_build_character_thought_block` `:1057`、`_build_expression_inject_block` `:1148`、`_retry_reply_without_forbidden` `:1163`、`_apply_forbidden_phrase_guard` `:1213`
- 插件被喂整颗对象：`services/chat_service.py:3910-3911`（`ctx["chat_service"] = self`、`ctx["brain"] = self.brain`）

**建议**：把闲聊回合收成一个编排器（门控 → Thought → prompt 零件 → 回复 → 守卫 → 展示），`process()` 只调用它。工具 / 日记 / 传感器留在外面。插件 `ctx` 只传窄端口（知识导入、知识检索），不要传 `ChatService`。

---

## 2. 高度耦合

### 2.1 插件 ↔ 大脑内部

- `plugins/local_knowledge/plugin.py:117` / `:220` 要求 `context["brain"]`，直接调 `import_knowledge_from_file` / `search_knowledge`。
- 同一插件里还有「`brain._retrieve_knowledge` 是内部方法，但 Python 里可以直接调」这类旁路注释（约 `:327`）。插件协议已经泄漏实现。

### 2.2 ChatService ↔ AdvancedMemory：用字符串手术共享 prompt

- `modules/advanced_memory.py:1567` `_extract_runtime_system_additions` 用字符串替换，从 ChatService 的 `system_text`（`chat_service.py:4680`）里抠掉 `DEFAULT_PERSONA` / `SYSTEM_RULES_PROMPT` / 时间头，再按工具标记切分。
- 两边共同拥有 prompt，却没有一份类型化的 `PromptParts`。改一边的文本格式，另一边静默坏掉。

### 2.3 GUI 绕过已有 `gui_api`

仓库里已经有给 Qt / HTTP 用的门面：

- `services/gui_api/memory_service.py`（`MemoryGuiService`，约 `:48`）
- `services/gui_api/knowledge_service.py`（`KnowledgeGuiService`，约 `:37`）

但 `modules/gui` 几乎不用它们（目前只看到 `assistant_badge_editor.py` 引用了 `gui_api`）。

| 调用方 | 实际依赖 | 本应走的门面 |
| --- | --- | --- |
| `memory_editor.py:72` | 自己 `MemoryCoreService(...).initialize()` | `MemoryGuiService` + 进程内那一份 Memory Core |
| `memory_editor.py:1199` | 对话框里直接 `import chromadb` 做向量页 | 只读查询走 gui_api |
| `knowledge_manager.py:44` / `:910` / `:959` | `brain.import_knowledge_from_file` / `rebuild_knowledge_collection` / `search_knowledge` | `KnowledgeGuiService` |
| `settings.py:5729` | `brain=getattr(self.main_app, "brain", None)` 塞进记忆对话框 | Settings 不该认识 runtime brain |

打开「设置 → 记忆」会再启动一套 Memory Core（repair、缓存、写回钩子），和运行时那套并排。

### 2.4 配置成为全局垃圾场

- `config.py` 里 `MEMORY_SETTINGS`、`CHARACTER_NATURAL_CHAT` 持续膨胀。
- `chat_service.py:118-130` 的 `ImportError` 回退再抄一份同样的字典。改默认值要改两处。

### 2.5 包边界泄漏

- `modules/memory/__init__.py` 再导出 retrieval helpers，却 **不导出** `knowledge_gate`。
- 生产代码走深路径：`modules/advanced_memory.py:47`。包的公开面已经不可信。

### 2.6 通道分类逻辑双份

- `natural_chat_pipeline.py:89` `_message_type`
- `chat_service.py:1029` `_natural_chat_message_type` 再实现一遍

门控被抽出去了，ChatService 仍自己做频道分型。

**建议**：对话框只跟 `MemoryGuiService` / `KnowledgeGuiService` 说话；插件只拿知识/记忆端口；ChatService 与 AdvancedMemory 之间传结构化 `PromptParts`，禁止字符串抠补。

---

## 3. 重复劳作 / 多次处理

一次普通桌面 / QQ 闲聊（`chat_service.py:3900` → `:4752`）会对同一批输入做多轮加工。

### 3.1 同一段短期记忆被读四次

| 次数 | 位置 | 用途 |
| --- | --- | --- |
| 1 | `chat_service.py:1976` → Thought 近 6 条（`:1100`） | Character Thought |
| 2 | `:2135` 表情选择 | `select_expressions` |
| 3 | `advanced_memory.py:1708` `short_ctx[-8:]` | `build_prompt` |
| 4 | `memory_core/service.py:872` `recent_messages` | `build_reply_context` |

Thought 还在 `character_thought.py:218` 把这些行再格式化一遍。

### 3.2 「怎么说话」被写四遍

同一回合里叠了：

1. `SYSTEM_RULES_PROMPT` + 角色 prompt（`advanced_memory.py:1683`）
2. `build_scene_prompt`（`natural_chat_pipeline.py:232`，注入点 `chat_service.py:1912`）
3. Thought 块（`:4684`）+ 表情块（`:4686`）
4. 残留的 `NATURAL_REPLY_FALLBACK_HINTS` / `_build_natural_habits_block`（`:2143`，polish 路径 `:2254` 仍在用）

四个作者写同一条指令。

### 3.3 一轮闲聊最多 3～5 次 LLM

默认配置关掉了 polish，但 Thought + 表情选择默认开：

1. Character Thought LLM（`chat_service.py:1057-1113`）
2. `select_expressions` LLM（`memory_core/service.py:1407`，经 `_load_expression_library_hints` `:2130`）
3. `brain.build_prompt`（`:4752`）里还可能再跑 Memory Core 印象 / rerank
4. 主回复
5. 可选 `_infer_reply_emotion_with_llm`（`reply_flow_service.py:265`）
6. 可选禁词重试 LLM（`chat_service.py:1190`）

为「怎么说一句短话」付了 3～5 次模型调用。

### 3.4 情绪被决定两次

- Thought 已产出 `emotion_level`（`character_thought.py:118`）
- 主回复若没有 `<emo>`，再走 `_infer_reply_emotion_with_llm`（`reply_flow_service.py:265`）

### 3.5 知识文本被清洗两次

- `format_knowledge_hits_for_prompt` 已跑 `clean_injected_context`（`modules/memory/retrieval.py:56`、`:123`）
- `build_prompt` 再跑一次（`advanced_memory.py:1797`）

### 3.6 知识导入有三条进度循环

都落到 `brain.import_knowledge_from_file`：

1. 插件 GUI 路径：`plugins/local_knowledge/plugin.py:132`
2. 插件 `learn`：`:244`
3. Knowledge Manager worker：`knowledge_manager.py:25`（约 `:44` 起）

`_import_one_file` 只抽出了单文件调用，自适应慢导入循环（限速观察、动态 sleep、added/skipped/failed 记账）仍是两份。

### 3.7 两套「让回复自然」的系统

- 闲聊：Thought + guard + scene shell
- 传感器：自己的 `_rescue_sensor_template_reply`（`chat_service.py:5507`）；`sensor_reply_service` 不引用新 pipeline

**建议**：Thought **或** 表情选择只留一个预回复姿态步骤；表情优先走已有词法回退（`memory_core/service.py:1432`）。Thought 已给出 `emotion_level` 就不要再推断情绪。短期记忆读一次，向下传递。导入只留知识门面上的一个 ingest 函数。

---

## 4. 过度设计

### 4.1 「Pipeline」名不副实

`services/chat_support/natural_chat_pipeline.py`（294 行）只放了配置、scope、场景行，以及对 `forbidden_phrase_guard` 的薄包装。真实顺序仍在 `ChatService.process`。多了一个模块 + `ThoughtGateDecision` + `NaturalChatConfig`，控制流没搬走。

### 4.2 Character Thought 是完整规划器，压缩后只剩一行

- 六字段封闭本体：`want` / `angle` / `avoid` / `emotion_level`（`character_thought.py:82`）
- 为此多一次 LLM，再压成一行 prompt（`:262`）
- `short_shell` 接了线却没用（`:265`）
- 对 1～2 句回复来说，这是在说话人前面再放一个策划

### 4.3 两个成本开关都是空的

| 开关 | 位置 | 实际行为 |
| --- | --- | --- |
| `timeout_ms`（默认 2500） | `character_thought.py:325` | `future.result(timeout=...)` 超时后，`with ThreadPoolExecutor` 仍 `shutdown(wait=True)`。调用方还要等完整 LLM。`chat_service.py:1102` 再包一层 `asyncio.to_thread`，内层线程池是多余的。 |
| `character_thought_max_tokens` | `config.py:708` → `character_thought.py:316` | 唯一用法是 `_ = max_tokens`。没有 token 预算。 |

### 4.4 Polish「被替换」但没删

仍活着：

- `_polish_natural_reply`（`chat_service.py:2220`）
- `_build_natural_habits_block`（`:2143`）
- `qq_polish_mode=legacy`（`config.py:713`）
- `should_run_polish`（`natural_chat_pipeline.py:264`）

默认关闭的遗留路径，两套「自然化」并存。

### 4.5 「模型这回合需要知道什么」叠了四套活系统 + 一套死的

| 层 | 位置 | 状态 |
| --- | --- | --- |
| Memory Core 回复上下文 | `memory_core/service.py:862` | 活 |
| ContextAssembler 近历史 | `context_assembler.py` | 活 |
| 知识门控 + Chroma | `knowledge_gate.py:13`，`advanced_memory.py:1748` | 活 |
| 回合后抽事实 LLM | `services/memory_writeback.py` | 活 |
| `modules/memory/prompt_builder.py` | 未接线，仍有无条件 `retrieve_knowledge`（`:90`） | 死，但留在树里 |

`knowledge_gate.py:38` 的 `should_retrieve_knowledge` 是对 `knowledge_retrieval_decision` 的一行包装，测试两边都 import。

### 4.6 表情注入两个格式化器

- 主路径：`format_expression_block`
- polish / 传感器路径：`_build_natural_habits_block` 里的 `【表达习惯参考】`

配置还对不上：

- `NaturalChatConfig.expression_inject_max_items` 默认 1（`natural_chat_pipeline.py:33`）
- 产品默认 1（`config.py:711`）
- `from_mapping` 用 `raw.get(...) or 3`（`:62`）
- `_build_expression_inject_block` 回退也是 `or 3`（`chat_service.py:1158`）
- `format_expression_block` 再硬切 `items[:2]`（`natural_chat_pipeline.py:273`）

运营无法真正设定「1 / 2 / 3」。

### 4.7 又一条输出管道

`Live2DApplication` 的静音 TTS 路径新增按段口型 + `append_ui`，模拟「在说话但没声音」。与真实 TTS pacing 并列。

### 4.8 指标桶混用

禁词重试记成 `caller="character_thought"` / `task_type="gatekeeper"`（`chat_service.py:1194`）。Thought、gatekeeper、rewrite 进同一个成本桶。

**建议**：删掉未接线的 `prompt_builder.py`（或留 5 行 stub，生产 import 即抛）。一个默认值（1），formatter 遵守 `expression_inject_max_items`。不用的 `short_shell` 删掉。默认关闭的 polish 路径限期删除。Thought 超时做在 `asyncio.wait_for` 上，并真正传入 max tokens。

---

## 5. 本轮一并核实的正确性缺陷

架构审查时落到的实 bug，不是风格偏好。

### 5.1 Thought 超时绑不住 LLM

- **位置**：`services/chat_support/character_thought.py:325`
- `future.result(timeout=2.5s)` 超时后线程池 `shutdown(wait=True)`，worker 继续跑，调用方照样等完。
- `chat_service.py:1102` 已包 `asyncio.to_thread`，回合仍会被完整 Thought 调用拖住，即使 trace 写了 `thought_skipped_reason=timeout`。
- `chat_with_ai` 若挂死，2.5s 上限不会生效。

### 5.2 流式路径先把禁词展示出去

- **位置**：`services/chat_service.py:5313`
- 模型文本已在 `:5271-5298` `assistant.stream.feed` 给 UI，之后 `finalize_stream_reply` 才 `_apply_forbidden_phrase_guard`。
- 记忆 / gateway 存的是改写后的 `full_reply`，用户已经看过原句。
- catchphrase / share 的 `feed_chunks`（`:5325-5332`）来自守卫前的 finalize 结果 → UI、TTS、落库可能三份不一致。
- 桌面 / QQ 闲聊在 `natural_reply_candidate` 时被强制非流式（`reply_flow_service.py:87-92`），但图片 / 前言 / 非 direct 来源仍走这条流式路径，且仍在 Thought / guard 范围内。

### 5.3 `VISION_MODE` 默认值静默翻转

- **位置**：`config.py:873`，`"separate"` → `"direct"`
- 已有安装会从「先描述再说话」变成「一次视觉+说话」。
- 同变更还要求升级后重建索引库 + 一键学习（`docs/TROUBLESHOOTING.md` 4.1），旧行切块不会迁移。应做成一次性迁移提示，而不是脚注。

### 5.4 `.gitignore` 乱码文件名

- **位置**：`.gitignore:115`
- 写入了 `鎶€鏈€荤粨.md`，UTF-8 checkout 上忽略不了真正的中文文件名。

---

## 6. 建议的收敛顺序

本节是审查当日的初稿。**以第 7.9 节重排后的计划为准**；P0–P4a 已落地，见 [README.md](README.md)。不要按下面 7 条字面再做一遍（尤其第 5 条「不再传入 chat_service」会破发）。

按「先止血、再减负、最后拆上帝」：

1. **修超时与 max tokens**  
   去掉内层 `ThreadPoolExecutor`；超时做在 `asyncio.wait_for`。要么把 `character_thought_max_tokens` 传进调用，要么删掉这个配置项。

2. **修流式 × 禁词**  
   `scope_matches` 时强制非流式，或先缓冲再 flush，或命中后重发替换句。

3. **砍一轮闲聊的 LLM 次数**  
   Thought 与表情选择二选一；表情默认词法；已有 `emotion_level` 不再二次推断。

4. **GUI 接到已有 gui_api**  
   记忆 / 知识对话框停掉私有 Memory Core 和 `brain.*` 直调。

5. **插件 ctx 收窄**  
   不再传入整颗 `chat_service` / `brain`。知识导入只留门面上一个函数，插件和 GUI 共用。

6. **删死代码与双轨**  
   删除或 stub `modules/memory/prompt_builder.py`。限期删除默认关闭的 polish 路径。统一 `expression_inject_max_items`。

7. **最后再拆 `ChatService.process()`**  
   闲聊编排器独立出去之后，再谈把工具 / 日记 / 传感器从 5600+ 行的类里搬出去。先抽 pipeline 模块、却把控制流留在上帝类里，是本轮过度设计的核心症状。

---

## 7. 修复计划审查（对照第 6 节）

审查对象就是上一节那 7 步。方向「先止血、再减负、最后拆上帝」成立。下面按步拆：哪条能做、哪条写错了、哪条会把现网打挂。

**总评：** 第 1、2 步该立刻做，但第 1 步写的超时方案修不掉问题；第 3 步把两套不同的「情绪」焊在一起了；第 5 步按字面执行会打断十几个插件。第 4、7 步范围写大了，需要先补门面再接线，不能当「改 import」做。

### 7.1 第 1 步：超时与 max tokens —— 方案不成立

计划写：去掉内层线程池，超时改挂 `asyncio.wait_for`。

对照代码：

- `generate_character_thought` 是同步函数，调用方已经是 `asyncio.to_thread(...)`（`chat_service.py:1102`）。
- `asyncio.wait_for` 取消的是 Task，**取消不了线程里的 `requests` / OpenAI SDK**。超时后回合能往下走，LLM 仍在占着连接，和现在「假超时」只是换了层皮。
- 真超时已经在 `chat_with_ai(..., timeout_sec=30)`（`modules/llm.py:789`）上。Thought 要 2.5s，应把 `timeout_sec=timeout_ms/1000` 传进去，让 HTTP 层断。
- `chat_with_ai` **没有** `max_tokens` 形参。计划里「传进去」不是一行修改，要打通 OpenAI / Responses / Gemini 三条 transport。做不到就删掉 `character_thought_max_tokens`，别留空开关。

应改成：

1. 删内层 `ThreadPoolExecutor`。
2. `chat_fn(..., timeout_sec=timeout_s)`，用现有 HTTP 超时。
3. `max_tokens`：扩 `chat_with_ai`，或删配置。不要假装已接线。
4. 测试：mock 一个挂住的 `chat_fn`，断言墙钟 < 超时 + 余量，且回合继续。

不要做：只包一层 `wait_for` 然后宣称超时修好了。

### 7.2 第 2 步：流式 × 禁词 —— 列了三个选项，没有选定

三个选项互斥，产品代价不同：

| 选项 | 对 TTS | 对打字机 UI | 风险 |
| --- | --- | --- | --- |
| `scope_matches` 一律非流式 | 整句再播 | 无逐字 | 闲聊桌面/QQ 已是非流式（`reply_flow_service.py:87-92`）；会改图片/前言/非 direct 的观感 |
| 先缓冲再 flush | 等于非流式 | 可保留（先攒后喷） | `speak=True` 时缓冲没有意义，音已经出了 |
| 命中后重发替换句 | 撤不回已播的禁词 | 用户先看见脏句 | 最差 |

选定建议：

- `scope_matches` 且 `speak=True`：走非流式（守卫后再展示/播）。
- `scope_matches` 且只显示不播：可缓冲，守卫后再 `feed`。
- 守卫必须发生在 catchphrase / share 的 `feed_chunks` **之前**。现在是先 `finalize_stream_reply`（`:5304`）再守卫（`:5313`），再把守卫前的 `feed_chunks` 发出去（`:5325`）。计划没写这一刀，修完仍会三份不一致。

### 7.3 第 3 步：砍 LLM —— 有一处事实错误

**不要**「Thought 已有 `emotion_level` 就不再推断情绪」。

- Thought 的 `emotion_level` 是 `light | medium | heavy`（姿态强度，`character_thought.py:12,86`）。
- `_infer_reply_emotion_with_llm` 产出的是 Live2D `<emo>`（happy/sad/neutral 等，`reply_flow_service.py:265`）。
- 两套本体。焊在一起 Live2D 会拿到 `"medium"` 这种非法表情。

**不要**把 Thought 和表情选择做成互斥产品开关。Thought 管本轮姿态，表情库管口癖样本，职责不同。

表情选择的词法回退也没有计划以为的那么现成：

```1432:1433:modules/memory_core/service.py
        if not selected_rows and not self.llm_call:
```

`llm_call` 存在时（生产路径一定存在）词法分支根本不走。空选择也不会回退。要省这次 LLM，得改这个条件，或闲聊场景显式不传 `llm_call`。

这一步真正该做、且低风险的是：

- 闲聊表情选择改为词法（修好上面的门闩）。
- 短期记忆窗口取一次，向下传给 Thought / 表情 / `build_prompt` / Memory Core（原审查第 3.1 节，7 步计划漏了）。
- Live2D 情绪推断保留，除非另做 `emotion_level → intensity` 的映射，且仍要合法 emo 标签。

### 7.4 第 4 步：GUI 接 gui_api —— 门面还不够对话框用

知识侧门面基本齐：`KnowledgeGuiService` 已有 `search` / `import_file` / `rebuild` / `learn_configured_dirs`。`knowledge_manager` 改接线即可。但 `learn_configured_dirs` 仍把 `{"brain": brain}` 塞回插件（`knowledge_service.py:226`），只改对话框 import 泄漏还在。

记忆侧对不上：

| 对话框现在用的 | `MemoryGuiService` |
| --- | --- |
| `list_current_memory_records` / upsert / delete / category override | 有（含 `set_category_override`） |
| `store.list_transcript`（转写页） | **无** |
| 对话框里直接 `import chromadb`（向量页，`memory_editor.py:1199`） | **无列表 API**（只有 `rebuild_vector_index`） |
| `repository.character_subject_id` | **无** |

另外：即使改走 `MemoryGuiService`，若仍走 `core_factory` 自己 `initialize()`，第二套 Memory Core 还在。第一刀应是注入进程内那份 `brain.memory_core`，禁止对话框再 new 一套。不要一上来重写 1300 行对话框。

### 7.5 第 5 步：收窄插件 ctx —— 按字面做会破发

「不再传入整颗 `chat_service` / `brain`」会打到至少这些现网依赖：

| 插件 | 从 ctx 拿走的 |
| --- | --- |
| `agently_mail` / `qq_reminder` / `qq_music` / `_claw_email` | `_send_gateway_reply` / `_send_gateway_image_reply` |
| `qq_draw` / `meme_pack` | gateway / 发图 |
| `mode_preset` | `presenter.set_tts_enabled` |
| `music_player` | `handle_music_event` |
| `skill_runtime` / `qq_role_switch` | `app` / `skill_manager` |
| `agent_runtime` / `mcp_tools` | `agent_runtime` / `mcp_bridge` |
| `qq_help` | `plugin_manager` |
| `local_knowledge` | `brain` |

这是插件宿主协议重做，不是自然语言修复的后续。必须拆开：

- **5a（可跟知识 GUI 一起做）：** 只给 `local_knowledge` 一个 `KnowledgePort`（import / search / stats / rebuild）。
- **5b（独立项目）：** `GatewayPort` / `PresenterPort` / `AppPort` 齐了，再从 ctx 拿掉 `chat_service`。

未做 5b 之前，`ctx["chat_service"] = self` 必须留着。

### 7.6 第 6 步：删死代码 —— 步骤太靠后，且漏了更小的止血

- `expression_inject_max_items` 配置对不上（1 / `or 3` / 硬切 `[:2]`）是一行默认值问题，不该排到拆完上帝之后。放进 P0。
- `VISION_MODE` 默认翻转、`.gitignore` 乱码在第 5 节已核实，7 步计划没接。也是 P0。
- 删 polish：`tests/test_chat_service_smoke.py` 仍在 monkeypatch `_polish_natural_reply`。删路径要先改测试，或先留空实现再删。
- `modules/memory/prompt_builder.py` 生产无引用，可以删。先 `rg` 再删，不要留「会 raise 的 stub」除非担心外部仓库 import。

### 7.7 第 7 步：拆 `process()` —— 目标对，入口没写清

风险是再抽一个不拥有控制流的 `*_pipeline.py`（`natural_chat_pipeline.py` 已经这样了）。

可验收的切口：

- 只对 `plain_direct_chat_candidate` 走 `CasualTurn.run()`。
- 这个对象拥有：Thought → prompt 零件 → 主回复 → 守卫 → present。
- `process()` 匹配后 `return await casual.run(...)`。工具 / 日记 / 传感器仍留在 `process()`。
- 没有第三条「自然化」路径（传感器 rescue 先不动，避免一次改两条主路径）。

没有这条验收标准，第 7 步只是再加一层 helper。

### 7.8 计划漏掉、但应该进排期的

| 项 | 应落入 | 为什么 |
| --- | --- | --- |
| `VISION_MODE` 保持旧默认，新行为 opt-in | P0 | 已有安装静默换视觉路径 |
| 知识库重建做成升级提示，不是 TROUBLESHOOTING 脚注 | P0 / 发布说明 | 旧行切块不迁移 |
| `.gitignore` 乱码文件名 | P0 | 一行 |
| 短期记忆窗口只取一次往下传 | P1 | 纯减负，无产品决策 |
| 禁词重试 `caller` 从 `character_thought` 拆开 | P1 | 指标被污染 |
| `_message_type` 双份 | P1 或跟第 7 步 | 小重复 |
| ChatService ↔ AdvancedMemory 字符串抠 prompt | 跟第 7 步 | 没有 `PromptParts` 就拆不干净 |
| 传感器另一套「自然化」 | 第 7 步之后 | 不要和第 7 步绑在一起 |

### 7.9 重排后的计划（采用这一份）

**P0 止血（小时级，不拆架构）**

1. Thought：删内层线程池；`chat_with_ai(..., timeout_sec=...)`。
2. `max_tokens`：接线或删配置。
3. 流式守卫：`scope_matches && speak` → 非流式；守卫先于 catchphrase/share 的 feed。
4. `VISION_MODE` 默认改回 `separate`。
5. 修 `.gitignore`；统一 `expression_inject_max_items` 为 1，formatter 遵守它。

**P1 减负（天级，不改产品语义）**

1. 闲聊表情选择改词法，修好 `llm_call` 把门闩。
2. 短期记忆读一次，向下传。
3. 禁词重试换独立 `caller`。
4. **不**用 `emotion_level` 替换 Live2D 情绪推断。

**P2 GUI 接线（先补门面，再改对话框）**

1. `MemoryEditor` 注入进程内 `memory_core`，禁止第二次 `initialize()`。
2. 转写 / 向量列表补进 `MemoryGuiService` 后再拆对话框直访。
3. `KnowledgeManager` 改走已有 `KnowledgeGuiService`。
4. 知识 ingest 收到门面里，插件 GUI / `learn` / 对话框共用。

**P3 删双轨（P1 行为稳定之后）**

1. 删默认关闭的 polish 路径，并改测试。
2. 删 `prompt_builder.py`。

**P4 插件端口（独立项目，禁止混进自然语言修复）**

1. 先做 `KnowledgePort`。
2. Gateway / Presenter / App 端口齐了再撤 `ctx["chat_service"]`。

**P5 拆闲聊编排（P0–P3 之后）**

1. 仅 `plain_direct_chat_candidate` 委托给拥有完整顺序的 `CasualTurn`。
2. 不要再做一个不拥有控制流的 pipeline 模块。

---

## 附录：审查统计

| 项 | 值 |
| --- | --- |
| 已跟踪变更 | 75 files, +4892 / -1207 |
| 未跟踪文件 | 20 |
| 已核实缺陷 | 3 bug / 9 suggestion / 2 nit |
| 本轮真正在减负的部分 | `knowledge_gate.py`（闲聊不再无条件灌知识）、Memory Core `is_current` 人设读取 |
| 本轮主要在加负的部分 | 每个闲聊回合多 1～2 次 LLM；`ChatService` 方法数继续涨；polish 双轨仍在 |
