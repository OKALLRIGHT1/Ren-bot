# modules/advanced_memory.py
import os
import re
import json
import time
import uuid
import hashlib
import itertools
import threading
from datetime import datetime, timezone
from modules.memory_sqlite import get_memory_store
from concurrent.futures import ThreadPoolExecutor
import networkx as nx
import jieba
import jieba.analyse
import chromadb
from chromadb.utils import embedding_functions
import requests
from chromadb import EmbeddingFunction, Documents, Embeddings
from config import SYSTEM_RULES_PROMPT, DEFAULT_PERSONA
from modules.character_manager import character_manager
from config import MEMORY_DB_PATH, EMBEDDING_CONFIG, MEMORY_SETTINGS

from core.logger import get_logger

# 可选：用于 LLM 记忆筛选（复用你现有 llm.py 的 chat_with_ai）
# 只在 MEMORY_SETTINGS["use_llm_selector"]=True 时才会调用
try:
    from modules.llm import chat_with_ai
except Exception:
    chat_with_ai = None




# ========= 1) 远程嵌入函数（保持你原来的方式） =========
class RemoteBGEFunction(EmbeddingFunction):
    """远程嵌入函数（增强版）"""

    def __init__(self, api_url, api_key, model_name, fallback_fn=None, timeout=12, max_retries=2):
        self._logger = get_logger()
        if self._logger is None:
            import logging
            self._logger = logging.getLogger(__name__)
            self._logger.setLevel(logging.INFO)
            if not self._logger.handlers:
                handler = logging.StreamHandler()
                handler.setFormatter(logging.Formatter(
                    '%(asctime)s [%(levelname)s] %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S'
                ))
                self._logger.addHandler(handler)

        # ✅ 并发安全：添加线程锁
        self._lock = threading.Lock()

        # ✅ 性能优化：查询结果缓存
        self._query_cache = {}
        self._cache_ttl = 300
        self._cache_hits = 0
        self._cache_misses = 0

        self.api_url = api_url
        self.api_key = api_key
        self.model_name = model_name
        self.fallback_fn = fallback_fn
        self.timeout = timeout
        self.max_retries = max(1, int(max_retries))
        self._last_dim = None

    def __call__(self, input: Documents) -> Embeddings:
        """生成嵌入向量（线程安全版本）"""
        # ✅ 使用锁保护 API 调用
        with self._lock:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            payload = {"input": input, "model": self.model_name}

            last_err = None
            for attempt in range(self.max_retries):
                try:
                    response = requests.post(
                        self.api_url,
                        json=payload,
                        headers=headers,
                        timeout=self.timeout
                    )

                    if response.status_code == 200:
                        data = response.json()
                        if isinstance(data, dict) and isinstance(data.get("data"), list) and data["data"]:
                            embs = [item.get("embedding") for item in data["data"]]
                            embs = [e for e in embs if isinstance(e, list) and e]

                            if embs:
                                self._last_dim = len(embs[0])

                            if len(embs) == len(input):
                                return embs

                            last_err = f"length mismatch: got {len(embs)} expect {len(input)}"
                        else:
                            last_err = f"invalid response: {data}"
                    else:
                        last_err = f"HTTP {response.status_code}: {response.text[:100]}"

                except Exception as e:
                    last_err = str(e)

                # ✅ 重试前等待
                if attempt < self.max_retries - 1:
                    time.sleep(0.2 * (attempt + 1))

            self._logger.warning(f"⚠️ 嵌入接口失败: {last_err}")

            # 1) fallback：本地 embedding
            if self.fallback_fn is not None:
                try:
                    embs = self.fallback_fn(input)
                    if isinstance(embs, list) and len(embs) == len(input) and embs:
                        self._logger.info(f"✅ 使用本地 fallback embedding")
                        return embs
                except Exception as e:
                    self._logger.warning(f"⚠️ fallback embedding 失败: {e}")

            # 2) 零向量兜底
            dim = self._last_dim or 1024
            self._logger.warning(f"⚠️ 使用零向量兜底 (dim={dim})")
            return [[0.0] * dim for _ in input]


# ========= 2) Profile（用户档案：稳定事实单独存） =========
# modules/advanced_memory.py 中的 ProfileStore 类

