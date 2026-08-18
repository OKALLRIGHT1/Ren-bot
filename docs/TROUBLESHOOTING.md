# Troubleshooting

这里记录当前项目最常见的问题和排查顺序。先看日志，再改配置；不要直接盲改提示词或删除代码。

## 1. Live2D 说话不带动作 / 表情

先看 `logs/agent.log` 是否出现：

```text
[EmotionController] emotion=...
[Live2D Emotion] emotion=... source=... exp=... mtn=...
```

判断方式：

- 没有 `[EmotionController]`：说明对话链路没有发 `live2d.emotion`。
- 有 `[EmotionController]`，没有 `[Live2D Emotion]`：说明可能只走了控制器路径，或播放阶段没触发。
- 有 `[Live2D Emotion]` 但没动：检查当前角色服装的 `emotion_map`，motion 名是否真实存在。
- 刚触发就回默认：检查是否被 `live2d.go_idle` 覆盖。

注意：

- `neutral` 和 `idle` 是两个不同情绪。当前设计是回复时先走模型给出的情绪，气泡/语音结束后再回 idle。
- 如果 TTS 关闭，仍应触发文字口型和情绪动作，但回 idle 会按估算文本时长延迟。
- 如果 `happy` 和 `neutral` 配了同一个 motion，看起来就会像没动，只能看到表情变化。
- 当前正常收尾不该由 `assistant.stream.end` 直接回 idle；优先检查 `modules/tts/router.py` 的 `all_done`，以及 `services/chat_service.py` 里是否还有显式 `_emit_idle_status(...)` 抢先触发。
- 如果说话前就先做完动作，优先查 `services/chat_service.py` 里 `model_reply` / `model_stream_reply` / `sensor_reply` 的 `prefer_motion`；当前设计是模型阶段只预置表情，motion 由 TTS 播放或气泡显示时触发。

## 2. TTS 关闭了还在说话

检查：

- GUI 里的 TTS 开关是否真的保存。
- `logs/agent.log` 启动时的 `TTS 启动状态`。
- QQ 来源默认不驱动桌面 TTS；如果听到声音，多半不是 QQ 入站触发，而是本地输入、屏幕感知或插件输出。

如果仍异常：

- 搜索最近日志中的 `assistant.utter`、`speak`、`TTS disabled`。
- 确认设置保存后是否重启完成。

## 3. 语音没播完气泡就没了

常见原因：

- Rhubarb 口型分析耗时被算进了气泡显示时间。
- 音频时长估算太短。
- TTS 队列被打断或状态提前回 idle。
- `TTS_AUTO_TRANSLATE=True` 且角色 `prompt_lang=ja` 时，语音读的是日文翻译，气泡显示的是中文原文；如果翻译模型输出了 `Output:`、引号或解释，听起来会比气泡多。

当前修复方向：

- 气泡应在播放指令发出后再计时。
- Rhubarb 超时要杀进程，不能一直卡住。
- TTS 关闭时用文字估算口型和气泡时长。
- 流式回复即使队列短暂见底，也要等 `stop_stream()` 后才能让 `all_done` 回 idle；否则会出现“话没说完动作先停了”。
- TTS 翻译结果进入 GPT-SoVITS 前应清洗掉标签、代码块、外层引号和解释性前缀；如果仍听到多余内容，查 `modules/tts/router.py` 的 `tts_translate` 日志。

## 4. 知识库导入卡住

优先看日志是否出现：

```text
RPM limit exceeded
fallback embedding
```

排查：

- 远程 embedding 被限频时，导入会非常慢。
- 推荐本地 Ollama `bge-m3`。
- 开启慢速导入或自适应慢速模式。
- 大 JSON / XML 文件先确认解析器能抽出有效条目，否则可能显示“导入 0 条”。
- 普通 `.md/.txt` 已改为按段落 / 标题分块。旧按行碎片不会在库里自动变形；点「一键学习」时，没有 `chunker_version` 的旧清单会重新导入并删掉该文件残留的按行块。
- 标题栏出现「需要重建」、或换过 embedding 模型时：先点「重建索引库」清空集合和清单，再点「一键学习」。只点学习不会清掉不兼容向量。
- 同一文件未改内容且已是新分块再学会按 checksum 跳过；改过的文件会先写入新块再删旧块。导入失败不会推进 `data/knowledge_import_manifest.json`。删除源文件不会自动清库，需要目录重建或显式删除。

