"""
学习进化系统 - 让角色在模拟基础上慢慢学习和进化
包含：价值观学习、用户偏好学习、适应性回应风格
"""
import json
import os
import threading
import random
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
import sqlite3

import logging

logger = None  # 延迟初始化

def _get_logger():
    """延迟获取logger实例"""
    global logger
    if logger is None:
        # 直接创建一个简单的控制台logger，不依赖 get_logger()
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.INFO)
        if not logger.handlers:  # 避免重复添加handler
            handler = logging.StreamHandler()
            handler.setLevel(logging.INFO)
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            logger.addHandler(handler)
    return logger


@dataclass
class PersonalityWeights:
    """性格权重 - 定义角色的个性倾向"""
    politeness: float = 0.5       # 礼貌程度 (0-1)
    humor: float = 0.3            # 幽默感 (0-1)
    seriousness: float = 0.7       # 严肃程度 (0-1)
    emotional: float = 0.4         # 情感表达 (0-1)
    curiosity: float = 0.6         # 好奇心 (0-1)
    patience: float = 0.7          # 耐心程度 (0-1)
    energy: float = 0.8            # 活力 (0-1)


@dataclass
class UserPreferences:
    """用户偏好 - 学习用户的喜好"""
    response_length: str = "medium"  # short/medium/long
    emoji_usage: float = 0.3         # 表情使用频率 (0-1)
    emotional_support: float = 0.5   # 情感支持需求 (0-1)
    technical_depth: float = 0.6     # 技术深度 (0-1)
    topic_interests: Dict[str, float] = None  # 话题兴趣度
    preferred_tones: List[str] = None         # 偏好的语气
    
    def __post_init__(self):
        if self.topic_interests is None:
            self.topic_interests = {}
        if self.preferred_tones is None:
            self.preferred_tones = ["gentle", "calm"]


@dataclass
class InteractionFeedback:
    """交互反馈 - 记录用户的反应"""
    timestamp: str
    user_input: str
    response: str
    emotion: str
    reaction: str  # positive/negative/neutral
    feedback_type: str  # explicit/explicit_negative/implicit_positive/implicit_negative


