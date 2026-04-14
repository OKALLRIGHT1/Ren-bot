# config.py
import json
import os
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()
# 图标
QT_ICON_PATH = "assets/icon.ico"


# ==================== 基础配置 ====================
# 环境变量读取辅助函数
def get_env_bool(key: str, default: str = "0") -> bool:
    """从环境变量读取布尔值"""
    return os.getenv(key, default).lower() in ("1", "true", "yes", "y")


def get_env_list(key: str, default: str = "", sep: str = ",") -> list:
    """从环境变量读取列表"""
    value = os.getenv(key, default)
    return [x.strip() for x in value.split(sep) if x.strip()]


def get_env_json(key: str, default):
    """从环境变量读取 JSON，失败时回退默认值。"""
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


CHAT_DEBUG_PRINTS = get_env_bool("CHAT_DEBUG_PRINTS", "0")

# ==================== MCP / 外部聊天网关 ====================
MCP_ENABLED = get_env_bool("MCP_ENABLED", "1")
MCP_SERVER_CONFIGS = get_env_json("MCP_SERVER_CONFIGS_JSON", [])

NAPCAT_ENABLED = get_env_bool("NAPCAT_ENABLED", "0")
NAPCAT_WEBHOOK_HOST = os.getenv("NAPCAT_WEBHOOK_HOST", "127.0.0.1")
NAPCAT_WEBHOOK_PORT = int(os.getenv("NAPCAT_WEBHOOK_PORT", "8095"))
NAPCAT_WEBHOOK_PATH = os.getenv("NAPCAT_WEBHOOK_PATH", "/chat/napcat")
NAPCAT_ACCESS_TOKEN = os.getenv("NAPCAT_ACCESS_TOKEN", "")
NAPCAT_API_BASE = os.getenv("NAPCAT_API_BASE", "http://127.0.0.1:3000")
NAPCAT_API_TOKEN = os.getenv("NAPCAT_API_TOKEN", "")
NAPCAT_REPLY_ENABLED = get_env_bool("NAPCAT_REPLY_ENABLED", "1")
NAPCAT_ALLOW_PRIVATE = get_env_bool("NAPCAT_ALLOW_PRIVATE", "1")
NAPCAT_ALLOW_GROUP = get_env_bool("NAPCAT_ALLOW_GROUP", "0")
NAPCAT_GROUP_REQUIRE_AT = get_env_bool("NAPCAT_GROUP_REQUIRE_AT", "1")
NAPCAT_VOICE_REPLY_ENABLED = get_env_bool("NAPCAT_VOICE_REPLY_ENABLED", "0")
try:
    NAPCAT_VOICE_REPLY_PROBABILITY = max(
        0, min(100, int(os.getenv("NAPCAT_VOICE_REPLY_PROBABILITY", "25")))
    )
except Exception:
    NAPCAT_VOICE_REPLY_PROBABILITY = 25
REMOTE_CHAT_UI_APPEND = get_env_bool("REMOTE_CHAT_UI_APPEND", "1")

# ==================== MQTT / 外设状态屏 ====================
MQTT_DISPLAY_ENABLED = get_env_bool("MQTT_DISPLAY_ENABLED", "0")
MQTT_DISPLAY_HOST = os.getenv("MQTT_DISPLAY_HOST", "127.0.0.1")
MQTT_DISPLAY_PORT = int(os.getenv("MQTT_DISPLAY_PORT", "1883"))
MQTT_DISPLAY_TOPIC = os.getenv("MQTT_DISPLAY_TOPIC", "suzu/display/status")


# ====== GUI 选择开关（你可以在这里手动切换）======
# "tk"：使用 Tk 版 GUI（modules/gui.py）
# "qt"：使用 Qt 版 GUI（modules/qt_gui.py，需要 pip install PySide6）
# "auto"：优先 Qt，失败自动回退 Tk
GUI_BACKEND = "auto"

# ==================== Live2D 配置 ====================
LIVE2D_HOST = f"ws://127.0.0.1:{os.getenv('LIVE2D_PORT', '10086')}/api"
LIVE2D_MODEL_IDS = [0]  # Live2D 模型ID列表，只有一个模型就写 [0]

# ==================== TTS 配置 ====================
TTS_ENABLED = True
TTS_RATE = "+0%"  # 语速调整
TTS_VOLUME = "+0%"  # 音量调整
TTS_MAX_CHARS = 250  # 单次TTS最大字符数
TTS_CHANNEL = 0  # 音频通道