## 4.1 怎么重新学习知识库

1. 确认知识目录还在：`更多功能 → 知识库管理`，目录表里勾选要学的文件夹，点「保存目录」。
2. 看标题栏统计。出现「需要重建」就先点「重建索引库」，确认清空。
3. 即使没有红字，升级后第一次也建议：`重建索引库` → `一键学习`。这样旧按行碎片和旧清单一起清掉。
4. 点「一键学习」，等结果。应看到新增条数；若全是「跳过」且片段数几乎没变，说明还在用旧块，回到第 2 步清空后再学。
5. 用下方「检索验证」搜一句文档里的原话。命中应是整段，而不是单行。
6. 聊天里要用资料时，明确说「设定 / 资料 / 知识库 / 文档里 / 词条」。闲聊默认不查库。
7. 插件命令 `learn ||| 目录` 和 GUI 一键学习同一套导入；未改文件会 skip。要整库重来仍先重建。

推荐本地配置：

```text
EMBEDDING_API_URL=http://127.0.0.1:11434/v1/embeddings
EMBEDDING_API_KEY=ollama
EMBEDDING_MODEL_NAME=bge-m3
```

## 4.2 启动闪退：UNIQUE constraint failed memory_records

日志里如果是：

```text
_ensure_persona_unique_indexes
sqlite3.IntegrityError: UNIQUE constraint failed: memory_records.subject_id, memory_records.kind, memory_records.key
```

原因是人设唯一索引曾经误套到日记 / 任务（`episode` / `other`）上。库里本来就可以有同名日记副本。当前索引只约束 `preference / fact / rule / profile / relation`。更新代码后再启动即可，不用删库。

## 5. Ollama bge-m3 没跑

检查：

```bash
ollama list
ollama pull bge-m3
ollama serve
```

注意：

- embedding 调用不一定会在 Ollama 界面里显眼显示。
- 先用小文本测试 embedding 接口，再跑大批量导入。
- 笔记本 4060 可以跑 `bge-m3`；大型聊天模型要按显存选择。

## 6. QQ / NapCat 没反应

检查顺序：

1. 日志是否有 `NapCat gateway listening`。
2. NapCat 上报地址是 HTTP 还是反向 WS。
3. 私聊 / 群聊开关是否允许。
4. 群聊是否要求 @。
5. 主人 QQ、白名单、黑名单是否拦截。
6. 插件权限是否允许 QQ 主人触发。
7. 如果是联网搜索，QQ 里不该先回“我先去后台处理/整理资料”；现在搜索型 delegate 应同步返回实际结果。若仍出现占位，优先查 `services/chat_support/delegate_flow_service.py` 的 `should_use_background_delegate()`、delegate trigger 和 `plugins/search/config.json` 的 `"type"`。
8. 如果 QQ 搜索只回一个句号，先看日志是否出现 `search_web 现在仅允许通过副脑委托执行。`。这通常说明 `plugins/search/config.json` 把搜索插件配成了 `react`，覆盖了 `plugins/search/plugin.py` 里的 `type = "delegate"`，导致执行时没有注入 `delegate_mode`。正确配置应为 `"type": "delegate"`。
9. 如果“今年是 2026 年 / 过时 / 不对 / 重新查一下”这类纠错重查没有触发搜索，优先查 `services/chat_support/text_utils.py` 的 `is_search_retry_correction_request()` 和 `ChatService._resolve_followup_search_query()`；明确纠错重查应直接形成搜索 query。轻量模型适合做规则拿不准的灰区分类，不应替代这些高置信规则。
10. 如果气泡里出现“【动作/微表情】”“正文不要引用标签和动作”“回复只要正文本身”等内部提示，优先查回复出口清理链路：`services/chat_support/text_utils.py` 的 `strip_internal_tags()` 和 `services/chat_support/reply_flow_service.py` 的 `flush_stream_buffer()`；流式气泡必须在 `assistant.stream.feed` 前完成清理。

