"""
混合检索引擎
结合向量检索和关键词检索，使用RRF算法融合结果
"""
import math
import re
import asyncio
from typing import List, Dict, Any, Optional, Tuple
from collections import defaultdict

import numpy as np

try:
    import jieba
    import jieba.analyse
except ImportError:
    jieba = None

try:
    from rank_bm25 import BM25Okapi
except ImportError:
    BM25Okapi = None

from modules.llm import chat_with_ai


class BM25Retriever:
    """基于BM25的关键词检索器"""
    
    def __init__(self, documents: List[Dict[str, Any]]):
        """
        初始化BM25检索器
        
        Args:
            documents: 文档列表，每个文档包含 'text' 和 'metadata'
        """
        self.documents = documents
        self.corpus = [doc['text'] for doc in documents]
        self.tokenized_corpus = []
        self.bm25 = None
        
        if jieba:
            self.tokenized_corpus = [self._tokenize(text) for text in self.corpus]
            if BM25Okapi:
                self.bm25 = BM25Okapi(self.tokenized_corpus)
        else:
            print("⚠️ [BM25] jieba未安装，BM25功能不可用")
    
    def _tokenize(self, text: str) -> List[str]:
        """
        分词
        
        Args:
            text: 文本
            
        Returns:
            词汇列表
        """
        if jieba is None:
            # 简单的分词策略
            words = re.findall(r'[\w]+', text.lower())
            return words
        
        return list(jieba.cut(text))
    
    def retrieve(self, query: str, top_k: int = 10) -> List[Dict[str, Any]]:
        """
        检索相关文档
        
        Args:
            query: 查询文本
            top_k: 返回前k个结果
            
        Returns:
            排序后的文档列表，每个文档包含额外字段：score, rank
        """
        if self.bm25 is None:
            # 退化为简单的关键词匹配
            return self._simple_keyword_search(query, top_k)
        
        query_tokens = self._tokenize(query)
        scores = self.bm25.get_scores(query_tokens)
        
        # 获取top_k索引
        top_indices = np.argsort(scores)[::-1][:top_k]
        
        results = []
        for rank, idx in enumerate(top_indices):
            doc = self.documents[idx].copy()
            doc['score'] = float(scores[idx])
            doc['rank'] = rank + 1
            results.append(doc)
        
        return results
    
    def _simple_keyword_search(self, query: str, top_k: int) -> List[Dict[str, Any]]:
        """
        简单的关键词搜索（BM25不可用时的备选方案）
        
        Args:
            query: 查询文本
            top_k: 返回前k个结果
            
        Returns:
            排序后的文档列表
        """
        query_words = set(self._tokenize(query.lower()))
        
        scored_docs = []
        for doc in self.documents:
            doc_words = set(self._tokenize(doc['text'].lower()))
            
            # 计算重叠度
            overlap = len(query_words & doc_words)
            if overlap > 0:
                score = overlap / len(query_words)
                scored_docs.append((score, doc))
        
        # 排序
        scored_docs.sort(key=lambda x: x[0], reverse=True)
        
        results = []
        for rank, (score, doc) in enumerate(scored_docs[:top_k]):
            doc_copy = doc.copy()
            doc_copy['score'] = score
            doc_copy['rank'] = rank + 1
            results.append(doc_copy)
        
        return results