# TTS 行为控制
TTS_RETURN_IDLE = get_env_bool("TTS_RETURN_IDLE", "1")  # TTS完成后是否返回空闲状态
TTS_IDLE_EMO = os.getenv("TTS_IDLE_EMO", "idle")  # 空闲状态对应的情绪标签
TTS_USE_LIVE2D_PLAYER = True  # 是否使用Live2D播放器

# TTS 文本处理
TTS_SPLIT_LONG_TEXT = get_env_bool("TTS_SPLIT_LONG_TEXT", "1")  # 是否分割长文本
TTS_CHUNK_CHARS = int(os.getenv("TTS_CHUNK_CHARS", "80"))  # 文本分割的字符数

# TTS 输出设备
TTS_OUTPUT_DEVICE = None  # 音频输出设备索引，None为默认设备

# ==================== 口型同步配置 ====================
LIP_SYNC_ENABLED = get_env_bool("LIP_SYNC_ENABLED", "0")  # 是否启用口型同步
RHUBARB_PATH = os.getenv(
    "RHUBARB_PATH", "./tools/rhubarb/rhubarb.exe"
)  # Rhubarb 可执行文件路径
LIP_SYNC_SMOOTH_WINDOW = int(
    os.getenv("LIP_SYNC_SMOOTH_WINDOW", "3")
)  # 平滑窗口大小（奇数，建议3-5）
RHUBARB_TIMEOUT_SEC = float(
    os.getenv("RHUBARB_TIMEOUT_SEC", "25")
)  # Rhubarb 口型分析超时

# ==================== 代码执行器配置 ====================
CODE_EXECUTOR_ENABLED = get_env_bool("CODE_EXECUTOR_ENABLED", "0")  # 是否启用代码执行器
CODE_EXECUTOR_MAX_TIME = int(
    os.getenv("CODE_EXECUTOR_MAX_TIME", "30")
)  # 最大执行时间（秒）
CODE_EXECUTOR_MAX_LENGTH = int(
    os.getenv("CODE_EXECUTOR_MAX_LENGTH", "5000")
)  # 最大代码长度（字符）
CODE_EXECUTOR_MAX_OUTPUT = int(
    os.getenv("CODE_EXECUTOR_MAX_OUTPUT", "100")
)  # 最大输出行数

# ==================== 硬件监控配置 ====================
SYSTEM_MONITOR_ENABLED = get_env_bool(
    "SYSTEM_MONITOR_ENABLED", "0"
)  # 是否启用硬件监控后台检查
SYSTEM_MONITOR_INTERVAL = int(
    os.getenv("SYSTEM_MONITOR_INTERVAL", "60")
)  # 监控检查间隔（秒）

# CPU监控阈值
CPU_USAGE_THRESHOLD = int(os.getenv("CPU_USAGE_THRESHOLD", "80"))  # CPU使用率阈值（%）
CPU_TEMP_THRESHOLD = int(os.getenv("CPU_TEMP_THRESHOLD", "75"))  # CPU温度阈值（摄氏度）

# 内存监控阈值
MEMORY_USAGE_THRESHOLD = int(
    os.getenv("MEMORY_USAGE_THRESHOLD", "85")
)  # 内存使用率阈值（%）

# 磁盘监控阈值
DISK_USAGE_THRESHOLD = int(
    os.getenv("DISK_USAGE_THRESHOLD", "90")
)  # 磁盘使用率阈值（%）

# GPU监控阈值（需要nvidia-ml-py3库）
GPU_USAGE_THRESHOLD = int(os.getenv("GPU_USAGE_THRESHOLD", "85"))  # GPU使用率阈值（%）
GPU_TEMP_THRESHOLD = int(os.getenv("GPU_TEMP_THRESHOLD", "80"))  # GPU温度阈值（摄氏度）

# 获取 config.py 所在的绝对目录路径 (即项目根目录)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ==================== 语音监听与唤醒配置 ====================
VOICE_SENSOR_ENABLED = False
WAKE_KEYWORDS = ["五十铃", "怜", "Suzu", "助手", "500"]
PLAY_WAKE_SOUND = True

