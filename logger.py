"""
日志管理模块 - 精简版
提供基本的彩色控制台输出
"""


class Logger:
    """简化的日志类，只提供控制台彩色输出"""

    # ANSI颜色代码
    COLOR_RESET = "\033[0m"
    COLOR_INFO = "\033[36m"      # 青色
    COLOR_SUCCESS = "\033[32m"   # 绿色
    COLOR_WARNING = "\033[33m"   # 黄色
    COLOR_ERROR = "\033[31m"     # 红色

    def info(self, message: str):
        """普通信息（青色）"""
        print(f"{self.COLOR_INFO}{message}{self.COLOR_RESET}")

    def success(self, message: str):
        """成功信息（绿色）"""
        print(f"{self.COLOR_SUCCESS}✓ {message}{self.COLOR_RESET}")

    def warning(self, message: str):
        """警告信息（黄色）"""
        print(f"{self.COLOR_WARNING}⚠ {message}{self.COLOR_RESET}")

    def error(self, message: str):
        """错误信息（红色）"""
        print(f"{self.COLOR_ERROR}✗ {message}{self.COLOR_RESET}")
