# Security Remediation Plan

本文档只保留当前仍有价值的安全边界、未闭环风险和验证方式。
一次性 code review 全文已归档删除；未修复项收口到本节。

## 当前安全原则

1. 远程入口默认最小权限，QQ 非主人默认不能触发高风险工具。
2. 本地文件、代码执行、MCP、外部代理、邮件发送等能力必须经过插件权限和上下文门控。
3. 工具失败要返回明确错误，不伪装成功。
4. 敏感信息不能写入日志、聊天回复、测试快照或文档示例。
5. 依赖安装、代码执行、文件写入、系统重启等动作必须可追踪，并保留确认边界。

## 当前已落地的边界

- `PluginManager` 支持 local / QQ owner / QQ others / group mention 权限矩阵。
- `mcp_tools` 默认限制远程 QQ 调用，可通过配置白名单放行。
- `user_files` 只允许访问明确白名单根目录。
- `workspace_ops` 写操作需要确认 token。
- `code_executor` 使用受限执行环境，并对输出做敏感信息脱敏（**仍非真正隔离沙箱，见未闭环**）。
- GUI HTTP / WS 和 NapCat 接入有 token / 来源校验。
- GUI 密钥脱敏可识别 setting `type=secret/password`（`modules.security_redaction.is_secret_setting`）。
- QQ 文件、网页读取、搜索、邮件、远程控制等插件都应显式配置 access control。
- search 插件 config 与实现类型已统一为 `delegate`。

## 未闭环风险（优先处理）

来源：2026-07-30 代码审查；下列项在审查时仍未闭环，改动安全边界时优先处理。

### P1 — 媒体加载 SSRF / 本地文件边界

- 位置：`integrations/chat_gateway/media_utils.py`
- 现状：`http(s)://` 直接 `urlopen`；支持 `file://` 与任意本地 path；缺少内网/环回/元数据 IP 过滤、跳转限制、大小与 MIME 约束。
- 影响：远程 QQ 图片元数据可控时，可能打到内网或本机路径，再进入视觉模型 / 外部接口。
- 目标：统一 URL/路径安全策略；远程来源禁止 `file://` 与任意本地 path；限制体积、跳转与最终地址。

### P1 — `code_executor` 伪沙箱

- 现状：正则/AST 黑名单 + 本机 `sys.executable` 子进程，不是隔离运行时。
- 目标：隔离执行（低权限用户/容器/无网络/临时 FS）；黑名单仅作辅助。默认禁用并要求显式本地确认。

### P1 — 高风险写操作未统一进入 `ActionGate`

- 现状：`services/action_gate.py` 存在，但未成为插件执行主入口。
- 旁路样例：`backup_manager`（空 `access_control`、恢复可覆盖关键文件）、`open_app`（`Popen`、默认映射含高风险项、空 `access_control`）。
- 目标：文件写、配置编辑、代码执行、进程启动、备份恢复、邮件发送统一经 ActionGate 或等价确认网关。

### P1 — 远程高权限插件面过大 / 空 access_control 不可观测

- 样例：`user_files`、`qq_file_browser`、`qq_screenshot`、`code_agent`、邮件、`skill_runtime`、`app_control` 等经 QQ 主人可达；`search` 允许 others；部分插件 `access_control` 为空依赖隐式默认。
- 目标：启动输出权限矩阵；空配置显式警告或失败；成本型/写操作默认收紧。

### P1/P2 — `open_app` 能力语义与风险不闭环

- 现状：子串匹配后直接启动进程；缺少明确的 command-only / NL 能力声明；失败回退映射可能含高风险命令。
- 目标：明确仅命令触发或完整 NL 能力；移除高风险默认项；接入确认闸门。

### 仍需仓库拥有者手动处理

- `.env` 若曾进入 Git 历史，需确认后轮换密钥；工作区仅保留 `.env.example`。

## 工程边界提醒（非直接漏洞，但影响安全改动）

- `services/chat_service.py`、`modules/gui/dialogs/settings.py`、`integrations/gui_http.py`、`core/application.py` 体量过大，安全相关改动应小步、有测试。
- 外部 agent / Codex / Claude / MCP 不能绕过路径白名单和确认门。
- 插件 `config.json type` 与 `plugin.py` 类属性并存；启动期应保持一致，避免类型回归。

## 推荐验证

改动安全边界后至少运行：

```powershell
python -m pytest tests\test_plugin_access_control.py tests\security_smoke.py -q
python -m py_compile modules\plugin_manager.py integrations\gui_http.py integrations\chat_gateway\server.py
```

涉及文件、MCP、代码代理时补充：

```powershell
python -m pytest tests\test_user_files_plugin.py tests\test_agent_runtime_plugin.py tests\test_code_agent_plugin.py -q
```

涉及 QQ 网关 / 媒体时补充：

```powershell
python -m pytest tests\test_napcat_gateway_auth.py tests\test_napcat_gateway_events.py -q
```

涉及 GUI 脱敏时，确认 cookie / `type=secret` 字段不会明文返回。
