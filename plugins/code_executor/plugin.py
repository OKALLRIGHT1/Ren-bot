import asyncio
import re
import subprocess
import tempfile
import os
import shutil
import sys
from pathlib import Path
from typing import Optional, Dict, Any
from core.logger import get_logger
from plugins.plugin_utils import handle_plugin_errors, safe_get_context

# 导入配置
try:
    from config import (
        CODE_EXECUTOR_MAX_TIME,
        CODE_EXECUTOR_MAX_LENGTH,
        CODE_EXECUTOR_MAX_OUTPUT
    )
except ImportError:
    # 如果配置不存在，使用默认值
    CODE_EXECUTOR_MAX_TIME = 30
    CODE_EXECUTOR_MAX_LENGTH = 5000
    CODE_EXECUTOR_MAX_OUTPUT = 100

# 不在导入时获取logger，而是在使用时获取
# logger = get_logger()

def _get_logger():
    """安全获取logger，如果未初始化则返回None"""
    try:
        return get_logger()
    except:
        return None


class Plugin:
    """
    Python代码执行插件
    支持在受限环境中执行Python代码，用于数据分析和计算
    """
    
    # 安全配置
    ALLOWED_IMPORTS = {
        'math', 'random', 'statistics', 'fractions', 'decimal',
        'datetime', 'json', 'collections', 'itertools', 'functools',
        'typing', 'dataclasses', 'enum',
        'numpy', 'pandas', 'matplotlib.pyplot', 'seaborn',
        'scipy', 'scipy.stats'
    }
    
    RESTRICTED_MODULES = {
        'os', 'sys', 'subprocess', 'shutil', 'importlib',
        'eval', 'exec', 'compile', '__import__',
        'open', 'file', 'input', 'raw_input',
        'socket', 'urllib', 'http', 'ftplib', 'requests',
        'sqlite3', 'pickle', 'hashlib', 'tempfile', 'pathlib',
        'ctypes', 'threading', 'multiprocessing',
        'webbrowser', 'smtplib', 'telnetlib'
    }
    
    # 从配置文件读取安全限制
    MAX_EXECUTION_TIME = CODE_EXECUTOR_MAX_TIME  # 秒
    MAX_CODE_LENGTH = CODE_EXECUTOR_MAX_LENGTH   # 字符
    MAX_OUTPUT_LINES = CODE_EXECUTOR_MAX_OUTPUT   # 行
    MAX_LINE_LENGTH = 200    # 字符
    
    @handle_plugin_errors("代码执行器")
    async def run(self, args: str, ctx: Dict[str, Any]) -> str:
        """
        执行Python代码
        
        Args:
            args: 要执行的Python代码
            ctx: 上下文信息
        
        Returns:
            执行结果或错误信息
        """
        if not args or not args.strip():
            return "❌ 请提供要执行的Python代码"
        
        # 提取代码块（支持```python ... ```格式）
        code = self._extract_code(args)
        
        # 验证代码安全
        validation_result = self._validate_code(code)
        if not validation_result['valid']:
            return f"❌ 代码安全检查失败: {validation_result['reason']}"
        
        log = _get_logger()
        if log:
            log.info(f"准备执行代码: {len(code)} 字符")
        
        # 异步执行代码
        try:
            result = await self._execute_code(code)
            return self._format_output(result)
        except Exception as e:
            log = _get_logger()
            if log:
                log.error(f"代码执行失败: {e}")
            return f"❌ 执行失败: {str(e)}"
    
    def _extract_code(self, text: str) -> str:
        """从文本中提取Python代码块"""
        # 匹配 ```python ... ``` 格式
        code_block = re.search(r'```python\s*?\n(.*?)```', text, re.DOTALL)
        if code_block:
            return code_block.group(1).strip()
        
        # 匹配 ``` ... ``` 格式（无语言标识）
        code_block = re.search(r'```\s*?\n(.*?)```', text, re.DOTALL)
        if code_block:
            return code_block.group(1).strip()
        
        # 直接返回文本
        return text.strip()
    
    def _validate_code(self, code: str) -> Dict[str, Any]:
        """
        验证代码安全性
        
        Returns:
            {'valid': bool, 'reason': str}
        """
        # 检查代码长度
        if len(code) > self.MAX_CODE_LENGTH:
            return {
                'valid': False,
                'reason': f'代码长度超过限制（{self.MAX_CODE_LENGTH}字符）'
            }
        
        # 检查导入语句
        for module in self.RESTRICTED_MODULES:
            # 检查 import module, from module import, __import__(module)
            patterns = [
                rf'\bimport\s+{module}\b',
                rf'\bfrom\s+{module}\s+import\b',
                rf'__import__\([\'"]{module}[\'"]\)'
            ]
            for pattern in patterns:
                if re.search(pattern, code, re.MULTILINE):
                    return {
                        'valid': False,
                        'reason': f'禁止使用模块: {module}'
                    }
        
        # 检查危险函数
        dangerous_patterns = [
            r'\beval\s*\(', r'\bexec\s*\(', r'\bcompile\s*\(',
            r'\bopen\s*\(', r'\bfile\s*\(',
            r'\bsubprocess\.', r'\bos\.system',
            r'\b__import__\s*\('
        ]
        
        for pattern in dangerous_patterns:
            if re.search(pattern, code):
                return {
                    'valid': False,
                    'reason': f'禁止使用危险函数: {pattern}'
                }
        
        # 检查无限循环风险（简单启发式）
        if re.search(r'while\s+True:', code):
            if 'break' not in code:
                return {
                    'valid': False,
                    'reason': '检测到潜在无限循环'
                }
        
        return {'valid': True, 'reason': ''}
    
    async def _execute_code(self, code: str) -> Dict[str, Any]:
        """
        在独立进程中执行代码
        
        Returns:
            {'success': bool, 'stdout': str, 'stderr': str, 'execution_time': float}
        """
        # 创建临时工作目录
        temp_dir = tempfile.mkdtemp(prefix='code_executor_')
        
        try:
            # 写入代码文件
            code_file = Path(temp_dir) / 'script.py'
            code_file.write_text(code, encoding='utf-8')
            
            # 准备执行环境
            env = os.environ.copy()
            # 移除网络相关的环境变量
            env.pop('HTTP_PROXY', None)
            env.pop('HTTPS_PROXY', None)
            env.pop('http_proxy', None)
            env.pop('https_proxy', None)
            # 设置PYTHONPATH为空，禁止导入项目模块
            env['PYTHONPATH'] = ''
            
            # 执行代码
            start_time = asyncio.get_event_loop().time()
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(code_file),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=temp_dir,
                env=env
            )
            
            try:
                # 等待完成，带超时
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.MAX_EXECUTION_TIME
                )
                
                execution_time = asyncio.get_event_loop().time() - start_time
                
                return {
                    'success': process.returncode == 0,
                    'stdout': stdout.decode('utf-8', errors='replace'),
                    'stderr': stderr.decode('utf-8', errors='replace'),
                    'execution_time': execution_time,
                    'returncode': process.returncode
                }
                
            except asyncio.TimeoutError:
                # 超时，强制终止进程
                try:
                    process.kill()
                    await process.wait()
                except:
                    pass
                
                return {
                    'success': False,
                    'stdout': '',
                    'stderr': f'执行超时（超过{self.MAX_EXECUTION_TIME}秒）',
                    'execution_time': self.MAX_EXECUTION_TIME,
                    'returncode': -1
                }
                
        finally:
            # 清理临时目录
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception as e:
                log = _get_logger()
                if log:
                    log.warning(f"清理临时目录失败: {e}")
    
    def _format_output(self, result: Dict[str, Any]) -> str:
        """格式化执行结果"""
        if not result['success']:
            # 执行失败，显示错误信息
            if result['stderr']:
                error_lines = result['stderr'].strip().split('\n')
                # 只显示最后几行错误
                error_msg = '\n'.join(error_lines[-5:])
                return f"❌ 执行失败:\n```\n{error_msg}\n```"
            return "❌ 执行失败，无错误信息"
        
        # 执行成功
        output = result['stdout'].strip()
        
        if not output:
            return "✅ 执行成功（无输出）"
        
        # 限制输出长度
        lines = output.split('\n')
        if len(lines) > self.MAX_OUTPUT_LINES:
            output = '\n'.join(lines[:self.MAX_OUTPUT_LINES])
            output += f"\n...（还有 {len(lines) - self.MAX_OUTPUT_LINES} 行）"
        
        # 截断过长的行
        lines = output.split('\n')
        truncated_lines = []
        for line in lines:
            if len(line) > self.MAX_LINE_LENGTH:
                truncated_lines.append(line[:self.MAX_LINE_LENGTH] + '...')
            else:
                truncated_lines.append(line)
        output = '\n'.join(truncated_lines)
        
        # 格式化输出
        execution_time = result['execution_time']
        time_str = f"{execution_time:.2f}秒" if execution_time >= 0.1 else f"{execution_time*1000:.0f}毫秒"
        
        return f"✅ 执行成功（耗时: {time_str}）:\n```\n{output}\n```"