class VectorRetriever:
    """基于向量嵌入的检索器"""
    
    def __init__(self, collection):
        """
        初始化向量检索器
        
        Args:
            collection: ChromaDB集合
        """
        self.collection = collection
    
    async def retrieve(self, query: str, top_k: int = 10, 
                       filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        向量检索
        
        Args:
            query: 查询文本
            top_k: 返回前k个结果
            filters: 元数据过滤条件
            
        Returns:
            排序后的文档列表，每个文档包含额外字段：score, rank
        """
        try:
            # 执行检索
            query_results = self.collection.query(
                query_texts=[query],
                n_results=top_k,
                where=filters,
                include=["documents", "metadatas", "distances"]
            )
            
            documents = (query_results.get("documents") or [[]])[0]
            metadatas = (query_results.get("metadatas") or [[]])[0]
            distances = (query_results.get("distances") or [[]])[0]
            
            # 构建结果
            results = []
            for rank, (doc, meta, dist) in enumerate(zip(documents, metadatas, distances)):
                # 将距离转换为相似度（ChromaDB使用余弦距离）
                similarity = 1.0 - float(dist)
                
                result = {
                    'text': doc,
                    'metadata': meta,
                    'score': similarity,
                    'rank': rank + 1
                }
                results.append(result)
            
            return results
        
        except Exception as e:
            print(f"⚠️ [VectorRetriever] 检索失败: {e}")
            return []


class HybridRetriever:
    """混合检索器：融合向量检索和BM25检索"""
    
    def __init__(self, vector_retriever: VectorRetriever, 
                 bm25_retriever: Optional[BM25Retriever] = None,
                 k: int = 60):
        """
        初始化混合检索器
        
        Args:
            vector_retriever: 向量检索器
            bm25_retriever: BM25检索器（可选）
            k: RRF融合参数
        """
        self.vector_retriever = vector_retriever
        self.bm25_retriever = bm25_retriever
        self.k = k  # RRF参数
    
    async def retrieve(
        self,
        query: str,
        top_k: int = 10,
        use_rerank: bool = True,
        use_query_expansion: bool = True,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        混合检索
        
        Args:
            query: 查询文本
            top_k: 返回前k个结果
            use_rerank: 是否使用LLM重排序
            use_query_expansion: 是否使用查询扩展
            filters: 元数据过滤条件
            
        Returns:
            排序后的文档列表
        """
        # 1. 查询扩展
        expanded_queries = [query]
        if use_query_expansion and jieba:
            expanded_queries = self._expand_query(query)
        
        # 2. 执行向量检索
        vector_results = []
        for q in expanded_queries:
            results = await self.vector_retriever.retrieve(q, top_k * 2, filters)
            vector_results.extend(results)
        
        # 3. 执行BM25检索（如果可用）
        bm25_results = []
        if self.bm25_retriever:
            for q in expanded_queries:
                results = self.bm25_retriever.retrieve(q, top_k * 2)
                bm25_results.extend(results)
        
        # 4. 使用RRF融合结果
        fused_results = self._rrf_fusion(vector_results, bm25_results, top_k * 2)
        
        # 5. LLM重排序（如果启用）
        if use_rerank:
            fused_results = await self._llm_rerank(query, fused_results, top_k)
        else:
            # 取top_k
            fused_results = fused_results[:top_k]
        
        return fused_results
    
    def _expand_query(self, query: str, top_n: int = 3) -> List[str]:
        """
        查询扩展：使用TF-IDF提取关键词
        
        Args:
            query: 原始查询
            top_n: 提取关键词数量
            
        Returns:
            扩展后的查询列表
        """
        if jieba is None:
            return [query]
        
        try:
            keywords = jieba.analyse.extract_tags(query, topK=top_n)
            
            # 原始查询
            expanded = [query]
            
            # 关键词组合
            if keywords:
                expanded.append(" ".join(keywords))
            
            return expanded
        
        except Exception as e:
            print(f"⚠️ [QueryExpansion] 扩展失败: {e}")
            return [query]
    
    def _rrf_fusion(self, vector_results: List[Dict], 
                   bm25_results: List[Dict], 
                   top_k: int) -> List[Dict]:
        """
        使用RRF算法融合两种检索结果
        
        Args:
            vector_results: 向量检索结果
            bm25_results: BM25检索结果
            top_k: 返回前k个结果
            
        Returns:
            融合后的结果列表
        """
        # 构建文档ID到分数的映射
        scores = defaultdict(float)
        doc_map = {}
        
        # 处理向量检索结果
        for doc in vector_results:
            doc_id = hash(doc['text'])
            rank = doc.get('rank', len(vector_results))
            
            # RRF公式：1 / (k + rank)
            rrf_score = 1.0 / (self.k + rank)
            scores[doc_id] += rrf_score
            doc_map[doc_id] = doc
        
        # 处理BM25检索结果
        for doc in bm25_results:
            doc_id = hash(doc['text'])
            rank = doc.get('rank', len(bm25_results))
            
            rrf_score = 1.0 / (self.k + rank)
            scores[doc_id] += rrf_score
            
            # 如果文档不在map中，添加
            if doc_id not in doc_map:
                doc_map[doc_id] = doc
        
        # 按融合分数排序
        sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
        
        # 构建最终结果
        results = []
        for doc_id in sorted_ids[:top_k]:
            doc = doc_map[doc_id].copy()
            doc['score'] = scores[doc_id]
            doc['rank'] = results.__len__() + 1
            results.append(doc)
        
        return results
    
    async def _llm_rerank(self, query: str, candidates: List[Dict], top_k: int) -> List[Dict]:
        """
        使用LLM对检索结果重新排序
        
        Args:
            query: 原始查询
            candidates: 候选文档列表
            top_k: 返回前k个结果
            
        Returns:
            重排序后的文档列表
        """
        if not candidates or len(candidates) <= top_k:
            return candidates
        
        try:
            # 构建重排序prompt
            prompt = self._build_rerank_prompt(query, candidates, top_k)
            
            # 调用LLM
            response = await asyncio.to_thread(
                chat_with_ai,
                [{"role": "user", "content": prompt}],
                task_type="default"
            )
            
            # 解析LLM响应
            reranked = self._parse_rerank_response(response, candidates)
            
            if reranked:
                return reranked[:top_k]
            else:
                # 解析失败，返回原始top_k
                return candidates[:top_k]
        
        except Exception as e:
            print(f"⚠️ [LLMRerank] 重排序失败: {e}")
            return candidates[:top_k]
    
    def _build_rerank_prompt(self, query: str, candidates: List[Dict], top_k: int) -> str:
        """
        构建重排序提示词
        
        Args:
            query: 查询文本
            candidates: 候选文档
            top_k: 需要返回的数量
            
        Returns:
            提示词
        """
        docs_text = "\n\n".join([
            f"{i+1}. [{doc.get('score', 0):.2f}] {doc['text'][:200]}..."
            for i, doc in enumerate(candidates[:15])  # 限制候选数量
        ])
        
        prompt = f"""你是一个智能文档排序助手。

用户查询：{query}

候选文档：
{docs_text}

任务：从以上候选文档中选择最相关的 {top_k} 个文档，并按相关性从高到低排序。

输出格式：只输出文档编号，用逗号分隔，例如：3,1,5,2,4

注意：
1. 优先选择直接回答用户问题的文档
2. 选择信息最全面、最准确的文档
3. 考虑文档的语义相关性，不仅仅是关键词匹配
4. 只输出数字编号，不要输出其他内容

请输出排序后的文档编号（只输出 {top_k} 个）："""
        
        return prompt
    
    def _parse_rerank_response(self, response: str, candidates: List[Dict]) -> List[Dict]:
        """
        解析LLM重排序响应
        
        Args:
            response: LLM响应
            candidates: 原始候选文档
            
        Returns:
            重排序后的文档列表
        """
        try:
            # 提取数字
            numbers = re.findall(r'\d+', response)
            indices = [int(n) - 1 for n in numbers if 0 < int(n) <= len(candidates)]
            
            # 去重并保持顺序
            seen = set()
            unique_indices = []
            for idx in indices:
                if idx not in seen:
                    seen.add(idx)
                    unique_indices.append(idx)
            
            # 构建重排序结果
            reranked = []
            for rank, idx in enumerate(unique_indices):
                if idx < len(candidates):
                    doc = candidates[idx].copy()
                    doc['rank'] = rank + 1
                    reranked.append(doc)
            
            return reranked
        
        except Exception as e:
            print(f"⚠️ [LLMRerank] 解析响应失败: {e}")
            return []


class QueryRewriter:
    """查询重写器：优化查询以提高检索质量"""
    
    def __init__(self):
        pass
    
    async def rewrite(self, query: str, context: Optional[str] = None) -> str:
        """
        重写查询
        
        Args:
            query: 原始查询
            context: 对话上下文（可选）
            
        Returns:
            重写后的查询
        """
        # 如果没有jieba，直接返回原查询
        if jieba is None:
            return query
        
        # 简单的重写策略
        keywords = jieba.analyse.extract_tags(query, topK=5)
        
        if not keywords:
            return query
        
        # 返回关键词组合
        rewritten = " ".join(keywords)
        
        # 如果原查询太短，保留原查询
        if len(query) < 10:
            return query
        
        return rewritten
