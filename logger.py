"""
日志管理模块 - 精简版
提供基本的彩色控制台输出
"""
import sys
import io


class Logger:
    """简化的日志类，只提供控制台彩色输出"""

    # ANSI颜色代码
    COLOR_RESET = "\033[0m"
    COLOR_INFO = "\033[36m"      # 青色
    COLOR_SUCCESS = "\033[32m"   # 绿色
    COLOR_WARNING = "\033[33m"   # 黄色
    COLOR_ERROR = "\033[31m"     # 红色

    def __init__(self):
        """初始化日志器，配置UTF-8输出"""
        # 尝试将stdout重新配置为UTF-8编码
        try:
            if sys.stdout.encoding != 'utf-8':
                sys.stdout = io.TextIOWrapper(
                    sys.stdout.buffer,
                    encoding='utf-8',
                    errors='replace'  # 无法编码的字符用?替代
                )
        except:
            # 如果失败，保持默认配置
            pass

    def _safe_print(self, text: str):
        """安全打印，处理编码问题"""
        try:
            print(text)
        except UnicodeEncodeError:
            # 尝试使用errors='replace'
            try:
                print(text.encode(sys.stdout.encoding or 'utf-8', errors='replace').decode(sys.stdout.encoding or 'utf-8'))
            except:
                # 最后的fallback：只保留ASCII字符
                print(text.encode('ascii', errors='replace').decode('ascii'))

    def info(self, message: str):
        """普通信息（青色）"""
        self._safe_print(f"{self.COLOR_INFO}{message}{self.COLOR_RESET}")

    def success(self, message: str):
        """成功信息（绿色）"""
        try:
            self._safe_print(f"{self.COLOR_SUCCESS}✓ {message}{self.COLOR_RESET}")
        except:
            self._safe_print(f"{self.COLOR_SUCCESS}[OK] {message}{self.COLOR_RESET}")

    def warning(self, message: str):
        """警告信息（黄色）"""
        try:
            self._safe_print(f"{self.COLOR_WARNING}⚠ {message}{self.COLOR_RESET}")
        except:
            self._safe_print(f"{self.COLOR_WARNING}[WARN] {message}{self.COLOR_RESET}")

    def error(self, message: str):
        """错误信息（红色）"""
        try:
            self._safe_print(f"{self.COLOR_ERROR}✗ {message}{self.COLOR_RESET}")
        except:
            self._safe_print(f"{self.COLOR_ERROR}[ERROR] {message}{self.COLOR_RESET}")
