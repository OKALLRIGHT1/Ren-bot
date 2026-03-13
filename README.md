自娱自乐小程序，目前有些东西还没上传，主要上传是为了防止ai给我改崩了就顺便上传了，于是用ai写了一下readme
---

# Live2D-LLM 桌面智能陪伴助手

Live2D-LLM 是一个功能强大且高度可扩展的桌面 AI 助手。它结合了大型语言模型（LLM）、Live2D 虚拟形象、高级记忆引擎与丰富的插件生态，致力于提供具备“真人感”的深度陪伴体验。

项目不仅支持本地桌面的无缝交互，还通过 NapCat 接入了 QQ 生态，并全面支持 MCP（Model Context Protocol）以连接外部工具链。

---

## 🚀 核心特性

* **多模态交互与拟真表现**：支持文本、语音、屏幕视觉感知。集成 GPT-SoVITS 等 TTS 引擎与 Rhubarb 口型同步，具备情绪状态机与自适应的主动搭话机制（如次日健康/任务跟进）。
* **高级记忆架构**：采用 SQLite（精确对话记录）与向量数据库（语义召回）双轨制。支持多会话隔离（本地桌面与 QQ 各群聊/私聊数据互不干扰），并能根据用户反馈自动调整长期记忆权重。
* **多平台网关（NapCat QQ 接入）**：内置基于 OneBot 标准的 Chat Gateway。支持 QQ 私聊/群聊接入、主人身份鉴权、QQ 接收图片视觉识别、概率性语音回发以及 QQ 远程桌面截图。
* **MCP 工具桥**：全面支持 Model Context Protocol。可通过可视化 GUI 直接配置 `stdio` 或 `streamable_http` 类型的本地/远程服务器，并通过自然语言无缝调用外部能力。
* **现代化 Qt GUI**：提供精致的卡片式桌面控制面板。内置独立的设置中心、记忆编辑器、插件管理器以及控制台风格的专属“代码助手（Codex）”窗口，支持自定义 UI 调色板。

---

## 🏗️ 架构概览

项目主干架构清晰，分层明确：

* **入口与编排 (`core/application.py`)**：负责 EventBus、状态机、TTS、GUI 及传感器的全局调度。
* **对话主流程 (`services/chat_service.py`)**：处理 Gatekeeper 拦截、上下文拼装、工具路由、LLM 调用与记忆写入。
* **记忆体系 (`modules/advanced_memory.py`)**：双轨记忆调度，支持依据时效与相关性动态构建 Prompt。
* **插件体系 (`modules/plugin_manager.py`)**：支持 `react`（模型调用）、`direct`（用户触发）、`observe`（旁路观察）三类插件，并具备严格的（本地/QQ/主人）细粒度权限管控。

---

## ⚙️ 快速上手

### 1. 环境准备（还没写requirements.txt）

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

## 🛠️ 插件系统（很多还没测）

当前系统内置功能强大的插件管理，所有插件均在 `plugins/` 目录下热加载。

重点插件包括：

* **task_manager**：统一的任务中枢，支持待办追踪与跨日进度询问。
* **workspace_ops**：代码与文件助手，支持带二次确认的安全文件读写。
* **qq_screenshot**：允许在 QQ 端发送 `截图发我`，自动将电脑主屏或指定窗口回传。

---

> **Note**: 如遇依赖缺失，可利用设置页中的 `Dependency Health` 一键修复。

