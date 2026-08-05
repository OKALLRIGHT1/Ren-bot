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
- `code_executor` 委托本机 Codex CLI / Claude Code（无内置 Python 沙箱）；输出做敏感信息脱敏；默认关闭且需 ActionGate 确认。
- GUI HTTP / WS 和 NapCat 接入有 token / 来源校验。
- GUI 密钥脱敏可识别 setting `type=secret/password`（`modules.security_redaction.is_secret_setting`）。
- QQ 文件、网页读取、搜索、邮件、远程控制等插件都应显式配置 access control。
- search 插件 config 与实现类型已统一为 `delegate`。

## 已落地（2026-08 P0 权限契约）

- **ActorKind**：`local` / `qq_owner` / `qq_other` + channel `local_ui` / `private` / `group`（`services/security/actor.py`）。
- **HIGH 仅** local，或 **qq_owner 私聊**；**群聊 owner 也不允许 HIGH**。
- **媒体策略**：`integrations/chat_gateway/media_policy.py`；远程（含 owner）禁止 `file://`/裸 path/私网 IP；身份信任 ≠ 内容信任。
- **ActionGate** 已接入 `PluginManager._run_with_timeout`；`open_app` 信任列表 → `system.spawn_process_trusted` 免确认+审计；列表外 HIGH；`backup_manager` / `code_executor` / 邮件写操作可解析 action。
- **code_executor**：默认关；需 Gate 确认；**改为调用本机 Codex/Claude CLI**（`modules/code_agent`），不再内嵌 AST 沙箱。
- **确认交互**：ActionGate `requires_confirmation` → 本地 Qt 弹窗当场确认，或远程 `confirmation_required` + 聊天「确认/取消」；`AgentRuntime` / gate re-run 注入 `action_confirmed`。
- **code_agent**：`system.code_agent` 高风险门控；与 `code_executor` 共用外部 CLI 栈。
- **memory_items**：默认仅 `todo`；语义记忆走 `memory_records`。

## 仍未闭环 / 后续

### P1 — 外部 CLI 本身的权限面

- `code_executor` / `code_agent` 依赖本机已安装的 Codex/Claude 及用户账号权限；需持续约束 cwd、确认与 owner 私聊边界。

### P1 — 远程高权限插件面仍偏大

- 样例：`user_files`、`qq_file_browser`、`code_agent`、`skill_runtime` 等 owner 可达；需持续收紧与确认策略对齐 ActionGate。
- 空 `access_control`：启动 WARNING 列表；后续可 `access_control_strict`。

### P1/P2 — `open_app` 列表外路径

- 信任列表外当前不直接启动（需确认策略）；可产品化确认流。

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