class ProfileStore:
    """
    用户与助手的静态档案管理 (JSON)
    支持双角色 (user/agent) 和动态 Likes 分类
    """

    def __init__(self, path: str):
        self.path = path
        # 默认结构：必须包含 user 和 agent
        self.data = {
            "user": {
                "name": "Master",
                "likes": {"general": []},
                "dislikes": [],
                "status": [],
                "notes": []
            },
            "agent": {
                "name": "Suzu",
                "likes": {"general": []},
                "dislikes": [],
                "traits": []
            },
            "updated_at": None,
        }
        self.load()

    def load(self):
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    # 深度合并，防止某些 key 丢失
                    for role in ["user", "agent"]:
                        if role in loaded:
                            # 如果 likes 是旧的列表格式，转为字典
                            if "likes" in loaded[role] and isinstance(loaded[role]["likes"], list):
                                loaded[role]["likes"] = {"general": loaded[role]["likes"]}

                            self.data[role].update(loaded[role])

                    self.data["updated_at"] = loaded.get("updated_at")
        except Exception as e:
            print(f"⚠️ [Profile] 加载失败: {e}")

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            self.data["updated_at"] = datetime.now(timezone.utc).isoformat()
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ [Profile] 保存失败: {e}")

    def extract_and_update(self, role: str, text: str):
        """
        使用 LLM 从对话中提取用户/Agent的画像信息 (喜好/状态/性格等)
        """
        if not chat_with_ai:
            return

        # 1. 过滤：太短的句子不提取
        t = (text or "").strip()
        if len(t) < 4:
            return

        # 2. 过滤：如果是指令或系统消息
        if t.startswith("[") or t.startswith("System:"):
            return

        # 3. 构造 Prompt
        target_role_desc = "用户(User)" if role == "user" else "五十铃怜(Assistant)"

        prompt = f"""
Analyze the following conversation snippet.
Speaker Role: {target_role_desc}
Speaker's Words: "{t}"

Task: Extract facts about the Speaker into a STRUCTURED JSON.
Structure Requirement:
- "likes": A dictionary with sub-categories:
    - "music": Songs, artists, bands, genres
    - "games": Game titles, platforms, types
    - "food": Food, drinks, flavors
    - "general": Hobbies, habits, colors, or anything else
- "dislikes": List of things hated
- "status": List of current activities (e.g. "writing patent")
- "name": String
- "traits": List of personality traits (only if Speaker is Assistant)

Rules:
1. Classify carefully. "Elden Ring" -> likes.games, "MyGO" -> likes.music.
2. Output JSON ONLY.
Example: {{"likes": {{"music": ["MyGO"], "games": ["Minecraft"]}}, "status": ["busy"]}}
"""
        try:
            # 4. 调用 LLM (使用 summary 或 gatekeeper 模型以节省成本)
            response = chat_with_ai(
                [{"role": "user", "content": prompt}],
                task_type="summary",
                caller="profile_extract",
            )

            # 5. 解析结果
            m = re.search(r"\{.*\}", response, flags=re.DOTALL)
            if m:
                data = json.loads(m.group(0))
                if not data: return

                target_key = "user" if role == "user" else "agent"
                has_update = False

                # A. 处理 Name
                # A. 处理 Name (加固版)
                if "name" in data and data["name"]:
                    new_name = str(data["name"]).strip()
                    current_name = self.data[target_key].get("name")

                    # 黑名单：这些绝对不是名字
                    bad_names = ["user", "User", "USER", "用户", "unknown", "Unknown", "None", "我", "自己"]
                    # 注意：如果您希望它叫您Master，可以把Master从黑名单去掉。
                    # 但通常建议名字手动改 json，不要让 AI 自动改，防止它改回 "User"

                    is_bad = new_name in bad_names or len(new_name) < 2

                    # 只有当：新名字不在黑名单 AND (当前没名字 OR 当前名字是默认User) 时，才允许改
                    # 意思是：一旦您手动改成了 "Soyo"，AI 就再也改不动它了
                    if not is_bad:
                        if (not current_name) or (current_name in ["user", "User", "用户"]):
                            self.data[target_key]["name"] = new_name
                            has_update = True
                            print(f"📝 [Profile] 自动捕获名字: {new_name}")
                        else:
                            # 如果已有名字且不同，记录日志但不覆盖
                            if current_name != new_name:
                                print(f"🛡️ [Profile] 拦截名字覆盖: {current_name} -> {new_name} (已忽略)")

                # B. 处理 Likes (嵌套字典)
                if "likes" in data and isinstance(data["likes"], dict):
                    # 确保目标也是字典
                    if not isinstance(self.data[target_key].get("likes"), dict):
                        self.data[target_key]["likes"] = {"music": [], "games": [], "food": [], "general": []}

                    for category, items in data["likes"].items():
                        # 只允许白名单分类
                        if category not in ["music", "games", "food", "general"]:
                            category = "general"

                        if isinstance(items, list):
                            current_list = self.data[target_key]["likes"].get(category, [])
                            for item in items:
                                if item not in current_list and len(item) < 20:
                                    current_list.append(item)
                                    # 限制长度
                                    limit = 50 if category in ["music", "games"] else 30
                                    if len(current_list) > limit: current_list.pop(0)

                                    # 写回
                                    self.data[target_key]["likes"][category] = current_list
                                    has_update = True
                                    print(f"📝 [Profile] 新增档案 ({target_key}.likes.{category}): {item}")

                # C. 处理 Dislikes / Status / Traits (普通列表)
                for field in ["dislikes", "status", "traits"]:
                    if field in data and isinstance(data[field], list):
                        current_list = self.data[target_key].get(field, [])
                        for item in data[field]:
                            if item not in current_list and len(item) < 20:
                                current_list.append(item)
                                if len(current_list) > 20: current_list.pop(0)
                                self.data[target_key][field] = current_list
                                has_update = True
                                print(f"📝 [Profile] 新增档案 ({target_key}.{field}): {item}")

                if has_update:
                    self.save()

        except Exception as e:
            print(f"⚠️ [Profile] 提取失败: {e}")

    def format_for_prompt(self) -> str:
        out = []

        # 🟢 辅助函数：专门用来格式化一个人
        def _format_one_role(role_key, display_name):
            data = self.data.get(role_key, {})
            lines = []

            # 1. 名字 (核心称呼强化)
            name = data.get("name")
            if name:
                # 🟡 修改点：如果是用户，直接强制要求 AI 使用该称呼
                if role_key == "user":
                    lines.append(f"【称呼指引】你必须称呼对方为：{name}")
                else:
                    lines.append(f"- {display_name}称呼/名字：{name}")

            # 2. 状态 (安全处理列表切片)
            status = data.get("status")
            if isinstance(status, list) and status:
                lines.append(f"- {display_name}当前状态：{'、'.join(status[-3:])}")

            # 3. 喜好 (分类展示，兼容字典)
            likes = data.get("likes", {})
            if isinstance(likes, dict):
                if likes.get("music"):
                    lines.append(f"- [{display_name}喜好] 音乐：{'、'.join(likes['music'][-8:])}")
                if likes.get("games"):
                    lines.append(f"- [{display_name}喜好] 游戏：{'、'.join(likes['games'][-5:])}")
                if likes.get("food"):
                    lines.append(f"- [{display_name}喜好] 食物：{'、'.join(likes['food'][-5:])}")
                if likes.get("general"):
                    lines.append(f"- [{display_name}喜好] 其他：{'、'.join(likes['general'][-5:])}")

            # 4. 讨厌
            dislikes = data.get("dislikes")
            if isinstance(dislikes, list) and dislikes:
                lines.append(f"- {display_name}雷点/讨厌：{'、'.join(dislikes[-5:])}")

            # 5. 性格 (Agent 独有)
            traits = data.get("traits")
            if isinstance(traits, list) and traits:
                lines.append(f"- {display_name}性格标签：{'、'.join(traits[-5:])}")

            return lines

        # === 生成 User 部分 ===
        # 🟢 将 display_name 改为 "Master"，即便没搜到名字，也会以此兜底
        out.extend(_format_one_role("user", "Master"))

        # === 生成 Agent 部分 ===
        agent_lines = _format_one_role("agent", "我")

        if agent_lines:
            out.append("\n【五十铃怜的记忆备忘录 (Agent Profile)】")
            out.extend(agent_lines)

        return "\n".join(out)