常用地址：

```text
HTTP: http://127.0.0.1:8095/chat/napcat
WS:   ws://127.0.0.1:8095/chat/napcat
```

NapCat HTTP Webhook 要求 `Access Token`。启动时优先使用 `data/runtime_settings.json` 里的 `napcat_access_token`；如果 runtime 缺 token，会先尝试复用现有 NapCat `onebot11_*.json` 里的 websocket client token，完全找不到时才生成新 token。HTTP 侧需要通过 `Authorization: Bearer <token>`、`X-NapCat-Token` 或 URL 参数 `?access_token=<token>` 携带。本机反向 WebSocket 为兼容旧 NapCat 配置，允许 `127.0.0.1` / `::1` 无 token 连接；非本机 WS 仍需要 token。

如果 NapCat 反向 WS 报 `Unexpected server response: 401`，先确认 8095 是否由当前项目进程监听、连接来源是否是 loopback，再脱敏比较 runtime 和 NapCat 配置中的 token hash，不要直接打印 token 明文。若同时发现设置页里的主人 QQ、白名单或黑名单消失，优先检查 `data/runtime_settings.json` 是否被局部设置补丁覆盖；`apply_external_settings()` 应先合并完整 runtime 再应用局部 patch，不能把只包含久坐或 QQ 子集的 dict 直接写回完整配置。

### QQ 图片发不出去

如果日志里出现 `image_path_not_allowed`，说明 NapCat 文件白名单拒绝了本地图片路径。QQ 生图、截图和日报常先生成到系统临时目录；发送前应由 `services/chat_support/gateway_sender.py` 复制到项目内 `data/outbound/gateway_media`，再交给 NapCat。该目录位于 NapCat 默认允许的 `data/outbound` 下。

排查：

- 看日志里的 `[QQ-OUT-IMAGE-STAGED] transport_path=...` 是否指向 `data/outbound/gateway_media`。
- 如果仍被拒，检查 `integrations/chat_gateway/napcat.py` 的 `DEFAULT_ALLOWED_FILE_ROOTS` 是否包含 `data/outbound`。
- 不建议直接放开整个 `%TEMP%`；临时目录范围太大，容易绕过文件发送边界。

### QQ 长回复没有分段

当前 QQ 文本分段集中在 `services/chat_support/gateway_sender.py`：

- `split_gateway_text_parts()` 负责先按短句、换行和结构化内容拆分。
- `_split_long_gateway_part()` 会把过长自然句继续按中英文句号、问号、感叹号和逗号拆开。
- URL 和代码块应保留为单条消息，避免链接和代码被拆坏。
- 硬件状态的本地气泡也复用这套分段；如果 QQ 已分段但本地气泡没分段，优先查硬件状态专用发送路径是否绕过了 splitter。

### 切换角色后 QQ 昵称或头像没变

当前角色切换只同步 QQ 昵称和头像：

- 如果当前 QQ 昵称已经等于目标昵称，会跳过昵称修改。
- 头像只有角色配置了 avatar 文件时才会设置。
- 项目不会主动改签名、说说或在线状态；如果这些字段变化，优先查 NapCat `set_qq_profile` action 或外部 QQ 自动化。
- 如果昵称不同但没有变化，查 `core/application.py` 的角色切换日志，以及 NapCat 是否支持并放行 `set_qq_profile` / `set_qq_avatar`。

## 7. API Key 明明在别的软件可用，这里 401

常见原因：

- 模型绑定的供应商不是你改 key 的那个。
- `base_url` / `api_style` 不匹配。
- 任务路由没有按你以为的模型顺序走。
- `data/custom_models.json` 覆盖了 `config.py` 默认值。

