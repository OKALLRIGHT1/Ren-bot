# Project Status

接手只看**当前事实**。接线与收敛批次以 `docs/architecture/README.md` 为准。安全未闭环见 `docs/SECURITY_REMEDIATION_PLAN.md`。

## 当前结构

- Python 推荐入口：`main.py` 守护进程 → `boot.py` → `core/application.py`；`boot.py` 仅用于临时调试核心进程。
- Windows 一键安装/启动：`setup_env.py`（`安装环境.bat` / `启动.bat` / `启动-无窗口.vbs`）；就绪标记 `.suzu-ready`。
- 对话主链路：`services/chat_service.py`，辅助服务在 `services/chat_support/`。
- 插件系统：`modules/plugin_manager.py` 和 `plugins/`。窄口：`knowledge` / `gateway` / `tts` / `app`；`brain` 仍在。**不再**注入 `ctx["chat_service"]` 或 `ctx["skills"]`。
- Qt 桌面设置中心：`modules/gui/`；记忆/知识页走 `services/gui_api/`。
- GUI HTTP/WS：`integrations/gui_http.py` / `integrations/gui_ws.py`，领域逻辑优先 `services/gui_api/`。
- Live2D 前端：独立仓库；本仓通过认证 GUI 通道通信。
- 桌面活动与久坐：Tauri 上报 `/gui/activity-ingest`；主程序只消费 `source=live2d-tauri`。连上增强版后，弹窗由桌面端渲染后端模板（话术/表情包），LLM 口头提醒只走气泡；顶部文案为「工作了多久」。未连接时增强版可独立弹窗。稍后/关闭经 `POST /gui/sedentary/ack` 回告冷却。
- 记忆：SQLite 为写入真相源。每轮都按当前这句话加上一句助手回复检索，回复路径不再调用精排模型。写回先记入 `memory_writeback_pending`，聊天线程只负责入队；成功就删掉该行，失败退避，五次后标失败。称呼始终可注入，其它事实只有用得上才带上。提示要求自然说出，不要说成「你说过」或「我记得」。单值事实内容冲突时标成 conflicted，不覆盖旧条；明确纠正才废止旧条。人和事的关系按实体边保存，点名可以召回。
- 跟进：任务问句带上原文摘要；关心按话题，同一话题约 20 小时内不重复，勿扰时不发。跟进和正文分成两段气泡。四字以内和「你好」这类短句不做 Character Thought（超时约 1.2 秒）。静音气泡下限约 1 秒。
- 表情：离开当前情绪时先停一步再切到另一组；安静约 90 秒后强度下降，约 5 分钟才回到平静。闲置动作间隔偏长，刚有活动时不播。久坐提醒三句轮换。
- 屏幕吐槽：模型如果把英文草稿、`<neutral>` 这类标签或「数字符」的过程说出来，这一句直接丢掉，不念。
- 工具：函数调用轮和执行共用 `plugin_executable_in_pass`。`delegate` 工具不在 react 轮暴露。确认按会话保存，5 分钟过期，挂起时只挡新的工具调用。
- 插件默认超时 30 秒。超时回写是「结果未知，不要重试」，因为底层线程不一定已经停掉。

## 当前重点

- 主程序与 Live2D 前后端分离：`docs/superpowers/specs/2026-07-16-frontend-backend-separation-design.md` 与对应 07-16 计划。
- QQ / NapCat 走 `integrations/chat_gateway/`；QQ 来源默认不驱动本地 Live2D/TTS。
- Agent 工具经插件 + `ActionGate`；见 `docs/agent-runtime.md` 与安全文档。
- 知识/人设未做部分（A5/A6、B1–B4）：`docs/superpowers/plans/2026-08-13-knowledge-and-persona-timeline.md`。
- 近史 Task 12 Planner 未做：`docs/superpowers/plans/2026-08-05-conversation-continuity.md`。
- 工具轮部分 function call（默认 dual；P4b 已撤 `ctx["chat_service"]`；第三波 schema 见计划 P7）：`docs/superpowers/plans/2026-08-21-tool-calling-function-call.md`。
- 减重规则：`docs/architecture/project_slimdown.md`。体量请现测，不要信旧表。

## 文档树

### 用户 / 运维

- `README.md`：介绍与启动
- `MCP_QQ_SETUP_GUIDE.md`：QQ / NapCat / MCP
- `docs/TROUBLESHOOTING.md`：排查
- `docs/ESP32_TFT_QUICKSTART.md` / `docs/EMBEDDED_ASSISTANT_PLAN.md`：硬件（暂缓，保留）

### 开发（长期事实）

- `docs/architecture/README.md`：**接线与批次入口**
- `docs/architecture/memory.md`：记忆/知识契约
- `docs/architecture/shipped-features.md`：已落地行为
- `docs/architecture/project_slimdown.md`：瘦身门槛
- `docs/architecture/coupling-and-overdesign-review.md`：审查原文（P0–P4b 已标落地）
- `PLUGINS_GUIDE.md` / `docs/agent-runtime.md` / `docs/SECURITY_REMEDIATION_PLAN.md`

### 仍进行中的计划 / 设计

- `docs/superpowers/specs/2026-07-16-frontend-backend-separation-design.md`
- `docs/superpowers/plans/2026-07-16-frontend-backend-connection-profiles.md`
- `docs/superpowers/plans/2026-08-05-conversation-continuity.md`（Task 12 未做）
- `docs/superpowers/plans/2026-08-13-knowledge-and-persona-timeline.md`（A5/A6、B1–B4 未做）
- `docs/superpowers/plans/2026-08-21-tool-calling-function-call.md`（工具轮 FC，默认 dual）
- `docs/superpowers/plans/2026-08-27-qwen3-asr.md`（本机 ASR：代码已接 Qwen3-0.6B；待升 sherpa-onnx 并下载模型）
- `docs/superpowers/specs/2026-08-03-runtime-health-and-rust-screen-design.md`（已落地设计说明）

已做完并删掉的勾选表：08-03 运行健康/Rust 屏实施计划、08-04 健康 GUI 计划与设计、08-11 自然聊计划、08-12 屏幕会话次数计划；另删同日重复审查 `docs/CODE_REVIEW_COUPLING.md`。

## 维护规则

1. 长期事实写 `docs/architecture/` 或本文件，不要堆流水账。
2. 一次性 plan / review / 勾选表：做完即删，或只留 architecture 摘要。
3. 安全边界变化同步 `docs/SECURITY_REMEDIATION_PLAN.md`。
4. 用户向说明只写 `README.md` 和 setup/troubleshooting。
5. 硬件文档在砍掉硬件方向前保留。
