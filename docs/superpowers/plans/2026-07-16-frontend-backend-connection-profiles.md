# Live2D 增强版与 Python 后端连接档案 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Live2D 增强版建设为可独立运行的 Tauri 前端，并通过本地托管、本地附着或远程连接档案安全连接独立 Python 后端，同时保留旧 GUI 和纯 Live2D 兼容性。

**Architecture:** Tauri/Rust 保存连接档案、令牌和自有进程状态，React 只编辑非敏感元数据并显示脱敏状态。增强版主动连接 Python 的认证 HTTP/WSS，同一 WebSocket 承载 GUI 与 Live2D 指令；Python 的 Live2D 输出通过可插拔传输总线同时兼容旧本地 WebSocket。Rust/Tauri 继续作为活动采集唯一来源，久坐配置改由活动档案对应的认证 API 获取。

**Tech Stack:** Tauri 2、Rust、React 19、TypeScript、Vite、Vitest、Tungstenite、Reqwest、Python 3、aiohttp、websockets、pytest

---

## 仓库与执行边界

- Python 仓库：`D:/Desktop/live2d-suzu/live2d-llm`
- 增强版仓库：`D:/Desktop/live2d-suzu/live2d-enhanced`
- 设计规格：`D:/Desktop/live2d-suzu/live2d-llm/docs/superpowers/specs/2026-07-16-frontend-backend-separation-design.md`
- 两个仓库分别提交。一个任务同时改两端时，先提交协议提供端，再提交消费端。
- Python 仓库当前存在大量用户未提交改动。每次只暂存本任务列出的文件，不执行整体 `git add .`，不清理其他改动。
- 实施前在增强版仓库使用 `superpowers:using-git-worktrees` 创建隔离工作树；Python 仓库因用户改动密集，在当前工作树做窄范围修改并逐文件暂存。

## 必须保持的不变量

1. 旧 Python Qt GUI 不删除、不默认关闭。
2. 增强版只能停止或重启本次进程自己启动的 Python 子进程。
3. 本地附着和远程连接不提供进程停止、重启能力。
4. 非 loopback 地址只允许 HTTPS/WSS。
5. React 永远拿不到已有 token 明文。
6. 远程 Python 不需要访问桌面 `127.0.0.1:10086`。
7. Rust/Tauri 是桌面活动唯一采集源。
8. `live2d-only` 与增强版继续使用不同安装标识，可同时安装。

## 文件结构

### `live2d-enhanced`

- Create: `src-tauri/src/backend_profile.rs`：连接档案 schema、持久化、旧配置迁移和活动档案选择。
- Create: `src-tauri/src/backend_secret.rs`：操作系统凭据存储与 token 变更语义。
- Create: `src-tauri/src/backend_transport.rs`：HTTP/WSS 地址校验、认证请求和媒体下载。
- Create: `src-tauri/src/backend_supervisor.rs`：本地托管进程的启动、所有权、停止和退出清理。
- Create: `src-tauri/src/backend_bridge.rs`：单一后端 WebSocket 重连、命令队列和 Tauri 事件派发。
- Modify: `src-tauri/src/backend.rs`：迁移期只保留兼容 re-export，最终删除旧混合职责实现。
- Modify: `src-tauri/src/lib.rs`：注册状态和窄 Tauri commands，不再承载桥接线程实现。
- Modify: `src-tauri/src/activity.rs`：通过活动档案 transport 获取设置和上报，不读 Python 目录。
- Create: `src/control-center/connection-profiles.ts`：React 连接档案联合类型和纯状态转换。
- Create: `src/control-center/connection-profiles.test.ts`：模式切换、token 更新和按钮权限测试。
- Create: `src/control-center/BackendConnectionPanel.tsx`：连接档案管理 UI。
- Create: `src/backendLive2d.ts`：后端 Live2D 信封解析、命令 ID 去重和事件适配。
- Create: `src/backendLive2d.test.ts`：远程指令解析和重复投递测试。
- Modify: `src/control-center/pages.tsx`、`src/control-center/backend-status.ts`、`src/control-center/main.tsx`、`src/control-center/styles.css`、`src/main.ts`、`src/apiProtocol.ts`。
- Create: `src-tauri/tests/backend_profiles.rs`、`backend_transport.rs`、`backend_supervisor.rs`、`backend_bridge.rs`、`activity_profile_transport.rs`。

### `live2d-llm`

- Create: `integrations/gui_protocol.py`：GUI hello、能力声明和 Live2D 信封的唯一协议构造器。
- Modify: `integrations/gui_ws.py`：维护客户端能力，按能力广播，禁止 URL query token。
- Create: `integrations/gui_media.py`：短时媒体票据注册与受限读取。
- Modify: `integrations/gui_http.py`：活动配置和媒体下载 API。
- Create: `modules/live2d_transport.py`：本地旧 WS 与 GUI WS 输出总线。
- Modify: `modules/live2d.py`：只构造 Live2D 指令并交给输出总线。
- Modify: `core/application.py`：注册 GUI transport、协议 hello、活动配置失效事件和媒体票据。
- Create: `tests/test_gui_protocol.py`、`test_gui_ws_capabilities.py`、`test_gui_media.py`、`test_live2d_transport.py`、`test_remote_activity_config.py`。
- Modify: `tests/test_gui_ws_compat.py`、`test_live2d_motion_candidates.py`、`test_sedentary_runtime_settings.py`、`test_activity_ingest_storage.py`、`security_smoke.py`。

---

### Task 1: 定义连接档案、令牌存储和旧配置迁移

**Repository:** `D:/Desktop/live2d-suzu/live2d-enhanced`

**Files:**
- Create: `src-tauri/src/backend_profile.rs`
- Create: `src-tauri/src/backend_secret.rs`
- Create: `src-tauri/tests/backend_profiles.rs`
- Modify: `src-tauri/src/lib.rs`
- Modify: `src-tauri/Cargo.toml`

- [ ] **Step 1: 写失败测试固定三种档案和 token 不回传契约**

在 `src-tauri/tests/backend_profiles.rs` 覆盖：

