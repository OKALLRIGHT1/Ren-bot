# Live2D-LLM 插件指南

> 更新时间：2026-07-03  
> 这份文档只保留当前仍有效的插件结构、权限规则和使用方式。

## 0. 维护说明

- 当前插件以 `plugins/<plugin_name>/config.json + plugin.py` 为标准结构。
- 插件配置优先在 GUI 的 `设置 -> 插件管理` 中编辑；敏感字段会按 `secret/api_key/token/password` 等字段名遮挡。
- 新增插件时，先写清楚 `name/trigger/llm_command/aliases/description/type/access_control/settings`；需要自然语言触发时，优先补 `get_capabilities()`。
- QQ 侧插件必须认真设置权限，默认不要允许非主人触发截图、文件、系统控制、代码执行等能力。

## 1. 当前结构

- 插件入口：`modules/plugin_manager.py`
- 插件目录：`plugins/<plugin_name>/config.json + plugin.py`
- 插件管理 GUI：`设置 -> 插件管理`
- 当前支持四类插件：
  - `react`：由模型通过 `[CMD: ...]` 工具调用触发
  - `direct`：用户文本命中关键词后立即触发
  - `observe`：做截图/观察类补充，不直接替代主回复
  - `delegate`：由主脑把复杂任务委托给副脑执行；主脑只表达需求，副脑负责具体工具调用

## 2. 常用配置字段

- `name`：插件中文名
- `trigger`：内部主触发词
- `llm_command`：给模型使用的工具名
- `aliases`：显式命令、兼容别名和工具检索关键词；不要只靠它承载自然语言意图
- `type`：`react` / `direct` / `observe` / `delegate`
- `description`：功能说明
- `example_arg`：示例参数
- `timeout_sec`：执行超时秒数
- `access_control`：来源权限控制
- `settings`：插件自定义配置，GUI 可直接编辑

`type` 的运行时主来源是 `config.json`。如果 `plugin.py` 里的 `Plugin.type` 也存在，必须和 `config.json` 保持一致；不一致时系统会按 `config.json` 执行，并在启动日志里记录 `plugin_type_mismatch`。

## 2.1 能力声明

需要自然语言触发、避免误触，或希望被统一能力层稳定识别的插件，应在 `plugin.py` 中实现 `get_capabilities()`：

```python
from services.capability_manager import ToolCapability, ToolCapabilityMatch

def get_capabilities(self):
    return [
        ToolCapability(
            id="info.weather_now",
            plugin="info_gateway",
            trigger_mode="natural",  # natural / command_only
            match=self._match_weather_now,
            description="查询实时天气",
            examples=["上海今天的天气怎么样"],
        )
    ]

def _match_weather_now(self, text, ctx):
    if "天气" not in text or "接口" in text:
        return None
    return ToolCapabilityMatch(
        capability_id="info.weather_now",
        plugin="info_gateway",
        score=0.9,
        args=None,  # 不能稳定提取时允许为空，插件 run() 继续解析原文
        raw_text=text,
        reason="current_weather_query",
    )
```

- `trigger_mode="natural"`：自然语言 matcher 命中后可进入工具路由。
- `trigger_mode="command_only"`：只匹配 `/命令` 或明确指令，不响应普通闲聊。
- `score >= 0.7` 会被视为高置信命中；低置信候选只记录，不应直接抢路由。
- `args` 可为空。能稳定提取就填，不能稳定提取就让插件继续解析 `raw_text`。
- `aliases` 仍可保留 `/日报`、`/画图`、`/api` 等命令，但不要再把“天气”“画图”这类宽泛词当作唯一自然语言触发依据。
- 兼容规则：旧 `direct` 插件如果只有 `should_handle_direct()`，会被能力层自动包装成 `*.direct`；带 `/` 的别名会被自动包装成 `*.command`。这是迁移兼容层，新插件不要依赖它来表达复杂自然语言意图。

## 2.2 delegate 类型说明

