# 工作区与文件助手

`plugins/workspace_ops/` · 类型 `delegate` · 触发词 `workspace_ops`

在项目工作区内执行代码读取、搜索与安全写入。参数格式：action ||| arg1 ||| arg2 ...；可用 action: help, pwd, list_files, read_file, read_range, search_code, write_file, append_file, replace_text

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `workspace_ops` |
| LLM 指令 | `workspace_ops` |
| 插件类型 | `delegate` |
| 别名 | `代码助手`、`文件助手`、`工作区`、`workspace`、`codex` |

示例：

```
[CMD: workspace_ops | read_file ||| services/chat_service.py]
```

## 权限

`config.json` 未声明 `access_control`，由 `PluginManager._normalize_access_control` 套用默认：本地 ✅ / QQ 远程 ✅ / QQ 主人 ✅ / QQ 他人 ❌ / 群聊未 @ ❌。

## 动作

取自源码里的 action/alias 映射表：

`append` `append_file` `apply_change` `created` `discard_change` `file` `git_diff` `git_status` `git_summary` `help` `list_changes` `list_files` `pwd` `read_file` `read_range` `replace_text` `rollback_session` `run_cmd` `search_code` `session_changes` `session_context` `session_events` `task_list` `task_status`

## 依赖与数据

- 第三方包：`fnmatch`、`shlex`
- 宿主模块：`config`、`modules`、`services`
- 涉及的数据路径字面量：`.json`

## 源码

约 954 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
## 注意

**这个插件有 `run_cmd` 和 `write_file`，但 `config.json` 没有声明 `access_control`。**
它落到宿主默认值（QQ 远程 ✅ / QQ 主人 ✅），也就是主人可以从 QQ 私聊触发命令执行。
如果这不是你要的，在 `config.json` 里显式收紧，例如只留本地：

```json
"access_control": { "allow_local": true, "allow_remote_qq": false,
                    "allow_qq_owner": false, "allow_qq_others": false }
```

`modules/plugin_security_audit.py` 目前也没给这个 trigger 登记风险标记，
所以它不会出现在启动时的高风险清单里 —— 审计矩阵看不到它。

## 与其它文件类插件的关系

`user_files`、`qq_file_browser`、`workspace_ops` 各自实现了一套路径解析与越界检查
（用户目录 roots / `/bot` VFS 挂载 / 项目工作区）。三套独立实现意味着三处可能写错沙箱边界。
若要重构，抽共享的路径解析 + 权限判定，而不是合并插件 —— 三者 `access_control` 画像不同，
合并会把不同权限压成一份。
