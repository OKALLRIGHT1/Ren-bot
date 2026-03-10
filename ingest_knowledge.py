import os
from modules.advanced_memory import AdvancedMemorySystem


def main():
    print("=== 开始导入知识库 ===")

    # 1. 初始化记忆系统
    brain = AdvancedMemorySystem()

    # 2. 定义知识文件夹
    know_dir = "./knowledge_docs"
    if not os.path.exists(know_dir):
        os.makedirs(know_dir)
        print(f"❌ 文件夹 {know_dir} 不存在，已自动创建。请放入 txt 文件后重试。")
        return

    # 3. 遍历文件夹里的所有 txt
    total_chunks = 0
    for filename in os.listdir(know_dir):
        if filename.endswith(".txt"):
            file_path = os.path.join(know_dir, filename)
            count = brain.import_knowledge_from_file(file_path)
            total_chunks += count

    print(f"\n🎉 导入完成！总计新增 {total_chunks} 条知识点。")
    print("现在运行 main.py，她就能用到这些知识了。")


if __name__ == "__main__":
    main()