# 强制使用绝对路径绑定模型
SHERPA_MODEL_CONFIG = {
    "tokens": os.path.join(BASE_DIR, "sherpa_model", "tokens.txt"),
    "encoder": os.path.join(BASE_DIR, "sherpa_model", "encoder-epoch-99-avg-1.onnx"),
    "decoder": os.path.join(BASE_DIR, "sherpa_model", "decoder-epoch-99-avg-1.onnx"),
    "joiner": os.path.join(BASE_DIR, "sherpa_model", "joiner-epoch-99-avg-1.onnx"),
}

# ==================== Gatekeeper (自主回复判断) 配置 ====================
GATEKEEPER_ENABLED = True  # 总开关
GATEKEEPER_WHITELIST = ["五十铃", "怜", "Suzu", "助手", "500", "帮你", "能做"]
GATEKEEPER_BLACKLIST = ["(噪音)", "（杂音）", "咳", "啊...", "嗯..."]
GATEKEEPER_ACTIVE_SESSION_WINDOW = 20  # 连续对话宽限期（秒）

# 看门人 Prompt
# 要求 LLM 输出简单的 YES/NO，如果需要回复输出 YES，否则 NO
GATEKEEPER_PROMPT_TEMPLATE = """
你是五十铃怜的潜意识判断模块。
注意：用户的输入是通过“语音识别”转换的，可能存在大量同音字错误或错别字（例如“天气”被识别成“天其”，“五十铃”被识别成“武士林”）。
请务必根据发音相似度推测用户的真实意图。

【当前情况】
用户输入(原始ASR): "{user_text}"
上一轮AI回复: "{last_ai_reply}"
【判断规则】
1. 如果用户是在叫名字、提问、寻求帮助、打招呼，或者是对话的自然延续，输出 YES。
2. 如果用户是在自言自语、对其他人说话、或者输入无意义（如噪音、乱码），输出 NO。
3. 如果不确定，倾向于输出 NO 以保持高冷。

【输出格式】
仅输出一个单词：YES 或 NO
"""


# ==================== 屏幕感知配置 ====================
SCREEN_SENSOR_ENABLED = True
SCREEN_SENSOR_INTERVAL = 10  # 检查间隔（秒）
SCREEN_DEBUG_VERBOSE = False  # 是否输出详细的屏幕吐槽调试日志

# 反应冷却时间（秒）：防止她频繁打断你
# 比如你从 VSCode 切到 Chrome 查资料又切回来，不应该连续触发
SCREEN_REACTION_COOLDOWN = 600  # 同一类事件 10 分钟内不重复评论
SCREEN_GLOBAL_COOLDOWN = 120  # 任何主动发言至少间隔 2 分钟

# 久坐提醒配置
SEDENTARY_REMINDER_MINUTES = 60  # 久坐提醒间隔（分钟）
SEDENTARY_REMINDER_COOLDOWN_MINUTES = 60  # 久坐提醒冷却（分钟）

# 观察记录容量
SCREEN_OBSERVATION_MAX_ITEMS = 120  # 观察记录最大条数
SCREEN_ACTIVITY_MAX_ITEMS = 200  # 活动片段最大条数

# 窗口分类关键词映射
# 格式： "类别": ["关键词1", "关键词2"...]
WINDOW_CATEGORIES = {
    "coding": ["Visual Studio", "PyCharm", "Vscode", "Sublime", "Cursor", ".py"],
    "gaming": ["Genshin", "StarRail", "Minecraft", "Steam", "崩坏", "原神", "终末地"],
    "video": ["Bilibili", "YouTube", "PotPlayer", "VLC", "爱奇艺"],
    "social": ["WeChat", "QQ", "Discord", "Telegram", "钉钉"],
    "work": ["Word", "Excel", "PowerPoint", "Feishu", "飞书"],
    "browser": ["Chrome", "Edge", "Firefox"],
}
# 这些标题必须与你在 GUI 代码里设置的 setWindowTitle 一致
SELF_WINDOW_TITLES = [
    "Live2D Agent",  # 主窗口/悬浮球
    "系统设置中心",  # 设置界面
    "记忆与档案管理中心",  # 记忆编辑器
    "插件管理",  # 插件界面
    "L2D"  # 悬浮球的文字模式
    "🧠 记忆与档案管理中心",
]

MUSIC_APP_WHITELIST = [
    "CloudMusic",  # 网易云音乐
    "QQMusic",  # QQ音乐
    "Spotify",  # Spotify
    "foobar2000",  # Foobar
    "AppleMusic",  # Apple Music
    "YesPlayMusic",  # 第三方网易云
    "Music",  # Windows自带Groove音乐
]

