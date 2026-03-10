"""
智能文档分块器
基于语义相似度的自适应分块，保持上下文完整性
"""
import re
from typing import List, Dict, Optional
import numpy as np

from core.logger import get_logger

logger = get_logger(__name__)


class SemanticChunker:
    """
    语义分块器
    
    特点：
    - 基于段落、章节的边界分块
    - 使用语义相似度找到最佳分割点
    - 自适应块大小（根据内容密度）
    - 保持上下文完整性
    """
    
    def __init__(
        self,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        min_chunk_size: int = 100,
        max_chunk_size: int = 2000,
        use_semantic_split: bool = True,
    ):
        """
        初始化分块器
        
        Args:
            chunk_size: 目标块大小（字符数）
            chunk_overlap: 块之间重叠字符数
            min_chunk_size: 最小块大小
            max_chunk_size: 最大块大小
            use_semantic_split: 是否使用语义分割
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.min_chunk_size = min_chunk_size
        self.max_chunk_size = max_chunk_size
        self.use_semantic_split = use_semantic_split
        
        logger.info(f"语义分块器初始化: size={chunk_size}, overlap={chunk_overlap}")
    
    def chunk(self, text: str, metadata: Optional[Dict] = None) -> List[Dict]:
        """
        将文本分块
        
        Args:
            text: 要分块的文本
            metadata: 文档元数据
            
        Returns:
            分块列表，每个块包含：
            - content: 分块内容
            - metadata: 分块元数据
            - chunk_index: 分块索引
        """
        if not text or not text.strip():
            return []
        
        # 1. 预处理：清理文本
        text = self._clean_text(text)
        
        # 2. 按段落分割
        paragraphs = self._split_into_paragraphs(text)
        
        if not paragraphs:
            return []
        
        # 3. 创建分块
        chunks = []
        current_chunk = ""
        chunk_index = 0
        
        for i, para in enumerate(paragraphs):
            para = para.strip()
            if not para:
                continue
            
            # 如果当前段落单独就能构成一个块
            if len(para) > self.max_chunk_size:
                # 需要进一步分割这个大段落
                if current_chunk:
                    chunks.append(self._create_chunk(current_chunk, chunk_index, metadata))
                    chunk_index += 1
                    current_chunk = ""
                
                # 分割大段落
                sub_chunks = self._split_large_paragraph(para)
                for sub_chunk in sub_chunks:
                    chunks.append(self._create_chunk(sub_chunk, chunk_index, metadata))
                    chunk_index += 1
            
            # 如果加入这个段落会超过最大块大小
            elif len(current_chunk) + len(para) > self.max_chunk_size:
                if current_chunk:
                    chunks.append(self._create_chunk(current_chunk, chunk_index, metadata))
                    chunk_index += 1
                
                current_chunk = para
            
            # 如果加入这个段落会超过目标块大小，且不是第一个段落
            elif len(current_chunk) + len(para) > self.chunk_size and current_chunk:
                # 尝试在这里分割（语义边界）
                if self.use_semantic_split and self._is_good_split_point(para):
                    chunks.append(self._create_chunk(current_chunk, chunk_index, metadata))
                    chunk_index += 1
                    current_chunk = para
                else:
                    current_chunk += "\n\n" + para
            else:
                if current_chunk:
                    current_chunk += "\n\n" + para
                else:
                    current_chunk = para
        
        # 添加最后一个块
        if current_chunk:
            chunks.append(self._create_chunk(current_chunk, chunk_index, metadata))
        
        logger.info(f"文本分块完成: {len(chunks)} 个块")
        return chunks
    
    def _clean_text(self, text: str) -> str:
        """清理文本"""
        # 移除多余的空行
        text = re.sub(r'\n{3,}', '\n\n', text)
        # 统一换行符
        text = text.replace('\r\n', '\n')
        return text.strip()
    
    def _split_into_paragraphs(self, text: str) -> List[str]:
        """按段落分割文本"""
        # 尝试按双换行符分割
        paragraphs = text.split('\n\n')
        
        # 如果没有双换行符，按单换行符分割
        if len(paragraphs) == 1:
            paragraphs = text.split('\n')
        
        # 过滤空段落
        paragraphs = [p for p in paragraphs if p.strip()]
        
        return paragraphs
    
    def _split_large_paragraph(self, text: str) -> List[str]:
        """分割过大的段落"""
        # 按句子分割
        sentences = self._split_into_sentences(text)
        
        chunks = []
        current_chunk = ""
        
        for sent in sentences:
            if len(current_chunk) + len(sent) > self.chunk_size and current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = sent
            else:
                current_chunk += " " + sent if current_chunk else sent
        
        if current_chunk:
            chunks.append(current_chunk.strip())
        
        return chunks
    
    def _split_into_sentences(self, text: str) -> List[str]:
        """按句子分割（中文+英文）"""
        # 中英文句子分割
        sentences = re.split(r'([。！？.!?])', text)
        
        result = []
        for i in range(0, len(sentences) - 1, 2):
            sentence = sentences[i] + sentences[i + 1]
            if sentence.strip():
                result.append(sentence.strip())
        
        if len(sentences) % 2 == 1:
            last = sentences[-1].strip()
            if last:
                result.append(last)
        
        return result
    
    def _is_good_split_point(self, text: str) -> bool:
        """
        判断是否是好的分割点（基于启发式规则）
        在实际应用中，可以使用句子嵌入计算相似度
        """
        # 简单的启发式规则
        # 1. 以句号结尾
        if text.endswith('。') or text.endswith('!') or text.endswith('?'):
            return True
        
        # 2. 包含关键词（如"因此"、"所以"等）
        split_keywords = ['因此', '所以', '此外', '另外', '然而', '但是']
        for kw in split_keywords:
            if text.startswith(kw):
                return True
        
        return False
    
    def _create_chunk(
        self,
        content: str,
        chunk_index: int,
        metadata: Optional[Dict] = None
    ) -> Dict:
        """创建分块"""
        chunk_metadata = {
            'chunk_index': chunk_index,
            'chunk_size': len(content),
        }
        
        if metadata:
            chunk_metadata.update(metadata)
        
        return {
            'content': content,
            'metadata': chunk_metadata
        }
    
    def add_overlap(self, chunks: List[Dict]) -> List[Dict]:
        """
        为分块添加重叠内容
        
        Args:
            chunks: 分块列表
            
        Returns:
            添加重叠后的分块列表
        """
        if self.chunk_overlap == 0:
            return chunks
        
        result = []
        
        for i, chunk in enumerate(chunks):
            content = chunk['content']
            metadata = chunk['metadata'].copy()
            
            # 如果不是第一个块，添加前一个块的尾部
            if i > 0:
                prev_content = chunks[i - 1]['content']
                overlap = prev_content[-self.chunk_overlap:] if len(prev_content) > self.chunk_overlap else prev_content
                content = overlap + "\n\n" + content
            
            result.append({
                'content': content,
                'metadata': metadata
            })
        
        return result


class SemanticChunkerWithEmbedding(SemanticChunker):
    """
    基于嵌入的语义分块器
    使用句子嵌入计算语义相似度，找到最佳分割点
    """
    
    def __init__(self, *args, embedding_fn=None, **kwargs):
        """
        初始化
        
        Args:
            embedding_fn: 嵌入函数，接受文本列表返回嵌入向量列表
        """
        super().__init__(*args, **kwargs)
        self.embedding_fn = embedding_fn
    
    def _is_good_split_point(self, text: str, prev_text: str = "") -> bool:
        """
        使用嵌入判断是否是好的分割点
        
        Args:
            text: 当前段落
            prev_text: 前一个段落
            
        Returns:
            如果是好的分割点返回True
        """
        if not self.embedding_fn:
            return super()._is_good_split_point(text)
        
        if not prev_text:
            return True
        
        try:
            # 计算两个段落的嵌入
            embeddings = self.embedding_fn([prev_text, text])
            
            if len(embeddings) < 2:
                return True
            
            # 计算余弦相似度
            sim = np.dot(embeddings[0], embeddings[1]) / (
                np.linalg.norm(embeddings[0]) * np.linalg.norm(embeddings[1])
            )
            
            # 如果相似度低，说明语义发生了变化，是好的分割点
            return sim < 0.7
            
        except Exception as e:
            logger.warning(f"计算嵌入相似度失败: {e}")
            return super()._is_good_split_point(text)