- `delegate` 表示该插件默认不由主人格直接执行，而是先进入一次“副脑”推理回合，再由副脑决定是否调用。
- 适合：`workspace_ops`、`mcp_tools`、`search_web` 这类复杂、多步、强结构化能力。
- 其中 `delegate` 适合多步任务、联网搜索、MCP 工具、工作区操作等复杂能力。
- 当前接入方式是最小实现：
- 主脑负责对话与自然语言表达
- 副脑负责复杂任务的 `[CMD: ...]` 调用
- 插件执行仍复用现有 `plugin_manager.execute_commands(...)`
- 可通过 `delegate_status` 直接查看最近副脑任务，例如：`副脑任务`、`副脑任务 任务ID`

## 3. 当前插件清单

插件会随本地目录变化而变化，以下是当前主线能力分类。实际启用状态以 GUI 插件管理器为准。

### 3.1 效率与信息类

- `task_manager`：任务中枢，处理待办、提醒、任务跟进
- `delegate_status`：查看最近副脑任务与单个任务详情
- `timer`：番茄钟 / 倒计时
- `search`：联网搜索
- `local_knowledge`：本地知识库 / 第二大脑

- `dice_plugin`：骰子投掷（.r / .ra / .rc / .sc）
- `diary_export`：日记导出与整理
- `life_manager`：生活数据 / 记录管理
- `llm_monitor`：模型监控
- `mode_preset`：模式预设

### 3.2 系统与工作区类

- `open_app`：快速启动，本地直接打开应用
- `system_monitor`：系统运维，查状态和部分系统控制
- `workspace_ops`：工作区与文件助手
- `code_executor`：代码执行器
- `backup_manager`：备份恢复
- `music_player`：音乐播放器

### 3.3 感知与远程类

- `vision`：视觉感知，负责截图/拍照分析
- `qq_screenshot`：QQ 远程截图，负责把当前屏幕直接发回 QQ
- `qq_draw`：QQ 固定命令生图，直连兼容 OpenAI 风格的图片接口并回发 QQ
- `qq_reminder`：QQ 私聊定时提醒，支持工作日/每天定时提醒
- `qq_help`：QQ 功能总览，快速查看常用能力和命令
- `qq_role_switch`：QQ 角色切换，可在 QQ 中查看角色列表并快速切换角色
- `mcp_tools`：MCP 工具桥
- 表情包相关插件：从数据库版表情包库中按语义、标签、情绪选择图片，具体名称以本地插件目录为准

`qq_role_switch` 支持：`/角色列表`、`/角色 当前`、`/角色 角色名`，切换时会同步角色 TTS 和默认服装。

`qq_help` 支持按类别查询，例如：`/帮助 生图`、`/帮助 提醒`、`/帮助 文件`。

## 4. 插件权限控制

- 现在每个插件都可以在 `config.json` 里声明 `access_control`
- 支持字段：
  - `allow_local`：是否允许本地入口触发（GUI、语音、传感器、本机工作流）
  - `allow_remote_qq`：是否允许 QQ 网关触发
  - `allow_qq_owner`：QQ 来源下是否允许主人触发
  - `allow_qq_others`：QQ 来源下是否允许其他联系人或群成员触发
  - `allow_group_without_at`：是否允许群聊免 @ 触发（仅对该插件生效）
- 默认策略：
  - `allow_local = true`
  - `allow_remote_qq = false`
  - `allow_qq_owner = false`
  - `allow_qq_others = false`
  - `allow_group_without_at = false`
- 需要 QQ 主人触发的插件必须在自身 `config.json` 中显式打开 `allow_remote_qq=true` 和 `allow_qq_owner=true`。
- 判定规则：
  - 本地来源只看 `allow_local`
  - QQ 来源先看 `allow_remote_qq`，再根据是否主人判断 `allow_qq_owner` / `allow_qq_others`
- GUI 已支持直接编辑这些开关：
  - 入口：`设置 -> 插件管理 -> 编辑`
  - 位置：插件编辑弹窗里的 `触发权限`
- 启动加载插件后，系统会输出一行 `[PluginSecurity]` 摘要，集中列出：
  - QQ 主人可远程触发的高风险插件
  - 其他 QQ 联系人可触发的插件
  - 群聊免 @ 触发的插件

### 示例

