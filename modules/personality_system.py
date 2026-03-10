"""
个性化系统 - 增强角色的智能和活人感
包含：时间感知、思考延迟、自言自语、情绪连贯性等
"""
import asyncio
import random
from datetime import datetime, time as dt_time
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field

from core.logger import get_logger

def _get_logger():
    """延迟获取 logger 实例"""
    return get_logger()

logger = None


@dataclass
class TimeContext:
    """时间上下文"""
    period: str  # morning, noon, afternoon, evening, night, late_night
    greeting: str
    tone: str
    topics: List[str]
    emotion_bias: str


@dataclass
class PersonalityState:
    """个性化状态"""
    current_mood: str = "normal"  # normal, good, tired, excited, concerned
    energy_level: int = 100  # 0-100
    social_mode: str = "casual"  # casual, work, emotional_support
    thinking_mode: bool = False
    last_thought_time: float = 0.0


class TimeAwareness:
    """时间感知系统"""
    
    TIME_CONTEXTS = {
        "early_morning": {  # 5:00-6:00
            "period": "early_morning",
            "greeting": ["这么早起来了吗", "早安"],
            "tone": "温和",
            "topics": ["晨练", "早餐", "今日计划"],
            "emotion_bias": "calm"
        },
        "morning": {  # 6:00-9:00
            "period": "morning",
            "greeting": ["早上好", "早安"],
            "tone": "温和",
            "topics": ["早餐", "今日计划", "天气"],
            "emotion_bias": "neutral"
        },
        "late_morning": {  # 9:00-12:00
            "period": "late_morning",
            "greeting": ["上午好"],
            "tone": "积极",
            "topics": ["工作", "学习", "计划"],
            "emotion_bias": "good"
        },
        "noon": {  # 12:00-14:00
            "period": "noon",
            "greeting": ["中午好"],
            "tone": "放松",
            "topics": ["午餐", "休息", "午休"],
            "emotion_bias": "calm"
        },
        "afternoon": {  # 14:00-18:00
            "period": "afternoon",
            "greeting": ["下午好"],
            "tone": "积极",
            "topics": ["工作", "学习", "效率"],
            "emotion_bias": "neutral"
        },
        "evening": {  # 18:00-22:00
            "period": "evening",
            "greeting": ["晚上好"],
            "tone": "柔和",
            "topics": ["晚餐", "今日总结", "放松", "休息"],
            "emotion_bias": "gentle"
        },
        "night": {  # 22:00-0:00
            "period": "night",
            "greeting": ["这么晚了"],
            "tone": "关心",
            "topics": ["休息", "明天计划", "早点睡"],
            "emotion_bias": "concerned"
        },
        "late_night": {  # 0:00-5:00
            "period": "late_night",
            "greeting": ["这么晚还没睡吗"],
            "tone": "担心",
            "topics": ["休息", "健康"],
            "emotion_bias": "worried"
        },
    }
    
    @classmethod
    def get_current_context(cls) -> TimeContext:
        """获取当前时间上下文"""
        now = datetime.now()
        hour = now.hour
        
        if 5 <= hour < 6:
            ctx = cls.TIME_CONTEXTS["early_morning"]
        elif 6 <= hour < 9:
            ctx = cls.TIME_CONTEXTS["morning"]
        elif 9 <= hour < 12:
            ctx = cls.TIME_CONTEXTS["late_morning"]
        elif 12 <= hour < 14:
            ctx = cls.TIME_CONTEXTS["noon"]
        elif 14 <= hour < 18:
            ctx = cls.TIME_CONTEXTS["afternoon"]
        elif 18 <= hour < 22:
            ctx = cls.TIME_CONTEXTS["evening"]
        elif 22 <= hour < 24:
            ctx = cls.TIME_CONTEXTS["night"]
        else:  # 0-5
            ctx = cls.TIME_CONTEXTS["late_night"]
        
        return TimeContext(
            period=ctx["period"],
            greeting=random.choice(ctx["greeting"]),
            tone=ctx["tone"],
            topics=ctx["topics"],
            emotion_bias=ctx["emotion_bias"]
        )
    
    @classmethod
    def get_greeting(cls) -> str:
        """获取适合的问候语"""
        ctx = cls.get_current_context()
        return ctx.greeting


