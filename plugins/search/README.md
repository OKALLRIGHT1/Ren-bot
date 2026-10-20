<!-- MANUAL README -->

# 联网搜索

问她网上的事时走这个插件。优先 Tavily；没有再试主程序里的联网模型、Grok 或 Exa；最后可以回退 DuckDuckGo。

## 怎么用

直接说：

- `搜一下今天有什么新闻`
- `查一下某某是谁`
- `给我提供链接`

也可以让她 `search 今天有什么新闻`。

## 权限

- 本地可用
- QQ 主人和其他人默认都可以问；群聊需要 @

## 必须填的 API（按优先级）

打开 **插件管理 → 联网搜索**：

| 项 | 填什么 |
| --- | --- |
| Tavily API Key | 第一优先。申请：https://tavily.com/ 。留空则跳过 Tavily |
| Grok API Key | 兼容搜索源。也可设环境变量 `XAI_API_KEY` 或 `GROK_API_KEY` |
| Grok Base URL | OpenAI 兼容地址，例如 `http://127.0.0.1:8317/v1` |
| Exa API Key | 仅在兼容源为 `exa` 时用。也可设 `EXA_API_KEY` |

密钥存在本机 `data/plugin_secrets.db`，不要写进 `config.json`。

更省事的做法：在 **设置 → 模型与路由** 里给模型勾选「联网搜索」用途，并排好「联网搜索」任务顺序。插件里的「联网搜索模型」队列留空时会跟这条路由走。

## 其它可配置项

| 项 | 说明 |
| --- | --- |
| 兼容搜索源 | 模型和任务路由都没配时才用，默认 `grok` |
| 失败回退 DDG | 默认开。前面都失败时用 DuckDuckGo |
| 默认返回条数 | Exa / DDG 条数 |

不填任何 Key 时，只要开了 DDG 回退，普通搜索往往还能用，质量会差一些。