```json
"access_control": {
  "allow_local": true,
  "allow_remote_qq": true,
  "allow_qq_owner": true,
  "allow_qq_others": false
}
```

## 5. QQ 远程截图插件

- 插件路径：`plugins/qq_screenshot/config.json`
- 作用：你在 QQ 里说“截图发我”“给我看看屏幕”之类的话时，助手会把当前电脑屏幕直接回发到 QQ
- 默认权限：
  - 本地：禁用
  - QQ 主人：允许
  - QQ 其他人：禁用
- 这样做的目的：
  - 避免本地普通截图请求被它误拦截
  - 避免其他 QQ 联系人直接看到你的电脑屏幕

### 常用说法

- `截图发我`
- `给我看看屏幕`
- `把屏幕发我`
- `截个图发我`
- `主屏截图发我`
- `全部屏幕发我`
- `第2屏截图发我`
- `截图发我，不带标题`

### 可在 GUI 修改的默认项

- `默认截图范围`：`primary` / `all` / `monitor`
- `默认屏幕序号`
- `附带当前窗口标题`

## 6. QQ 生图插件

- 插件路径：`plugins/qq_draw/config.json`
- 作用：只在 QQ 里响应固定斜杠命令 `/画图` 或 `/画画`，把后面的整段文本当成提示词，支持 `images/generations` 和 `chat/completions` 两种生图接口并把图片回发到 QQ
- 设计目的：
  - 必须固定命令触发，避免普通聊天误触发
  - 默认仅允许 QQ 主人使用
  - 默认支持群聊免 @，因为命令本身已经足够明确

### 触发方式

- `/画图 绘制一个丰川祥子在雨后的街道`
- `/画画 画一个赛博朋克风格的便利店夜景`

### 默认配置要点

- `base_url`：接口根地址，默认 `https://api.sub2api.froge-ai.com`
- `api_mode`：接口模式，`images` 表示标准图片接口，`chat` 表示聊天式生图模型
- `endpoint_path`：接口路径，`images` 常见是 `/v1/images/generations`，`chat` 常见是 `/v1/chat/completions`
- `api_key`：建议在 GUI 插件设置里填，或使用环境变量 `GROK_API_KEY`
- `model_name`：固定模型名，默认 `grok-2-image`
- `size_value`：默认尺寸，默认 `1024x1024`
- `extra_body_json`：额外请求体，默认空对象；如果你填写 `{"response_format":"b64_json"}`，插件会自动转成兼容对象格式
- `debug_logging`：调试日志开关；开启后会在控制台打印请求模式、路径和原始响应摘要，便于排查中转站问题

### 返回格式要求

- 当前 `qq_draw` 会优先从接口返回里解析以下字段：
  - `image_base64`
  - `base64`
  - `data`
  - `b64_json`
- 也支持返回图片 URL；插件会继续下载图片再回发 QQ
- 某些兼容站要求 `response_format` 不是字符串而是对象；现在插件会自动把 `"response_format":"b64_json"` 转成兼容格式，少掉一类 500 报错
- 某些兼容站把生图模型挂在 `/v1/chat/completions`；这时请把 `api_mode` 设成 `chat`，并确认返回里最终会带图片 URL 或 base64
- 如果你的接口返回的不是这些字段或 URL，需要把返回结构适配成上述格式，或者再扩展 `plugins/qq_draw/plugin.py`

### 推荐做法

- 先到 `设置 -> 插件管理 -> 编辑 -> QQ生图` 填好：
  - `API Base URL`
  - `接口模式`
  - `API Key`
  - `模型名`
  - 需要的话再补 `额外请求体(JSON)`
- 最后直接在 QQ 私聊测试：`/画图 你的提示词`

### 配置规范

- 涉及 `api_key`、`token`、`secret`、`password`、`access_key`、`authorization`、`bearer`、`client_secret` 的字段，现在会在插件编辑器里默认遮挡显示
- 这类敏感字段仍然可以继续放在前端配置里，方便你改；保存后真实值会写入本地 SQLite，插件目录下的 `config.json` 默认值保持为空
- 更推荐的长期做法仍然是：前端可编辑 + 环境变量兜底
- 插件配置字段类型建议统一使用：
  - `bool`
  - `choice`
  - `text`
  - `number`
  - `list`
  - `path`
  - `file`
  - `secret`