class ThinkingSimulator:
    """思考模拟系统 - 增加真实感"""
    
    SELF_TALK_PATTERNS = [
        "嗯……让我想想",
        "这个……有点复杂",
        "如果是这样的话……",
        "我想起来了……",
        "不对，应该是……",
        "等一下……",
        "唔……",
        "让我考虑一下"
    ]
    
    # 思考延迟时间（秒）
    DELAY_RANGES = {
        "simple": (0.3, 0.8),      # 简单问题
        "medium": (0.8, 1.8),     # 中等问题
        "complex": (1.5, 3.0),    # 复杂问题
        "very_complex": (2.5, 5.0) # 非常复杂
    }
    
    def __init__(self):
        self._last_think_time = 0.0
    
    def estimate_complexity(self, user_text: str) -> str:
        """估算问题复杂度"""
        text = user_text.strip()
        length = len(text)
        
        # 简单问题特征
        simple_patterns = ["你好", "天气", "几点", "在吗", "早上好", "晚上好"]
        if any(p in text for p in simple_patterns):
            return "simple"
        
        # 复杂问题特征
        complex_patterns = ["为什么", "怎么做", "如何", "分析", "解释", "建议", "帮我想想"]
        if any(p in text for p in complex_patterns):
            return "complex"
        
        # 非常复杂问题特征
        very_complex_patterns = ["帮我写", "设计", "规划", "详细", "全面"]
        if any(p in text for p in very_complex_patterns):
            return "very_complex"
        
        # 默认中等
        return "medium"
    
    async def think_before_respond(self, user_text: str, callback=None) -> Optional[str]:
        """在回复前模拟思考"""
        complexity = self.estimate_complexity(user_text)
        delay_range = self.DELAY_RANGES[complexity]
        delay = random.uniform(*delay_range)
        
        # 简单问题5%概率不思考，快速回复
        if complexity == "simple" and random.random() < 0.05:
            return None
        
        # 有自言自语的概率
        show_self_talk = complexity in ["complex", "very_complex"] and random.random() < 0.08
        
        if show_self_talk:
            self_talk = random.choice(self.SELF_TALK_PATTERNS)
            _get_logger().debug(f"自言自语: {self_talk}")
            if callback:
                await callback(self_talk, "think")
            # 自言自语后等待更短时间
            delay *= 0.6
        
        _get_logger().debug(f"思考延迟: {delay:.2f}秒 (复杂度: {complexity})")
        await asyncio.sleep(delay)
        
        return show_self_talk
    
    def get_thinking_emotion(self) -> str:
        """获取思考时的情绪"""
        return "think"


class EmotionContinuity:
    """情绪连贯性 - 避免情绪剧烈跳跃"""
    
    def __init__(self):
        self.emotion_history: List[str] = []
        self.max_history = 5
        self.current_mood = "neutral"
    
    def adjust_emotion(self, new_emotion: str, intensity: float) -> tuple:
        """调整情绪，确保连贯性"""
        # 记录历史
        self.emotion_history.append(new_emotion)
        if len(self.emotion_history) > self.max_history:
            self.emotion_history.pop(0)
        
        # 如果情绪相同，直接返回
        if new_emotion == self.current_mood:
            return new_emotion, intensity
        
        # 情绪距离检查
        emotion_groups = {
            "positive": ["happy", "excited"],
            "negative": ["sad", "angry", "worried"],
            "neutral": ["neutral", "calm", "gentle", "flustered", "confused", "think"]
        }
        
        # 找到两个情绪所属的组
        current_group = None
        new_group = None
        for group, emotions in emotion_groups.items():
            if self.current_mood in emotions:
                current_group = group
            if new_emotion in emotions:
                new_group = group
        
        # 如果跨组切换，需要过渡
        if current_group and new_group and current_group != new_group:
            # 使用当前情绪作为过渡，降低强度
            _get_logger().debug(f"情绪过渡: {self.current_mood} -> {new_emotion} (通过{self.current_mood})")
            self.current_mood = new_emotion
            return self.current_mood, intensity * 0.7
        
        # 同组切换，可以较快切换
        self.current_mood = new_emotion
        return new_emotion, intensity
    
    def get_mood(self) -> str:
        """获取当前心情"""
        return self.current_mood