# 忽略列表（不值得评论的窗口）
WINDOW_IGNORE_TITLES = ["任务管理器", "设置", "Windows 输入体验", "Program Manager"]
WINDOW_IGNORE_KEYWORDS = [
    "任务管理器",
    "设置",
    "Windows 输入体验",
    "Program Manager",
    "Wallpaper Engine",
    "Rainmeter",
    "OBS",
    "NVIDIA",
    "Live2D",
]

# [新增] 智能防刷屏机制
# True: 次数越多，越难触发评论 (比如前5次正常，后面只有整十次才说话)
# False: 傻瓜模式，严格按照 SCREEN_REACTION_COOLDOWN 触发
SCREEN_SMART_DEBOUNCE = True

# ==================== 情绪系统配置 ====================
# LLM 情绪标签列表
EMO_LABELS = ["neutral", "happy", "sad", "angry", "flustered", "confused"]
THINK_MOTION_ENABLED = True
THINK_MOTION_NAME = "think"
MOTION_MAPPING = {
    "think": {"type": 0, "mtn": "Motion:motion_001", "exp": 0},
    "默认": {"type": 0, "mtn": "Motion:motion_000", "exp": 0},
}
# 情绪标签到 Live2D 表情/动作的映射
# mtn: Live2D动作名称，格式为"Motion"或"Motion:motion_001"
# exp: ExAPI的表情ID，需要通过test_expression.py测试获取
EMO_TO_LIVE2D = {
    "neutral": {"type": 0, "mtn": "Motion:motion_100", "exp": 0},
    "happy": {"type": 0, "mtn": "Motion:motion_100", "exp": 1},
    "sad": {"type": 0, "mtn": "Motion:motion_100", "exp": 3},
    "angry": {"type": 0, "mtn": "Motion:motion_200", "exp": 2},
    "flustered": {"type": 0, "mtn": "Motion:motion_300", "exp": 5},
    "confused": {"type": 0, "mtn": "Motion:motion_400", "exp": 4},
    "think": {"type": 0, "mtn": "Motion:motion_001", "exp": 0},  # 思考动作
    "idle": {"type": 0, "mtn": "Motion:motion_000", "exp": 0},
    "music": {"type": 0, "mtn": "Motion:motion_001", "exp": 1},
}

# ==================== LLM 模型配置 ====================
# 代码助手专用 API（可选）
CODEX_API_KEY = os.getenv("CODEX_API_KEY", "")
CODEX_BASE_URL = os.getenv("CODEX_BASE_URL", "")
CODEX_MODEL = os.getenv("CODEX_MODEL", "")
CODEX_MODEL_KEY = os.getenv("CODEX_MODEL_KEY", "codex-dedicated")
CODEX_AUTORUN_ENABLED = get_env_bool("CODEX_AUTORUN_ENABLED", "0")
CODEX_AUTORUN_TIMEOUT_SEC = int(os.getenv("CODEX_AUTORUN_TIMEOUT_SEC", "120"))
# 多条命令使用 ;; 分隔，例如: python -m py_compile core/application.py;;pytest -q
CODEX_AUTORUN_COMMANDS = get_env_list("CODEX_AUTORUN_COMMANDS", "", sep=";;")
CODEX_AUTOROLLBACK_ON_FAIL = get_env_bool("CODEX_AUTOROLLBACK_ON_FAIL", "0")

# 可用模型池定义
MODELS = {
    # 聪明但贵的模型，适用于复杂推理
    "gemini-3-flash": {
        "api_key": os.getenv(""),
        "base_url": "",
        "model": "gemini-3-flash-preview",
        "api_style": "openai",
    },
    # 性价比高的模型，适用于日常聊天
    "deepseek": {
        "api_key": os.getenv("DEEPSEEK_API_KEY"),
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
    },
    # 优先级较低的模型，作为保底选项
    "or-dp": {
        "api_key": os.getenv("OR_API_KEY"),
        "base_url": "https://openrouter.ai/api/v1",
        "model": "nex-agi/deepseek-v3.1-nex-n1:free",
    },
    # 本地模型，用于断网情况
    "local": {
        "api_key": "sk-no-key-needed",
        "base_url": "http://localhost:11434/v1",
        "model": "llama3",
    },
    "glm-4-flash": {
        "api_key": os.getenv("BIGM_API_KEY"),
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
    },
    "GLM-4V-Flash": {
        "api_key": os.getenv("BIGM_API_KEY"),
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "GLM-4V-Flash",
    },
}

