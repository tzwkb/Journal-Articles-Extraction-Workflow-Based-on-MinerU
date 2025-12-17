"""
API调用重试工具 - 支持指数退避和智能错误处理
"""

import time
import os
import requests
from typing import Callable, Any, Optional
from urllib3.exceptions import NameResolutionError, MaxRetryError
from requests.exceptions import (
    ConnectionError,
    Timeout,
    HTTPError,
    RequestException
)
from requests.adapters import HTTPAdapter


# ===== 强制禁用系统代理 =====
# 清除所有可能的代理环境变量
for key in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy']:
    os.environ.pop(key, None)


# ===== 全局 Session 管理器（连接复用） =====
_global_session = None


def get_global_session() -> requests.Session:
    """获取全局共享的 Session 对象（单例模式）"""
    global _global_session
    if _global_session is None:
        _global_session = requests.Session()

        # 配置连接池（适用于所有API调用）
        adapter = HTTPAdapter(
            pool_connections=50,
            pool_maxsize=50,
            max_retries=0,
            pool_block=False
        )

        _global_session.mount('http://', adapter)
        _global_session.mount('https://', adapter)

        # 强制禁用代理（多种方式确保生效）
        _global_session.proxies = {}
        _global_session.trust_env = False  # 忽略环境变量中的代理设置

    return _global_session


class RetryConfig:
    """重试配置"""

    def __init__(
        self,
        max_retries: int = 5,
        initial_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        retry_on_dns_error: bool = True,
        retry_on_connection_error: bool = True,
        retry_on_timeout: bool = True,
        retry_on_5xx: bool = True,
        retry_on_429: bool = True
    ):
        """
        初始化重试配置

        Args:
            max_retries: 最大重试次数
            initial_delay: 初始延迟时间（秒）
            max_delay: 最大延迟时间（秒）
            exponential_base: 指数退避基数
            retry_on_dns_error: 是否在DNS错误时重试
            retry_on_connection_error: 是否在连接错误时重试
            retry_on_timeout: 是否在超时错误时重试
            retry_on_5xx: 是否在5xx服务器错误时重试
            retry_on_429: 是否在429限流错误时重试
        """
        self.max_retries = max_retries
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.retry_on_dns_error = retry_on_dns_error
        self.retry_on_connection_error = retry_on_connection_error
        self.retry_on_timeout = retry_on_timeout
        self.retry_on_5xx = retry_on_5xx
        self.retry_on_429 = retry_on_429


