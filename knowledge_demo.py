"""
高级知识库系统演示脚本
展示如何使用KnowledgeManager进行文档导入和检索
"""
import asyncio
import sys

from modules.advanced_memory import AdvancedMemorySystem
from modules.knowledge import KnowledgeManager


async def main():
    """主函数"""
    print("=" * 60)
    print("高级知识库系统演示")
    print("=" * 60)
    
    # 1. 初始化记忆系统
    print("\n📚 初始化记忆系统...")
    brain = AdvancedMemorySystem()
    knowledge_collection = brain.knowledge_collection
    print("✅ 记忆系统初始化完成")
    
    # 2. 创建知识管理器
    print("\n🔧 创建知识管理器...")
    km = KnowledgeManager(knowledge_collection)
    print("✅ 知识管理器创建完成")
    
    # 3. 显示统计信息
    print("\n📊 知识库统计信息:")
    stats = km.get_statistics()
    print(f"   文档总数: {stats['total_documents']}")
    print(f"   Chunk总数: {stats['total_chunks']}")
    print(f"   总大小: {stats['total_size_bytes'] / 1024:.2f} KB")
    print(f"   文件类型分布: {stats['file_types']}")
    
    # 4. 列出所有文档
    print("\n📋 已导入文档列表:")
    docs = km.list_documents()
    for doc in docs:
        print(f"   - {doc['file_name']}")
        print(f"     类型: {doc['file_type']}, Chunks: {doc['chunks_count']}")
        print(f"     标签: {', '.join(doc['tags'])}")
    
    # 5. 测试检索功能
    print("\n🔍 测试检索功能:")
    test_queries = [
        "五十铃怜",
        "魔法少女",
        "故事"
    ]
    
    for query in test_queries:
        print(f"\n   查询: '{query}'")
        
        # 纯向量检索
        print("   [向量检索]")
        results = await km.retrieve(
            query,
            top_k=3,
            use_hybrid=False,
            use_rerank=False
        )
        
        for i, result in enumerate(results, 1):
            print(f"     {i}. [分数: {result['score']:.3f}] {result['text'][:100]}...")
        
        # 混合检索
        print("   [混合检索]")
        results = await km.retrieve(
            query,
            top_k=3,
            use_hybrid=True,
            use_rerank=False
        )
        
        for i, result in enumerate(results, 1):
            print(f"     {i}. [分数: {result['score']:.3f}] {result['text'][:100]}...")
        
        # 混合检索 + LLM重排序
        print("   [混合检索 + LLM重排序]")
        results = await km.retrieve(
            query,
            top_k=3,
            use_hybrid=True,
            use_rerank=True
        )
        
        for i, result in enumerate(results, 1):
            print(f"     {i}. [分数: {result['score']:.3f}] {result['text'][:100]}...")
    
    # 6. 测试文档导入（可选）
    if len(sys.argv) > 1:
        file_path = sys.argv[1]
        print(f"\n📥 导入文档: {file_path}")
        
        result = await km.import_document(file_path)
        
        if result['success']:
            print(f"✅ 导入成功: {result['chunks_imported']} chunks")
        else:
            print(f"❌ 导入失败: {result['error']}")
    
    print("\n" + "=" * 60)
    print("演示完成")
    print("=" * 60)


if __name__ == "__main__":
    # 运行演示
    asyncio.run(main())
