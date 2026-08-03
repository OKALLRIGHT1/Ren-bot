# Project Status

本文档只保留接手项目需要的**当前事实**。一次性 plan / code review 全文默认不进库；安全未闭环项见 `docs/SECURITY_REMEDIATION_PLAN.md`。

## 当前结构

- Python 推荐入口：`main.py` 守护进程 → `boot.py` → `core/application.py`；`boot.py` 仅用于临时调试核心进程。
- 对话主链路：`services/chat_service.py`，辅助服务在 `services/chat_support/`。
- 插件系统：`modules/plugin_manager.py` 和 `plugins/`。
- Qt 桌面设置中心：`modules/gui/`；设置页增量放在 `modules/gui/settings_pages/`。
- GUI HTTP/WS：`integrations/gui_http.py` / `integrations/gui_ws.py`，领域逻辑优先 `services/gui_api/`。
- Live2D 前端：增强版 / live2d-only 独立仓库；本仓通过认证 GUI 通道与兼容传输通信。
- 桌面活动与久坐：Live2D/Tauri 端上报 `/gui/activity-ingest`；主程序只消费 `source=live2d-tauri` 事件，不回退本地键鼠采集。
- 记忆：SQLite 为写入真相源；语义分类与向量索引说明见 `docs/architecture/memory.md`。

## 当前重点

- 主程序与 Live2D 前后端分离（连接档案、认证 HTTP/WSS、活动上报）；设计见 `docs/superpowers/specs/2026-07-16-frontend-backend-separation-design.md`，实现计划见 `docs/superpowers/plans/` 中 2026-07-16 相关文档。
- QQ / NapCat 走 `integrations/chat_gateway/`；QQ 来源默认不驱动本地 Live2D/TTS。
- Agent 工具以插件方式接入（文件、代码代理、邮件、MCP、搜索等），高风险动作应对齐 `ActionGate` / 权限矩阵（见安全文档）。
- 记忆系统使用 SQLite + 可重建向量索引；嵌入模型走模型目录统一管理。
- 减重：`ChatService` / `SettingsDialog` / `GuiHttpServer` / `Live2DApplication` 仍为 Critical 体量，规则见 `docs/architecture/project_slimdown.md`。

## 体量快照（2026-07-31）

| 文件 | 行数 | 级别 |
| --- | ---: | --- |
| `modules/gui/dialogs/settings.py` | 5761 | Critical |
| `services/chat_service.py` | 5184 | Critical |
| `core/application.py` | 3399 | Critical |
| `integrations/gui_http.py` | 3394 | Critical |
| `modules/screen_sensor.py` | 1972 | High |
| `modules/plugin_manager.py` | 1554 | High |
| `config.py` | 839 | Watch |

测量命令见 `docs/architecture/project_slimdown.md`。

## 保留文档

### 用户 / 运维

- `README.md`：项目介绍与启动说明
- `MCP_QQ_SETUP_GUIDE.md`：QQ / NapCat / MCP 接入
- `docs/TROUBLESHOOTING.md`：常见问题排查
- `docs/ESP32_TFT_QUICKSTART.md`：ESP32 TFT 状态屏（硬件，暂缓但保留）
- `docs/EMBEDDED_ASSISTANT_PLAN.md`：嵌入式长期路线（硬件，暂缓但保留）

### 开发

- `PLUGINS_GUIDE.md`：插件开发、配置与权限
- `docs/agent-runtime.md`：Agent 工具运行时
- `docs/SECURITY_REMEDIATION_PLAN.md`：安全边界与未闭环风险
- `docs/architecture/project_slimdown.md`：减重原则与门槛
- `docs/architecture/memory.md`：记忆事实源与 GUI 组织
- `docs/architecture/shipped-features.md`：已落地特性摘要（搜索确认、徽章、嵌入目录）
- `skills/README.md`：文档型 Skill 说明

### 进行中设计 / 计划

- `docs/superpowers/specs/2026-07-16-frontend-backend-separation-design.md`
- `docs/superpowers/plans/2026-07-16-frontend-backend-connection-profiles.md`
- `docs/superpowers/plans/2026-07-16-complete-enhanced-gui-migration.md`

## 维护规则

1. 新增长期事实优先写进本文件，不要恢复流水账。
2. 一次性 plan、review、执行勾选表：做完即删或只保留 architecture 摘要；默认不进主文档树。
3. 安全边界变化同步更新 `docs/SECURITY_REMEDIATION_PLAN.md`。
4. 用户向说明只写 `README.md`（及明确的 setup/troubleshooting），不把内部实现计划塞进去。
5. 硬件相关文档在明确砍掉硬件方向前保留。