排查：

- 看日志里的 `task=... caller=...`。
- 看 `transport_order` 和实际模型名。
- 在 GUI 中检查模型是否绑定同一个供应商变量。
- 修改供应商 key 后，最好同步该供应商下所有模型。

## 8. 屏幕吐槽像总结 / 不认识自己

当前屏幕感知应该知道：

- Live2D Agent、系统设置中心、换装、表情、动作、TTS、model3、项目代码通常是在配置她自己。
- 桌面边缘 / 右下角 / 悬浮窗里的 Live2D 形象通常是她自己的桌面身体。
- 普通网页、QQ 群聊、番剧、游戏或图片主体里的动漫角色不能默认当成她。

如果仍像总结：

- 看 `logs/agent.log` 是否出现“吐槽仍像观察报告/助手话术，已跳过”。
- 视觉模型可能只返回画面描述，后续润色模型需要再压成临场短句。
- 不要只靠人格提示，视觉提示里要明确写“不要复述画面、不要说用户正在、不要总结”。
- 如果吐槽说到一半动作突然回 idle，优先查 `services/chat_service.py` 的 `_reset_sensor_motion_after()`；它现在只应做兜底，且有新回复插入时应取消，不该覆盖正常 TTS 收尾。屏幕吐槽的发送编排在 `services/chat_support/sensor_reply_service.py`，但 idle 兜底判定仍由 `ChatService` 持有。
- 如果吐槽太频繁，先看 `config.py` 的 `SCREEN_GLOBAL_COOLDOWN` 和 `SCREEN_REACTION_COOLDOWN`。当前默认全局冷却是 180 秒，`ChatService` 的感知兜底也跟随这个值。
- 如果一直不吐槽，先看是否出现 `ChatService 未实际吐槽，不进入冷却`、`Sensor Gatekeeper`、`吐槽仍像观察报告/助手话术，已跳过`。当前设计是只有 `ChatService.handle_sensor_event()` 最终实际发出吐槽时，`modules/screen_sensor.py` 才会写入全局/分类冷却；被锁、低强度、Gatekeeper 或模板过滤跳过的尝试不会消耗冷却。

### 她说我「打开了 N 次」（同页挂机误报）

**结论：只改 `live2d-llm` 即可修；不要求改 `live2d-enhanced-connection-profiles`。** Tauri 只报焦点事实，会话语义与话术在 Python。

根因简述：

1. 旧计数对每次应用前台切换 `+1`，通知/Alt-Tab 闪一下再回来也会涨。
2. Prompt 若写成「今天打开第 N 次」，模型容易照念。

当前口径：

- `daily_counts` = **独立前台会话**段数（离开 ≥ gap 再回才 +1），日统计展示为 `({n} 段会话)`。
- 吐槽上下文优先用 **本次停留 / 今日累计时长**；禁止夸张「打开了 N 次」。
- 输出侧有规则护栏（`sensor_utils.sanitize_sensor_open_count_reply`），命中则 strip 或丢弃，**不另开 polish LLM**。

可调：

```text
# config.py 或环境变量
SCREEN_APP_SESSION_REOPEN_GAP_SEC=90   # 默认 90；更钝可 180–300，更敏可 ~30
```

排查：

- 看 `data/sensor_stats.json` 的 `counts`：同页挂机不应线性涨到十几。
- 日志是否出现 `已去掉打开次数夸张表述`。
- 实现入口：`modules/screen_sensor.py`、`services/chat_support/sensor_utils.py`；可调 `SCREEN_APP_SESSION_REOPEN_GAP_SEC`（默认 90）。

## 9. 硬件状态没有回复 / 没进思考状态

当前硬件状态不是完全交给 LLM 猜工具，而是有确定性捷径。正常问法包括“硬件状态”“电脑状态”“CPU 占用怎么样”“内存使用率高不高”“显卡温度”等。

排查顺序：