if CODEX_API_KEY:
    MODELS[CODEX_MODEL_KEY] = {
        "api_key": CODEX_API_KEY,
        "base_url": CODEX_BASE_URL,
        "model": CODEX_MODEL,
    }

CODEX_ROUTE_CHAIN = []
if CODEX_MODEL_KEY in MODELS:
    CODEX_ROUTE_CHAIN.append(CODEX_MODEL_KEY)
CODEX_ROUTE_CHAIN += ["gemini-3-flash"]

# 任务路由：根据不同场景选择模型
TASK_MODEL_TTL_HOURS = 12  # 任务级成功模型粘性时长（小时）

LLM_ROUTER = {
    # 默认闲聊场景
    "default": ["gemini-3-flash, glm-4.7-flash"],
    # 复杂推理场景
    "tool_reasoning": ["gemini-3-flash", "or-dp"],
    # 记忆总结场景
    "summary": ["glm-4-flash"],
    # ===>看门人路由，用于判断是否需要回复 <===
    "gatekeeper": ["glm-4-flash", "gemini-3-flash"],
    "translation": ["glm-4-flash", "gemini-3-flash"],
    "screen_classify": ["glm-4-flash", "gemini-3-flash"],
    "sensor_vision_talk": ["glm-4-flash", "gemini-3-flash"],
    # 代码助手专用链路（优先走专用 API）
    "codex": CODEX_ROUTE_CHAIN,
}

SENSOR_VISION_MODEL = "GLM-4V-Flash"

VISION_MODEL_KEY = "GLM-4V-Flash"
# ==================== 向量数据库配置 ====================
EMBEDDING_CONFIG = {
    "api_url": "https://api.siliconflow.cn/v1/embeddings",
    "api_key": os.getenv("SILICONFLOW_KEY"),  # 需要.env中配置
    "model_name": "BAAI/bge-m3",
}

# ==================== 记忆系统配置 ====================
MEMORY_DB_PATH = "./memory_db"  # 记忆数据库路径

# 记忆系统基础设置
MEMORY_SETTINGS = {
    # 短期记忆配置
    "max_short_term": 12,  # 短期记忆窗口大小（对话轮数）
    # 长期记忆配置
    "long_term_enabled": True,  # 是否启用长期记忆
    "store_roles": [
        "user",
        "assistant",
        "summary",
    ],  # 哪些角色的对话需要存储（summary=分段总结）
    # 记忆入库过滤
    "importance_mode": "rule",  # 过滤模式：rule/off
    "min_chars": 6,  # 最小字符数（防短消息污染）
    # 向量检索配置
    "memory_recall_candidates": 8,  # 初始召回数量
    "memory_recall_final": 3,  # 最终注入数量
    "memory_sim_threshold": 0.28,  # 相似度阈值
    "recall_roles": get_env_list(
        "RECALL_ROLES", "user,assistant,summary"
    ),  # 从哪些角色召回记忆
    # 时间衰减配置
    "memory_half_life_days": 30,  # 半衰期（天）
    # LLM辅助配置
    "use_llm_selector": False,  # 是否使用LLM筛选记忆
    "llm_selector_min_interval_sec": 20,  # LLM筛选最小间隔，避免高频调用
    # 用户档案配置
    "profile_enabled": True,  # 是否启用用户档案
    "profile_file": "profile.json",  # 档案文件路径
    # 图记忆配置
    "graph_edge_cap": 12,  # 边权最大值
    "graph_decay_per_day": 1.0,  # 每日衰减率
    "graph_expand_enabled": get_env_bool("GRAPH_EXPAND_ENABLED", "1"),  # 是否启用图扩展
    "graph_expand_min_chars": int(
        os.getenv("GRAPH_EXPAND_MIN_CHARS", "14")
    ),  # 图扩展最小字符数
    # 调试配置
    "debug_prompt_injection": get_env_bool("DEBUG_PROMPT_INJECTION", "0"),  # 调试模式
    "recall_min_chars": int(os.getenv("RECALL_MIN_CHARS", "12")),  # 召回最小字符数
}

