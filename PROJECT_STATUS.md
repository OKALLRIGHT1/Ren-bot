# Project Status

接手只看**当前事实**。接线与收敛批次以 `docs/architecture/README.md` 为准。安全未闭环见 `docs/SECURITY_REMEDIATION_PLAN.md`。

## 当前结构

- Python 推荐入口：`main.py` 守护进程 → `boot.py` → `core/application.py`；`boot.py` 仅用于临时调试核心进程。
- 对话主链路：`services/chat_service.py`，辅助服务在 `services/chat_support/`。
- 插件系统：`modules/plugin_manager.py` 和 `plugins/`。知识插件窄口：`ctx["knowledge"]`（`BrainKnowledgePort`）；`ctx["brain"]` / `ctx["chat_service"]` 仍在。
- Qt 桌面设置中心：`modules/gui/`；记忆/知识页走 `services/gui_api/`。
- GUI HTTP/WS：`integrations/gui_http.py` / `integrations/gui_ws.py`，领域逻辑优先 `services/gui_api/`。
- Live2D 前端：独立仓库；本仓通过认证 GUI 通道通信。
- 桌面活动与久坐：Tauri 上报 `/gui/activity-ingest`；主程序只消费 `source=live2d-tauri`。
- 记忆：SQLite 为写入真相源；见 `docs/architecture/memory.md`。

## 当前重点

- 主程序与 Live2D 前后端分离：`docs/superpowers/specs/2026-07-16-frontend-backend-separation-design.md` 与对应 07-16 计划。
- QQ / NapCat 走 `integrations/chat_gateway/`；QQ 来源默认不驱动本地 Live2D/TTS。
- Agent 工具经插件 + `ActionGate`；见 `docs/agent-runtime.md` 与安全文档。
- 知识/人设未做部分（A5/A6、B1–B4）：`docs/superpowers/plans/2026-08-13-knowledge-and-persona-timeline.md`。
- 近史 Task 12 Planner 未做：`docs/superpowers/plans/2026-08-05-conversation-continuity.md`。
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
- `docs/architecture/coupling-and-overdesign-review.md`：审查原文（P0–P4a 已标落地）
- `PLUGINS_GUIDE.md` / `docs/agent-runtime.md` / `docs/SECURITY_REMEDIATION_PLAN.md`
- `skills/README.md`

### 仍进行中的计划 / 设计

- `docs/superpowers/specs/2026-07-16-frontend-backend-separation-design.md`
- `docs/superpowers/plans/2026-07-16-frontend-backend-connection-profiles.md`
- `docs/superpowers/plans/2026-08-05-conversation-continuity.md`（Task 12 未做）
- `docs/superpowers/plans/2026-08-13-knowledge-and-persona-timeline.md`（A5/A6、B1–B4 未做）
- `docs/superpowers/specs/2026-08-03-runtime-health-and-rust-screen-design.md`（已落地设计说明）

已做完并删掉的勾选表：08-03 运行健康/Rust 屏实施计划、08-04 健康 GUI 计划与设计、08-11 自然聊计划、08-12 屏幕会话次数计划；另删同日重复审查 `docs/CODE_REVIEW_COUPLING.md`。

## 维护规则

1. 长期事实写 `docs/architecture/` 或本文件，不要堆流水账。
2. 一次性 plan / review / 勾选表：做完即删，或只留 architecture 摘要。
3. 安全边界变化同步 `docs/SECURITY_REMEDIATION_PLAN.md`。
4. 用户向说明只写 `README.md` 和 setup/troubleshooting。
5. 硬件文档在砍掉硬件方向前保留。
