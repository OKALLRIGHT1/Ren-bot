# Info Gateway

`plugins/info_gateway/` · 类型 `react` · 触发词 `info_gateway`

Expose shared information sources such as ALAPI weather and daily news.

> ⚠️ **安全审计风险标记**：`paid_api`  
> 见 `modules/plugin_security_audit.py`。 开放给 QQ 远程时会计入高风险清单。

## 调用方式

| 项 | 值 |
| --- | --- |
| 触发词 | `info_gateway` |
| LLM 指令 | `info_gateway` |
| 插件类型 | `react` |
| 别名 | `/api`、`/info`、`/信息源` |
| 超时 | 30 秒 |

示例：

```
[CMD: info_gateway | /api weather_7d city=上海]
```

## 权限

| 渠道 | 允许 |
| --- | --- |
| 本地桌面 | ✅ |
| QQ 远程 | ✅ |
| QQ 主人 | ✅ |
| QQ 他人 | ❌ |
| 群聊未 @ | ❌ |

## 依赖与数据

- 宿主模块：`modules`、`services`
- 涉及的数据路径字面量：`config.json`

## 源码

约 378 行 Python。

---

本节以上由 `dev_scripts/gen_plugin_readme.py` 从 `config.json` 与源码 AST 生成，改完插件请重新运行以免过期。

<!-- KEEP BELOW: 手写补充，重新生成时保留 -->
## ALAPI token 的读取顺序

`_read_alapi_token()` 按此顺序回退：

1. 已注入的 `self.settings` 里的 `api_token`
2. `ctx["settings"]` 里的 `api_token`
3. 共享 secret store（`data/plugin_secrets.db`，见 `_read_shared_alapi_token`）
4. **直接读 `plugins/Isuzu_news/config.json`**

第 4 步是跨插件硬路径耦合，是迁移到共享 store 的历史残留。
删它之前先确认你的 token 已经进了 secret store，否则天气/日报会直接失效。