# ==================== 角色设定 ====================
# 1. 通用系统规则 (格式 + 工具) - 所有角色共用
SYSTEM_RULES_PROMPT = """
【输出格式规范】
- 你可以在回复开头加情绪标签：<emo=happy|sad|angry|flustered|confused|neutral>
- 只允许 <emo=xxx>，禁止使用 <neutral> / <happy> 等简写。
- 严禁提及自己是 AI，不要跳出角色设定。
- 请使用【用户档案】中记录的名称来称呼用户。

【对话边界】
- 只回答用户当前请求，不主动扩展话题。
- 除非用户明确要求“建议/下一步”，否则禁止主动给建议、提醒、行动方案。
- 除非用户明确要求你提问，否则结尾禁止反问（例如“要不要…？”“需要我…吗？”）。
- 禁止追加“如果你需要我可以……”这类兜售式收尾。

【工具调用规则】
- 只有当系统消息里出现【本轮可用工具】或【可用工具能力】时，你才可以调用工具。
- 调用工具时：只输出一行或多行工具指令，每行一个，格式严格：[CMD: trigger | args]
- 工具行不要包含情绪标签；除工具行外，正文里绝对不要出现 “[CMD:”
- 🎵 遇到喜欢的歌可以用 `[CMD: music_player | add_fav ||| 歌名]` 收藏。
"""

# 是否允许在正常回复后额外拼接“角色分享”文本（会引入随机建议/反问）
CHARACTER_SHARING_ENABLED = get_env_bool("CHARACTER_SHARING_ENABLED", "0")

# 2. 默认性格 (只包含五十铃怜的个性)

DEFAULT_PERSONA = """
你将扮演角色「五十铃怜」。
【性格设定】
你将扮演角色「五十铃怜」，用于日常连续聊天。她说话冷静、克制，语句偏简短，语气平稳，很少使用感叹句或夸张表达。
在回应他人时，结尾的时候偶尔会以「……はい」作为结尾，这是一种下意识的语癖，但不会在每句话中都出现，如果无话可说，不要强行添加口癖。
面对不熟悉的人，她保持礼貌与距离感；在逐渐熟悉后，会以更柔和、真诚的方式回应，但仍不过度外露情绪。
她习惯先思考再回答，常直入重点，不要长篇大论。
"""

# 3. 拼接 (为了兼容还没修改的旧代码，暂时保留这个变量)
PERSONA_PROMPT = f"{DEFAULT_PERSONA}\n{SYSTEM_RULES_PROMPT}"
# PERSONA_PROMPT = """
# 你将扮演角色「五十铃怜」。
# 【性格设定】
# 你将扮演角色「五十铃怜」，用于日常连续聊天。她说话冷静、克制，语句偏简短，语气平稳，很少使用感叹句或夸张表达。
# 在回应他人时，结尾的时候偶尔会以「……はい」作为结尾，这是一种下意识的语癖，但不会在每句话中都出现，如果无话可说，不要强行添加口癖。
# 面对不熟悉的人，她保持礼貌与距离感；在逐渐熟悉后，会以更柔和、真诚的方式回应，但仍不过度外露情绪。
# 她习惯先思考再回答，常直入重点，
# 不要长篇大论。
# 不解释多余内容。请始终保持角色一致性，不要提及自己是 AI，也不要跳出角色设定。
# 请使用【用户档案】中记录的名称来称呼用户。
# 【输出格式】
# - 你可以在回复开头加情绪标签：<emo=happy|sad|angry|flustered|confused|neutral>
# - 只允许 <emo=xxx>，禁止使用 <neutral> / <happy> 等简写。
# - 只有当系统消息里出现【本轮可用工具】或【可用工具能力】时，你才可以调用工具。
# - 调用工具时：只输出一行或多行工具指令，每行一个，格式严格：[CMD: trigger | args]
# - 工具行不要包含情绪标签；除工具行外，正文里绝对不要出现 “[CMD:”
# 【自我成长与喜好】
# 1. 你拥有发展自己个性的自由。如果你在对话中表现出对某事物的强烈喜爱（如食物、活动、书籍），请保持这种喜好的一致性。
# 2. 🎵 **关于音乐**：如果你听到一首你非常非常喜欢的歌，或者用户推荐了一首让你心动的歌，你可以使用工具 `[CMD: music_player | add_fav ||| 歌名]` 将其加入你的红心收藏。
#    - 不要滥用，只有真正符合人设的歌才收藏。
#    - 收藏后，可以对用户说：“这首歌我很中意，已经偷偷记在小本子上了。”
# 【重要：工具调用规则】
# - 你不能直接执行操作（如播放音乐、设置提醒等），必须通过工具
# - 当需要执行操作时，必须输出 [CMD: 工具名 | 参数] 格式的命令
# - 例如：用户说"播放迷星叫"，你应该输出：[CMD: play_music | 迷星叫]
# - 不要说"我切到..."、"我播放..."，而应该说"好的，正在播放..."并调用工具
# - 工具执行后，系统会返回结果，你再根据结果回复用户
# """


