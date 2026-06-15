from __future__ import annotations

import time
from typing import Any, Optional, Dict, Tuple

try:
    import hou  # type: ignore
except Exception:
    hou = None  # type: ignore


class ExecOpsMixin:
    """Python execution, shell execution, file operations."""

    class _ExecInterrupt(Exception):
        """execute_python 超时或用户停止时抛出的中断异常"""
        pass

    def execute_python(self, code: str, timeout: int = 30) -> Tuple[bool, Dict[str, Any]]:
        """在 Houdini Python 环境中执行代码

        类似 Cursor 的终端功能，可以执行任意 Python 代码。

        Args:
            code: 要执行的 Python 代码
            timeout: 超时时间（秒）

        Returns:
            (success, result) 其中 result 包含:
            {
                "output": str,      # 输出内容
                "return_value": Any, # 最后一个表达式的返回值
                "error": str,       # 错误信息（如果有）
                "execution_time": float  # 执行时间（秒）
            }

        安全注意：
        - 此功能允许执行任意代码，应谨慎使用
        - 危险操作（如删除文件）需要用户确认

        ★ 超时保护（v1.4.5）：
        使用 sys.settrace 在每行 Python 代码执行前检查超时和停止标志。
        超时或用户停止时抛出 _ExecInterrupt 中断代码执行，防止卡死主线程。
        注意：对 C 扩展内部的阻塞（如 hou.node.cook）无法中断，
        但能在 C 调用返回后的下一行 Python 代码处中断。
        """
        if hou is None:
            return False, {"error": "未检测到 Houdini API"}

        if not code or not code.strip():
            return False, {"error": "代码为空"}

        import io
        import sys
        import traceback
        import threading

        start_time = time.time()
        _stop_event = self._stop_event  # 缓存引用
        _deadline = start_time + max(timeout, 5)  # 最少 5 秒
        _check_interval = 0.5  # 每 0.5s 检查一次（避免过于频繁）
        _last_check = [start_time]  # 用列表以便在闭包中修改

        def _trace_timeout(frame, event, arg):
            """sys.settrace 回调：每行代码执行前检查超时和停止标志"""
            now = time.time()
            # 降低检查频率：距上次检查不足 _check_interval 则跳过
            if now - _last_check[0] < _check_interval:
                return _trace_timeout
            _last_check[0] = now
            # 检查停止标志
            if _stop_event and _stop_event.is_set():
                raise ExecOpsMixin._ExecInterrupt("用户已停止执行")
            # 检查超时
            if now > _deadline:
                raise ExecOpsMixin._ExecInterrupt(
                    f"代码执行超时（{timeout}s），已中断。"
                    f"如需更长时间，请增加 timeout 参数。"
                )
            return _trace_timeout

        # 捕获输出
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        old_trace = sys.gettrace()
        captured_output = io.StringIO()
        captured_error = io.StringIO()

        result = {
            "output": "",
            "return_value": None,
            "error": "",
            "execution_time": 0.0
        }

        try:
            sys.stdout = captured_output
            sys.stderr = captured_error

            # 准备执行环境
            exec_globals = {
                'hou': hou,
                '__builtins__': __builtins__,
            }
            exec_locals = {}

            # ★ 安装超时 trace
            sys.settrace(_trace_timeout)

            # 尝试作为表达式求值（返回最后一个值）
            try:
                # 先尝试 eval（单个表达式）
                return_value = eval(code.strip(), exec_globals, exec_locals)
                result["return_value"] = self._safe_repr(return_value)
            except SyntaxError:
                # 不是单个表达式，用 exec 执行
                exec(code, exec_globals, exec_locals)

                # 尝试获取最后一个赋值的值
                if exec_locals:
                    last_var = list(exec_locals.keys())[-1]
                    if not last_var.startswith('_'):
                        result["return_value"] = self._safe_repr(exec_locals[last_var])

            result["output"] = captured_output.getvalue()

            # 检查 stderr
            stderr_content = captured_error.getvalue()
            if stderr_content:
                result["output"] += f"\n[stderr]\n{stderr_content}"

            result["execution_time"] = time.time() - start_time
            return True, result

        except ExecOpsMixin._ExecInterrupt as e:
            result["error"] = str(e)
            result["output"] = captured_output.getvalue()
            result["execution_time"] = time.time() - start_time
            return False, result

        except Exception as e:
            result["error"] = f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
            result["output"] = captured_output.getvalue()
            result["execution_time"] = time.time() - start_time
            return False, result

        finally:
            # ★ 必须恢复原始 trace，否则影响后续所有 Python 执行
            sys.settrace(old_trace)
            sys.stdout = old_stdout
            sys.stderr = old_stderr

    def _safe_repr(self, value: Any, max_length: int = 1000) -> str:
        """安全地获取对象的字符串表示"""
        try:
            # 处理常见类型
            if value is None:
                return "None"
            if isinstance(value, (int, float, bool)):
                return str(value)
            if isinstance(value, str):
                if len(value) > max_length:
                    return repr(value[:max_length] + "...")
                return repr(value)
            if isinstance(value, (list, tuple)):
                if len(value) > 10:
                    items = [self._safe_repr(v, 100) for v in value[:10]]
                    return f"[{', '.join(items)}, ... ({len(value)} items total)]"
                items = [self._safe_repr(v, 100) for v in value]
                return f"[{', '.join(items)}]"
            if isinstance(value, dict):
                if len(value) > 10:
                    items = [f"{k}: {self._safe_repr(v, 100)}" for k, v in list(value.items())[:10]]
                    return f"{{{', '.join(items)}, ... ({len(value)} items total)}}"
                items = [f"{k}: {self._safe_repr(v, 100)}" for k, v in value.items()]
                return f"{{{', '.join(items)}}}"

            # Houdini 对象
            if hou and hasattr(value, 'path'):
                return f"<{type(value).__name__}: {value.path()}>"
            if hou and hasattr(value, 'name'):
                return f"<{type(value).__name__}: {value.name()}>"

            # 默认
            s = repr(value)
            if len(s) > max_length:
                return s[:max_length] + "..."
            return s
        except Exception:
            return f"<{type(value).__name__}>"

    def save_hip(self, file_path: Optional[str] = None) -> Tuple[bool, str]:
        """保存 HIP 文件"""
        if hou is None:
            return False, "未检测到 Houdini API"

        try:
            if file_path:
                hou.hipFile.save(file_path)
                return True, f"已保存到: {file_path}"
            else:
                hou.hipFile.save()
                return True, f"已保存: {hou.hipFile.path()}"
        except Exception as e:
            return False, f"保存失败: {str(e)}"
