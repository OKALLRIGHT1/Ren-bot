"""
知识管理器
统一管理文档导入、分块、检索等所有知识库操作
"""
import os
import hashlib
import json
import asyncio
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone
from pathlib import Path

from modules.knowledge.chunker import SemanticChunker, SemanticChunkerWithEmbedding
from modules.knowledge.retriever import HybridRetriever, BM25Retriever, VectorRetriever

try:
    from modules.embeddings.embedding_service import EmbeddingService
except ImportError:
    EmbeddingService = None


class DocumentMetadata:
    """文档元数据"""
    
    def __init__(self, file_path: str, chunk_id: str = None, **kwargs):
        self.file_path = file_path
        self.file_name = os.path.basename(file_path)
        self.file_type = self._get_file_type(file_path)
        self.file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        self.chunk_id = chunk_id
        self.imported_at = datetime.now(timezone.utc).isoformat()
        
        # 自定义元数据
        for key, value in kwargs.items():
            setattr(self, key, value)
    
    @staticmethod
    def _get_file_type(file_path: str) -> str:
        """获取文件类型"""
        ext = os.path.splitext(file_path)[1].lower()
        type_map = {
            '.txt': 'text',
            '.md': 'markdown',
            '.pdf': 'pdf',
            '.docx': 'docx',
            '.html': 'html',
            '.htm': 'html',
        }
        return type_map.get(ext, 'unknown')
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        result = {}
        for key, value in self.__dict__.items():
            if not key.startswith('_'):
                result[key] = value
        return result