1. 看日志是否出现 `hardware_status` 或 `hardware_status_polish`。
2. 看 `plugins/system_monitor` 是否加载，且插件类型允许 `react` 执行。
3. 看日志是否有 `[系统监控]` 或 `[CMD: check | ...]` 的执行结果。
4. 如果工具有原始结果但没有自然回复，检查 `hardware_status_polish` 对应模型供应商和 API Key。
5. 如果 QQ 没有长文本分段，查 `GatewaySender.split_gateway_text_parts()`；硬件状态本地气泡也应该走同一 splitter。

注意：普通“今天状态怎么样”“你状态还好吗”不应误触发硬件监控，这类应按普通聊天处理。

## 10. ClawEmail 邮件插件不可用

当前 `plugins/claw_email` 是 `direct` 插件，`/查邮件`、`/邮件诊断` 这类命令应直接路由，不需要先进入主 LLM 委托。

常见错误：

- `PROFILE_NOT_FOUND`：通常是把邮箱账号填到了 profile 字段。profile 是 mail-cli 的本地档案名，不是邮箱地址；没有明确创建 profile 时通常应留空。
- `KEYCHAIN_ERROR`：Codex 沙箱或非交互环境可能读不到系统钥匙串；如果真实应用进程里 `mail-cli --json auth test` 正常，则优先按真实应用上下文判断。
- `INBOX` 为空：先确认 folder id，默认收件箱通常是 `1`；还要确认 ClawEmail 是否允许该外部发件人或邮件通道规则是否已生效。
- 命令没有响应：检查插件配置是否仍是 `direct`，以及 `/查邮件` 是否在 direct map 中。

## 11. Rhubarb 口型没有效果

检查：

- `RHUBARB_PATH` 是否指向真实 `rhubarb.exe`。
- 日志是否有 `RhubarbLipSync 初始化完成`。
- 音频格式是否被 Rhubarb 支持。
- 是否超时。

当前推荐：

- 不要把 mp3 假复制成 ogg。
- 分析失败时走时长兜底口型。
- 短句要先分析出口型，再贴近播放指令发送。

## 12. chat_service.py 太长

当前它承担了太多职责：

- 主聊天编排
- 工具路由
- QQ 网关输出
- 屏幕感知
- 日记总结
- 自然化润色
- 情绪兜底

低风险重构顺序：

1. 继续保持 `services/chat_support/gateway_sender.py`，它已经承接 QQ 文本、语音、图片、文件回发和分段逻辑。
2. 屏幕感知纯 helper、前置 guard、发送编排和生成链路已拆到 `sensor_utils.py` / `sensor_event_guard.py` / `sensor_reply_service.py` / `sensor_event_service.py`；事件结果对象、重复发送分支、截图采样、`VISION_MODE` 分派和主生成编排已收口。下一步只做 P7-B 收尾复查，不要改 `_sensor_event_lock`、冷却写入和 idle 兜底语义。
3. 每日总结和日记主流程已拆到 `diary_service.py`；`chat_service.py` 只保留兼容 wrapper 和 transcript 读取入口。
4. 回复风格的确定性 helper 已拆到 `reply_style_service.py`，包括口癖、标签清理、短反应、反馈识别和部分自然化判断；LLM 润色、情绪兜底和自我认知提示仍暂留 `ChatService`，后续应继续小步迁移。
5. 流式 buffer flush 已迁到 `reply_flow_service.py`，但气泡可见文本仍必须复用 `text_utils.strip_internal_tags()` 这类统一清理器，避免内部提示词在 `assistant.stream.feed` 阶段提前显示。
6. 最后再删旧 helper；删除前必须先 `rg` 查调用点，确认不是插件、GUI、Application 或测试仍在间接调用。

原则：先拆服务，不改行为；每一步都跑 `python -m py_compile`。

## 13. 屏幕吐槽一整天没有触发

先判断是“事件源没进来”还是“ChatService 拒绝吐槽”：