class APIRetryHandler:
    """API重试处理器"""

    def __init__(self, config: Optional[RetryConfig] = None, logger=None, context_provider=None):
        """
        初始化重试处理器

        Args:
            config: 重试配置，如果为None则使用默认配置
            logger: 日志记录器
            context_provider: 上下文提供函数（返回字符串，用于日志前缀）
        """
        self.config = config or RetryConfig()
        self.logger = logger
        self.context_provider = context_provider

    def _log(self, level: str, message: str):
        """记录日志"""
        # 获取上下文前缀
        context = ""
        if self.context_provider:
            try:
                context = self.context_provider() + " "
            except:
                pass

        full_message = f"{context}{message}"

        if self.logger:
            getattr(self.logger, level, self.logger.info)(full_message)
        else:
            print(f"[{level.upper()}] {full_message}")

    def _calculate_delay(self, attempt: int) -> float:
        """
        计算延迟时间（指数退避）

        Args:
            attempt: 当前尝试次数（从1开始）

        Returns:
            延迟时间（秒）
        """
        delay = self.config.initial_delay * (self.config.exponential_base ** (attempt - 1))
        return min(delay, self.config.max_delay)

    def _should_retry(self, error: Exception, attempt: int) -> tuple[bool, str]:
        """
        判断是否应该重试

        Args:
            error: 异常对象
            attempt: 当前尝试次数

        Returns:
            (是否重试, 错误类型描述)
        """
        # 超过最大重试次数
        if attempt > self.config.max_retries:
            return False, "超过最大重试次数"

        # DNS解析错误
        if isinstance(error, (NameResolutionError, ConnectionError)):
            error_str = str(error).lower()
            if 'getaddrinfo failed' in error_str or 'failed to resolve' in error_str:
                return (self.config.retry_on_dns_error, "DNS解析错误" if self.config.retry_on_dns_error else "DNS解析错误（不重试）")

        # 连接错误
        if isinstance(error, (ConnectionError, MaxRetryError)):
            return (self.config.retry_on_connection_error, "连接错误" if self.config.retry_on_connection_error else "连接错误（不重试）")

        # 超时错误（区分 ConnectTimeout 和 ReadTimeout）
        if isinstance(error, Timeout):
            error_str = str(error)
            # ConnectTimeout: 连接建立超时，请求未到达服务器，不浪费token
            if 'ConnectTimeout' in error_str or 'Connection to' in error_str:
                return (self.config.retry_on_timeout, "连接超时(ConnectTimeout)")
            # ReadTimeout: 读取响应超时，服务器可能已处理，可能浪费token ⚠️
            elif 'ReadTimeout' in error_str or 'Read timed out' in error_str:
                return (self.config.retry_on_timeout, "读取超时(ReadTimeout)⚠️")
            else:
                return (self.config.retry_on_timeout, "请求超时")

        # HTTP错误
        if isinstance(error, HTTPError):
            status_code = error.response.status_code if hasattr(error, 'response') else None

            # 429 限流错误
            if status_code == 429 and self.config.retry_on_429:
                return True, "API限流(429)"

            # 5xx 服务器错误
            if status_code and 500 <= status_code < 600 and self.config.retry_on_5xx:
                return True, f"服务器错误({status_code})"

            return False, f"HTTP错误({status_code})"

        # 其他请求异常
        if isinstance(error, RequestException):
            return True, "请求异常"

        # JSON解析错误 - 可能是服务器返回格式错误，值得重试
        if 'JSON' in str(type(error).__name__) or 'JSONDecodeError' in str(error):
            return True, "JSON解析错误"

        # KeyError - 可能是API返回结构变化，值得重试
        if isinstance(error, KeyError):
            return True, "响应结构错误"

        # 默认不重试
        return False, "未知错误"

    def execute_with_retry(
        self,
        func: Callable,
        on_retry_callback: Optional[Callable[[int, str, str], None]] = None,
        *args,
        **kwargs
    ) -> Any:
        """
        执行函数并在失败时重试

        Args:
            func: 要执行的函数
            on_retry_callback: 重试回调函数 callback(attempt, error_type, error_detail)
            *args: 函数的位置参数
            **kwargs: 函数的关键字参数

        Returns:
            函数执行结果

        Raises:
            最后一次尝试的异常
        """
        attempt = 0
        last_error = None

        while attempt <= self.config.max_retries:
            attempt += 1

            try:
                # 执行函数
                result = func(*args, **kwargs)

                # 成功执行
                if attempt > 1:
                    self._log('info', f"✓ 重试成功（第{attempt}次尝试）")

                return result

            except Exception as e:
                last_error = e

                # 判断是否应该重试
                should_retry, error_type = self._should_retry(e, attempt)

                # 调用回调（如果有）
                if on_retry_callback:
                    try:
                        on_retry_callback(attempt, error_type, str(e))
                    except:
                        pass

                if not should_retry:
                    # 不重试，直接抛出异常
                    self._log('error', f"✗ {error_type}，不再重试")
                    raise

                # 计算延迟时间
                delay = self._calculate_delay(attempt)

                # 记录重试信息
                self._log(
                    'warning',
                    f"⚠ {error_type}，{delay:.1f}秒后进行第{attempt + 1}次尝试..."
                    f"（共{self.config.max_retries}次）"
                )
                self._log('warning', f"  错误详情: {str(e)}")

                # 等待后重试
                time.sleep(delay)

        # 所有重试都失败，抛出最后一个异常
        self._log('error', f"✗ 所有重试都失败（共尝试{attempt}次）")
        raise last_error


def make_api_request_with_retry(
    url: str,
    headers: dict,
    payload: dict,
    timeout: int = 180,
    retry_config: Optional[RetryConfig] = None,
    logger=None
) -> dict:
    """
    发送API请求并在失败时重试（便捷函数，使用全局Session连接复用）

    Args:
        url: API端点URL
        headers: 请求头
        payload: 请求体
        timeout: 超时时间（秒）
        retry_config: 重试配置
        logger: 日志记录器

    Returns:
        API响应JSON

    Raises:
        请求异常
    """
    handler = APIRetryHandler(retry_config, logger)
    session = get_global_session()  # 使用全局共享 Session

    def _make_request():
        response = session.post(
            url,
            headers=headers,
            json=payload,
            timeout=timeout,
            verify=True
        )
        response.raise_for_status()
        return response.json()

    return handler.execute_with_retry(_make_request)
