# main.py (守护进程版)
import sys
import subprocess
import time
import os

# 定义重启暗号 (如果在程序里 sys.exit(100)，守护进程就会立刻重启而不等待)
RESTART_EXIT_CODE = 100

def run_worker():
    """启动子进程运行 boot.py"""
    # 获取当前 python解释器路径
    python_exe = sys.executable
    # 获取 boot.py 的绝对路径
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "boot.py")
    
    print(f"🚀 [守护进程] 正在启动核心: {script_path}")
    
    # 启动子进程
    process = subprocess.Popen([python_exe, script_path])
    
    # 等待子进程结束
    process.wait()
    
    return process.returncode

def main():
    print("🛡️ [守护进程] Live2D-Suzu 崩溃守护已启动")
    
    while True:
        try:
            exit_code = run_worker()
            
            # 情况1: 正常退出 (Exit Code 0)
            if exit_code == 0:
                print("👋 [守护进程] 核心程序正常退出，即将关闭。")
                break
                
            # 情况2: 要求立刻重启 (Exit Code 100)
            # 比如你在聊天框输入 /reload 时，可以让程序 sys.exit(100)
            elif exit_code == RESTART_EXIT_CODE:
                print("♻️ [守护进程] 接收到重启指令，正在立即重载...")
                time.sleep(1) # 稍微歇一下防止IO冲突
                continue
                
            # 情况3: 异常崩溃 (Exit Code != 0)
            else:
                print(f"❌ [守护进程] 核心程序异常退出 (代码: {exit_code})")
                print("⚠️ [守护进程] 3秒后尝试自动复活...")
                time.sleep(3) # 冷却时间，防止无限快速重启卡死电脑
                
        except KeyboardInterrupt:
            print("\n🛑 [守护进程] 收到键盘中断，停止守护。")
            break
        except Exception as e:
            print(f"☠️ [守护进程] 守护进程本身发生错误: {e}")
            break

if __name__ == "__main__":
    main()