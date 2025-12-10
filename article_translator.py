"""
文章翻译引擎（精简版）
只保留核心翻译功能，但保留完整的术语库逻辑
"""

import re
import requests
import time
from typing import Optional, Dict, Tuple, List
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry as URLLibRetry
from retry_utils import APIRetryHandler, RetryConfig


class RateLimiter:
    """自适应速率限制器"""

    def __init__(self, initial_workers: int, max_workers: int, min_workers: int,
                 backoff: float, increase: float, success_threshold: float, increase_interval: int):
        self.current_workers = initial_workers
        self.max_workers = max_workers
        self.min_workers = min_workers
        self.backoff = backoff
        self.increase = increase
        self.success_threshold = success_threshold
        self.increase_interval = increase_interval

        self.success_count = 0
        self.total_count = 0
        self.last_increase_time = time.time()
        self.lock = Lock()

    def on_rate_limit_error(self):
        """遇到429错误，降低并发"""
        with self.lock:
            old_workers = self.current_workers
            self.current_workers = max(self.min_workers, int(self.current_workers * self.backoff))
            print(f"⚠️ 遇到速率限制，降低并发: {old_workers} -> {self.current_workers}")

    def on_success(self):
        """成功请求，统计成功率"""
        with self.lock:
            self.success_count += 1
            self.total_count += 1

            # 计算成功率
            if self.total_count >= 20:  # 至少20个样本
                success_rate = self.success_count / self.total_count
                current_time = time.time()

                # 如果成功率高且距离上次增加已过一段时间
                if (success_rate >= self.success_threshold and
                        current_time - self.last_increase_time >= self.increase_interval and
                        self.current_workers < self.max_workers):
                    old_workers = self.current_workers
                    self.current_workers = min(self.max_workers, int(self.current_workers * self.increase))
                    self.last_increase_time = current_time
                    print(f"✓ 提升并发: {old_workers} -> {self.current_workers}")

                    # 重置计数器
                    self.success_count = 0
                    self.total_count = 0

    def on_failure(self):
        """请求失败（非429错误）"""
        with self.lock:
            self.total_count += 1

    def get_current_workers(self) -> int:
        """获取当前并发数"""
        with self.lock:
            return self.current_workers


