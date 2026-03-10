# file: modules/listener_plugin.py

import asyncio
from typing import List, Dict

# 复用你项目中的 LLM 调用函数和角色设定
from modules.llm import chat_with_ai
from config import WAKE_KEYWORDS, DEFAULT_PERSONA
# 🟢 [新增] 引入 CharacterManager 以获取动态人设
try:
    from modules.character_manager import character_manager
except ImportError:
    character_manager = None


class ListenerPlugin:
    """
    一个“注意力看门人”插件，用于在监听模式下判断是否应该响应。
    """

    def __init__(self, brain):
        self.brain = brain
        # 唤醒词支持大小写不敏感
        self.agent_names = [name.lower() for name in (WAKE_KEYWORDS or [])]
        print(f"🤫 [ListenerPlugin] 已启动，将监听唤醒词: {self.agent_names}")

    def _get_active_persona(self) -> str:
        """获取当前激活角色的 Prompt"""
        if character_manager:
            char = character_manager.get_active_character()
            if char and char.get("prompt"):
                return char["prompt"]
        return DEFAULT_PERSONA

    async def should_reply(self, text: str) -> bool:
        """
        核心判断函数。
        Returns: True 如果应该回复，False 则忽略。
        """
        if not text or len(text.strip()) < 2:
            return False

        lower_text = text.lower()

        # 规则 1：如果包含唤醒词/自己的名字，立即响应
        if any(name in lower_text for name in self.agent_names):
            print("💡 [ListenerPlugin] 判断：应该回复 (直接提及)。")
            return True

        # 规则 2：如果上一条消息是自己发的，默认不回复，避免自我对话
        short_term_memory = self.brain.short_term_memory
        # 兼容 deque 或 list
        if short_term_memory:
            last_msg = short_term_memory[-1]
            if last_msg.get("role") == "assistant":
                print("🤫 [ListenerPlugin] 判断：不回复 (避免自我对话)。")
                return False

        # 规则 3：使用轻量级 LLM 进行智能判断
        # 获取上下文
        history_context = list(short_term_memory)[-5:] if short_term_memory else []
        history_str = "\n".join([f"- {msg.get('role')}: {msg.get('content')}" for msg in history_context])

        # 🟢 [修改] 使用动态获取的人设
        current_persona = self._get_active_persona()

        prompt = f"""
你是一个AI助手的“注意力过滤器”。任务：判断AI是否应该回复监听到的“最新消息”。

【回复规则】
1. 如果消息是在叫AI名字或直接提问，必须回复。
2. 如果消息与AI角色设定高度相关，可以回复。
3. 如果是无关的背景噪音、闲聊或其他人对话，不要回复。
4. 如果上一句是AI自己说的，绝对不要回复。

【当前AI角色设定】
{current_persona[:500]}... (截取部分)

【最近对话】
{history_str}

【最新消息】
"{text}"

---
判断AI是否应该回复？只输出 "yes" 或 "no"。
"""
        messages = [{"role": "system", "content": prompt}]

        try:
            # 使用同步的 chat_with_ai 函数，但在异步环境中运行它
            loop = asyncio.get_running_loop()
            decision = await loop.run_in_executor(
                None,
                chat_with_ai,
                messages,
                "gatekeeper"  # 使用 gatekeeper 节省成本
            )

            decision = (decision or "no").strip().lower()
            print(f"💡 [ListenerPlugin] LLM 判断结果: '{decision}'")

            if "yes" in decision:
                return True

            return False

        except Exception as e:
            print(f"⚠️ [ListenerPlugin] LLM 判断失败: {e}")
            return False