class LearningDatabase:
    """学习数据库 - 存储学习数据 (线程安全版)"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        parent_dir = os.path.dirname(os.path.abspath(self.db_path)) or "."
        os.makedirs(parent_dir, exist_ok=True)

        self._lock = threading.Lock()
        self.ready = False
        self._init_db()
        if not self.ready:
            raise RuntimeError(f"learning database initialization failed: {self.db_path}")

    def _init_db(self):
        """初始化数据库表"""
        try:
            with self._lock:  # 🟢 加锁
                with sqlite3.connect(self.db_path) as conn:
                    # ... (表结构定义保持不变) ...
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS personality_weights (
                            id INTEGER PRIMARY KEY,
                            weights TEXT NOT NULL,
                            updated_at TEXT NOT NULL,
                            version INTEGER DEFAULT 1
                        )
                    """)
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS user_preferences (
                            id INTEGER PRIMARY KEY,
                            preferences TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        )
                    """)
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS interaction_feedback (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            timestamp TEXT NOT NULL,
                            user_input TEXT,
                            response TEXT,
                            emotion TEXT,
                            reaction TEXT,
                            feedback_type TEXT
                        )
                    """)
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS topic_interests (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            topic TEXT NOT NULL,
                            interest_score REAL DEFAULT 0.5,
                            mention_count INTEGER DEFAULT 0,
                            last_mention TEXT
                        )
                    """)
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_timestamp ON interaction_feedback(timestamp)")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_topic_interest ON topic_interests(topic)")

                    self._ensure_default_weights(conn)
                    self._ensure_default_preferences(conn)
                    conn.commit()
            self.ready = True
            _get_logger().info("学习数据库初始化完成")
        except Exception as e:
            self.ready = False
            _get_logger().error(f"学习数据库初始化失败: {e}")

    def _ensure_default_weights(self, conn):
        """(内部辅助函数，由 _init_db 调用，不需要额外加锁)"""
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM personality_weights")
        if cursor.fetchone()[0] == 0:
            default_weights = PersonalityWeights()
            conn.execute("""
                INSERT INTO personality_weights (weights, updated_at, version)
                VALUES (?, ?, ?)
            """, (json.dumps(asdict(default_weights)), datetime.now().isoformat(), 1))

    def _ensure_default_preferences(self, conn):
        """(内部辅助函数，由 _init_db 调用，不需要额外加锁)"""
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM user_preferences")
        if cursor.fetchone()[0] == 0:
            default_prefs = UserPreferences()
            conn.execute("""
                INSERT INTO user_preferences (preferences, updated_at)
                VALUES (?, ?)
            """, (json.dumps(asdict(default_prefs)), datetime.now().isoformat()))

    def save_weights(self, weights: PersonalityWeights):
        """保存性格权重"""
        try:
            with self._lock:  # 🟢 加锁
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute("""
                        UPDATE personality_weights 
                        SET weights = ?, updated_at = ?
                        WHERE id = 1
                    """, (json.dumps(asdict(weights)), datetime.now().isoformat()))
                    conn.commit()
        except Exception as e:
            _get_logger().error(f"保存权重失败: {e}")

    def load_weights(self) -> PersonalityWeights:
        """加载性格权重"""
        try:
            with self._lock:  # 🟢 加锁
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT weights FROM personality_weights WHERE id = 1")
                    result = cursor.fetchone()
                    if result:
                        weights_dict = json.loads(result[0])
                        return PersonalityWeights(**weights_dict)
        except Exception as e:
            _get_logger().error(f"加载权重失败: {e}")
        return PersonalityWeights()

    def save_preferences(self, preferences: UserPreferences):
        """保存用户偏好"""
        try:
            with self._lock:  # 🟢 加锁
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute("""
                        UPDATE user_preferences 
                        SET preferences = ?, updated_at = ?
                        WHERE id = 1
                    """, (json.dumps(asdict(preferences)), datetime.now().isoformat()))
                    conn.commit()
        except Exception as e:
            _get_logger().error(f"保存偏好失败: {e}")

    def load_preferences(self) -> UserPreferences:
        """加载用户偏好"""
        try:
            with self._lock:  # 🟢 加锁
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT preferences FROM user_preferences WHERE id = 1")
                    result = cursor.fetchone()
                    if result:
                        prefs_dict = json.loads(result[0])
                        return UserPreferences(**prefs_dict)
        except Exception as e:
            _get_logger().error(f"加载偏好失败: {e}")
        return UserPreferences()

    def record_feedback(self, feedback: InteractionFeedback):
        """记录交互反馈"""
        try:
            with self._lock:  # 🟢 加锁
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute("""
                        INSERT INTO interaction_feedback 
                        (timestamp, user_input, response, emotion, reaction, feedback_type)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        feedback.timestamp, feedback.user_input, feedback.response,
                        feedback.emotion, feedback.reaction, feedback.feedback_type
                    ))
                    conn.commit()
        except Exception as e:
            _get_logger().error(f"记录反馈失败: {e}")

    def update_topic_interest(self, topic: str, delta: float):
        """更新话题兴趣度"""
        try:
            with self._lock:  # 🟢 加锁
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT interest_score, mention_count FROM topic_interests WHERE topic = ?",
                                   (topic,))
                    result = cursor.fetchone()

                    if result:
                        old_score, count = result
                        new_score = max(0.0, min(1.0, old_score + delta))
                        new_count = count + 1
                        conn.execute("""
                            UPDATE topic_interests 
                            SET interest_score = ?, mention_count = ?, last_mention = ?
                            WHERE topic = ?
                        """, (new_score, new_count, datetime.now().isoformat(), topic))
                    else:
                        conn.execute("""
                            INSERT INTO topic_interests (topic, interest_score, mention_count, last_mention)
                            VALUES (?, ?, ?, ?)
                        """, (topic, max(0.0, min(1.0, 0.5 + delta)), 1, datetime.now().isoformat()))

                    conn.commit()
        except Exception as e:
            _get_logger().error(f"更新话题兴趣失败: {e}")

    def get_topic_interests(self, limit: int = 20) -> Dict[str, float]:
        """获取话题兴趣度"""
        try:
            with self._lock:  # 🟢 加锁
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT topic, interest_score 
                        FROM topic_interests 
                        ORDER BY mention_count DESC, interest_score DESC 
                        LIMIT ?
                    """, (limit,))
                    return {row[0]: row[1] for row in cursor.fetchall()}
        except Exception as e:
            _get_logger().error(f"获取话题兴趣失败: {e}")
            return {}

    def get_recent_feedback(self, limit: int = 50) -> List[InteractionFeedback]:
        """获取最近的反馈"""
        try:
            with self._lock:  # 🟢 加锁
                with sqlite3.connect(self.db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT timestamp, user_input, response, emotion, reaction, feedback_type
                        FROM interaction_feedback
                        ORDER BY timestamp DESC
                        LIMIT ?
                    """, (limit,))

                    feedbacks = []
                    for row in cursor.fetchall():
                        feedbacks.append(InteractionFeedback(
                            timestamp=row["timestamp"], user_input=row["user_input"],
                            response=row["response"], emotion=row["emotion"],
                            reaction=row["reaction"], feedback_type=row["feedback_type"]
                        ))
                    return feedbacks
        except Exception as e:
            _get_logger().error(f"获取反馈失败: {e}")
            return []

    def reset_to_defaults(self):
        """清空学习数据并恢复默认值。"""
        try:
            with self._lock:
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute("DELETE FROM interaction_feedback")
                    conn.execute("DELETE FROM topic_interests")
                    conn.execute("DELETE FROM personality_weights")
                    conn.execute("DELETE FROM user_preferences")
                    self._ensure_default_weights(conn)
                    self._ensure_default_preferences(conn)
                    conn.commit()
            _get_logger().info("学习数据库已重置为默认值")
        except Exception as e:
            _get_logger().error(f"重置学习数据库失败: {e}")
            raise