class CharacterSharing:
    """角色分享系统 - 让角色有自己的想法和经历"""
    
    SHARING_PATTERNS = {
        "weather": [
            "今天天气不错呢，阳光很好",
            "外面好像在下雨，听着挺舒服的",
            "今天风挺大的",
            "看着天空，心情都变好了"
        ],
        "observation": [
            "刚才看到一只小鸟，挺可爱的",
            "发现了一个有趣的事情",
            "注意到窗外的景色很美",
            "刚才在想一些事情"
        ],
        "thought": [
            "有时候在想，对话也挺有意思的",
            "希望能帮到你",
            "每次和你聊天都学到新东西",
            "感觉我们越来越熟悉了"
        ],
        "recommendation": [
            "你可以试试听听音乐放松一下",
            "有空的时候多出去走走吧",
            "记得按时休息",
            "喝水很重要哦"
        ]
    }
    
    @classmethod
    def try_share(cls, probability: float = 0.08) -> Optional[str]:
        """尝试分享（概率性）"""
        if random.random() > probability:
            return None
        
        category = random.choice(list(cls.SHARING_PATTERNS.keys()))
        message = random.choice(cls.SHARING_PATTERNS[category])
        _get_logger().debug(f"角色分享: {category} - {message}")
        return message


class PersonalitySystem:
    """个性化系统核心"""
    
    def __init__(self):
        self.state = PersonalityState()
        self.time_awareness = TimeAwareness()
        self.thinking_simulator = ThinkingSimulator()
        self.emotion_continuity = EmotionContinuity()
        self.character_sharing = CharacterSharing()
    
    def update_state(self):
        """更新状态"""
        # 根据时间更新心情
        hour = datetime.now().hour
        if 22 <= hour or hour < 6:
            self.state.energy_level = max(50, self.state.energy_level - 5)
            if self.state.energy_level < 70:
                self.state.current_mood = "tired"
        elif 9 <= hour < 18:
            self.state.energy_level = min(100, self.state.energy_level + 3)
            if self.state.energy_level > 80:
                self.state.current_mood = "good"
        else:
            self.state.current_mood = "normal"
    
    def get_time_context(self) -> TimeContext:
        """获取时间上下文"""
        return self.time_awareness.get_current_context()
    
    def get_greeting(self) -> str:
        """获取问候语"""
        return self.time_awareness.get_greeting()
    
    async def think_before_respond(self, user_text: str, callback=None) -> Optional[str]:
        """思考后回复"""
        return await self.thinking_simulator.think_before_respond(user_text, callback)
    
    def adjust_emotion(self, emotion: str, intensity: float) -> tuple:
        """调整情绪"""
        return self.emotion_continuity.adjust_emotion(emotion, intensity)
    
    def try_share(self) -> Optional[str]:
        """尝试分享"""
        return self.character_sharing.try_share()
    
    def get_state(self) -> Dict[str, Any]:
        """获取当前状态"""
        return {
            "mood": self.state.current_mood,
            "energy": self.state.energy_level,
            "social_mode": self.state.social_mode,
            "thinking": self.state.thinking_mode
        }


# 全局实例
personality_system = PersonalitySystem()


def get_personality_system() -> PersonalitySystem:
    """获取个性化系统实例"""
    return personality_system