class KnowledgeManager:
    """知识管理器"""
    
    def __init__(
        self,
        knowledge_collection,
        embedding_service: Optional[Any] = None,
        use_semantic_chunker: bool = False
    ):
        """
        初始化知识管理器
        
        Args:
            knowledge_collection: ChromaDB知识库集合
            embedding_service: 嵌入服务（可选）
            use_semantic_chunker: 是否使用语义分块器
        """
        self.collection = knowledge_collection
        self.embedding_service = embedding_service
        
        # 初始化分块器
        if use_semantic_chunker and embedding_service:
            self.chunker = SemanticChunkerWithEmbedding(embedding_service)
        else:
            self.chunker = SemanticChunker()
        
        # 检索器
        self.vector_retriever = None
        self.bm25_retriever = None
        self.hybrid_retriever = None
        
        # 文档索引
        self.documents_index = {}
        self._load_index()
    
    def _load_index(self):
        """加载文档索引"""
        try:
            # 从ChromaDB加载所有文档元数据
            all_docs = self.collection.get(include=["metadatas", "documents"])
            
            for metadata, doc in zip(all_docs["metadatas"], all_docs["documents"]):
                file_path = metadata.get("file_path", "")
                if file_path:
                    if file_path not in self.documents_index:
                        self.documents_index[file_path] = []
                    self.documents_index[file_path].append({
                        "chunk_id": metadata.get("chunk_id"),
                        "text": doc,
                        "metadata": metadata
                    })
            
            print(f"✅ [KnowledgeManager] 加载了 {len(self.documents_index)} 个文档的索引")
        
        except Exception as e:
            print(f"⚠️ [KnowledgeManager] 加载索引失败: {e}")
    
    def _save_index(self):
        """保存文档索引（持久化）"""
        # 索引已经存储在ChromaDB中，这里可以额外保存到文件
        index_file = "knowledge_docs/index.json"
        os.makedirs(os.path.dirname(index_file), exist_ok=True)
        
        try:
            with open(index_file, 'w', encoding='utf-8') as f:
                json.dump(self.documents_index, f, ensure_ascii=False, indent=2)
            print(f"💾 [KnowledgeManager] 索引已保存到 {index_file}")
        except Exception as e:
            print(f"⚠️ [KnowledgeManager] 保存索引失败: {e}")
    
    def _generate_chunk_id(self, file_path: str, chunk_index: int) -> str:
        """生成chunk唯一ID"""
        file_hash = hashlib.md5(file_path.encode('utf-8')).hexdigest()[:8]
        return f"{file_hash}_{chunk_index}"
    
    def _extract_tags(self, text: str, max_tags: int = 5) -> List[str]:
        """
        从文本中提取标签
        
        Args:
            text: 文本内容
            max_tags: 最大标签数
            
        Returns:
            标签列表
        """
        try:
            import jieba
            import jieba.analyse
            keywords = jieba.analyse.extract_tags(text, topK=max_tags)
            return keywords
        except ImportError:
            # 简单的关键词提取
            words = text.split()
            return [w for w in words if len(w) > 1][:max_tags]
    
    def _summarize_text(self, text: str, max_length: int = 200) -> str:
        """
        生成文本摘要
        
        Args:
            text: 文本内容
            max_length: 最大长度
            
        Returns:
            摘要文本
        """
        # 简单摘要：取前max_length个字符
        if len(text) <= max_length:
            return text
        return text[:max_length] + "..."
    
    async def import_document(
        self,
        file_path: str,
        metadata: Optional[Dict[str, Any]] = None,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        导入单个文档
        
        Args:
            file_path: 文件路径
            metadata: 额外元数据
            chunk_size: 自定义块大小
            chunk_overlap: 自定义块重叠
            
        Returns:
            导入结果统计
        """
        if not os.path.exists(file_path):
            return {
                "success": False,
                "error": f"文件不存在: {file_path}",
                "chunks_imported": 0
            }
        
        # 读取文件内容
        content = self._read_file(file_path)
        if not content:
            return {
                "success": False,
                "error": f"无法读取文件: {file_path}",
                "chunks_imported": 0
            }
        
        # 分块
        chunks = self.chunker.chunk(
            content,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap
        )
        
        if not chunks:
            return {
                "success": False,
                "error": f"分块失败，没有生成chunks",
                "chunks_imported": 0
            }
        
        # 导入到ChromaDB
        imported_count = 0
        for i, chunk_data in enumerate(chunks):
            # 生成元数据
            doc_meta = DocumentMetadata(
                file_path=file_path,
                chunk_id=self._generate_chunk_id(file_path, i)
            )
            doc_meta.summary = self._summarize_text(chunk_data['text'])
            doc_meta.tags = self._extract_tags(chunk_data['text'])
            
            # 合并自定义元数据
            if metadata:
                for key, value in metadata.items():
                    setattr(doc_meta, key, value)
            
            # 添加到集合
            try:
                self.collection.add(
                    documents=[chunk_data['text']],
                    metadatas=[doc_meta.to_dict()],
                    ids=[f"doc_{doc_meta.chunk_id}"]
                )
                imported_count += 1
            except Exception as e:
                print(f"⚠️ [KnowledgeManager] 添加chunk失败: {e}")
        
        # 更新索引
        if imported_count > 0:
            if file_path not in self.documents_index:
                self.documents_index[file_path] = []
            
            for i, chunk_data in enumerate(chunks[:imported_count]):
                chunk_id = self._generate_chunk_id(file_path, i)
                doc_meta = DocumentMetadata(file_path, chunk_id)
                doc_meta.summary = self._summarize_text(chunk_data['text'])
                
                self.documents_index[file_path].append({
                    "chunk_id": chunk_id,
                    "text": chunk_data['text'],
                    "metadata": doc_meta.to_dict()
                })
            
            self._save_index()
        
        print(f"✅ [KnowledgeManager] 导入文档: {file_path} ({imported_count}/{len(chunks)} chunks)")
        
        return {
            "success": True,
            "file_path": file_path,
            "chunks_imported": imported_count,
            "total_chunks": len(chunks)
        }
    
    def _read_file(self, file_path: str) -> Optional[str]:
        """
        读取文件内容
        
        Args:
            file_path: 文件路径
            
        Returns:
            文件内容
        """
        file_type = DocumentMetadata._get_file_type(file_path)
        
        try:
            if file_type in ['text', 'markdown']:
                with open(file_path, 'r', encoding='utf-8') as f:
                    return f.read()
            
            elif file_type == 'pdf':
                from modules.parsers.pdf_parser import extract_text
                return extract_text(file_path)
            
            elif file_type == 'docx':
                from modules.parsers.docx_parser import extract_text
                return extract_text(file_path)
            
            elif file_type in ['html', 'htm']:
                from modules.parsers.markdown_parser import html_to_markdown
                return html_to_markdown(file_path)
            
            else:
                print(f"⚠️ [KnowledgeManager] 不支持的文件类型: {file_type}")
                return None
        
        except Exception as e:
            print(f"⚠️ [KnowledgeManager] 读取文件失败: {e}")
            return None
    
    async def import_directory(
        self,
        directory: str,
        file_patterns: List[str] = None,
        recursive: bool = True
    ) -> Dict[str, Any]:
        """
        批量导入目录中的文档
        
        Args:
            directory: 目录路径
            file_patterns: 文件模式列表（如['*.txt', '*.pdf']）
            recursive: 是否递归子目录
            
        Returns:
            导入结果统计
        """
        if not os.path.exists(directory):
            return {
                "success": False,
                "error": f"目录不存在: {directory}",
                "files_processed": 0,
                "chunks_imported": 0
            }
        
        # 默认文件模式
        if file_patterns is None:
            file_patterns = ['*.txt', '*.md', '*.pdf', '*.docx', '*.html']
        
        # 查找文件
        files = []
        dir_path = Path(directory)
        
        for pattern in file_patterns:
            if recursive:
                files.extend(dir_path.rglob(pattern))
            else:
                files.extend(dir_path.glob(pattern))
        
        # 去重
        file_paths = list(set([str(f) for f in files]))
        
        print(f"📁 [KnowledgeManager] 找到 {len(file_paths)} 个文件")
        
        # 批量导入
        results = {
            "success": True,
            "directory": directory,
            "files_processed": 0,
            "chunks_imported": 0,
            "failed_files": []
        }
        
        for file_path in file_paths:
            result = await self.import_document(file_path)
            
            if result["success"]:
                results["files_processed"] += 1
                results["chunks_imported"] += result["chunks_imported"]
            else:
                results["failed_files"].append({
                    "file_path": file_path,
                    "error": result.get("error", "Unknown error")
                })
        
        print(f"✅ [KnowledgeManager] 批量导入完成: {results['files_processed']} 个文件, {results['chunks_imported']} 个chunks")
        
        return results
    
    def list_documents(self) -> List[Dict[str, Any]]:
        """
        列出所有已导入的文档
        
        Returns:
            文档列表
        """
        docs = []
        for file_path, chunks in self.documents_index.items():
            if chunks:
                metadata = chunks[0].get("metadata", {})
                docs.append({
                    "file_path": file_path,
                    "file_name": metadata.get("file_name", os.path.basename(file_path)),
                    "file_type": metadata.get("file_type", "unknown"),
                    "file_size": metadata.get("file_size", 0),
                    "chunks_count": len(chunks),
                    "imported_at": metadata.get("imported_at", ""),
                    "tags": metadata.get("tags", [])
                })
        
        return docs
    
    def delete_document(self, file_path: str) -> Dict[str, Any]:
        """
        删除文档
        
        Args:
            file_path: 文件路径
            
        Returns:
            删除结果
        """
        if file_path not in self.documents_index:
            return {
                "success": False,
                "error": f"文档不存在: {file_path}",
                "chunks_deleted": 0
            }
        
        chunks = self.documents_index[file_path]
        chunk_ids = [f"doc_{chunk['chunk_id']}" for chunk in chunks]
        
        try:
            # 从ChromaDB删除
            self.collection.delete(ids=chunk_ids)
            
            # 从索引删除
            deleted_count = len(chunks)
            del self.documents_index[file_path]
            
            self._save_index()
            
            print(f"✅ [KnowledgeManager] 删除文档: {file_path} ({deleted_count} chunks)")
            
            return {
                "success": True,
                "file_path": file_path,
                "chunks_deleted": deleted_count
            }
        
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "chunks_deleted": 0
            }
    
    def get_document_chunks(self, file_path: str) -> List[Dict[str, Any]]:
        """
        获取文档的所有chunks
        
        Args:
            file_path: 文件路径
            
        Returns:
            chunks列表
        """
        return self.documents_index.get(file_path, [])
    
    def _init_retrievers(self):
        """初始化检索器"""
        if self.vector_retriever is None:
            self.vector_retriever = VectorRetriever(self.collection)
        
        if self.bm25_retriever is None:
            # 构建BM25索引
            all_docs = []
            for chunks in self.documents_index.values():
                all_docs.extend(chunks)
            
            if all_docs:
                self.bm25_retriever = BM25Retriever(all_docs)
        
        if self.hybrid_retriever is None:
            self.hybrid_retriever = HybridRetriever(
                self.vector_retriever,
                self.bm25_retriever
            )
    
    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        use_hybrid: bool = True,
        use_rerank: bool = False,
        file_filters: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        检索相关知识
        
        Args:
            query: 查询文本
            top_k: 返回前k个结果
            use_hybrid: 是否使用混合检索
            use_rerank: 是否使用LLM重排序
            file_filters: 文件路径过滤列表
            
        Returns:
            检索结果列表
        """
        self._init_retrievers()
        
        # 构建过滤条件
        filters = None
        if file_filters:
            filters = {"file_path": {"$in": file_filters}}
        
        # 执行检索
        if use_hybrid and self.hybrid_retriever:
            results = await self.hybrid_retriever.retrieve(
                query,
                top_k=top_k,
                use_rerank=use_rerank,
                filters=filters
            )
        elif self.vector_retriever:
            results = await self.vector_retriever.retrieve(
                query,
                top_k=top_k,
                filters=filters
            )
        else:
            results = []
        
        return results
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        获取知识库统计信息
        
        Returns:
            统计信息
        """
        total_chunks = sum(len(chunks) for chunks in self.documents_index.values())
        total_size = sum(
            sum(chunk.get("metadata", {}).get("file_size", 0) for chunk in chunks)
            for chunks in self.documents_index.values()
        )
        
        # 文件类型分布
        type_counts = {}
        for chunks in self.documents_index.values():
            if chunks:
                file_type = chunks[0].get("metadata", {}).get("file_type", "unknown")
                type_counts[file_type] = type_counts.get(file_type, 0) + 1
        
        return {
            "total_documents": len(self.documents_index),
            "total_chunks": total_chunks,
            "total_size_bytes": total_size,
            "file_types": type_counts
        }