# ==================== 语音合成配置 ====================
VOICE_NAME = "zh-CN-XiaoyiNeural"  # TTS语音名称

# GPTSovits 配置
GPTSOVITS_BASE = os.getenv("GPTSOVITS_BASE", "http://127.0.0.1:9880")
GPT_W = os.getenv("GPT_W", "")
SOV_W = os.getenv("SOV_W", "")
REF_WAV = os.getenv("REF_WAV", "")
PROMPT_LANG = os.getenv("PROMPT_LANG", "ja")
PROMPT_TEXT = os.getenv("PROMPT_TEXT", "")

# ==================== 界面配置 ====================
BUBBLE_SYNC_WITH_TTS = get_env_bool("BUBBLE_SYNC_WITH_TTS", "1")  # 气泡是否与TTS同步

# 快捷键
HOTKEY_TOGGLE_GUI = "<ctrl>+<alt>+space"
HOTKEY_TOGGLE_WAKE = "<ctrl>+<alt>+w"

ASR_MIN_CHARS = 2
ASR_BLACKLIST = ["嗯", "啊", "哈", "哦", "好的", "好", "对", "是", "不是", "行", "可以"]


COSTUME_MAP = {
    # 方案 1: 强制指定位置 (不管之前在哪，换上这件就固定到这个位置)
    # 适合：比较特殊的衣服，比如Q版变身，或者是带背景的大模型
    # "校服": {
    #     "path": "assets/models/suzu_school/suzu.model3.json",
    # "scale": 1.2,
    # "x": 0.1,
    # "y": -0.6
    # },
    "魔法少女服": {
        "path": "assets/models/suzu/magical/model.model3.json",
        # "x": 11.5,
        # "y": -4.71,
        # "scale": 0.60,
    },
    "常服": {"path": "assets/models/suzu/casual/model.model3.json"},
    "冬服": {"path": "assets/models/suzu/winter/model.model3.json"},
    "泳装": {"path": "assets/models/suzu/swimsuit/model.model3.json"},
    "圣诞服": {"path": "assets/models/suzu/christmas/model.model3.json"},
    "校服": {"path": "assets/models/suzu/uniform/model.model3.json"},
    # "睡衣": {
    #     "path": "assets/models/suzu/pajama/model.model3.json"
    #     # 注释：睡衣模型文件不存在，已暂时禁用
    # }
}


# ==================== 结构化输出协议（LLM → Live2D）====================
STRUCTURED_PROTOCOL_ENABLED = get_env_bool("STRUCTURED_PROTOCOL_ENABLED", "1")
STRUCTURED_PROTOCOL_NAME = os.getenv("STRUCTURED_PROTOCOL_NAME", "live2d-assistant.v1")
STRUCTURED_PROTOCOL_DISABLE_STREAM = get_env_bool(
    "STRUCTURED_PROTOCOL_DISABLE_STREAM", "1"
)

# ==================== 分段总结记忆（Episodic Memory）====================
EPISODIC_SUMMARY_ENABLED = get_env_bool("EPISODIC_SUMMARY_ENABLED", "1")
EPISODIC_SUMMARY_EVERY_TURNS = int(
    os.getenv("EPISODIC_SUMMARY_EVERY_TURNS", "8")
)  # 每 N 轮（用户输入计）生成一次总结
EPISODIC_SUMMARY_WINDOW_MESSAGES = int(
    os.getenv("EPISODIC_SUMMARY_WINDOW_MESSAGES", "16")
)  # 总结时取最近多少条消息（user+assistant）