```rust
#[test]
fn profile_summary_never_serializes_token() {
    let profile = BackendProfile::remote(
        "remote-main",
        "远程主机",
        "https://assistant.example.com/gui",
        "wss://assistant.example.com/gui",
    );
    let summary = profile.summary(true);
    let json = serde_json::to_string(&summary).unwrap();
    assert!(json.contains("\"has_token\":true"));
    assert!(!json.contains("secret-token"));
    assert!(!json.contains("access_token"));
}

#[test]
fn legacy_launch_config_migrates_once_to_managed_profile() {
    let root = unique_temp_dir("profile-migration");
    write_valid_legacy_launch_config(&root);
    let store = ProfileStore::new(root.join("backend-profiles.json"));
    let result = store.migrate_legacy(root.join("backend-launch.json")).unwrap();
    assert_eq!(result.profiles.len(), 1);
    assert_eq!(result.profiles[0].mode(), BackendMode::LocalManaged);
    assert!(root.join("backend-launch.migrated.bak").is_file());
}

#[test]
fn token_update_keep_replace_and_clear_are_explicit() {
    let secrets = MemorySecretStore::default();
    secrets.apply("local-main", TokenUpdate::Replace { value: "abc".into() }).unwrap();
    secrets.apply("local-main", TokenUpdate::Keep).unwrap();
    assert_eq!(secrets.read("local-main").unwrap().as_deref(), Some("abc"));
    secrets.apply("local-main", TokenUpdate::Clear).unwrap();
    assert_eq!(secrets.read("local-main").unwrap(), None);
}
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `cargo test --manifest-path src-tauri/Cargo.toml --test backend_profiles`

Expected: FAIL，提示 `backend_profile`、`BackendProfile` 和 `TokenUpdate` 尚不存在。

- [ ] **Step 3: 实现带版本的联合 schema 和原子保存**

在 `backend_profile.rs` 定义实际类型：

```rust
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum BackendMode { LocalManaged, LocalAttached, Remote }

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum LaunchMode { LegacyGui, Headless }

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct BackendProfile {
    pub id: String,
    pub name: String,
    pub mode: BackendMode,
    pub http_base_url: Option<String>,
    pub websocket_url: Option<String>,
    pub backend_root: Option<PathBuf>,
    pub python_executable: Option<PathBuf>,
    pub launch_mode: Option<LaunchMode>,
    pub start_on_enhanced_launch: bool,
    pub stop_owned_on_exit: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct BackendProfilesFile {
    pub schema_version: u32,
    pub active_profile_id: Option<String>,
    pub profiles: Vec<BackendProfile>,
}

#[derive(Clone, Debug, Serialize)]
pub struct BackendProfileSummary {
    #[serde(flatten)]
    pub profile: BackendProfile,
    pub has_token: bool,
}
```

`ProfileStore::save()` 必须写 `backend-profiles.tmp`，成功 `sync_all()` 后原子替换正式文件。校验 `id` 非空且唯一、活动 ID 必须存在、`local_managed` 必须有两个本地路径、`local_attached/remote` 必须有两个端点。

- [ ] **Step 4: 实现令牌存储抽象和系统凭据实现**

在 `backend_secret.rs` 定义：

```rust
pub enum TokenUpdate {
    Keep,
    Replace { value: String },
    Clear,
}

pub trait SecretStore: Send + Sync {
    fn read(&self, profile_id: &str) -> Result<Option<String>, String>;
    fn write(&self, profile_id: &str, token: &str) -> Result<(), String>;
    fn delete(&self, profile_id: &str) -> Result<(), String>;
}
```

增加 `keyring = "3"`，服务名固定为 `com.live2d-suzu.enhanced.backend`，账户名为 `profile_id`。`Debug` 实现只能输出 `has_token`。Tauri command 接收 `TokenUpdate`，返回 `BackendProfileSummary`。

- [ ] **Step 5: 运行测试和静态检查**

Run: `cargo test --manifest-path src-tauri/Cargo.toml --test backend_profiles`

Run: `cargo clippy --manifest-path src-tauri/Cargo.toml --all-targets -- -D warnings`

Expected: PASS；`backend-profiles.json` 测试快照不含 token。

- [ ] **Step 6: 提交**

```bash
git add src-tauri/Cargo.toml src-tauri/Cargo.lock src-tauri/src/backend_profile.rs src-tauri/src/backend_secret.rs src-tauri/src/lib.rs src-tauri/tests/backend_profiles.rs
git commit -m "feat: 增加后端连接档案与安全令牌存储"
```

---

### Task 2: 统一端点解析、TLS 约束和认证 transport

**Repository:** `D:/Desktop/live2d-suzu/live2d-enhanced`

**Files:**
- Create: `src-tauri/src/backend_transport.rs`
- Create: `src-tauri/tests/backend_transport.rs`
- Modify: `src-tauri/src/backend_profile.rs`
- Modify: `src-tauri/src/lib.rs`
- Modify: `src-tauri/Cargo.toml`
- Modify: `src-tauri/Cargo.lock`

- [ ] **Step 1: 写地址和认证失败测试**

```rust
#[test]
fn plaintext_is_only_allowed_for_loopback() {
    assert!(BackendEndpoints::parse(
        "http://127.0.0.1:8097/gui",
        "ws://127.0.0.1:8096/gui"
    ).is_ok());
    assert!(BackendEndpoints::parse(
        "https://assistant.example.com/gui",
        "wss://assistant.example.com/gui"
    ).is_ok());
    assert_eq!(
        BackendEndpoints::parse(
            "http://assistant.example.com/gui",
            "ws://assistant.example.com/gui"
        ).unwrap_err(),
        "非本机后端必须使用 HTTPS 和 WSS"
    );
}

#[test]
fn authenticated_request_uses_header_not_query() {
    let request = build_ws_request("wss://assistant.example.com/gui", "secret").unwrap();
    assert_eq!(request.headers()["X-GUI-Token"], "secret");
    assert!(!request.uri().to_string().contains("secret"));
}
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `cargo test --manifest-path src-tauri/Cargo.toml --test backend_transport`

Expected: FAIL，提示 `BackendEndpoints` 和 `build_ws_request` 未定义。

- [ ] **Step 3: 实现端点解析和单一认证客户端**

`BackendEndpoints::parse()` 使用 `url::Url`，HTTP 与 WS host 必须一致；仅 `127.0.0.1`、`::1`、`localhost` 可使用明文。增加 `url = "2"`，并为 Tungstenite 启用 `rustls-tls-native-roots`，确保 `wss://` 不是只通过字符串校验而是可以真实握手。`BackendTransport` 持有 `BackendEndpoints + SecretStore + profile_id`，提供：

```rust
pub trait BackendClient: Send + Sync {
    fn health(&self) -> Result<(), String>;
    fn request_json(&self, request: &BackendApiRequest) -> Result<Value, String>;
    fn websocket_request(&self) -> Result<Request<()>, String>;
    fn download_media(&self, ticket: &str, max_bytes: usize) -> Result<PathBuf, String>;
}
```

`ActiveBackendClient` 持有当前活动 `profile_id` 和 `Arc<dyn BackendClient>`，只由档案切换命令替换，bridge、activity 和 Tauri API commands 共享该实例。所有 HTTP 请求和 WS 握手只使用 `X-GUI-Token`。API allowlist 保留在 Rust 边界，不接受完整任意 URL；媒体 ticket 只允许 `[A-Za-z0-9_-]{20,128}`。

- [ ] **Step 4: 将本地托管端点发现收束到 profile resolver**

`local_managed` 只在 Rust 后端层读取 `<backend_root>/data/runtime_settings.json`，解析后转换为 `BackendEndpoints`；`local_attached/remote` 直接使用档案端点。React 和 activity 模块都不得读取该文件。

- [ ] **Step 5: 运行目标测试**

Run: `cargo test --manifest-path src-tauri/Cargo.toml --test backend_transport`

Expected: PASS，且测试服务确认请求头含 token、URL 不含 token。

- [ ] **Step 6: 提交**

```bash
git add src-tauri/Cargo.toml src-tauri/Cargo.lock src-tauri/src/backend_transport.rs src-tauri/src/backend_profile.rs src-tauri/src/lib.rs src-tauri/tests/backend_transport.rs
git commit -m "feat: 统一后端连接与安全传输校验"
```

---

### Task 3: 拆分本地进程管理并锁定所有权

**Repository:** `D:/Desktop/live2d-suzu/live2d-enhanced`

**Files:**
- Create: `src-tauri/src/backend_supervisor.rs`
- Create: `src-tauri/tests/backend_supervisor.rs`
- Modify: `src-tauri/src/backend.rs`
- Modify: `src-tauri/src/lib.rs`
- Modify: `src-tauri/tests/backend_connection.rs`

- [ ] **Step 1: 写外部进程不可停止和启动模式测试**

```rust
#[test]
fn attached_backend_never_becomes_owned() {
    let supervisor = BackendSupervisor::default();
    let profile = local_attached_profile(fake_backend.endpoints());
    let status = supervisor.status(&profile, &fake_backend.client()).unwrap();
    assert_eq!(status.state, "ready");
    assert!(!status.owned_by_enhanced);
    assert_eq!(supervisor.stop(&profile).unwrap_err(), "后端不是增强版启动的进程");
}

#[test]
fn managed_legacy_gui_does_not_force_headless() {
    let command = build_backend_command(&managed_profile(LaunchMode::LegacyGui)).unwrap();
    assert!(command.get_envs().all(|(key, _)| key != "GUI_BACKEND"));
}

#[test]
fn managed_headless_sets_explicit_environment() {
    let command = build_backend_command(&managed_profile(LaunchMode::Headless)).unwrap();
    assert!(command.get_envs().any(|(key, value)| {
        key == "GUI_BACKEND" && value == Some(std::ffi::OsStr::new("headless"))
    }));
}
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `cargo test --manifest-path src-tauri/Cargo.toml --test backend_supervisor`

Expected: FAIL，提示 `BackendSupervisor` 尚不存在。

- [ ] **Step 3: 从 `backend.rs` 移出进程职责**

`BackendSupervisor` 只保存 `Mutex<Option<OwnedBackendProcess>>`。`start()` 仅接受 `local_managed`；若 health 已就绪但没有自有 `Child`，返回 attached 状态，不启动第二个进程。`stop/restart/stop_on_exit` 首先核对活动档案 ID 与启动时保存的 profile ID，再操作 Child。

状态必须包含：

```rust
pub struct BackendStatus {
    pub profile_id: Option<String>,
    pub mode: Option<BackendMode>,
    pub state: String,
    pub message: String,
    pub owned_by_enhanced: bool,
    pub can_start: bool,
    pub can_stop: bool,
    pub can_restart: bool,
}
```

权限由 Rust 返回，React 不自行推断破坏性操作权限。

- [ ] **Step 4: 保持现有生命周期集成测试通过**

把 `src-tauri/tests/backend_connection.rs` 的旧 `BackendProcessState` 用例迁到 supervisor；保留“重复 start 不产生第二进程”“restart PID 改变”“退出只停止自有 PID”断言。

- [ ] **Step 5: 运行测试**

Run: `cargo test --manifest-path src-tauri/Cargo.toml --test backend_supervisor --test backend_connection`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add src-tauri/src/backend_supervisor.rs src-tauri/src/backend.rs src-tauri/src/lib.rs src-tauri/tests/backend_supervisor.rs src-tauri/tests/backend_connection.rs
git commit -m "refactor: 分离后端进程管理与连接状态"
```

---

### Task 4: 将事件桥改为活动档案驱动的单一连接

**Repository:** `D:/Desktop/live2d-suzu/live2d-enhanced`

**Files:**
- Create: `src-tauri/src/backend_bridge.rs`
- Create: `src-tauri/tests/backend_bridge.rs`
- Modify: `src-tauri/src/lib.rs`
- Modify: `src-tauri/src/backend.rs`

- [ ] **Step 1: 写重连、队列和档案切换测试**

```rust
#[test]
fn profile_change_invalidates_connected_generation() {
    let state = BackendBridgeState::default();
    let connected = state.generation();
    state.profile_changed("remote-main");
    assert!(state.should_reconnect(connected));
}

#[test]
fn commands_are_rejected_while_queue_is_full() {
    let state = BackendBridgeState::with_capacity(1);
    state.enqueue(json!({"type":"command","name":"mode_status"})).unwrap();
    assert_eq!(
        state.enqueue(json!({"type":"command","name":"mode_status"})).unwrap_err(),
        "后端命令队列已满"
    );
}
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `cargo test --manifest-path src-tauri/Cargo.toml --test backend_bridge`

Expected: FAIL，提示 `backend_bridge` 尚不存在。

- [ ] **Step 3: 实现桥接状态机**

将 `BackendEventBridgeState` 和 `run_backend_event_bridge()` 从 `lib.rs` 移到 `backend_bridge.rs`。连接循环每次从 `ProfileStore` 读取活动档案，通过 `BackendTransport.websocket_request()` 主动连接，连接后发送带能力列表的 hello。重连退避固定为 `1s, 2s, 5s, 10s, 30s`，连接成功后重置。

入站事件映射：

```rust
match payload.get("type").and_then(Value::as_str) {
    Some("live2d_protocol") => app.emit("backend-live2d-command", payload),
    Some("activity_config_changed") => app.emit("backend-activity-config-changed", payload),
    _ => app.emit("backend-gui-event", payload),
}
```

桥状态返回 `profile_id/connected/message/last_error`，不含 endpoint token。

- [ ] **Step 4: 收窄 `lib.rs` commands**

Tauri commands 固定为 `list_backend_profiles`、`save_backend_profile`、`delete_backend_profile`、`set_active_backend_profile`、`get_backend_status`、`start_backend`、`stop_backend`、`restart_backend`、`backend_api_request`、`send_backend_gui_command`、`get_backend_bridge_status`。全部继续要求 `control-center` window label。

- [ ] **Step 5: 运行测试和 clippy**

Run: `cargo test --manifest-path src-tauri/Cargo.toml --test backend_bridge`

Run: `cargo clippy --manifest-path src-tauri/Cargo.toml --all-targets -- -D warnings`

Expected: PASS，`lib.rs` 不再包含 WebSocket 重连循环。

- [ ] **Step 6: 提交**

```bash
git add src-tauri/src/backend_bridge.rs src-tauri/src/backend.rs src-tauri/src/lib.rs src-tauri/tests/backend_bridge.rs
git commit -m "refactor: 以活动档案驱动后端事件桥"
```

---

### Task 5: 重做控制中心的连接档案界面

**Repository:** `D:/Desktop/live2d-suzu/live2d-enhanced`

**Files:**
- Create: `src/control-center/connection-profiles.ts`
- Create: `src/control-center/connection-profiles.test.ts`
- Create: `src/control-center/BackendConnectionPanel.tsx`
- Modify: `src/control-center/pages.tsx`
- Modify: `src/control-center/backend-status.ts`
- Modify: `src/control-center/backend-status.test.ts`
- Modify: `src/control-center/main.tsx`
- Modify: `src/control-center/styles.css`

- [ ] **Step 1: 写前端联合类型和权限测试**

```ts
it("never sends the stored token back to React", () => {
  const draft = profileToDraft({
    id: "remote-main",
    name: "远程主机",
    mode: "remote",
    http_base_url: "https://example.com/gui",
    websocket_url: "wss://example.com/gui",
    has_token: true,
  });
  expect(draft.token).toBe("");
  expect(draft.hasToken).toBe(true);
  expect(buildTokenUpdate(draft)).toEqual({ mode: "keep" });
});

it("only exposes process actions returned by Rust", () => {
  expect(availableBackendActions({ can_start: false, can_stop: false, can_restart: false }, false))
    .toEqual({ canStart: false, canStop: false, canRestart: false });
});
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `npx vitest run src/control-center/connection-profiles.test.ts src/control-center/backend-status.test.ts`

Expected: FAIL，提示新模块和新状态字段不存在。

- [ ] **Step 3: 实现连接档案纯状态层**

`connection-profiles.ts` 定义 `LocalManagedProfileDraft | LocalAttachedProfileDraft | RemoteProfileDraft`。`buildSaveRequest()` 只提交当前模式字段，切换模式时删除不适用字段；token 未编辑为 `{mode:"keep"}`，输入新值为 `{mode:"replace",value}`，显式点击清除为 `{mode:"clear"}`。

- [ ] **Step 4: 实现可扫描的连接面板**

布局固定为：左侧档案列表，右侧当前档案表单，顶部状态条，底部连接与进程操作。模式使用分段控件；只有 `local_managed` 显示目录、Python、启动模式和两个生命周期开关；`local_attached` 显示本地端点；`remote` 显示 HTTPS/WSS 和证书提示。token 输入框显示占位“已保存”但 value 为空。

停止、重启按钮只读取 Rust 的 `can_stop/can_restart`。外部后端 ready 时显示“已附着，进程由外部管理”。

- [ ] **Step 5: 运行前端测试和构建**

Run: `npm run test:control`

Run: `npm run build`

Expected: PASS；TypeScript 无联合类型遗漏；构建产物生成。

- [ ] **Step 6: 提交**

```bash
git add src/control-center/connection-profiles.ts src/control-center/connection-profiles.test.ts src/control-center/BackendConnectionPanel.tsx src/control-center/pages.tsx src/control-center/backend-status.ts src/control-center/backend-status.test.ts src/control-center/main.tsx src/control-center/styles.css
git commit -m "feat: 增加三模式后端连接档案界面"
```

---

### Task 6: 为 Python GUI WebSocket 增加能力协商

**Repository:** `D:/Desktop/live2d-suzu/live2d-llm`

**Files:**
- Create: `integrations/gui_protocol.py`
- Modify: `integrations/gui_ws.py`
- Modify: `core/application.py`
- Create: `tests/test_gui_protocol.py`
- Create: `tests/test_gui_ws_capabilities.py`
- Modify: `tests/test_gui_ws_compat.py`
- Modify: `tests/security_smoke.py`

- [ ] **Step 1: 写 hello、能力广播和禁用 query token 测试**

```python
def test_parse_hello_accepts_enhanced_capabilities():
    hello = parse_gui_hello({
        "type": "hello",
        "client": "live2d-enhanced",
        "protocol_version": 1,
        "capabilities": ["gui.v1", "live2d.protocol.v1"],
    })
    assert hello.client == "live2d-enhanced"
    assert "live2d.protocol.v1" in hello.capabilities


@pytest.mark.asyncio
async def test_capability_broadcast_only_targets_supported_clients():
    server = GuiWebSocketServer(access_token="secret")
    capable = FakeWs()
    legacy = FakeWs()
    server._clients.update({capable, legacy})
    server._client_capabilities[capable] = {"live2d.protocol.v1"}
    server._client_capabilities[legacy] = {"gui.v1"}
    await server.broadcast_capability("live2d.protocol.v1", {"type": "live2d_protocol"})
    assert len(capable.sent) == 1
    assert legacy.sent == []


def test_ws_query_token_is_not_accepted():
    server = GuiWebSocketServer(access_token="secret")
    assert server._extract_token(FakeWs(headers={}), "/gui?token=secret") == ""
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest tests/test_gui_protocol.py tests/test_gui_ws_capabilities.py tests/test_gui_ws_compat.py -q`

Expected: FAIL，提示协议模块、能力表和能力广播不存在。

- [ ] **Step 3: 实现协议构造器和客户端能力表**

`gui_protocol.py` 定义不可变 `GuiHello`，严格限制 `protocol_version == 1`、client 长度 64、能力数量 32、单项长度 64。定义：

```python
def build_live2d_envelope(command_id: str, message: dict[str, object]) -> dict[str, object]:
    return {
        "type": "live2d_protocol",
        "version": 1,
        "command_id": command_id,
        "message": message,
    }
```

`GuiWebSocketServer` 增加 `_client_capabilities`，连接关闭时同步删除。首个 hello 更新能力并调用现有 message handler；未 hello 客户端仍可使用 `gui.v1` 兼容事件，但不接收 Live2D 协议。新增异步 `broadcast_capability()` 和线程安全 `emit_capability()`；后者必须沿用现有 `emit()` 的事件循环调度方式，不能从业务线程直接操作 websocket。

- [ ] **Step 4: 移除 URL query token**

`_extract_token()` 只接受 `X-GUI-Token` 或 `Authorization: Bearer`。更新安全测试，确保 token 不出现在日志和 URL。增强版当前已使用 header，因此不影响现有连接。

- [ ] **Step 5: 运行测试**

Run: `python -m pytest tests/test_gui_protocol.py tests/test_gui_ws_capabilities.py tests/test_gui_ws_compat.py tests/security_smoke.py -q`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add integrations/gui_protocol.py integrations/gui_ws.py core/application.py tests/test_gui_protocol.py tests/test_gui_ws_capabilities.py tests/test_gui_ws_compat.py tests/security_smoke.py
git commit -m "feat: 增加 GUI WebSocket 能力协商"
```

---

### Task 7: 将 Python Live2D 输出改为可插拔传输总线

**Repository:** `D:/Desktop/live2d-suzu/live2d-llm`

**Files:**
- Create: `modules/live2d_transport.py`
- Modify: `modules/live2d.py`
- Modify: `core/application.py`
- Create: `tests/test_live2d_transport.py`
- Modify: `tests/test_live2d_motion_candidates.py`

- [ ] **Step 1: 写多传输、相同 command ID 和失败语义测试**

```python
@pytest.mark.asyncio
async def test_bus_delivers_same_command_id_to_every_transport():
    local = RecordingTransport()
    gui = RecordingTransport()
    bus = Live2DTransportBus([local, gui], id_factory=lambda: "cmd-1")
    result = await bus.send({"msg": 13200, "msgId": 2, "data": {"mtn": "idle"}})
    assert result.delivered == 2
    assert local.calls[0].command_id == "cmd-1"
    assert gui.calls[0].command_id == "cmd-1"


@pytest.mark.asyncio
async def test_bus_succeeds_when_one_transport_delivers():
    bus = Live2DTransportBus([FailingTransport(), RecordingTransport()])
    result = await bus.send({"msg": 13302, "msgId": 4, "data": {}})
    assert result.delivered == 1
    assert len(result.errors) == 1
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest tests/test_live2d_transport.py tests/test_live2d_motion_candidates.py -q`

Expected: FAIL，提示 `Live2DTransportBus` 不存在。

- [ ] **Step 3: 实现输出总线和两个 transport**

`Live2DTransportBus.send(message)` 生成一次 UUID，将不可变 `Live2DDelivery(command_id, message)` 并发交给 transport。`LegacyLocalWebSocketTransport` 封装当前连接池、端口扫描和发送，并把同一个 `command_id` 写入原始消息顶层；旧客户端会忽略额外字段。`GuiWebSocketTransport` 调用 `gui_ws_server.emit_capability("live2d.protocol.v1", build_live2d_envelope(...))`。

全部失败时抛 `Live2DDeliveryError` 并保留每个 transport 的明确错误；至少一个成功则返回统计并记录 warning，不吞掉失败。

- [ ] **Step 4: 让 `modules/live2d.py` 只构造消息**

保留所有公开函数签名。把 `_send_to_models()` 改为调用模块级 `configure_live2d_transport(bus)` 注入的总线；未配置时默认只创建 `LegacyLocalWebSocketTransport`，保证 Python 独立运行和 `live2d-only` 兼容。`core/application.py` 在 GUI WS 初始化后注入组合总线。

- [ ] **Step 5: 运行 Live2D 回归测试**

Run: `python -m pytest tests/test_live2d_transport.py tests/test_live2d_motion_candidates.py tests/test_character_editor_preview.py tests/test_emotion_controller_idle_random.py -q`

Expected: PASS，现有 motion/expression 消息格式不变。

- [ ] **Step 6: 提交**

```bash
git add modules/live2d_transport.py modules/live2d.py core/application.py tests/test_live2d_transport.py tests/test_live2d_motion_candidates.py
git commit -m "refactor: 统一 Live2D 指令输出传输"
```

---

### Task 8: 增强版接收远程 Live2D 指令并去重

**Repository:** `D:/Desktop/live2d-suzu/live2d-enhanced`

**Files:**
- Create: `src/backendLive2d.ts`
- Create: `src/backendLive2d.test.ts`
- Modify: `src/main.ts`
- Modify: `src/apiProtocol.ts`
- Modify: `src-tauri/src/backend_bridge.rs`
- Modify: `src-tauri/src/websocket.rs`

- [ ] **Step 1: 写信封解析和双通道去重测试**

```ts
it("unwraps a versioned backend command", () => {
  expect(parseBackendLive2dEnvelope({
    type: "live2d_protocol",
    version: 1,
    command_id: "cmd-1",
    message: { msg: 13200, msgId: 2, data: { mtn: "idle" } },
  })).toEqual({
    commandId: "cmd-1",
    message: { msg: 13200, msgId: 2, data: { mtn: "idle" } },
  });
});

it("accepts the first copy and rejects a duplicate command id", () => {
  const dedupe = createCommandDedupe({ maxEntries: 512, ttlMs: 60_000, now: () => 1000 });
  expect(dedupe.accept("cmd-1")).toBe(true);
  expect(dedupe.accept("cmd-1")).toBe(false);
});
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `npx vitest run src/backendLive2d.test.ts`

Expected: FAIL，提示模块不存在。

- [ ] **Step 3: 实现解析和有界 TTL 去重**

解析器只接受 `version=1`、非空 `command_id` 和对象 `message`。`createCommandDedupe()` 使用 `Map<string, number>`，每次插入先删除过期项，超过 512 条删除最早项。

本地旧 WS 接收到含顶层 `command_id` 的消息时也先调用同一 dedupe；Python 的 legacy transport 在原始消息顶层附加 `command_id`，旧客户端会忽略额外字段。

- [ ] **Step 4: 接入现有 `handleApiCommand`**

`src/main.ts` 同时监听 `live2d-api-command` 和 `backend-live2d-command`，两者归一化后调用同一个 `handleApiCommand`。无效信封、未知 msg 或重复 command ID 只记录一次明确 warning，不执行动作。

- [ ] **Step 5: 运行前端和 Rust 测试**

Run: `npm run test:control`

Run: `npm run test:frontend`

Run: `cargo test --manifest-path src-tauri/Cargo.toml websocket`

Run: `cargo test --manifest-path src-tauri/Cargo.toml --test backend_bridge`

Expected: PASS；同一气泡或动作从两个通道到达只执行一次。

- [ ] **Step 6: 提交**

```bash
git add src/backendLive2d.ts src/backendLive2d.test.ts src/main.ts src/apiProtocol.ts src-tauri/src/backend_bridge.rs src-tauri/src/websocket.rs
git commit -m "feat: 接收并去重后端 Live2D 指令"
```

---

### Task 9: 为远程音频增加短时媒体票据

**Repositories:** `live2d-llm`，然后 `live2d-enhanced`

**Python Files:**
- Create: `integrations/gui_media.py`
- Modify: `integrations/gui_http.py`
- Modify: `modules/live2d_transport.py`
- Modify: `core/application.py`
- Create: `tests/test_gui_media.py`

**Enhanced Files:**
- Modify: `src-tauri/src/backend_transport.rs`
- Modify: `src-tauri/src/backend_bridge.rs`
- Modify: `src/resourcePolicy.ts`
- Modify: `src/main.ts`
- Modify: `src-tauri/tests/backend_transport.rs`

- [ ] **Step 1: 写 Python 票据安全测试**

```python
def test_registry_only_serves_registered_file_once(tmp_path):
    audio = tmp_path / "reply.wav"
    audio.write_bytes(b"RIFFtest")
    registry = GuiMediaRegistry(ttl_seconds=60, max_bytes=1024)
    ticket = registry.register(audio, media_type="audio/wav")
    opened = registry.consume(ticket)
    assert opened.path == audio
    with pytest.raises(MediaTicketError, match="已使用"):
        registry.consume(ticket)


def test_registry_rejects_directory_and_oversized_file(tmp_path):
    registry = GuiMediaRegistry(ttl_seconds=60, max_bytes=4)
    with pytest.raises(MediaTicketError):
        registry.register(tmp_path, media_type="audio/wav")
    big = tmp_path / "big.wav"
    big.write_bytes(b"12345")
    with pytest.raises(MediaTicketError, match="过大"):
        registry.register(big, media_type="audio/wav")
```

- [ ] **Step 2: 运行 Python 测试并确认失败**

Run: `python -m pytest tests/test_gui_media.py -q`

Expected: FAIL，提示 `GuiMediaRegistry` 不存在。

- [ ] **Step 3: 实现媒体注册和认证下载**

票据使用 `secrets.token_urlsafe(32)`，默认 TTL 120 秒、最大 32 MiB，只接受 `audio/wav`、`audio/mpeg`、`audio/ogg`。`GET /gui/media/{ticket}` 经过现有 GUI token 中间件后流式返回；注册表只保存 resolve 后的文件路径和元数据，不接受请求方提供路径。

`GuiWebSocketTransport` 遇到 `msg=13500/13600` 且 sound 是本地文件时，注册票据并在信封增加：

```json
{"media":{"ticket":"...","content_type":"audio/wav"}}
```

legacy transport 继续使用原始本地路径。

- [ ] **Step 4: 运行 Python 安全回归并提交**

Run: `python -m pytest tests/test_gui_media.py tests/test_activity_ingest_storage.py tests/security_smoke.py -q`

Expected: PASS。

```bash
git add integrations/gui_media.py integrations/gui_http.py modules/live2d_transport.py core/application.py tests/test_gui_media.py tests/security_smoke.py
git commit -m "feat: 增加远程 Live2D 音频票据"
```

- [ ] **Step 5: 写 Rust 下载上限和缓存测试**

测试服务返回 33 MiB 时 `download_media()` 必须中止并删除临时文件；正常 WAV 保存到 `app_cache_dir/backend-media/<ticket>.wav`，响应 MIME 与扩展名不一致时拒绝。

- [ ] **Step 6: 实现 Rust 下载与本地 asset 转换**

`backend_bridge` 在发出 `backend-live2d-command` 前下载信封中的 media，使用同一档案 token，替换 `message.data.sound` 为本地缓存路径。缓存文件按 10 分钟 TTL 清理，并在增强版退出时删除；下载失败发 `backend-bridge-status` 错误，不把服务器路径交给前端。

- [ ] **Step 7: 运行增强版测试并提交**

Run: `cargo test --manifest-path src-tauri/Cargo.toml --test backend_transport`

Run: `npm run test:frontend`

Expected: PASS。

```bash
git add src-tauri/src/backend_transport.rs src-tauri/src/backend_bridge.rs src-tauri/tests/backend_transport.rs src/resourcePolicy.ts src/main.ts
git commit -m "feat: 下载并播放远程后端音频"
```

---

### Task 10: 将久坐配置和活动上报绑定到活动档案

**Repositories:** `live2d-llm`，然后 `live2d-enhanced`

**Python Files:**
- Modify: `integrations/gui_http.py`
- Modify: `core/application.py`
- Create: `tests/test_remote_activity_config.py`
- Modify: `tests/test_sedentary_runtime_settings.py`
- Modify: `tests/test_activity_ingest_storage.py`

**Enhanced Files:**
- Modify: `src-tauri/src/activity.rs`
- Modify: `src-tauri/src/backend_transport.rs`
- Create: `src-tauri/tests/activity_profile_transport.rs`

- [ ] **Step 1: 写 Python 活动配置 API 测试**

```python
@pytest.mark.asyncio
async def test_activity_config_returns_only_client_fields():
    app = FakeApp(runtime_settings={
        "sedentary_reminder_minutes": 45,
        "sedentary_break_minutes": 8,
        "sedentary_cooldown_minutes": 30,
        "gui_access_token": "must-not-leak",
    })
    response = await GuiHttpServer(app_ref=app)._handle_activity_config(FakeRequest())
    payload = json.loads(response.text)
    assert payload["data"]["sedentary_reminder_minutes"] == 45
    assert "gui_access_token" not in response.text
```

- [ ] **Step 2: 运行 Python 测试并确认失败**

Run: `python -m pytest tests/test_remote_activity_config.py -q`

Expected: FAIL，提示 `/activity-config` handler 不存在。

- [ ] **Step 3: 实现专用只读 API 和配置失效事件**

新增 `GET /gui/activity-config`，只返回：`revision`、`monitor_enabled`、三个久坐分钟数、`include_process_path`、`include_window_title`、`include_browser_context`。`apply_external_settings()` 检测这些字段变化后广播 `{"type":"activity_config_changed","revision":N}`。

保留 `_sync_live2d_activity_settings()` 仅供独立 `live2d-only` 兼容，增强版不再依赖该文件。测试明确该兼容写入不能成为增强版配置来源。

- [ ] **Step 4: 运行 Python 测试并提交**

Run: `python -m pytest tests/test_remote_activity_config.py tests/test_sedentary_runtime_settings.py tests/test_activity_ingest_storage.py -q`

Expected: PASS。

```bash
git add integrations/gui_http.py core/application.py tests/test_remote_activity_config.py tests/test_sedentary_runtime_settings.py tests/test_activity_ingest_storage.py
git commit -m "feat: 提供统一活动与久坐配置接口"
```

- [ ] **Step 5: 写 Rust 活动 transport 测试**

```rust
#[test]
fn activity_uses_active_profile_for_config_and_ingest() {
    let client = RecordingBackendClient::with_activity_config(45, 8, 30);
    let source = ActivityBackend::new(client.clone());
    assert_eq!(source.load_config().unwrap().window_minutes, 45);
    source.post(sample_event()).unwrap();
    assert_eq!(client.paths(), vec!["/activity-config", "/activity-ingest"]);
}
```

- [ ] **Step 6: 改造 `activity.rs`**

删除 `activity_settings()` 对 Python `runtime_settings.json` 的读取。活动线程从共享 `ActiveBackendClient` 获取端点和 token；每 60 秒或收到 `backend-activity-config-changed` 后刷新配置。离线时使用最后成功配置；没有缓存时使用编译期默认值。上报失败更新状态但不清零本地 sedentary tracker。

- [ ] **Step 7: 运行 Rust 测试并提交**

Run: `cargo test --manifest-path src-tauri/Cargo.toml --test activity_profile_transport activity`

Expected: PASS，现有键鼠活动和休息判定测试继续通过。

```bash
git add src-tauri/src/activity.rs src-tauri/src/backend_transport.rs src-tauri/tests/activity_profile_transport.rs
git commit -m "refactor: 通过活动档案同步久坐配置"
```

---

### Task 11: 收束启动、退出和旧 GUI 共存行为

**Repository:** `D:/Desktop/live2d-suzu/live2d-enhanced`

**Files:**
- Modify: `src-tauri/src/lib.rs`
- Modify: `src-tauri/src/backend_profile.rs`
- Modify: `src-tauri/src/backend_supervisor.rs`
- Modify: `src/control-center/backend-status.ts`
- Modify: `src/control-center/control-state.ts`
- Modify: `src/control-center/control-state.test.ts`
- Modify: `src-tauri/tests/backend_supervisor.rs`

- [ ] **Step 1: 写启动和退出决策测试**

```rust
#[test]
fn startup_only_spawns_managed_profile_with_opt_in() {
    assert_eq!(startup_action(&managed_profile(false)), StartupAction::ConnectOnly);
    assert_eq!(startup_action(&managed_profile(true)), StartupAction::StartOwned);
    assert_eq!(startup_action(&attached_profile()), StartupAction::ConnectOnly);
    assert_eq!(startup_action(&remote_profile()), StartupAction::ConnectOnly);
}

#[test]
fn exit_never_stops_external_process() {
    let supervisor = BackendSupervisor::default();
    supervisor.attach_for_test(external_ready_profile());
    supervisor.cleanup_on_exit().unwrap();
    assert_eq!(external_process_probe(), ProcessState::Running);
}
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `cargo test --manifest-path src-tauri/Cargo.toml --test backend_supervisor startup_only_spawns_managed_profile_with_opt_in`

Expected: FAIL，提示 `startup_action` 未定义。

- [ ] **Step 3: 实现明确启动决策**

Tauri setup 顺序固定为：迁移档案 -> 启动 bridge -> 若活动档案是 managed 且 `start_on_enhanced_launch=true` 则调用 supervisor -> 启动 activity monitor。连接到已运行后端时 supervisor 不声明所有权。退出时先停 bridge/activity，再仅按 supervisor 内存中的 Child 和 `stop_owned_on_exit` 清理。

- [ ] **Step 4: 保持旧 GUI 默认可用**

旧配置迁移后的 `launch_mode` 固定为 `legacy_gui`。控制中心首次新建 managed 档案也默认 `legacy_gui`。只有用户显式选择“无窗口后端”才设置 headless。删除任何“连接增强版后自动隐藏或关闭 Qt GUI”的调用。

- [ ] **Step 5: 运行测试**

Run: `cargo test --manifest-path src-tauri/Cargo.toml --test backend_supervisor --test backend_profiles`

Run: `npm run test:control`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add src-tauri/src/lib.rs src-tauri/src/backend_profile.rs src-tauri/src/backend_supervisor.rs src-tauri/tests/backend_supervisor.rs src/control-center/backend-status.ts src/control-center/control-state.ts src/control-center/control-state.test.ts
git commit -m "fix: 保持旧 GUI 与增强版安全共存"
```

---

### Task 12: 增加双端契约和端到端验收

**Repositories:** 两个仓库

**Python Files:**
- Create: `tests/test_enhanced_backend_contract.py`
- Create: `tests/helpers/enhanced_backend_contract.py`
- Modify: `tests/test_gui_ws_capabilities.py`
- Modify: `tests/test_remote_activity_config.py`

**Enhanced Files:**
- Create: `scripts/fake-python-backend.py`
- Create: `scripts/test-backend-profiles.mjs`
- Modify: `package.json`
- Modify: `scripts/test-install-coexistence.mjs`

- [ ] **Step 1: 写 Python 协议契约测试**

测试从 `tests.helpers.enhanced_backend_contract` 导入 `run_contract_scenario`，固定以下序列：认证 WS 连接 -> enhanced hello -> 收到 status/config/character/costumes -> 发送 GUI command -> 后端发送 `live2d_protocol` -> HTTP 获取 activity-config -> POST activity-ingest。断言所有请求使用 header token，响应和日志不含 token。

- [ ] **Step 2: 运行 Python 契约测试并确认失败**

Run: `python -m pytest tests/test_enhanced_backend_contract.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'tests.helpers.enhanced_backend_contract'`。

- [ ] **Step 3: 实现双端契约夹具和增强版假后端验收脚本**

`tests/helpers/enhanced_backend_contract.py` 实现 `run_contract_scenario(app_factory, token)`：在随机 loopback 端口启动真实 `GuiHttpServer/GuiWebSocketServer`，执行 Step 1 的完整序列，并在 `finally` 中关闭服务。`fake-python-backend.py` 只绑定 `127.0.0.1` 随机端口，提供 health、activity-config、activity-ingest、media 和 GUI WS；记录收到的 hello、命令和活动事件。`test-backend-profiles.mjs` 启动假后端并调用 Rust integration test，覆盖 managed/attached/remote URL 校验与 bridge Live2D 事件。

在 `package.json` 增加：

```json
"test:backend-contract": "node scripts/test-backend-profiles.mjs"
```

- [ ] **Step 4: 运行两个仓库完整测试**

Python Run:

```bash
python -m pytest tests/test_enhanced_backend_contract.py tests/test_gui_protocol.py tests/test_gui_ws_capabilities.py tests/test_gui_media.py tests/test_live2d_transport.py tests/test_remote_activity_config.py tests/test_activity_ingest_storage.py tests/test_sedentary_runtime_settings.py tests/security_smoke.py -q
```

Enhanced Run:

```bash
npm run check
npm run test:backend-contract
```

Expected: 全部 PASS；clippy 无 warning；前端构建成功。

- [ ] **Step 5: 进行人工本地验收**

按固定顺序验证：

1. 先单独启动旧 Python GUI，再启动增强版并选 local attached；状态为 ready，停止和重启按钮禁用，关闭增强版后 Python PID 不变。
2. 关闭 Python，选 local managed 且 `launch_mode=legacy_gui`；点击启动后旧 GUI 可见，增强版显示 owned；点击停止只结束该 PID。
3. local managed 取消 `stop_owned_on_exit`；关闭增强版后 Python 继续运行。
4. 使用本机 TLS 反向代理建立 remote 档案；增强版主动建立 WSS，聊天、动作、表情、气泡和 TTS 音频可达，服务器不连接桌面 10086。
5. 修改久坐分钟数后收到配置失效事件，增强版刷新配置；活动事件进入 Python `source=live2d-tauri`。
6. 同时安装并启动 `live2d-only` 与增强版，安装目录和 AppData 标识互不覆盖。

- [ ] **Step 6: 分别提交契约测试**

Python:

```bash
git add tests/helpers/enhanced_backend_contract.py tests/test_enhanced_backend_contract.py tests/test_gui_ws_capabilities.py tests/test_remote_activity_config.py
git commit -m "test: 增加增强版前后端协议契约验收"
```

Enhanced:

```bash
git add scripts/fake-python-backend.py scripts/test-backend-profiles.mjs scripts/test-install-coexistence.mjs package.json
git commit -m "test: 增加连接档案端到端验收"
```

---

### Task 13: 更新文档、版本并打包增强版

**Repositories:** 两个仓库

**Files:**
- Modify: `D:/Desktop/live2d-suzu/live2d-llm/README.md`
- Modify: `D:/Desktop/live2d-suzu/live2d-enhanced/README.md`
- Create: `D:/Desktop/live2d-suzu/live2d-enhanced/scripts/test-release-metadata.mjs`
- Modify: `D:/Desktop/live2d-suzu/live2d-enhanced/package.json`
- Modify: `D:/Desktop/live2d-suzu/live2d-enhanced/package-lock.json`
- Modify: `D:/Desktop/live2d-suzu/live2d-enhanced/src-tauri/Cargo.toml`
- Modify: `D:/Desktop/live2d-suzu/live2d-enhanced/src-tauri/Cargo.lock`
- Modify: `D:/Desktop/live2d-suzu/live2d-enhanced/src-tauri/tauri.conf.json`

- [ ] **Step 1: 写版本和安装标识一致性失败测试**

`scripts/test-release-metadata.mjs` 读取 `package.json`、`src-tauri/Cargo.toml` 和 `src-tauri/tauri.conf.json`，断言三处版本均为 `0.3.0-alpha.1`，并断言 identifier 为 `com.live2d-suzu.enhanced` 且不等于 `com.live2d-only.app`。

- [ ] **Step 2: 运行测试并确认失败**

Run: `node scripts/test-release-metadata.mjs`

Expected: FAIL，明确报告当前 `0.2.0-alpha.2` 与目标 `0.3.0-alpha.1` 不一致。

- [ ] **Step 3: 更新用户文档**

Python README 说明：后端可独立运行、HTTP/WSS 暴露要求 TLS 反向代理、GUI token 配置位置、增强版主动连接、Rust 活动唯一来源。增强版 README 说明三种档案、进程所有权、token 不回显、旧 GUI 共存、远程音频限制和排障命令。不要新增第二份面向用户的连接文档。

- [ ] **Step 4: 将增强版版本统一到 `0.3.0-alpha.1`**

同步修改 npm、Cargo 和 Tauri 三处版本。保持：

```json
{
  "productName": "Live2D Suzu Enhanced",
  "identifier": "com.live2d-suzu.enhanced"
}
```

不得改成 `live2d-only` 的标识。

- [ ] **Step 5: 运行版本测试和发布前验证**

Run: `node scripts/test-release-metadata.mjs`

Run: `npm run check`

Run: `npm run test:backend-contract`

Run: `npm run tauri build`

Expected: 全部 PASS，并生成：

`src-tauri/target/release/bundle/nsis/Live2D Suzu Enhanced_0.3.0-alpha.1_x64-setup.exe`

- [ ] **Step 6: 验证安装并存和包校验值**

Run: `npm run test:coexistence`

Run: `Get-FileHash 'src-tauri/target/release/bundle/nsis/Live2D Suzu Enhanced_0.3.0-alpha.1_x64-setup.exe' -Algorithm SHA256`

Expected: coexistence PASS；记录非空 SHA-256；安装包标识不覆盖纯 Live2D。

- [ ] **Step 7: 提交 Python README**

```bash
git add README.md
git commit -m "docs: 说明增强版前后端分离连接方式"
```

- [ ] **Step 8: 提交增强版文档、版本和发布测试**

```bash
git add README.md scripts/test-release-metadata.mjs package.json package-lock.json src-tauri/Cargo.toml src-tauri/Cargo.lock src-tauri/tauri.conf.json
git commit -m "release: 更新增强版至 0.3.0-alpha.1"
```

---

## 最终验收矩阵

| 场景 | 预期结果 |
|---|---|
| Python 单独启动 | 旧 GUI、插件、记忆和本地 Live2D 兼容链路可用 |
| 增强版单独启动 | 模型、托盘、滚轮缩放和独立久坐提醒可用 |
| 本地附着 | 可聊天和收事件，不可停止外部 Python |
| 本地托管 legacy GUI | 可启停自有 PID，旧 GUI 保持可见 |
| 本地托管 headless | 用户显式选择后无 Qt 窗口运行 |
| 增强版退出 | 默认不停止 Python；仅 opt-in 且自有 PID 时停止 |
| 远程 HTTPS/WSS | GUI、Live2D、活动上报和音频均走出站连接 |
| 远程明文地址 | 保存档案时明确拒绝 |
| token 已保存 | React 只看到 `has_token=true`，拿不到明文 |
| 双通道重复指令 | 同一 `command_id` 只执行一次 |
| 久坐设置变化 | 本地和远程均通过认证 API 热刷新 |
| 双安装 | `live2d-only` 与 enhanced 安装和 AppData 不冲突 |

## 完成定义

- 两个仓库的目标测试、完整 `npm run check` 和 Python 契约测试全部通过。
- 人工验收六个场景全部记录结果。
- 旧 GUI 没有被删除、隐藏或默认切成 headless。
- Python 代码不再要求远程服务器主动连接桌面 Live2D 端口。
- `activity.rs` 不再读取 Python 后端目录中的久坐配置。
- 档案 JSON、日志、Tauri 事件和 React state 中均无 token 明文。
- 增强版 `0.3.0-alpha.1` NSIS 安装包生成并通过并存测试。
