自娱自乐小程序，仓库里保留的是当前能跑起来的一套桌面助手主干，文档也尽量按现在已经落地的能力来写。
---

# Live2D-LLM 桌面智能陪伴助手

Live2D-LLM 是一个功能强大且高度可扩展的桌面 AI 助手。它结合了大型语言模型（LLM）、Live2D 虚拟形象、高级记忆引擎与丰富的插件生态，致力于提供具备“真人感”的深度陪伴体验。

项目不仅支持本地桌面的无缝交互，还通过 NapCat 接入了 QQ 生态，并全面支持 MCP（Model Context Protocol）以连接外部工具链。

---

## 🚀 核心特性

* **多模态交互与拟真表现**：支持文本、语音、屏幕视觉感知。集成 GPT-SoVITS 等 TTS 引擎与 Rhubarb 口型同步，具备情绪状态机与自适应的主动搭话机制（如次日健康/任务跟进）。
* **结构化桌面活动采集**：支持通过 Rust sidecar 采集前台窗口与活动事件，并回流到 Python 主程序生成更稳的日报/总结。
* **高级记忆架构**：采用 SQLite（精确对话记录）与向量数据库（语义召回）双轨制。支持多会话隔离（本地桌面与 QQ 各群聊/私聊数据互不干扰），并能根据用户反馈自动调整长期记忆权重。
* **多平台网关（NapCat QQ 接入）**：内置基于 OneBot 标准的 Chat Gateway。支持 QQ 私聊/群聊接入、主人身份鉴权、QQ 接收图片视觉识别、概率性语音回发以及 QQ 远程桌面截图。
* **MCP 工具桥**：全面支持 Model Context Protocol。可通过可视化 GUI 直接配置 `stdio` 或 `streamable_http` 类型的本地/远程服务器，并通过自然语言无缝调用外部能力。
* **现代化 Qt GUI**：提供精致的卡片式桌面控制面板。内置独立的设置中心、记忆编辑器、插件管理器以及控制台风格的专属“代码助手”窗口，支持自定义 UI 调色板。

---
## 前端界面

### 主体部分

前端分为两个部分：一个是 Live2D 立绘本体，一个是控制面板悬浮球（蓝色的灵魂宝石）。
<img width="342" height="577" alt="image" src="https://github.com/user-attachments/assets/1e575a65-2147-4eea-a88e-1c7eeab01aae" />

### 控制面板

控制面板支持换装、TTS 开关、语音识别开关、免打扰模式以及设置入口。
<img width="876" height="203" alt="image" src="https://github.com/user-attachments/assets/50f5745f-c661-4344-aa84-aba3b6113af2" />

### 设置界面

设置界面可以修改程序核心参数、管理插件、配置 QQ / MCP、编辑记忆和知识库相关功能。
<img width="1535" height="1161" alt="image" src="https://github.com/user-attachments/assets/a969db34-0f02-4c1e-a44a-26bcdae05e59" />



---
## 🏗️ 架构概览

项目主干架构清晰，分层明确：

* **入口与编排 (`core/application.py`)**：负责 EventBus、状态机、TTS、GUI 及传感器的全局调度。
* **对话主流程 (`services/chat_service.py`)**：处理 Gatekeeper 拦截、上下文拼装、工具路由、LLM 调用与记忆写入。
* **记忆体系 (`modules/advanced_memory.py`)**：双轨记忆调度，支持依据时效与相关性动态构建 Prompt。
* **插件体系 (`modules/plugin_manager.py`)**：支持 `react`（模型调用）、`direct`（用户触发）、`observe`（旁路观察）三类插件，并具备严格的（本地/QQ/主人）细粒度权限管控。

---

## ⚙️ 快速上手

### 1. 环境准备

建议使用 `conda` 创建独立环境：

```bash
conda create -n live2d-llm python=3.10
conda activate live2d-llm
pip install -r requirements.txt

```

### 2. 启动项目

使用守护进程或直接启动 GUI：

```bash
# 开发调试推荐
python boot.py

# 生产环境守护拉起
python main.py

```

### 2.1 Rust 活动采集器（可选增强）

如果你要启用 Rust sidecar 屏幕采集器，请先在项目根目录执行：

```bash
cargo build --release --manifest-path rust-activity-agent/Cargo.toml
```

构建完成后，主程序会在启动时自动尝试拉起：

- `rust-activity-agent/target/release/rust-activity-agent.exe`

并在退出时一起关闭。

### 3. 配置向导

首次启动后，可通过主界面的 **设置中心 (⚙️)** 进行可视化配置。推荐两套基础运行策略：

* **高陪伴人格（沉浸体验）**：开启主动记忆筛选（`use_llm_selector=True`），缩短屏幕感知与主动搭话的冷却时间，助手会更频繁地参与你的日常。
* **稳定省调用（低碳模式）**：关闭主动记忆筛选，拉长 Gatekeeper 静默窗口，适合在后台安静挂机，仅在明确呼叫时响应。

---

## 🔌 外部接入指南

### QQ / NapCat 接入

在 GUI 设置的 **MCP / QQ** 页面中配置 Webhook。本程序作为消息网关，同端口兼容 HTTP Webhook 与反向 WebSocket。建议配置主副号隔离，并启用“仅响应 @我”以降低群聊噪音。具体步骤请参阅内置的 `MCP_QQ_SETUP_GUIDE.md`。

### MCP (Model Context Protocol) 接入

无需手动编辑 JSON，在 GUI 中点击 **+ 本地进程** 或 **+ HTTP 服务器**，填入启动命令或 URL 即可。保存后程序将自动拉取远程工具，在聊天中可通过 `查一下麦当劳优惠券` 等自然语言自动路由并触发调用。

---

## 🛠️ 插件系统

当前系统内置功能强大的插件管理，所有插件均在 `plugins/` 目录下热加载。

重点插件包括：

* **task_manager**：统一的任务中枢，支持待办追踪与跨日进度询问。
* **workspace_ops**：代码与文件助手，支持带二次确认的安全文件读写。
* **qq_screenshot**：允许在 QQ 端发送 `截图发我`，自动将电脑主屏或指定窗口回传。
* **qq_draw**：允许在 QQ 端通过 `/画图` 或 `/画画` 触发生图并回发图片。
* **qq_reminder**：允许在 QQ 私聊中创建工作日 / 每天 / 指定周几的定时提醒。
* **qq_role_switch**：允许在 QQ 中查看角色列表、切换当前角色，并同步角色默认服装与角色 TTS。
* **local_knowledge**：支持在主页的 `更多功能 -> 知识库管理` 中选择目录、一键学习、搜索验证、按目录启停与删除知识。

## 🧠 每日总结与 QQ

- 每日总结 / 日记会综合：屏幕活动、完整对话历史、主人跨渠道聊天记录。
- 主人跨渠道聊天记录现在会进一步区分：本地聊天、QQ 私聊、QQ 群聊。
- 如果你当天主要在 NapCat QQ 群里互动，后续新生成的总结会更容易明确写出群聊内容，而不是误判成没怎么用 QQ。

---

> **Note**: 如遇依赖缺失，可利用设置页中的 `Dependency Health` 一键修复。

