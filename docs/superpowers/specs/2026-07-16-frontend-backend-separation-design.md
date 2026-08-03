# Live2D 增强版与 Python 后端分离设计

**日期：** 2026-07-16

**状态：** 现行设计（连接档案 / 迁移实现见 `docs/superpowers/plans/`）

> 早期草案 `docs/REMOTE_LIVE2D_GUI_PLAN.md` 已并入本节「附录：后端远程化基础与历史约束」，请勿再维护旧文件。

## 目标

将 `live2d-enhanced` 固定为独立桌面前端，将 `live2d-llm` 固定为独立 Python 后端。两者可以分别安装、启动、升级和部署；增强版可选连接、启动、停止或重启本地后端，但不得取得外部进程的所有权，也不得要求远程服务器反向访问桌面端口。

## 系统边界

### Live2D 增强版

负责：

- Live2D 模型渲染、动作、表情、气泡和音频播放。
- 控制中心、托盘和后续悬浮球。
- 本机键鼠与前台程序活动采集，以及久坐提醒的本地显示。
- 保存连接档案和本地进程所有权状态。
- 主动连接 Python 后端的 HTTP API 与 WebSocket 事件通道。

不负责：

- 聊天推理、插件执行、模型路由、记忆和业务规则。
- 替远程服务器管理系统进程。
- 保存或返回 Python 插件密钥。

### Python 后端

负责：

- 聊天、插件、模型、记忆、搜索、日报和其他业务能力。
- 提供认证 HTTP API 与 WebSocket 事件通道。
- 接收增强版活动事件并形成屏幕感知、久坐状态和日报数据。
- 通过已建立的 GUI WebSocket 下发 Live2D 指令。

不负责：

- 在远程模式下主动连接桌面 `127.0.0.1:10086`。
- 管理增强版窗口、托盘或本机输入监听。
- 将服务器本地文件路径直接交给远程桌面播放。

## 连接档案

增强版保存多个连接档案，任一时刻只有一个活动档案。档案元数据保存在 Tauri `app_config_dir/backend-profiles.json`；令牌不写入该 JSON，而是以 `profile_id` 为键保存在操作系统凭据存储中。

```json
{
  "schema_version": 1,
  "active_profile_id": "local-main",
  "profiles": [
    {
      "id": "local-main",
      "name": "本机主程序",
      "mode": "local_managed",
      "http_base_url": null,
      "websocket_url": null,
      "backend_root": "D:/Desktop/live2d-suzu/live2d-llm",
      "python_executable": "D:/Python/python.exe",
      "launch_mode": "legacy_gui",
      "start_on_enhanced_launch": false,
      "stop_owned_on_exit": false,
      "has_token": true
    }
  ]
}
```

### `local_managed`

- 必须配置后端目录和 Python 路径。
- 启动后从后端 `data/runtime_settings.json` 发现实际 HTTP/WS 端点。
- `launch_mode=legacy_gui` 时不设置 `GUI_BACKEND=headless`；迁移期默认使用该模式，旧 GUI 继续可用。
- `launch_mode=headless` 仅由用户显式选择。
- 只有增强版本次进程创建并持有 `Child` 的 PID 才是 `owned_by_enhanced=true`。
- 只允许停止或重启自有进程。

### `local_attached`

- 连接已经运行的本地后端。
- 可手工填写 loopback HTTP/WS 地址，也可选择后端目录后只读发现 `runtime_settings.json`。
- 永远不显示停止或重启按钮，不记录进程所有权。

### `remote`

- 必须显式填写 HTTPS 与 WSS 地址。
- 增强版主动建立出站连接；服务器不连接桌面端口。
- 不提供进程启动、停止或重启操作。
- 非 loopback 地址禁止 `http://` 和 `ws://`。

## 令牌契约

React 侧只能提交令牌变更意图：

```ts
type TokenUpdate =
  | { mode: "keep" }
  | { mode: "replace"; value: string }
  | { mode: "clear" };
```

Rust 返回的档案只包含 `has_token: boolean`，不包含令牌、掩码令牌或可还原的密文。日志、Debug 输出、错误信息、Tauri 事件和测试快照都不得出现令牌。

## 统一通信

### HTTP

HTTP 用于请求/响应型操作：健康检查、设置、插件、记忆、活动配置、活动上报和一次性媒体下载。所有 `/gui/*` 路由继续使用 `X-GUI-Token`；非 loopback 部署由反向代理提供 TLS。

### WebSocket

增强版主动连接后端 WSS，并在连接后发送：

```json
{
  "type": "hello",
  "client": "live2d-enhanced",
  "protocol_version": 1,
  "capabilities": ["gui.v1", "live2d.protocol.v1", "activity.config.v1"]
}
```

同一通道承载：

- GUI 日志、状态、角色和服装事件。
- GUI 命令。
- Live2D 原始协议指令。
- 活动配置失效通知。

Live2D 指令使用以下信封，不重新定义已有 `msg/data` 协议：

```json
{
  "type": "live2d_protocol",
  "version": 1,
  "command_id": "uuid",
  "message": {"msg": 13200, "msgId": 2, "data": {"id": 0, "mtn": "idle"}}
}
```

本地旧 WebSocket 与 GUI WebSocket 可以同时收到同一指令，因此每条指令必须带相同 `command_id`；增强版维护有界 TTL 去重表，保证同一动作、气泡或音频只执行一次。`live2d-only` 不要求理解新字段，继续按原协议工作。

## 远程音频

