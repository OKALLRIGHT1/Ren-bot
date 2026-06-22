# main.py (守护进程版)
import sys

# Force standard streams to use UTF-8 encoding on Windows to prevent UnicodeEncodeError with emojis
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

import subprocess
import time
import os
import atexit
import hashlib

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

# 定义重启暗号 (如果在程序里 sys.exit(100)，守护进程就会立刻重启而不等待)
RESTART_EXIT_CODE = 100
LOCK_FILE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "live2d_suzu_main.lock"
)
_LOCK_HANDLE = None

if os.name == "nt":
    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _KERNEL32.CreateMutexW.argtypes = (
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    )
    _KERNEL32.CreateMutexW.restype = wintypes.HANDLE
    _KERNEL32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _KERNEL32.CloseHandle.restype = wintypes.BOOL
    _ERROR_ALREADY_EXISTS = 183


def _lock_name() -> str:
    root = os.path.dirname(os.path.abspath(__file__)).lower()
    digest = hashlib.sha1(root.encode("utf-8")).hexdigest()
    return f"Local\\Live2D_Suzu_Main_{digest}"


def acquire_single_instance_lock():
    """Prevent multiple watchdog processes from managing the same workspace."""
    global _LOCK_HANDLE
    if os.name == "nt":
        handle = _KERNEL32.CreateMutexW(None, True, _lock_name())
        if not handle:
            return None
        if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
            _KERNEL32.CloseHandle(handle)
            return None
        _LOCK_HANDLE = handle
        atexit.register(release_single_instance_lock, handle)
        return handle

    os.makedirs(os.path.dirname(LOCK_FILE_PATH), exist_ok=True)
    lock_file = open(LOCK_FILE_PATH, "a+", encoding="utf-8")
    try:
        import fcntl

        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_file.close()
        return None
    try:
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(str(os.getpid()))
        lock_file.flush()
    except OSError:
        try:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        lock_file.close()
        return None
    atexit.register(release_single_instance_lock, lock_file)
    return lock_file


def release_single_instance_lock(lock_file):
    global _LOCK_HANDLE
    if os.name == "nt":
        try:
            if lock_file:
                _KERNEL32.CloseHandle(lock_file)
        except Exception:
            pass
        if lock_file == _LOCK_HANDLE:
            _LOCK_HANDLE = None
        return

    try:
        import fcntl

        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        lock_file.close()
    except Exception:
        pass

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
    lock_file = acquire_single_instance_lock()
    if lock_file is None:
        print("⚠️ [守护进程] 已有 Live2D-Suzu 实例在运行，本次启动已退出。")
        return

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
