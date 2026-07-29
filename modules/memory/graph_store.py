import json
import os
import time
from datetime import datetime

import networkx as nx

from config import MEMORY_DB_PATH, MEMORY_SETTINGS


class GraphMemory:
    def __init__(self, graph_file="graph.json"):
        self.graph_file = os.path.join(MEMORY_DB_PATH, graph_file)
        self.G = nx.Graph()
        self.stopwords = set(
            [
                "什么",
                "怎么",
                "为什么",
                "因为",
                "就是",
                "然后",
                "但是",
                "如果",
                "我们",
                "你们",
                "他们",
            ]
        )
        self.last_decay_day = None
        self._related_cache = {}
        self._cache_ttl = 600
        self._cache_hits = 0
        self._cache_misses = 0
        self.load_graph()

    def load_graph(self):
        if os.path.exists(self.graph_file):
            try:
                with open(self.graph_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
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
        for u, v, d in list(self.G.edges(data=True)):
            w = float(d.get("weight", 1.0)) * decay
            if w < 0.05:
                try:
                    self.G.remove_edge(u, v)
                except Exception:
                    pass
            else:
                self.G[u][v]["weight"] = w

    def save_graph(self):
        os.makedirs(os.path.dirname(self.graph_file), exist_ok=True)
        self._apply_decay_if_needed()
        data = nx.node_link_data(self.G, edges="links")
        with open(self.graph_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def maybe_apply_decay(self):
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

        for kw in start_keywords:
            if kw not in self.G:
                continue
            degree = self.G.degree(kw)
            if degree > 50:
                subgraph = nx.ego_graph(self.G, kw, radius=1)
                pr = nx.pagerank(subgraph, max_iter=50, tol=1e-6)
                activated_nodes.update({k: v for k, v in pr.items() if v > 0.01})
                continue

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
                        if (
                            neighbor not in activated_nodes
                            or transfer > activated_nodes[neighbor]
                        ):
                            activated_nodes[neighbor] = transfer
                            next_layer.append(neighbor)
                current_layer = next_layer

        result = sorted(activated_nodes.items(), key=lambda x: x[1], reverse=True)
        filtered = [k for k, _v in result if k not in start_keywords][:top_k]
        self._related_cache[cache_key] = (time.time(), filtered)
        return filtered