class ArticleTranslator:
    """文章翻译引擎"""

    def __init__(
        self,
        api_key: str,
        api_url: str,
        model: str,
        glossary: Optional[Dict[str, str]] = None,
        case_sensitive: bool = False,
        whole_word_only: bool = True,
        config: Optional[Dict] = None
    ):
        """
        初始化翻译器

        Args:
            api_key: API密钥
            api_url: API基础URL
            model: 模型名称
            glossary: 术语表字典 {"English": "中文"}
            case_sensitive: 术语替换是否区分大小写（默认False）
            whole_word_only: 是否只匹配完整单词（默认True）
            config: 配置字典（用于读取API参数和并发配置）
        """
        self.api_key = api_key
        self.api_url = api_url.rstrip('/')
        self.model = model
        self.chat_endpoint = f"{self.api_url}/chat/completions"
        self.glossary = glossary or {}
        self.case_sensitive = case_sensitive
        self.whole_word_only = whole_word_only

        # 从config读取参数（如果提供）
        self.config = config or {}
        self.timeout = self.config.get('api', {}).get('timeout', 120)
        self.temperature = self.config.get('api', {}).get('temperature', 0.3)
        self.max_tokens = self.config.get('api', {}).get('max_tokens', 65536)

        # ===== 新增：创建共享的 Session 对象进行连接复用 =====
        self.session = requests.Session()

        # 配置连接池：池大小 = 最大并发数 * 2
        max_workers = self.config.get('concurrency', {}).get('max_translation_workers', 100)
        pool_size = min(max_workers * 2, 200)  # 限制最大200

        # 配置 HTTPAdapter（连接复用和连接池管理）
        adapter = HTTPAdapter(
            pool_connections=pool_size,      # 连接池数量
            pool_maxsize=pool_size,          # 连接池最大大小
            max_retries=0,                   # 禁用urllib3自动重试（我们用自己的重试逻辑）
            pool_block=False                 # 连接池满时不阻塞
        )

        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)

        # 设置默认请求头
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Connection": "keep-alive"       # 保持连接
        })

        # 强制禁用代理（多种方式确保生效）
        self.session.proxies = {}
        self.session.trust_env = False  # 忽略环境变量中的代理设置
        # ===== 连接复用配置结束 =====

        # 初始化速率限制器
        concurrency_config = self.config.get('concurrency', {})
        self.rate_limiter = RateLimiter(
            initial_workers=concurrency_config.get('initial_translation_workers', 20),
            max_workers=concurrency_config.get('max_translation_workers', 100),
            min_workers=concurrency_config.get('min_translation_workers', 1),
            backoff=concurrency_config.get('rate_limit_backoff', 0.5),
            increase=concurrency_config.get('rate_limit_increase', 1.2),
            success_threshold=concurrency_config.get('success_threshold', 0.95),
            increase_interval=concurrency_config.get('increase_interval', 30)
        )

        # 初始化重试处理器
        retry_config = self.config.get('retry', {})
        self.retry_handler = APIRetryHandler(
            config=RetryConfig(
                max_retries=retry_config.get('translation_max_retries', 3),
                initial_delay=retry_config.get('translation_initial_delay', 1.0),
                max_delay=retry_config.get('translation_max_delay', 30.0),
                exponential_base=retry_config.get('translation_exponential_base', 2.0),
                retry_on_dns_error=retry_config.get('retry_on_dns_error', True),
                retry_on_connection_error=retry_config.get('retry_on_connection_error', True),
                retry_on_timeout=retry_config.get('retry_on_timeout', True),
                retry_on_5xx=retry_config.get('retry_on_5xx', True),
                retry_on_429=retry_config.get('retry_on_429_translation', False)  # 429由rate_limiter处理
            ),
            logger=None  # 翻译器通常没有logger，使用print
        )

        # 术语替换统计
        self.total_replacements = 0
        self.total_terms_used = 0
        self._replacement_lock = Lock()

    def translate(self, text: str, context: Optional[Dict] = None) -> str:
        """
        翻译文本

        Args:
            text: 待翻译文本
            context: 上下文信息 {
                'chapter_title': '章节标题',
                'chapter_summary': '章节摘要',
                'keywords': ['关键词1', '关键词2']
            }

        Returns:
            翻译后的文本
        """
        if not text or not text.strip():
            return ""

        # 1. 应用术语表（不显示详细日志）
        text_with_glossary, replacement_count = self.apply_glossary(text, show_log=False)

        # 累计术语替换统计（线程安全）
        if replacement_count > 0:
            with self._replacement_lock:
                self.total_replacements += replacement_count

        # 2. 构建提示词
        prompt = self._build_prompt(text_with_glossary, context)

        # 3. 调用API（带重试）
        for attempt in range(3):
            try:
                # 添加小延迟（减轻服务器压力，避免连接被强制关闭）
                if attempt > 0:
                    time.sleep(0.1 * attempt)  # 第2次尝试延迟0.1秒，第3次0.2秒

                translation = self._call_llm(prompt)

                # 清理翻译结果
                translation = self._clean_output(translation)

                return translation

            except Exception as e:
                if attempt < 2:
                    wait_time = 2 ** attempt
                    time.sleep(wait_time)
                else:
                    # 最后一次失败，返回原文
                    return text

    def _call_llm(self, prompt: str) -> str:
        """
        调用LLM API（使用 Session 进行连接复用）

        Args:
            prompt: 提示词

        Returns:
            LLM响应文本
        """
        messages = [
            {
                "role": "system",
                "content": "你是专业的学术文档翻译助手。"
            },
            {
                "role": "user",
                "content": prompt
            }
        ]

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens
        }

        # 使用重试处理器包装API调用
        def _make_api_call():
            # 使用共享的 Session 对象（自动复用连接）
            response = self.session.post(
                self.chat_endpoint,
                json=payload,
                timeout=self.timeout,
                verify=True
            )

            # 处理429错误
            if response.status_code == 429:
                self.rate_limiter.on_rate_limit_error()
                response.raise_for_status()

            response.raise_for_status()
            return response.json()

        # 执行带重试的API调用
        result = self.retry_handler.execute_with_retry(_make_api_call)

        # 记录成功
        self.rate_limiter.on_success()

        return result['choices'][0]['message']['content'].strip()

    def apply_glossary(self, text: str, show_log: bool = False) -> Tuple[str, int]:
        """
        应用术语库进行预翻译替换（完整版逻辑）

        Args:
            text: 原始文本
            show_log: 是否显示替换日志

        Returns:
            (替换后的文本, 替换次数)
        """
        if not self.glossary or not text:
            return text, 0

        # URL保护
        modified_text, url_placeholders = self._protect_urls(text)

        # 术语替换
        replacement_count = 0
        replaced_terms = []

        # 按术语长度排序（长的先替换）
        sorted_terms = sorted(self.glossary.items(), key=lambda x: len(x[0]), reverse=True)

        for source_term, target_term in sorted_terms:
            if not source_term or not target_term:
                continue

            # 构建正则表达式
            pattern = r'\b' + re.escape(source_term) + r'\b' if self.whole_word_only else re.escape(source_term)
            flags = 0 if self.case_sensitive else re.IGNORECASE

            # 查找匹配
            matches = re.findall(pattern, modified_text, flags=flags)
            if matches:
                count = len(matches)
                modified_text = re.sub(pattern, target_term, modified_text, flags=flags)
                replacement_count += count
                replaced_terms.append((source_term, target_term, count))

        # 显示替换日志
        if show_log and replaced_terms:
            print(f"  术语替换: {len(replaced_terms)} 个术语，共 {replacement_count} 处")

        # 恢复URL
        modified_text = self._restore_urls(modified_text, url_placeholders)

        return modified_text, replacement_count

    def _protect_urls(self, text: str) -> Tuple[str, Dict[str, str]]:
        """
        提取URL并用占位符替换

        Args:
            text: 原始文本

        Returns:
            (替换后的文本, {占位符: URL})
        """
        # 合并URL匹配正则
        url_pattern = (
            r'(?:https?|ftp|ftps)://[^\s<>"\'\)]+|'
            r'(?:dx\.)?doi\.org/[^\s<>"\'\)]+|'
            r'www\.[a-zA-Z0-9][-a-zA-Z0-9]*\.[^\s<>"\'\)]+|'
            r'\[([^\]]+)\]\(([^\)]+)\)'
        )

        urls = re.findall(url_pattern, text)

        # 展平Markdown链接
        url_list = []
        for match in urls:
            if isinstance(match, tuple):
                url_list.append(f'[{match[0]}]({match[1]})')
            else:
                url_list.append(match)

        # 去重并按长度排序
        url_list = sorted(set(url_list), key=len, reverse=True)

        # 创建占位符
        url_placeholders = {}
        modified_text = text
        for i, url in enumerate(url_list):
            placeholder = f"__URL_PLACEHOLDER_{i}__"
            url_placeholders[placeholder] = url
            modified_text = modified_text.replace(url, placeholder)

        return modified_text, url_placeholders

    def _restore_urls(self, text: str, url_placeholders: Dict[str, str]) -> str:
        """恢复URL占位符"""
        for placeholder, url in url_placeholders.items():
            text = text.replace(placeholder, url)
        return text

    def _build_prompt(self, text: str, context: Optional[Dict]) -> str:
        """
        构建翻译提示词

        Args:
            text: 待翻译文本
            context: 上下文信息

        Returns:
            完整提示词
        """
        prompt_parts = [
            "请将以下英文翻译成中文。",
            "",
            "要求：",
            "1. 保持学术风格和专业术语准确性",
            "2. 保留原文的段落结构和格式",
            "3. **保持所有URL链接（http://或https://开头）原样不变，不要翻译或修改**",
            "4. 直接输出翻译结果，不要添加任何解释",
            "5. 不要添加\"译文:\"、\"翻译:\"等前缀",
            "6. 如果有被误翻译、误术语替换的URL，记得进行修复"
        ]

        # 添加上下文
        if context:
            prompt_parts.append("")
            prompt_parts.append("【文档上下文】")

            if context.get('chapter_title'):
                prompt_parts.append(f"所属章节: {context['chapter_title']}")

            if context.get('chapter_summary'):
                prompt_parts.append(f"章节摘要: {context['chapter_summary']}")

            if context.get('keywords'):
                keywords = ', '.join(context['keywords'])
                prompt_parts.append(f"关键词: {keywords}")

            # 添加上下文窗口（前后文）
            if context.get('prev_text') or context.get('next_text'):
                prompt_parts.append("")
                prompt_parts.append("【上下文参考】")
                prompt_parts.append("注意：如果当前段落语义不完整或是句子片段，请参考上下文理解完整含义。")

                if context.get('prev_text'):
                    prev = context['prev_text'].strip()
                    if prev:
                        prompt_parts.append(f"上文末尾: ...{prev}")

                if context.get('next_text'):
                    next_text = context['next_text'].strip()
                    if next_text:
                        prompt_parts.append(f"下文开头: {next_text}...")

        # 添加待翻译文本
        prompt_parts.append("")
        prompt_parts.append("【待翻译文本】")
        prompt_parts.append(text)

        return "\n".join(prompt_parts)

    def _clean_output(self, text: str) -> str:
        """
        清理翻译结果中的额外标记

        Args:
            text: 原始翻译结果

        Returns:
            清理后的译文
        """
        cleaned = text.strip()

        # 移除常见的前缀标记（合并正则）
        prefixes = r'^(?:译文|翻译|【译文】|【翻译】|\[译文\]|\[翻译\]|Translation|以下是翻译|翻译如下|翻译结果)[：:\s]+'
        cleaned = re.sub(prefixes, '', cleaned, flags=re.IGNORECASE)

        # 移除首尾的引号（统一处理）
        quote_pairs = [('"', '"'), ('「', '」'), ('『', '』'), ('《', '》')]
        for open_q, close_q in quote_pairs:
            if cleaned.startswith(open_q) and cleaned.endswith(close_q):
                cleaned = cleaned[1:-1]
                break

        return cleaned.strip()

    def translate_batch(self, tasks: List[Tuple[str, Optional[Dict]]]) -> List[str]:
        """
        批量并发翻译（使用自适应速率限制）

        Args:
            tasks: [(text, context), ...] 待翻译任务列表

        Returns:
            翻译结果列表
        """
        if not tasks:
            return []

        # 重置术语替换统计
        self.total_replacements = 0

        results = [None] * len(tasks)

        # 使用动态并发数
        def translate_single(index: int, text: str, context: Optional[Dict]) -> Tuple[int, str]:
            """翻译单个文本并返回索引和结果"""
            translation = self.translate(text, context)
            return index, translation

        # 并发翻译
        with ThreadPoolExecutor(max_workers=self.rate_limiter.get_current_workers()) as executor:
            futures = {
                executor.submit(translate_single, i, text, context): i
                for i, (text, context) in enumerate(tasks)
            }

            for future in as_completed(futures):
                try:
                    index, translation = future.result()
                    results[index] = translation
                except Exception as e:
                    # 失败时返回原文
                    index = futures[future]
                    results[index] = tasks[index][0]
                    self.rate_limiter.on_failure()

        # 显示术语替换总计
        if self.total_replacements > 0:
            print(f"\n📊 术语替换统计: 共替换 {self.total_replacements} 处\n")

        return results

    def close(self):
        """关闭 Session 连接池"""
        if hasattr(self, 'session'):
            self.session.close()

    def __enter__(self):
        """支持上下文管理器"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出时自动关闭连接"""
        self.close()