1. 查当天 activity events。如果 `data/sensor_stats.json` 的 `updated_at` 在更新，但 `counts/durations/observations` 都为空，同时 `modules.memory_sqlite.list_activity_events(date_str=当天)` 为 0，优先按活动采集链路排查。
2. 主程序不再自动拉起旧活动 sidecar，也不会切回 Python 本地窗口轮询；活动事件唯一来源是 live2d-only 的 Tauri/Rust 上报。
3. 查 `modules.memory_sqlite.list_activity_events(date_str=当天, source="live2d-tauri")` 是否持续出现 `foreground_changed` / `activity_sample`。如果没有，先检查 live2d-only 是否启动、`/gui/activity-ingest` 是否可达、GUI HTTP token 是否一致。
4. `ScreenSensor._recent_rust_events()` 只读取 `source=live2d-tauri`。数据库里的旧 `rust-agent`、`python-screen-sensor` 事件不会再参与屏幕感知和久坐计时。
5. 如果 activity events 有数据但仍不吐槽，再查 `Sensor Gatekeeper`、`低强度事件跳过`、`ChatService 未实际吐槽，不进入冷却`、以及 `SCREEN_GLOBAL_COOLDOWN` / `SCREEN_REACTION_COOLDOWN`。
6. `services/chat_support/sensor_event_guard.py` 只负责前置过滤；如果它返回 `reply_cooldown`，说明刚回复过，ChatService 按全局冷却静默跳过。

## 14. 久坐提醒没有弹出或重复弹出

1. live2d-only 的 Rust 会上报 `source=live2d-tauri`，并在 `activity_sample` / `sedentary_alert` 里携带 `sedentary.active_minutes`。先查 `modules.memory_sqlite.list_activity_events(date_str=当天, source="live2d-tauri")`。
2. 顶栏久坐时间只显示 live2d Rust payload。没有可信 `sedentary.active_minutes` 时会显示采集中，不再从 Python 窗口轮询、旧 `rust-agent`、或本地 `current_window_start_time` 推导。
3. 如果只有 `activity_sample` 没有 `sedentary_alert`，检查 live2d-only 的右键菜单久坐设置、Rust tracker 参数，以及 `/gui/activity-ingest` 是否持续成功。
4. `sedentary_alert` 到达后仍不弹，先查是否开启 `DND_MODE`；再查 Qt GUI 是否已启动并通过 `ScreenSensor.set_sedentary_popup_callback()` 接入 `QtChatTrayApp.show_sedentary_popup()`。
5. 中央确认弹窗由 `modules/gui/sedentary_popup.py` 读取 `SEDENTARY_POPUP_*` 配置；图片优先调用本地 QQ 表情包库 `plugins/meme_pack/data/memes.sqlite` 的只选不发送接口，只接受本地图片路径或 `file://` 本地 URI，不走 QQ 发送。
6. 如果本地 QQ 表情包库未选到本地图片或选择失败，会回退到 `SEDENTARY_POPUP_IMAGE_PATH`；该路径可以填表情包图片/GIF，路径不存在时会退回文字占位。
7. 弹窗默认 `SEDENTARY_POPUP_AUTO_CLOSE_SECONDS = 20` 秒后自动关闭，设为 `0` 可禁用自动关闭；自动关闭按普通关闭处理，不会触发“稍后提醒”。
8. Live2D 气泡/TTS 仍走 `services/chat_support/active_alert_service.py` 和 `presenter.present()`；当前弹窗不是 Windows 系统 Toast，live2d Rust 只负责采集和上报事件。
9. 主程序重启后不再自己桥接事件时间轴；只要 live2d-only 仍在运行，下一次 `live2d-tauri` payload 会恢复顶栏计时。
10. 离开电脑后是否归零由 live2d Rust 的 `rest_streak` / `break_minutes` 决定。若离开很久仍累计，优先查 live2d-only Rust 是否持续上报 `presence=active`，或 Windows `GetLastInputInfo` 是否被远程控制/播放器/外设持续刷新。
