import sqlite3
import os

DB_PATH = "./memory/memory.sqlite"


def fix_table():
    print("🛠️ 开始修复数据库表结构...")

    if not os.path.exists(DB_PATH):
        print("❌ 找不到数据库文件，请先运行一次主程序。")
        return

    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()

        # 手动执行建表语句
        print("正在创建 daily_screen_stats 表...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_screen_stats (
                date TEXT PRIMARY KEY,
                summary_json TEXT,
                total_hours REAL,
                updated_at TEXT
            )
        """)

        conn.commit()
        print("✅ 修复完成！daily_screen_stats 表已创建。")
        print("现在重启主程序，红色报错应该会消失。")

    except Exception as e:
        print(f"❌ 修复失败: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    fix_table()