class LearningSystem:
    """学习系统核心"""
    
    # 学习率 - 控制学习速度
    LEARNING_RATES = {
        "explicit": 0.1,              # 显式反馈：学习较快
        "implicit_positive": 0.03,    # 隐式正向：学习较慢
        "implicit_negative": 0.05     # 隐式负向：学习较慢
    }
    
    # 权重范围限制
    WEIGHT_BOUNDS = {
        "politeness": (0.2, 0.9),
        "humor": (0.1, 0.8),
        "seriousness": (0.3, 0.9),
        "emotional": (0.1, 0.8),
        "curiosity": (0.2, 0.9),
        "patience": (0.3, 0.9),
        "energy": (0.3, 1.0)
    }
    
    def __init__(self, db_path: str):
        self.db = LearningDatabase(db_path)
        self.weights = self.db.load_weights()
        self.preferences = self.db.load_preferences()
        self._interaction_count = 0
    
    def record_interaction(
        self,
        user_input: str,
        response: str,
        emotion: str,
        feedback_type: str = "neutral",
        reaction: str = "neutral"
    ):
        """记录一次交互"""
        self._interaction_count += 1
        
        feedback = InteractionFeedback(
            timestamp=datetime.now().isoformat(),
            user_input=user_input,
            response=response,
            emotion=emotion,
            reaction=reaction,
            feedback_type=feedback_type
        )
        
        self.db.record_feedback(feedback)
        
        # 如果有反馈，进行学习
        if feedback_type != "neutral":
            self._learn_from_feedback(feedback)
        
        # 分析并更新话题兴趣
        self._analyze_topics(user_input, response)
        
        _get_logger().debug(f"记录交互 #{self._interaction_count}, 反馈: {feedback_type}")
    
    def _learn_from_feedback(self, feedback: InteractionFeedback):
        """从反馈中学习"""
        learning_rate = self.LEARNING_RATES.get(feedback.feedback_type, 0.01)
        
        # 根据反馈调整权重
        if feedback.reaction == "positive":
            # 正向反馈：增强当前风格
            self._adjust_weights(feedback.emotion, learning_rate * 0.5)
        elif feedback.reaction == "negative":
            # 负向反馈：调整相反方向
            self._adjust_weights(feedback.emotion, -learning_rate)
        
        # 保存更新后的权重
        self.db.save_weights(self.weights)
    
    def _adjust_weights(self, emotion: str, delta: float):
        """调整权重"""
        # 根据情绪类型调整对应权重
        emotion_to_weight = {
            "happy": "humor",
            "sad": "emotional",
            "angry": "patience",
            "flustered": "emotional",
            "confused": "curiosity",
            "neutral": "politeness"
        }
        
        weight_key = emotion_to_weight.get(emotion, "politeness")
        
        if weight_key in self.weights.__dict__:
            current_value = getattr(self.weights, weight_key)
            min_val, max_val = self.WEIGHT_BOUNDS.get(weight_key, (0.0, 1.0))
            
            # 计算新值，确保在范围内
            new_value = max(min_val, min(max_val, current_value + delta))
            setattr(self.weights, weight_key, new_value)
            
            _get_logger().debug(f"调整权重 {weight_key}: {current_value:.2f} -> {new_value:.2f}")
    
    def _analyze_topics(self, user_input: str, response: str):
        """分析话题并更新兴趣度"""
        # 简单的关键词提取（可以用更复杂的NLP）
        keywords = self._extract_keywords(user_input)
        
        for keyword in keywords:
            # 话题出现次数越多，兴趣度略微增加
            delta = 0.02 if len(keyword) > 2 else 0.01
            self.db.update_topic_interest(keyword, delta)
    
    def _extract_keywords(self, text: str) -> List[str]:
        """提取关键词"""
        # 简单实现：提取2-4字的中文词汇
        import re
        # 匹配2-4个中文字符
        pattern = r'[\u4e00-\u9fa5]{2,4}'
        matches = re.findall(pattern, text)
        
        # 过滤常见停用词
        stopwords = {"这个", "那个", "什么", "怎么", "为什么", "可以", "应该", "不过", "就是", "还是"}
        keywords = [kw for kw in matches if kw not in stopwords]
        
        return list(set(keywords))  # 去重
    
    def get_adapted_response_style(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """获取适应性的回应风格"""
        style = {
            "politeness_level": self.weights.politeness,
            "humor_probability": self.weights.humor,
            "seriousness_level": self.weights.seriousness,
            "emotional_level": self.weights.emotional,
            "response_length": self.preferences.response_length,
            "emoji_probability": self.preferences.emoji_usage,
        }
        
        # 基于当前时间段调整
        hour = datetime.now().hour
        if 22 <= hour or hour < 6:
            # 深夜：更温柔，少幽默
            style["politeness_level"] = min(1.0, style["politeness_level"] + 0.1)
            style["humor_probability"] = max(0.0, style["humor_probability"] - 0.2)
        elif 9 <= hour < 18:
            # 白天：更活跃
            style["energy_level"] = self.weights.energy
        
        # 基于话题兴趣调整
        topics = self.db.get_topic_interests(limit=5)
        if topics:
            style["relevant_topics"] = list(topics.keys())
        
        return style
    
    def get_learning_progress(self) -> Dict[str, Any]:
        """获取学习进度"""
        return {
            "interaction_count": self._interaction_count,
            "personality_weights": asdict(self.weights),
            "user_preferences": asdict(self.preferences),
            "top_topics": self.db.get_topic_interests(limit=10),
            "recent_feedback_count": len(self.db.get_recent_feedback(limit=100))
        }
    
    def get_character_state(self) -> str:
        """获取角色当前状态描述"""
        weights = self.weights
        
        # 生成性格描述
        if weights.humor > 0.6 and weights.emotional > 0.5:
            personality = "活泼开朗"
        elif weights.seriousness > 0.7 and weights.patience > 0.6:
            personality = "认真耐心"
        elif weights.emotional > 0.6 and weights.politeness > 0.6:
            personality = "温柔礼貌"
        elif weights.curiosity > 0.7:
            personality = "好奇心强"
        else:
            personality = "温和自然"
        
        # 生成活力描述
        if weights.energy > 0.8:
            energy = "充满活力"
        elif weights.energy > 0.6:
            energy = "精神不错"
        else:
            energy = "比较平静"
        
        return f"性格: {personality}, 状态: {energy}, 交互次数: {self._interaction_count}"


# 全局实例
_learning_system = None
_learning_system_lock = threading.Lock()


def get_learning_system(db_path: str = None) -> LearningSystem:
    """获取学习系统实例"""
    global _learning_system
    with _learning_system_lock:
        if _learning_system is None:
            if db_path is None:
                from config import MEMORY_DB_PATH
                db_path = os.path.join(MEMORY_DB_PATH, "learning.db")

            _learning_system = LearningSystem(db_path)

        return _learning_system


def reset_learning_system(db_path: str = None, remove_db: bool = False) -> LearningSystem:
    """重置全局学习系统单例，并返回新实例。"""
    global _learning_system
    with _learning_system_lock:
        if db_path is None:
            from config import MEMORY_DB_PATH
            db_path = os.path.join(MEMORY_DB_PATH, "learning.db")

        parent_dir = os.path.dirname(os.path.abspath(db_path)) or "."
        os.makedirs(parent_dir, exist_ok=True)

        _learning_system = None
        if remove_db and os.path.exists(db_path):
            try:
                os.remove(db_path)
            except PermissionError:
                _get_logger().warning(f"删除学习数据库失败（文件占用），改为原地重置: {db_path}")
                LearningDatabase(db_path).reset_to_defaults()

        _learning_system = LearningSystem(db_path)
        return _learning_system