- 新增插件时不需要提前建数据库字段；只要配置项类型是 `secret/password`，或字段名命中上述敏感关键词，首次保存时会自动写入 SQLite

## 7. QQ 定时提醒插件

- 插件路径：`plugins/qq_reminder/config.json`
- 作用：在 QQ 私聊中创建周期提醒，到点后只提醒你本人，不发群里

### 示例

- `/提醒 每周1到周5 17:20 提醒我打卡`
- `/提醒 工作日 五点二十 提醒我打卡`
- `/提醒列表`
- `/提醒删除 1`
- `/提醒测试 1`

### 行为说明

- 自动把提醒目标锁定为当前 QQ 私聊 `private:<你的QQ号>`
- 支持工作日、每天、周区间、多个周几
- 提醒任务保存在 `data/qq_reminders.json`

## 8. QQ 角色切换

- 插件路径：`plugins/qq_role_switch/config.json`
- 作用：在 QQ 中查看角色列表、查询当前角色，并直接切换角色

### 常用命令

- `/角色列表`
- `/角色 当前`
- `/角色 丰川祥子`
- `/丰川祥子`

### 切换效果

- 切换当前角色提示词
- 切换角色专属 GPT-SoVITS 配置（如果已启用）
- 自动切换该角色当前默认服装
- 桌面 GUI 的角色状态会同步刷新

## 9. QQ 语音回复行为

- 当 QQ 命中语音回复时，现在会：
  - 先发送语音
  - 再附带发送一份文本
- 适合群聊里既想听语音，又想保留可读文本记录的场景

## 10. QQ 文件浏览白名单

- `qq_file_browser` 现在支持通过 `额外挂载目录` 增加白名单目录
- 配置格式：`别名|本地路径`
- 例如：`qqsave|D:\QQDownloads`
- 配完后可在 QQ 里使用：
  - `/cd /qqsave`
  - `/ls`
  - `/get 1`

## 11. 现在如何学习知识库

- 当前本地知识库入口是 `knowledge_base`
- 现在也可以在主页的 `更多功能 -> 知识库管理` 中直接选择目录并一键学习
- 如果你以前用过 `ingest_knowledge.py`，默认的旧目录 `knowledge_docs` 现在会自动出现在知识库管理窗口里，方便重新纳入新流程
- 知识库管理窗口现在还支持：
  - 查看当前知识片段数
  - 清空并重建知识库
  - 查看最近配置的知识目录
  - 直接搜索验证结果
  - 对单个目录做启用 / 禁用开关
- 如果你希望某个目录的旧知识立刻失效，可以在窗口中选中该目录，再点“删除选中目录知识”
- 用法：`learn ||| 路径` 或 `search ||| 查询词`
- 示例：
  - `knowledge_base ||| learn ||| D:\项目资料`
  - `knowledge_base ||| search ||| NapCat 配置方法`
- 当前会自动扫描目录下这些文件：
  - `.md`
  - `.txt`
  - `.py`
  - `.json`
- 学进去的内容会进入本地知识库检索层，后续普通对话中会被自动补充到上下文
- 对宝可梦百科这类结构化 JSON，现在会自动走专用兼容：优先抽取中/日/英名字、简介、效果、形态和可学习关系，避免整份超长 JSON 只按行切碎
- 如果你使用远程 embedding，建议开启 `慢速导入模式`：每处理一批文件自动暂停一次，能明显减少 RPM 限频
- 现在还支持 `自适应慢速模式`：一旦检测到限频，会自动临时拉长暂停时间

## 12. 每日总结与 QQ 数据

- `每日总结 / 日记` 现在会综合：
  - 屏幕活动
  - 完整对话历史
  - 主人跨渠道聊天记录
- 其中主人聊天记录现在会进一步区分：
  - 本地聊天
  - QQ 私聊
  - QQ 群聊
- 如果你当天主要是在 NapCat QQ 群里互动，后续新生成的总结会更容易明确写出群聊内容，而不是误判成“没怎么用 QQ”
- 现在也支持接入 Live2D/Tauri 桌面活动事件，作为屏幕活动摘要的唯一采集来源

