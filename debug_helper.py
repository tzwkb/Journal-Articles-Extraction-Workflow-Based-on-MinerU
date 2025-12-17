"""
API调试工具
提供统一的API请求调试输出
"""

import json


class APIDebugger:
    """API调试工具"""

    def __init__(self, logger=None, enabled=True):
        """
        初始化调试器

        Args:
            logger: 日志记录器（可选）
            enabled: 是否启用调试输出
        """
        self.logger = logger
        self.enabled = enabled

    def log_request(self, url, headers, payload, pdf_data=None):
        """
        记录API请求信息

        Args:
            url: 请求URL
            headers: 请求头
            payload: 请求负载
            pdf_data: PDF数据（可选）
        """
        if not self.enabled:
            return

        # 掩码敏感数据
        safe_headers = self._mask_sensitive_data(headers)

        # 记录基本信息
        if self.logger:
            self.logger.info(f"API Request to: {url}")
            self.logger.info(f"Headers: {safe_headers}")
        else:
            print(f"[API Debug] Request to: {url}")
            print(f"[API Debug] Headers: {safe_headers}")

        # 处理PDF数据
        if pdf_data:
            base64_size_kb = len(pdf_data) / 1024
            base64_size_mb = base64_size_kb / 1024
            if self.logger:
                self.logger.info(f"PDF data size: {base64_size_mb:.2f} MB (base64)")
            else:
                print(f"[API Debug] PDF data size: {base64_size_mb:.2f} MB (base64)")

            # 估算原始大小
            original_size_mb = (base64_size_mb * 0.75)
            if self.logger:
                self.logger.info(f"Estimated original PDF size: {original_size_mb:.2f} MB")
            else:
                print(f"[API Debug] Estimated original PDF size: {original_size_mb:.2f} MB")

            # 显示base64预览（前100字符）
            base64_preview = pdf_data[:100] + "..." if len(pdf_data) > 100 else pdf_data
            if self.logger:
                self.logger.info(f"Base64 preview: {base64_preview}")
            else:
                print(f"[API Debug] Base64 preview: {base64_preview}")

        # 记录payload信息
        if payload:
            payload_json = json.dumps(self._summarize_payload(payload, pdf_data), ensure_ascii=False, indent=2)
            payload_size_kb = len(payload_json) / 1024
            payload_size_mb = payload_size_kb / 1024

            if self.logger:
                self.logger.info(f"Payload size: {payload_size_mb:.2f} MB")
            else:
                print(f"[API Debug] Payload size: {payload_size_mb:.2f} MB")

            # 显示payload预览（截断超长行）
            payload_preview = payload_json.split('\n')[:20]
            if self.logger:
                for line in payload_preview:
                    # 截断超过200字符的行
                    truncated_line = line if len(line) <= 200 else line[:200] + "..."
                    self.logger.info(f"  {truncated_line}")
            else:
                print(f"[API Debug] Payload preview:")
                for line in payload_preview:
                    # 截断超过200字符的行
                    truncated_line = line if len(line) <= 200 else line[:200] + "..."
                    print(f"  {truncated_line}")

            # 如果有messages字段，显示prompt预览
            if isinstance(payload, dict) and 'messages' in payload:
                messages = payload.get('messages', [])
                for item in messages:
                    if isinstance(item, dict):
                        content = item.get('content', '')
                        if content and len(content) > 100:
                            prompt_preview = content[:100] + '...'
                            if self.logger:
                                self.logger.info(f"Prompt preview: {prompt_preview}")
                            else:
                                print(f"[API Debug] Prompt preview: {prompt_preview}")

    def _mask_sensitive_data(self, headers):
        """
        掩码敏感数据

        Args:
            headers: 请求头字典

        Returns:
            掩码后的请求头
        """
        if not headers:
            return headers

        safe_headers = headers.copy()

        # 掩码Authorization
        for key in safe_headers:
            if 'authorization' in key.lower() or 'token' in key.lower() or 'key' in key.lower():
                if len(safe_headers[key]) > 8:
                    safe_headers[key] = safe_headers[key][:8] + '...'

        return safe_headers

    def _summarize_payload(self, payload, pdf_data=None):
        """
        总结payload内容（递归处理嵌套结构）

        Args:
            payload: 原始payload
            pdf_data: PDF数据

        Returns:
            总结后的payload
        """
        if not payload:
            return payload

        # 处理字典
        if isinstance(payload, dict):
            result = {}
            for key, value in payload.items():
                # 递归处理值
                if isinstance(value, dict):
                    result[key] = self._summarize_payload(value, pdf_data)
                elif isinstance(value, list):
                    result[key] = [self._summarize_payload(item, pdf_data) for item in value]
                elif isinstance(value, str) and len(value) > 1000:
                    # 检查是否是base64字符串
                    if self._is_base64_like(value[:100]):
                        size_mb = len(value) / 1024 / 1024
                        preview = value[:50] + "..."
                        result[key] = f"<Base64 data: {size_mb:.2f} MB, preview: {preview}>"
                    elif len(value) > 500:
                        # 普通长文本也截断
                        result[key] = value[:500] + f"... ({len(value)} chars total)"
                    else:
                        result[key] = value
                else:
                    result[key] = value
            return result

        # 处理列表
        elif isinstance(payload, list):
            return [self._summarize_payload(item, pdf_data) for item in payload]

        # 其他类型直接返回
        else:
            return payload

    def _is_base64_like(self, text: str) -> bool:
        """判断文本是否看起来像base64编码"""
        if not text:
            return False
        # 检查是否大部分字符都是base64字符集
        base64_chars = set('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=')
        matching = sum(1 for c in text if c in base64_chars)
        return matching / len(text) > 0.9  # 90%以上是base64字符