服务器本地音频路径对远程桌面无效。Python 后端新增短时媒体票据：

- 只允许注册 TTS/插件已经生成并明确交付给 Live2D 的文件。
- 票据使用随机 ID、单次或短时有效、限制文件大小和 MIME 类型。
- `/gui/media/{ticket}` 需要 `X-GUI-Token`。
- Rust 使用活动档案的令牌下载到 Tauri 缓存，再把本地 asset URL 交给前端。
- 票据过期、下载失败或类型不支持时明确报错，不回退到服务器文件路径。

动作、表情和气泡不依赖媒体下载。远程模型文件迁移不属于本轮范围；远程模式继续使用增强版本地模型。

## Live2D 传输兼容

`modules/live2d.py` 继续负责构造现有 `msg/msgId/data`，但不再直接拥有唯一传输策略。新建输出总线同时支持：

- `LegacyLocalWebSocketTransport`：保留 `live2d-only` 和旧本地客户端兼容。
- `GuiWebSocketTransport`：通过已连接且声明 `live2d.protocol.v1` 的 GUI 客户端发送。

业务调用方不判断本地或远程。输出总线负责生成一次 `command_id`，并把同一指令交给所有可用传输；任一传输成功即视为已投递，全部失败才返回错误。

## 久坐与活动数据

Rust/Tauri 仍是桌面活动的唯一采集源，Python 不恢复键鼠或窗口轮询。

- 增强版从活动档案对应的 `GET /gui/activity-config` 获取久坐阈值、休息时长、冷却时间和隐私字段。
- 增强版向该档案对应的 `POST /gui/activity-ingest` 上报。
- 本地和远程走同一 `BackendTransport`，不再从 Python 后端目录读取久坐配置。
- 无后端或离线时，增强版使用最后一次成功配置；从未同步过时使用内置默认值。
- 独立 Live2D 使用时仍可本地提醒，但不会伪造已上报成功。

## 旧 GUI 共存

- 本轮不删除、不禁用、不自动关闭 Python Qt GUI。
- 连接到已经运行的旧 GUI 后端时，增强版进入 `local_attached` 语义，停止和重启不可用。
- `local_managed` 默认以 `legacy_gui` 启动，用户可显式改为 headless。
- 只有当增强版功能验收覆盖旧 GUI 后，才另立迁移计划讨论默认 headless 或移除旧 GUI。

## 迁移与回滚

- 旧 `backend-launch.json` 首次启动时迁移成一个 `local_managed` 档案，原文件保留为 `.migrated.bak`。
- 迁移后的 `start_on_enhanced_launch` 和 `stop_owned_on_exit` 保留旧值，但界面明确显示进程所有权。
- 新档案保存失败时不得覆盖旧文件；使用临时文件和原子替换。
- Python 新协议在增强版未声明能力时继续走本地旧 WebSocket，因此可独立回滚任一仓库。

## 不变量

1. Python 后端在没有增强版时仍能独立运行。
2. 增强版在没有 Python 后端时仍能作为纯 Live2D 程序运行。
3. 外部启动的 Python 进程绝不被增强版停止。
4. 非 loopback 连接绝不允许明文 HTTP/WS。
5. 令牌绝不返回 React 或写入档案 JSON。
6. 远程后端不依赖访问桌面入站端口。
7. 久坐活动只有 Rust/Tauri 一个采集源。
8. 旧 GUI 在替代验收完成前保持可用。

## 非目标

- 本轮不删除旧 Python GUI。
- 本轮不把 Python 业务逻辑迁入 Tauri。
- 本轮不实现远程进程管理或 SSH。
- 本轮不传输完整远程 Live2D 模型包。
- 本轮不修改 `live2d-only` 的安装标识和独立发行方式。

## 附录：后端远程化基础与历史约束

以下内容来自早期 Remote Backend + Local Live2D GUI 方案，与上文不变量一致，仅补充后端侧已有端口与排障面。

### 当前可用状态

- Python 后端仍可本地完整运行；Qt 设置中心仍是稳定入口之一。
- GUI HTTP / WS 已提供远程化基础。
- 本地 Tauri / 增强版逐步接管展示；音频与 Live2D 播放应在桌面侧完成，服务器本地路径不能直接交给远程客户端。

### GUI 端点与运行时设置

环境变量（也可写入 `data/runtime_settings.json`）：

- `GUI_WS_HOST` / `GUI_WS_PORT` / `GUI_WS_PATH`（默认 `127.0.0.1` / `8096` / `/gui`）
- `GUI_HTTP_HOST` / `GUI_HTTP_PORT` / `GUI_HTTP_PREFIX`（默认 `127.0.0.1` / `8097` / `/gui`）

`0.0.0.0` 只建议在防火墙、内网、VPN 或反代鉴权后使用。GUI HTTP 使用 `gui_access_token`，接受 `Authorization: Bearer <token>` 或 `X-GUI-Token`；非 loopback 必须 HTTPS/WSS。

### 排障接口（示例）

- `GET /gui/outbound?limit=50`
- `GET /gui/reply-effects?limit=50&session_id=...`
- `GET /gui/deferred-tools/stats`
- `GET /gui/plugins/config?trigger=xxx`（config + schema）

### 语音与路径

真正远程分离时：后端通过已建立的 GUI WS 下发文本、情绪、动作和**可下载的媒体票据/URL**，由本地 Tauri 播放；不要依赖服务器本机绝对路径，也不要依赖服务器回连桌面 `127.0.0.1:10086`。