## 13. Live2D/Tauri 桌面活动上报

- Live2D 桌面端负责采集并上报：
  - 前台应用
  - 窗口标题
  - 基础浏览器上下文（初版）
  - active / idle / locked 状态
- 它会把结构化事件上报到：
  - `POST /gui/activity-ingest`
- 主程序不再启动或回退到本地 Python 轮询，只读取 `source=live2d-tauri` 的事件。

## 14. 联网搜索双接口

- `联网搜索` 现在支持同时配置：
  - `Exa 本地地址`
  - `Exa 远程地址`
- 并可通过 `优先本地接口` 控制先试本地还是远程
- 适合：
  - 本地有反代服务时优先走本地
  - 本地失败时自动回退远程
- 在插件编辑器中可直接点击 `检测搜索接口`，快速检查本地/远程 Exa 地址是否连通

## 15. 新插件最小检查清单

1. `config.json` 中 `trigger` 必须稳定，不要随意改名。
2. `llm_command` 应该短、明确，并能被模型看懂。
3. `description` 要写用户会怎么说，而不是只写内部实现。
4. 自然语言触发写进 `get_capabilities()`；`aliases` 只放 `/命令`、兼容别名和工具检索关键词。
5. 高风险插件必须默认 `allow_qq_others=false`。
6. 插件返回值应尽量是短文本或结构化结果，不要直接返回超长日志。
7. 需要 GUI 配置的字段放到 `settings`，不要散落到代码常量里。
8. 涉及路径的插件要做白名单或工作区限制。
9. 涉及网络请求的插件要有超时和错误摘要。
10. 新增后先在本地文本入口测试，再开放 QQ。

## 15. 已知依赖与注意事项

- `search` 兼容 `ddgs`（推荐）与旧版 `duckduckgo_search`
- `vision` / `qq_screenshot` 依赖截图能力：
  - 优先走 `PIL.ImageGrab`
  - 环境允许时也可走 `pyautogui`
- `qq_draw` 依赖可用的图片生成 HTTP 接口；既支持 `/v1/images/generations`，也支持把生图模型挂在 `/v1/chat/completions` 的兼容站
- 如果你使用的是中转站或兼容层，请重点确认：`base_url`、`endpoint_path`、`model_name` 是否和对方文档一致
- `qq_role_switch` 依赖角色已经在形象管理里配置过服装；未配置默认服装时只能切角色状态，无法同步 Live2D 模型
- 某些插件依赖本机环境或权限：
  - 例如日志文件、系统控制、工作区读写、外部程序路径
- 如果新增插件后没生效：
  - 先到插件管理里刷新
  - 不行就重启程序

## 16. 相关文档分工

- `MCP_QQ_SETUP_GUIDE.md`：NapCat / QQ / MCP 的接入与使用
- `PROJECT_STATUS.md`：当前主干状态、近期改动和下一步边界
- `docs/TROUBLESHOOTING.md`：常见问题排查
- `docs/SECURITY_REMEDIATION_PLAN.md`：当前安全边界和验证方式

## 17. mcp_tools 路由配置（2026-03-08）

`mcp_tools` 现在支持自然语言优先路由，配置项在：
- `plugins/mcp_tools/config.json -> settings`

关键字段：
- `intent_route_enabled`：是否启用自然语言优先路由
- `intent_route_brand_keywords`：品牌/业务域关键词
- `intent_route_action_keywords`：动作关键词
- `intent_route_web_search_override_keywords`：显式联网覆盖关键词

规则：
- 品牌词 + 动作词 => 优先 `mcp_tools`
- 命中联网覆盖词 => 优先联网搜索，不抢占 `mcp_tools`

GUI 入口：
- `设置 -> MCP -> MCP 自然语言路由`


## 18. search 插件输出补充（2026-03-12）
- 对价格/行情类问题，会抽取关键数值置顶显示“关键数值：…”
- 当用户明确要“链接/网址/link/url”时，结果中补充“链接：”行，便于后续发卡片/点击。