# ========= 3) 图记忆（关键词关系） =========
class GraphMemory:
    def __init__(self, graph_file="graph.json"):
        self.graph_file = os.path.join(MEMORY_DB_PATH, graph_file)
        self.G = nx.Graph()
        self.stopwords = set(["什么", "怎么", "为什么", "因为", "就是", "然后", "但是", "如果", "我们", "你们", "他们"])
        self.last_decay_day = None

        # ✅ 性能优化：添加关键词关联缓存
        self._related_cache = {}
        self._cache_ttl = 600  # 10分钟缓存
        self._cache_hits = 0
        self._cache_misses = 0

        self.load_graph()

    def load_graph(self):
        if os.path.exists(self.graph_file):
            try:
                with open(self.graph_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # networkx 兼容：node_link_graph 的 edges key 在不同版本会不一样
                try:
                    self.G = nx.node_link_graph(data, edges="links")
                except Exception:
                    self.G = nx.node_link_graph(data)
            except Exception:
                self.G = nx.Graph()

    def _apply_decay_if_needed(self):
        decay = float(MEMORY_SETTINGS.get("graph_decay_per_day", 1.0))
        if decay >= 0.9999:
            return
        today = datetime.now().date().isoformat()
        if self.last_decay_day == today:
            return
        self.last_decay_day = today

        # 对边权做衰减
        for u, v, d in list(self.G.edges(data=True)):
            w = float(d.get("weight", 1.0))
            w *= decay
            if w < 0.05:
                try:
                    self.G.remove_edge(u, v)
                except Exception:
                    pass
            else:
                self.G[u][v]["weight"] = w

    def save_graph(self):
        os.makedirs(os.path.dirname(self.graph_file), exist_ok=True)  # ✅ 确保目录存在
        self._apply_decay_if_needed()
        data = nx.node_link_data(self.G, edges="links")
        with open(self.graph_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def maybe_apply_decay(self):
        """在启动时或定时调用"""
        self._apply_decay_if_needed()
        self.save_graph()

    def add_concept_link(self, keyword1, keyword2):
        if keyword1 == keyword2:
            return

        cap = int(MEMORY_SETTINGS.get("graph_edge_cap", 12))

        if self.G.has_edge(keyword1, keyword2):
            w = float(self.G[keyword1][keyword2].get("weight", 1.0))
            self.G[keyword1][keyword2]["weight"] = min(w + 1.0, cap)
        else:
            self.G.add_edge(keyword1, keyword2, weight=1.0)

        self.save_graph()

    def get_related_keywords(self, start_keywords, depth=2, top_k=5):
        # ✅ 性能优化：检查缓存
        cache_key = f"{','.join(sorted(start_keywords))}:{depth}:{top_k}"
        if cache_key in self._related_cache:
            cached_time, cached_result = self._related_cache[cache_key]
            if time.time() - cached_time < self._cache_ttl:
                self._cache_hits += 1
                return cached_result

        self._cache_misses += 1

        activated_nodes = {}
        start_keywords = [k for k in start_keywords if k and k not in self.stopwords]

        for kw in start_keywords:
            if kw in self.G:
                activated_nodes[kw] = 1.0

        # ✅ 性能优化：使用 NetworkX 的优化算法
        for kw in start_keywords:
            if kw in self.G:
                # ✅ 限制子图大小，避免计算过大
                degree = self.G.degree(kw)
                if degree > 50:
                    # 如果节点连接太多，限制半径并使用子图
                    subgraph = nx.ego_graph(self.G, kw, radius=1)
                    # 使用 PageRank 算法评估节点重要性
                    pr = nx.pagerank(subgraph, max_iter=50, tol=1e-6)
                    activated_nodes.update({k: v for k, v in pr.items() if v > 0.01})
                else:
                    # 正常扩散
                    current_layer = [kw]
                    for _ in range(depth):
                        next_layer = []
                        for node in current_layer:
                            if node not in self.G:
                                continue
                            score = activated_nodes[node]
                            if score < 0.2:
                                continue
                            for neighbor in self.G.neighbors(node):
                                if neighbor in self.stopwords:
                                    continue
                                edge_weight = float(self.G[node][neighbor].get("weight", 1.0))
                                transfer = score * 0.5 * (1 - 1 / (edge_weight + 1))
                                if neighbor not in activated_nodes:
                                    activated_nodes[neighbor] = 0.0
                                    next_layer.append(neighbor)
                                activated_nodes[neighbor] += transfer
                        current_layer = next_layer

        result = sorted(activated_nodes.items(), key=lambda x: x[1], reverse=True)
        filtered = [k for k, v in result if k not in start_keywords][:top_k]

        # ✅ 性能优化：缓存结果
        self._related_cache[cache_key] = (time.time(), filtered)

        return filtered




class AdvancedMemorySystem:
    def __init__(self):
        self._lock = threading.Lock()
        # 默认 2，后续会从 MEMORY_SETTINGS 覆盖
        self.recall_min_chars = 2

        # 🟢 [修正] ThreadPoolExecutor 来自 concurrent.futures，不是 threading
        # max_workers=1 保证写入顺序，避免并发写入导致时序混乱
        self._executor = ThreadPoolExecutor(max_workers=1)

        # 1. 数据库连接
        self.sqlite_store = get_memory_store()

        # 2. ChromaDB 连接
        self.chroma_client = chromadb.PersistentClient(path=MEMORY_DB_PATH)
        fallback = embedding_functions.DefaultEmbeddingFunction()

        if EMBEDDING_CONFIG.get("api_url"):
            self.embedding_fn = RemoteBGEFunction(
                api_url=EMBEDDING_CONFIG["api_url"],
                api_key=EMBEDDING_CONFIG.get("api_key", ""),
                model_name=EMBEDDING_CONFIG["model_name"],
                fallback_fn=fallback
            )
        else:
            self.embedding_fn = fallback

        self.memory_collection = self.chroma_client.get_or_create_collection(
            name="waifu_memory_advanced", embedding_function=self.embedding_fn
        )
        self.knowledge_collection = self.chroma_client.get_or_create_collection(
            name="waifu_knowledge_base", embedding_function=self.embedding_fn
        )

        # 3. 图谱
        self.graph = GraphMemory()

        # 4. Profile 档案管理
        self.profile_path = os.path.join(os.path.dirname(MEMORY_DB_PATH), "profile.json")
        self.profile = ProfileStore(self.profile_path)
        self.profile_enabled = True

        # 5. 短期记忆 (RAM)
        self.max_short_term = int(MEMORY_SETTINGS.get("max_short_term", 12))
        self.short_term_memory = []
        self.session_short_term_memory = {}
        self._session_short_term_loaded = set()
        self._restore_short_term_from_db()

        # 配置
        self.store_roles = set(MEMORY_SETTINGS.get("store_roles", ["user"]))
        self.long_term_enabled = bool(MEMORY_SETTINGS.get("long_term_enabled", True))

        # 工具历史
        self.tool_history = []
        self.max_tool_history = 12
        self.tool_context_max_chars = 500

        # Logger
        self._logger = get_logger()

        # ========== 补全缺失的配置属性 ==========

        # 检索配置
        # 兼容新旧 key，优先使用 config.py 中的新命名
        self.cand_k = int(MEMORY_SETTINGS.get("memory_recall_candidates", MEMORY_SETTINGS.get("cand_k", 8)))
        self.final_k = int(MEMORY_SETTINGS.get("memory_recall_final", MEMORY_SETTINGS.get("final_k", 3)))
        self.sim_threshold = float(MEMORY_SETTINGS.get("memory_sim_threshold", MEMORY_SETTINGS.get("sim_threshold", 0.28)))
        self.half_life_days = float(MEMORY_SETTINGS.get("memory_half_life_days", MEMORY_SETTINGS.get("half_life_days", 30.0)))
        self.recall_roles = MEMORY_SETTINGS.get("recall_roles", ["user", "assistant", "summary"])
        self.use_llm_selector = bool(MEMORY_SETTINGS.get("use_llm_selector", False))
        self.llm_selector_min_interval_sec = float(MEMORY_SETTINGS.get("llm_selector_min_interval_sec", 20))
        self._last_llm_selector_ts = 0.0
        self.recall_min_chars = int(MEMORY_SETTINGS.get("recall_min_chars", self.recall_min_chars))

        # 图扩展配置
        self.graph_expand_enabled = bool(MEMORY_SETTINGS.get("graph_expand_enabled", True))
        self.graph_expand_min_chars = int(MEMORY_SETTINGS.get("graph_expand_min_chars", 6))

        # 调试配置
        self.debug_prompt_injection = bool(MEMORY_SETTINGS.get("debug_prompt_injection", False))

        # 缓存
        self._query_cache = {}
        self._cache_ttl = 300
        self._cache_hits = 0
        self._cache_misses = 0
    def _extract_keywords(self, text: str):
        """提取关键词，用于图谱扩展"""
        if not text: return []
        try:
            # 使用 jieba 提取关键词
            return jieba.analyse.extract_tags(text, topK=5)
        except Exception:
            return []

    def _stable_md5(self, text: str) -> str:
        """生成稳定的 MD5 hash"""
        if not text: return ""
        return hashlib.md5(text.encode("utf-8")).hexdigest()
    def _restore_short_term_from_db(self):
        """启动时恢复最近对话"""
        if not self.sqlite_store: return
        try:
            rows = self.sqlite_store.list_transcript(limit=self.max_short_term, session_scope="global")
            if rows:
                self.short_term_memory = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
                print(f"🧠 [Memory] 恢复 {len(self.short_term_memory)} 条短期记忆")
        except Exception as e:
            print(f"⚠️ 恢复短期记忆失败: {e}")

    def _restore_session_short_term_from_db(self, session_id: str):
        session_key = str(session_id or "").strip()
        if not session_key or not self.sqlite_store or session_key in self._session_short_term_loaded:
            return
        try:
            rows = self.sqlite_store.list_transcript(
                limit=self.max_short_term,
                session_id=session_key,
                session_scope="specific",
            )
            if rows:
                self.session_short_term_memory[session_key] = [
                    {"role": r["role"], "content": r["content"]} for r in reversed(rows)
                ]
            else:
                self.session_short_term_memory.setdefault(session_key, [])
            self._session_short_term_loaded.add(session_key)
        except Exception as e:
            print(f"⚠️ 恢复会话短期记忆失败({session_key}): {e}")

    def _append_short_term_memory(self, role, content, session_id: str = None):
        item = {"role": role, "content": content}
        session_key = str(session_id or "").strip()
        if session_key:
            bucket = self.session_short_term_memory.setdefault(session_key, [])
            bucket.append(item)
            if len(bucket) > self.max_short_term:
                bucket.pop(0)
            self._session_short_term_loaded.add(session_key)
            return
        self.short_term_memory.append(item)
        if len(self.short_term_memory) > self.max_short_term:
            self.short_term_memory.pop(0)


    # ================= 核心：添加记忆 (异步优化版) =================

    def add_memory(self, role, content, session_id: str = None):
        """
        主线程只做最快的内存操作(RAM)，慢速 IO 操作(SQLite/Chroma/LLM提取)扔到后台线程池。
        这样可以显著减少 UI 卡顿。
        """
        with self._lock:
            # 1. 极速写入 RAM 短期记忆 (立即生效，供下一轮对话使用)
            self._append_short_term_memory(role, content, session_id=session_id)

        # 2. 提交慢速任务到后台 (SQLite, Chroma, Graph, Profile提取)
        self._executor.submit(self._background_save_memory, role, content, session_id)

    def _background_save_memory(self, role, content, session_id: str = None):
        """后台慢速任务：处理磁盘 IO、向量计算、LLM 提取等耗时操作"""
        try:
            # A. 写入 SQLite (全量日志)
            if self.sqlite_store:
                try:
                    self.sqlite_store.add_transcript(role, content, session_id=session_id)
                except Exception as e:
                    print(f"❌ [Memory] SQLite 写入失败: {e}")

            # B. 自动提取 Profile 档案 (LLM 操作，较慢)
            # 如果开启了 Profile 且角色符合 (user/agent)，尝试提取
            if self.profile_enabled and self.profile:
                try:
                    self.profile.extract_and_update(role, content)
                except Exception as e:
                    print(f"⚠️ [Profile] 后台提取失败: {e}")

            # C. 写入 Vector DB (条件过滤 + Embedding 计算)
            if self._should_store_long_term(role, content):
                meta = {
                    "role": role,
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "kind": "chat"
                }
                if session_id:
                    meta["session_id"] = str(session_id)
                # 确保 ID 唯一且有序
                msg_id = f"mem_{int(time.time() * 1000)}_{role}_{uuid.uuid4().hex[:8]}"

                try:
                    self.memory_collection.add(
                        documents=[content],
                        metadatas=[meta],
                        ids=[msg_id],
                    )
                except Exception as e:
                    print(f"⚠️ [Memory] 向量库写入失败: {e}")

            # D. 更新图谱 (仅用户，CPU 密集)
            if role == "user":
                try:
                    keywords = self._extract_keywords(content)
                    for k1, k2 in itertools.combinations(keywords, 2):
                        self.graph.add_concept_link(k1, k2)
                except Exception as e:
                    print(f"⚠️ [Memory] 图记忆更新失败: {e}")

        except Exception as e:
            print(f"❌ [Background Memory] 后台任务异常: {e}")
            import traceback
            traceback.print_exc()



    def _fetch_profile_from_db(self) -> str:
        """从 SQLite 获取 User 和 当前角色 的档案"""
        if not self.sqlite_store: return ""

        # 获取当前角色ID
        active_id = "default_char"
        if character_manager:
            active_id = character_manager.data.get("active_id", "default_char")

        # 查库：只查 active 的档案数据
        items = self.sqlite_store.list_items(status="active", limit=1000)

        user_lines = []
        agent_lines = []

        for it in items:
            typ = it.get("type")
            text = it.get("text", "")
            tags = it.get("tags") or []

            # 兼容旧tags: 如果 tags 是字符串，尝试转列表（有些库可能会这样）
            if isinstance(tags, str):
                try:
                    tags = json.loads(tags)
                except:
                    tags = []

            # ---------------- User 档案 ----------------
            if typ == "user_profile" or "role:user" in tags:
                if "name" in tags:
                    user_lines.insert(0, f"- 称呼：{text}")
                elif "status" in tags:
                    user_lines.append(f"- 状态：{text}")
                elif "dislikes" in tags:
                    user_lines.append(f"- 雷点：{text}")
                elif "note" in tags:
                    user_lines.append(f"★ 备注：{text}")
                elif "likes" in tags:
                    cat = tags[-1] if len(tags) > 1 and tags[-1] != "likes" else "general"
                    user_lines.append(f"- 喜好({cat})：{text}")

            # ---------------- Agent 档案 (需匹配 ID) ----------------
            elif typ == "agent_profile" or any(t.startswith("role:") for t in tags):
                # 检查归属
                role_tag = next((t for t in tags if t.startswith("role:")), None)
                # 如果有 role:xxx 且不等于当前 active_id，跳过
                if role_tag and role_tag != f"role:{active_id}":
                    continue
                # 如果没有 role:xxx，默认视为通用或 default_char

                if "name" in tags:
                    agent_lines.insert(0, f"- 你的名字：{text}")
                elif "traits" in tags:
                    agent_lines.append(f"- 性格：{text}")
                elif "dislikes" in tags:
                    agent_lines.append(f"- 讨厌：{text}")
                elif "likes" in tags:
                    cat = tags[-1] if len(tags) > 1 and tags[-1] != "likes" else "general"
                    agent_lines.append(f"- 喜好({cat})：{text}")

        out = []
        if user_lines:
            out.append("【用户档案】")
            out.extend(user_lines)
        if agent_lines:
            out.append("\n【自我认知 (你)】")
            out.extend(agent_lines)

        return "\n".join(out)

    def _should_store_long_term(self, role: str, content: str) -> bool:
        """
        判断是否需要存入长期记忆 (规则 + LLM 双重判断)
        """
        if not self.long_term_enabled:
            return False

        if role not in self.store_roles:
            return False

        t = (content or "").strip()
        if not t:
            return False

        # 1. 基础过滤：太短的通常是废话 (嗯、哦、哈哈)
        # 中文环境下，少于 2 个字且没有特定符号的，基本可以扔
        if len(t) < 2:
            return False

        # 过滤常见口语噪声
        noise = ["嗯", "哦", "好的", "行", "哈哈", "ok", "OK", "emmm", "…", "...", "真的吗", "是吗"]
        if t.lower() in noise:
            return False

        # 2. 【快速通道】规则判断 (省流)
        # 如果包含这些强特征词，直接存，不需要问 LLM
        fast_triggers = [
            "我叫", "名字", "生日", "住在", "工作", "学校",
            "喜欢", "讨厌", "不爱", "爱好", "偏好",
            "记住", "别忘", "提醒", "计划", "目标",
            "正在", "打算", "准备", "最近", "忙", "专利", "项目",  # 把刚才加的也放这
            "因为", "所以", "觉得", "认为"
        ]
        if any(k in t for k in fast_triggers):
            return True

        # 3. 【智能通道】LLM 语义判断 (漏网之鱼)
        # 如果没命中关键词，但句子长度尚可(比如 > 4字)，可能是隐晦的重要信息
        # 比如：“彻底搞砸了，心情很差” (没命中关键词，但很重要)
        if len(t) >= 4 and chat_with_ai and self.use_llm_selector:
            now_ts = time.time()
            if now_ts - self._last_llm_selector_ts < self.llm_selector_min_interval_sec:
                return False
            try:
                # 使用最便宜的模型 (gatekeeper / summary)
                # 构造一个极简 Prompt
                prompt = f"""
Judge if this message contains useful facts/status/emotions worth remembering.
Message: "{t}"
Output ONLY "YES" or "NO".
"""
                decision = chat_with_ai(
                    [{"role": "user", "content": prompt}],
                    task_type="gatekeeper",  # 👈 用最便宜的模型
                    caller="memory_selector",
                )
                self._last_llm_selector_ts = now_ts

                if decision and "YES" in decision.strip().upper():
                    print(f"🧠 [Memory] LLM 判定此句值得记忆: {t}")
                    return True
            except Exception:
                pass

        return False

    def _format_memory_item(self, meta: dict, doc: str) -> str:
        role = meta.get("role", "user")
        ts = meta.get("ts", "")
        short_ts = ts.replace("T", " ").replace("Z", "")[:16] if ts else ""
        prefix = "你" if role == "user" else "我"
        return f"- [{short_ts}] {prefix}：{doc}"

    def _recency_score(self, ts_iso: str) -> float:
        if not ts_iso:
            return 0.0
        try:
            t = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            age_days = max(0.0, (now - t).total_seconds() / 86400.0)
            # 半衰期模型：score = 0.5^(age/half_life)
            return 0.5 ** (age_days / max(1e-6, self.half_life_days))
        except Exception:
            return 0.0

    @staticmethod
    def _dist_to_sim(dist: float) -> float:
        # Chroma distance：不同 backend 可能不同，这里做一个安全映射（越小越相似）
        try:
            d = float(dist)
        except Exception:
            return 0.0
        # 常见 cosine distance 在 0~2，取 1-d 的近似，再 clamp
        sim = 1.0 - d
        return max(0.0, min(1.0, sim))

    @staticmethod
    def _is_recall_intent_query(text: str) -> bool:
        t = (text or "").strip().lower()
        if not t:
            return False
        cues = [
            "记得", "还记得", "忘了", "之前", "刚才", "上午", "早上", "昨天", "前天",
            "说过", "提过", "怎么了", "为什么", "当时", "回忆",
            "remember", "forgot", "earlier", "previously", "what happened",
            "腹泻", "断食", "拉肚子", "体检", "医院", "生病", "不舒服",
        ]
        return any(k in t for k in cues)

    def _extract_recall_terms(self, text: str) -> list:
        t = (text or "").strip()
        if not t:
            return []
        terms = []
        try:
            for w in jieba.lcut(t):
                w = (w or "").strip()
                if len(w) >= 2:
                    terms.append(w.lower())
        except Exception:
            pass
        for w in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z]{3,}", t):
            terms.append(w.lower())

        stop = {
            "今天", "现在", "这个", "那个", "就是", "然后", "因为", "所以", "觉得",
            "怎么", "什么", "一下", "一下子", "可以", "是不是", "有没有", "为什么",
            "你", "我", "他", "她", "它", "我们", "你们", "他们",
            "please", "could", "would", "should", "think", "about",
        }
        dedup, seen = [], set()
        for w in terms:
            if w in stop or len(w) < 2 or w in seen:
                continue
            seen.add(w)
            dedup.append(w)
        return dedup[:18]

    @staticmethod
    def _role_recall_weight(role: str, strict_user_fact: bool = False) -> float:
        r = (role or "").strip().lower()
        if strict_user_fact:
            if r == "user":
                return 0.20
            if r == "summary":
                return 0.12
            return -0.15
        if r == "user":
            return 0.10
        if r == "summary":
            return 0.05
        if r == "assistant":
            return -0.03
        return 0.0

    @staticmethod
    def _score_text_overlap(doc: str, terms: list) -> float:
        if not doc or not terms:
            return 0.0
        d = doc.lower()
        hit = sum(1 for t in terms if t in d)
        return min(1.0, hit / max(1.0, len(terms)))

    def _retrieve_from_transcript_fallback(self, search_text: str, limit: int = 4, strict_user_fact: bool = False, session_id: str = None) -> list:
        """
        向量召回为空时，从 transcript 做轻量兜底召回，避免“明明说过却回忆不到”。
        """
        if not self.sqlite_store:
            return []
        t = (search_text or "").strip()
        if not t:
            return []

        terms = self._extract_recall_terms(t)
        role_allow = {"user", "summary"} if strict_user_fact else set(self.recall_roles or [])
        items = []
        seen = set()
        session_key = str(session_id or "").strip()
        try:
            rows = self.sqlite_store.list_transcript(
                limit=max(limit * 12, 120),
                offset=0,
                session_id=session_key,
                session_scope="specific" if session_key else "global",
            )
            for kw in terms[:6]:
                try:
                    rows.extend(self.sqlite_store.list_transcript(
                        query=kw,
                        limit=18,
                        offset=0,
                        session_id=session_key,
                        session_scope="specific" if session_key else "global",
                    ))
                except Exception:
                    pass

            for r in rows:
                role = (r.get("role") or "user").strip()
                if role_allow and role not in role_allow:
                    continue
                doc = str(r.get("content") or "").strip()
                if not doc:
                    continue
                row_id = int(r.get("id", 0) or 0)
                if row_id and row_id in seen:
                    continue
                overlap = self._score_text_overlap(doc, terms)
                if terms and overlap <= 0.0:
                    continue
                ts_iso = str(r.get("ts_iso") or "")
                rec = self._recency_score(ts_iso)
                role_w = self._role_recall_weight(role, strict_user_fact=strict_user_fact)
                score = overlap * 0.62 + rec * 0.28 + role_w
                items.append({
                    "id": f"tr_{row_id}",
                    "doc": doc,
                    "meta": {"role": role, "ts": ts_iso, "kind": "transcript_fallback"},
                    "sim": overlap,
                    "rec": rec,
                    "score": score,
                })
                if row_id:
                    seen.add(row_id)
        except Exception:
            return []
        items.sort(key=lambda x: x["score"], reverse=True)
        return items[: max(1, int(limit))]

    # ---------- 新增：导入知识（修复 hash(chunk) 不稳定问题） ----------
    def import_knowledge_from_file(self, file_path):
        if not os.path.exists(file_path):
            return 0

        print(f"📖 正在读取知识文件: {file_path}")
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        chunks = [c.strip() for c in content.split("\n") if c.strip()]
        count = 0

        for chunk in chunks:
            if len(chunk) < 5:
                continue

            chunk_id = "know_" + self._stable_md5(chunk)
            try:
                existing = self.knowledge_collection.get(ids=[chunk_id])
                if not existing.get("ids"):
                    self.knowledge_collection.add(
                        documents=[chunk],
                        metadatas=[{"source": os.path.basename(file_path)}],
                        ids=[chunk_id],
                    )
                    count += 1
            except Exception:
                # get/add 失败就跳过
                pass

        print(f"✅ 成功导入 {count} 条新知识！")
        return count

    # ---------- 记忆写入 ----------
    # def add_memory(self, role, content):
    #     """添加记忆（线程安全 + 双写 SQLite/Chroma）"""
    #     with self._lock:
    #         try:
    #             # 1. 🟢 [修复] 必须先写入 SQLite (全量日志)
    #             try:
    #                 self.sqlite_store.add_transcript(role, content)
    #             except Exception as e:
    #                 print(f"❌ [Memory] SQLite 写入严重失败: {e}")
    #
    #             # 2. 更新 RAM 短期记忆
    #             self.short_term_memory.append({"role": role, "content": content})
    #             if len(self.short_term_memory) > self.max_short_term:
    #                 self.short_term_memory.pop(0)
    #
    #             # 3. 更新 Profile (JSON)
    #
    #             # 4. 写入 Vector DB (条件过滤)
    #             if self._should_store_long_term(role, content):
    #                 meta = {
    #                     "role": role,
    #                     "ts": datetime.now(timezone.utc).isoformat(),
    #                     "kind": "chat",
    #                 }
    #                 msg_id = f"mem_{int(time.time() * 1000)}_{role}_{uuid.uuid4().hex[:8]}"
    #
    #                 try:
    #                     self.memory_collection.add(
    #                         documents=[content],
    #                         metadatas=[meta],
    #                         ids=[msg_id],
    #                     )
    #                 except Exception as e:
    #                     print(f"⚠️ [Memory] 向量库写入失败: {e}")
    #
    #             # 5. 更新图谱 (仅用户)
    #             if role == "user":
    #                 try:
    #                     keywords = self._extract_keywords(content)
    #                     for k1, k2 in itertools.combinations(keywords, 2):
    #                         self.graph.add_concept_link(k1, k2)
    #                 except Exception as e:
    #                     print(f"⚠️ [Memory] 图记忆更新失败: {e}")
    #
    #         except Exception as e:
    #             print(f"❌ [Memory] add_memory 主流程异常: {e}")
    #             import traceback
    #             traceback.print_exc()

    # ---------- 记忆检索：候选召回 + 时间衰减重排 + 可选 LLM 决策 ----------
    def _retrieve_memories(self, search_text: str, session_id: str = None):
        # ✅ 性能优化：检查查询缓存
        session_key = str(session_id or "").strip()
        cache_key = self._stable_md5(search_text + f":{self.final_k}:{session_key}")
        if cache_key in self._query_cache:
            cached_time, cached_result = self._query_cache[cache_key]
            if time.time() - cached_time < self._cache_ttl:
                self._cache_hits += 1
                return cached_result

        self._cache_misses += 1

        candidates = []
        seen_doc = set()
        strict_user_fact = self._is_recall_intent_query(search_text)
        role_allow = {"user", "summary"} if strict_user_fact else set(self.recall_roles or [])

        try:
            query_kwargs = {
                "query_texts": [search_text],
                "n_results": self.cand_k,
                "include": ["documents", "metadatas", "distances"],
            }
            if session_key:
                query_kwargs["where"] = {"session_id": session_key}
            res = self.memory_collection.query(**query_kwargs)

            docs = (res.get("documents") or [[]])[0]
            metas = (res.get("metadatas") or [[]])[0]
            dists = (res.get("distances") or [[]])[0]
            ids = (res.get("ids") or [[]])[0]

            # 某些版本可能 ids 为空/长度不齐，兜底
            if not ids or len(ids) != len(docs):
                ids = list(ids) if ids else []
                for i in range(len(docs) - len(ids)):
                    ids.append(f"mem_noid_{i}")

            for doc, meta, dist, _id in zip(docs, metas, dists, ids):
                meta = meta or {}
                if not session_key and str(meta.get("session_id") or "").strip():
                    continue
                role = (meta.get("role") or "user").strip()

                # ✅ role 过滤：默认只召回 user，减少带偏
                if role_allow and role not in role_allow:
                    continue

                sim = self._dist_to_sim(dist)
                if sim < self.sim_threshold:
                    continue

                doc_norm = re.sub(r"\s+", " ", (doc or "").strip())
                if not doc_norm:
                    continue

                doc_key = self._stable_md5(doc_norm)
                if doc_key in seen_doc:
                    continue
                seen_doc.add(doc_key)

                rec = self._recency_score(meta.get("ts", ""))
                role_w = self._role_recall_weight(role, strict_user_fact=strict_user_fact)
                score = sim * 0.68 + rec * 0.27 + role_w

                candidates.append({
                    "id": _id,
                    "doc": doc_norm,
                    "meta": meta,
                    "sim": sim,
                    "rec": rec,
                    "score": score,
                })
        except Exception:
            pass

        fb_items = self._retrieve_from_transcript_fallback(
            search_text,
            limit=max(self.final_k, 4),
            strict_user_fact=strict_user_fact,
            session_id=session_key,
        )
        if not candidates:
            candidates = fb_items
        elif fb_items:
            known = {str(c.get("id")) for c in candidates}
            for it in fb_items:
                if str(it.get("id")) in known:
                    continue
                candidates.append(it)

        # 先按综合分排序
        candidates.sort(key=lambda x: x["score"], reverse=True)

        # 可选：让 LLM 从 topN 里挑最相关的 2~3 条
        if self.use_llm_selector and chat_with_ai and len(candidates) > self.final_k:
            picked = self._llm_pick_memories(search_text, candidates[: min(10, len(candidates))], want=self.final_k)
            if picked:
                id_set = set(picked)
                candidates = [c for c in candidates if c["id"] in id_set]
                order = {mid: i for i, mid in enumerate(picked)}
                candidates.sort(key=lambda x: order.get(x["id"], 9999))

        top = candidates[: self.final_k]
        self._query_cache[cache_key] = (time.time(), top)
        return top



    def _llm_pick_memories(self, query: str, candidates: list, want: int = 3):
        """
        输出：候选 id 列表（最多 want 个）
        """
        try:
            lines = []
            for i, c in enumerate(candidates):
                role = c["meta"].get("role", "user")
                ts = c["meta"].get("ts", "")
                lines.append(f"{i}. id={c['id']} role={role} ts={ts}\n   内容：{c['doc']}")

            prompt = (
                    "你是一个“记忆筛选器”。任务：从候选记忆中挑选与当前问题最相关的记忆。\n"
                    "规则：\n"
                    f"- 最多选 {want} 条\n"
                    "- 优先选择：用户偏好/身份信息/未完成计划/明确事实\n"
                    "- 如果不相关就不要选\n"
                    "输出要求：只输出 JSON，例如：{\"ids\":[\"id1\",\"id2\"]}\n\n"
                    f"当前输入：{query}\n\n候选记忆：\n" + "\n".join(lines)
            )

            resp = chat_with_ai(
                [{"role": "system", "content": prompt}],
                task_type="summary",
                caller="memory_rerank",
            ) or ""

            m = re.search(r"\{.*\}", resp, flags=re.S)
            if not m:
                return []
            obj = json.loads(m.group(0))
            ids = obj.get("ids", [])
            if not isinstance(ids, list):
                return []
            cand_ids = {c["id"] for c in candidates}
            ids = [x for x in ids if isinstance(x, str) and x in cand_ids]
            return ids[:want]
        except Exception:
            return []

    def _retrieve_knowledge(self, search_text: str, k: int = 2):
        know = []
        try:
            res = self.knowledge_collection.query(
                query_texts=[search_text],
                n_results=k,
                include=["documents", "metadatas", "distances"],
            )
            docs = (res.get("documents") or [[]])[0]
            for d in docs:
                if d:
                    know.append(d)
        except Exception:
            pass
        return know

    # ---------- 缓存管理（性能优化） ----------
    def clear_query_cache(self):
        """清理过期的查询缓存"""
        now = time.time()
        self._query_cache = {
            k: v for k, v in self._query_cache.items()
            if now - v[0] < self._cache_ttl
        }
        # ✅ 修复：使用 self._logger
        if self._logger:
            self._logger.info(f"查询缓存已清理，剩余 {len(self._query_cache)} 条")
        else:
            print(f"🧠 [Memory] 查询缓存已清理，剩余 {len(self._query_cache)} 条")

    def get_cache_stats(self) -> dict:
        """获取缓存统计信息"""
        total = self._cache_hits + self._cache_misses
        hit_rate = (self._cache_hits / total * 100) if total > 0 else 0

        stats = {
            "total_queries": total,
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
            "hit_rate": hit_rate,
            "cached_items": len(self._query_cache)
        }

        # ✅ 修复：使用 self._logger
        if self._logger:
            self._logger.debug(f"缓存统计: {stats}")

        return stats

    # ---------- 构建 Prompt ----------
    # ---------- 工具使用记录（用于 ToolRouter/工具轮上下文） ----------
    def record_tool_use(self, triggers, tool_feedback: str = "", user_text: str = ""):
        """记录本轮工具执行信息（不默认注入到 prompt，只有 tool_intent 才会注入）。"""
        # ✅ 并发安全：使用锁保护工具历史记录
        with self._lock:
            try:
                trig = [t.strip() for t in (triggers or []) if isinstance(t, str) and t.strip()]
                if not trig and not tool_feedback:
                    return
                item = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "triggers": trig[:12],
                    "user": (user_text or "").strip()[:120],
                    "result": (tool_feedback or "").strip()[:900],
                }
                self.tool_history.append(item)
                if len(self.tool_history) > self.max_tool_history:
                    self.tool_history = self.tool_history[-self.max_tool_history:]
            except Exception:
                pass

    def _format_tool_history(self, tool_intent=None) -> str:
        """只挑与本轮 tool_intent 相关的最近几条，避免浪费 token。"""
        try:
            intent = set([t.strip() for t in (tool_intent or []) if isinstance(t, str) and t.strip()])
            if not intent:
                return ""
            if not self.tool_history:
                return ""

            picked = []
            for it in reversed(self.tool_history):
                it_trig = set(it.get("triggers") or [])
                if it_trig & intent:
                    picked.append(it)
                if len(picked) >= 3:
                    break

            if not picked:
                return ""

            picked.reverse()
            lines = []
            for it in picked:
                ts = (it.get("ts") or "").replace("T", " ").replace("Z", "")[:16]
                trig = ",".join(it.get("triggers") or [])
                u = it.get("user") or ""
                r = it.get("result") or ""
                lines.append(f"- [{ts}] triggers={trig}\n  用户：{u}\n  结果：{r}")

            out = "\n".join(lines).strip()
            if len(out) > self.tool_context_max_chars:
                out = out[-self.tool_context_max_chars:]
            return out
        except Exception:
            return ""

    def build_prompt(self, current_user_text, system_persona, tool_intent=None, session_id: str = None):
        print("🔍 [系统] 正在进行双路检索（向量记忆 + 知识库）.")

        # 0. 提取时间头
        time_header = ""
        if "【当前时间】" in system_persona:
            time_header = system_persona.split("\n")[0]

        # 1. 动态构建 Persona
        active_char = character_manager.get_active_character()

        if active_char and active_char.get("prompt"):
            core_persona = active_char["prompt"]
        else:
            core_persona = DEFAULT_PERSONA

        # 🟢 拼装：时间 + 性格 + 通用规则
        final_system = f"{time_header}\n\n{core_persona}\n\n{SYSTEM_RULES_PROMPT}"

        # 2. 补全工具说明
        tool_desc = ""
        if "【可用工具能力】" in system_persona:
            parts = system_persona.split("【可用工具能力】")
            if len(parts) > 1:
                tool_desc = "【可用工具能力】" + parts[1]
        elif "【工具】" in system_persona:
            parts = system_persona.split("【工具】")
            if len(parts) > 1:
                tool_desc = "【工具】" + parts[1]

        if tool_desc:
            final_system += "\n\n" + tool_desc

        # 3. 准备检索参数
        raw_user = (current_user_text or "").strip()
        tool_mode = bool(tool_intent)
        recall_intent = self._is_recall_intent_query(raw_user)
        do_recall = (not tool_mode) and ((len(raw_user) >= self.recall_min_chars) or recall_intent)

        # 4. 图扩散关键词扩展
        search_text = raw_user
        if self.graph_expand_enabled and len(raw_user) >= self.graph_expand_min_chars:
            keywords = self._extract_keywords(raw_user)
            try:
                related = self.graph.get_related_keywords(keywords, depth=2, top_k=5)
                if related:
                    search_text = raw_user + " " + " ".join(related)
            except Exception:
                pass

        # 5. 长期记忆检索 (Vector)
        session_key = str(session_id or "").strip()
        mem_items = self._retrieve_memories(search_text, session_id=session_key) if do_recall else []
        mem_text = ""
        if mem_items:
            mem_text = "\n".join([self._format_memory_item(m["meta"], m["doc"]) for m in mem_items])

        # 6. 知识库检索
        know_items = [] if tool_mode else self._retrieve_knowledge(search_text, k=2)
        know_text = ""
        if know_items:
            know_text = "\n".join([f"· {k}" for k in know_items])

        # 🟢 7. 用户档案 (Profile) - 关键修改！
        # 优先从 SQLite 数据库读取 (CharacterManager 写入的地方)
        profile_text = self._fetch_profile_from_db()

        # 如果数据库没读到，且开启了 JSON Profile，尝试用 JSON 补充
        if not profile_text and self.profile_enabled and self.profile:
            profile_text = self.profile.format_for_prompt()

        # 8. SQLite 记忆源 (Tasks/Notes/Episodes)
        sqlite_notes_text = ""
        sqlite_tasks_text = ""
        sqlite_episodes_text = ""
        try:
            from modules.memory_sqlite import format_active_tasks_for_prompt, format_notes_for_prompt, format_recent_episodes_for_prompt
            if self.sqlite_store:
                sqlite_tasks_text = format_active_tasks_for_prompt(self.sqlite_store, limit=6)
                sqlite_notes_text = format_notes_for_prompt(self.sqlite_store, max_items=24)
                sqlite_episodes_text = format_recent_episodes_for_prompt(self.sqlite_store, limit=3)
        except Exception:
            pass

        # 9. 最终组装 System Content
        if profile_text:
            final_system += "\n\n【用户档案与自我认知】:\n" + profile_text

        if sqlite_tasks_text:
            final_system += "\n\n【当前待办/承诺】:\n" + sqlite_tasks_text

        if sqlite_notes_text:
            final_system += "\n\n【重要笔记 (Memory Items)】:\n" + sqlite_notes_text

        if sqlite_episodes_text:
            final_system += "\n\n【近期对话摘要 (Episodes)】:\n" + sqlite_episodes_text

        if know_text:
            final_system += "\n\n【相关知识库】:\n" + know_text

        if mem_text:
            final_system += (
                    "\n\n【回忆片段】(仅供参考):\n"
                    "当涉及用户既往事实时，优先相信 user 原话；assistant 推断若冲突则降级处理。\n"
                    + mem_text
            )

        # 10. 工具上下文
        tool_ctx = self._format_tool_history(tool_intent)
        if tool_ctx:
            final_system += "\n\n【工具使用记录】:\n" + tool_ctx

        # 构建消息列表
        messages = [{"role": "system", "content": final_system}]
        if session_key:
            self._restore_session_short_term_from_db(session_key)
            short_ctx = list(self.session_short_term_memory.get(session_key, []))
        else:
            short_ctx = self.short_term_memory
        if recall_intent:
            short_ctx = [m for m in short_ctx if (m.get("role") or "").strip() == "user"]
        messages += short_ctx
        messages += [{"role": "user", "content": current_user_text}]

        return messages