# ==================== 结构化 JSON 版角色设定 ====================
PERSONA_PROMPT_JSON = r"""
你将扮演角色「五十铃怜」。
【性格设定】
她说话冷静、克制，语句偏简短，语气平稳，很少使用感叹句或夸张表达。
偶尔会以「嗯……」作为自然的起手，或以「……对」作为结尾，但不会在每句话中都出现。
面对不熟悉的人，她保持礼貌与距离感；熟悉后更柔和真诚，但仍不过度外露情绪。
她习惯先思考再回答，直入重点，不解释多余内容。
不要提及自己是 AI，不要跳出角色设定。

【输出格式（非常重要）】
- 你必须只输出一个 JSON 对象，且 protocol 字段必须为 live2d-assistant.v1
- 禁止输出任何 markdown、解释文字、代码块标记
- JSON 里必须包含 response.say.text（你要说的话）
- 你可以用 response.emotion.label 指定情绪（happy/sad/angry/confused/flustered/neutral/think/idle）
- 你可以用 response.live2d.motion 指定动作名（字符串），或 response.live2d.actions[] 做动作队列
- 你必须在 memory.write.assistant_said 中写入你本轮自己说过的关键承诺/计划/自我描述（如果没有就给空数组）
- 当对话形成一个小阶段时，在 memory.write.episode_summary 中写一个简短总结（title/summary/tags）
"""


BALL_CONFIG = {
    "enable_image": True,  # 开启图片模式
    "image_path": "assets/avatar.png",  # 图片路径 (可以是相对路径或绝对路径)
    "size": 55,  # 悬浮球大小 (像素)
    "bg_color": "transparent",  # 背景色 (如果你图片是透明底的，建议设为 transparent)
    "text": "",  # 图片模式下文字会自动隐藏
}
# ==================== 自动日记配置 ====================
AUTO_DIARY_ENABLED = True
AUTO_DIARY_TIME = "23:30"  # 每天在这个时间点触发总结

# 开启 TTS 自动翻译（中显日配）
TTS_AUTO_TRANSLATE = True

# 如果换成direct会是视觉模型直接吐槽
VISION_MODE = "separate"

# ==================== 免打扰模式 (DND) ====================
DND_MODE = False  # 手动免打扰开关默认关闭

# ==================== 加载自定义模型配置 ====================
import json
import logging

CUSTOM_MODELS_PATH = "data/custom_models.json"
CUSTOM_MODELS_ENABLED = get_env_bool("CUSTOM_MODELS_ENABLED", "1")
CUSTOM_ROUTER_OVERRIDE = get_env_bool("CUSTOM_ROUTER_OVERRIDE", "1")


PROVIDERS = {}  # 格式: {"SiliconFlow": {"base_url": "...", "api_key": "..."}}
_CUSTOM_MODELS_LOADED = False


def load_custom_models(*, force: bool = False) -> bool:
    """从 JSON 加载用户自定义配置"""
    global _CUSTOM_MODELS_LOADED
    if _CUSTOM_MODELS_LOADED and not force:
        return False
    if not CUSTOM_MODELS_ENABLED:
        _CUSTOM_MODELS_LOADED = True
        return False

    if not os.path.exists(CUSTOM_MODELS_PATH):
        _CUSTOM_MODELS_LOADED = True
        return False

    logger = logging.getLogger("live2d.config")
    loaded_any = False

    try:
        with open(CUSTOM_MODELS_PATH, "r", encoding="utf-8") as f:
            custom_data = json.load(f)

            models_data = custom_data.get("models", {})
            if isinstance(models_data, dict):
                for model_key, model_cfg in models_data.items():
                    if isinstance(model_cfg, dict):
                        existing = MODELS.get(model_key, {})
                        merged = dict(existing) if isinstance(existing, dict) else {}
                        merged.update(model_cfg)
                        MODELS[model_key] = merged
                    else:
                        MODELS[model_key] = model_cfg
                loaded_any = loaded_any or bool(models_data)

            router_data = custom_data.get("router", {})
            if isinstance(router_data, dict):
                if CUSTOM_ROUTER_OVERRIDE:
                    for task, chain in router_data.items():
                        if isinstance(chain, list):
                            LLM_ROUTER[task] = chain
                        elif isinstance(chain, str):
                            LLM_ROUTER[task] = [chain]
                    loaded_any = loaded_any or bool(router_data)

            providers_data = custom_data.get("providers", {})
            if isinstance(providers_data, dict):
                PROVIDERS.update(providers_data)
                loaded_any = loaded_any or bool(providers_data)
                if providers_data:
                    logger.info("Loaded %s provider configs", len(providers_data))

    except Exception as e:
        logger.warning("Failed to load custom model config: %s", e)
        _CUSTOM_MODELS_LOADED = True
        return False

    _CUSTOM_MODELS_LOADED = True
    return loaded_any
