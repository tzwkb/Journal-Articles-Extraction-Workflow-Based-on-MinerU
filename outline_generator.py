"""
大纲生成模块
分析 PDF 并生成文档大纲
"""

import json
import base64
import os
from pathlib import Path
import fitz  # PyMuPDF
from retry_utils import get_global_session, RetryConfig, APIRetryHandler
from debug_helper import APIDebugger


class OutlineGenerator:
    """文档大纲生成器"""

    def __init__(self, config: dict, logger, output_base: Path):
        """
        初始化大纲生成器

        Args:
            config: 配置字典
            logger: 日志记录器实例
            output_base: 输出基础路径
        """
        self.config = config
        self.logger = logger
        self.output_base = output_base

        # 从配置文件读取 PDF 处理参数
        pdf_config = config.get('pdf_processing', {})
        self.max_pdf_size_mb = pdf_config.get('max_pdf_size_mb', 8)
        self.max_pages_for_outline = pdf_config.get('max_pages_for_outline', 12)

        # 读取调试模式配置
        self.debug_mode = config.get('debug', {}).get('enabled', False)
        self.debugger = APIDebugger(logger, self.debug_mode)

        # 启动时清理旧的临时PDF文件
        self._cleanup_old_temp_files()

    def _cleanup_old_temp_files(self):
        """清理旧的临时 PDF 文件"""
        try:
            temp_dir = self.output_base / "cache"
            if temp_dir.exists():
                temp_files = list(temp_dir.glob("temp_pdf_*.pdf"))
                for temp_file in temp_files:
                    self._delete_temp_file(temp_file)
                if temp_files:
                    self.logger.info(f"✓ 已清理 {len(temp_files)} 个旧的临时文件")
        except:
            pass

    def _delete_temp_file(self, temp_path: Path, log_success=False):
        """
        删除临时文件（带重试）

        Args:
            temp_path: 临时文件路径
            log_success: 是否记录成功日志
        """
        if not temp_path.exists():
            return

        import time
        for attempt in range(3):
            try:
                temp_path.unlink()
                if log_success:
                    self.logger.info("✓ 已清理临时文件")
                return
            except PermissionError:
                if attempt < 2:
                    time.sleep(0.5)
                else:
                    self.logger.warning(f"⚠ 无法删除临时文件: {temp_path}")
                    self.logger.warning("  文件将在下次运行时被覆盖")
            except Exception as e:
                self.logger.warning(f"⚠ 清理临时文件失败: {e}")
                return

    def _prepare_pdf_file(self, pdf_path: str) -> tuple:
        """
        准备 PDF 文件，自动处理大文件（保存为临时文件而非 base64）

        Args:
            pdf_path: PDF 文件路径

        Returns:
            (临时PDF文件路径, 使用的页数)
        """
        pdf_path_obj = Path(pdf_path)
        file_size_mb = pdf_path_obj.stat().st_size / (1024 * 1024)

        self.logger.info(f"PDF 文件大小: {file_size_mb:.2f} MB")

        # 如果文件小于限制，直接返回原文件
        if file_size_mb <= self.max_pdf_size_mb:
            self.logger.info(f"✓ 文件大小合适，使用完整 PDF")
            return str(pdf_path), -1  # -1 表示所有页

        # 文件过大，只提取前 N 页
        self.logger.warning(f"⚠ 文件过大 ({file_size_mb:.2f} MB > {self.max_pdf_size_mb} MB)")
        self.logger.info(f"→ 自动提取前 {self.max_pages_for_outline} 页用于生成大纲...")

        try:
            # 打开 PDF
            doc = fitz.open(pdf_path)
            total_pages = len(doc)

            # 确定要提取的页数
            pages_to_extract = min(self.max_pages_for_outline, total_pages)

            # 创建新的 PDF（只包含前 N 页）
            new_doc = fitz.open()
            for page_num in range(pages_to_extract):
                new_doc.insert_pdf(doc, from_page=page_num, to_page=page_num)

            # 保存到自定义临时目录（避免 Windows 临时文件权限问题）
            import time
            temp_dir = self.output_base / "cache"
            temp_dir.mkdir(parents=True, exist_ok=True)

            # 使用时间戳创建唯一的临时文件名
            timestamp = int(time.time() * 1000)
            temp_pdf_path = temp_dir / f"temp_pdf_{timestamp}.pdf"

            # 保存文档
            new_doc.save(str(temp_pdf_path))

            # 关闭文档
            new_doc.close()
            doc.close()

            extracted_size_mb = temp_pdf_path.stat().st_size / (1024 * 1024)
            self.logger.success(
                f"✓ 已提取前 {pages_to_extract}/{total_pages} 页 "
                f"(约 {extracted_size_mb:.2f} MB)"
            )

            return str(temp_pdf_path), pages_to_extract

        except Exception as e:
            self.logger.error(f"✗ PDF 提取失败: {e}")
            self.logger.info("→ 尝试使用完整 PDF（可能会失败）")
            return str(pdf_path), -1

    def generate_outline(self, pdf_path: str, output_paths: dict = None) -> dict:
        """
        生成文档大纲

        Args:
            pdf_path: PDF文件路径
            output_paths: 自定义输出路径字典（可选）

        Returns:
            文档大纲字典
        """
        self.logger.info("\n>>> 步骤1: 生成文档大纲...")

        # 确定outline路径
        if output_paths and 'outline' in output_paths:
            outline_path = output_paths['outline']
        else:
            outline_path = self.output_base / "cache/outline.json"
            outline_path.parent.mkdir(parents=True, exist_ok=True)

        # 如果已存在大纲，直接加载
        if Path(outline_path).exists():
            self.logger.info("发现已有大纲，直接加载...")
            with open(outline_path, 'r', encoding='utf-8') as f:
                outline = json.load(f)
            self.logger.success(f"大纲已加载: {len(outline.get('structure', []))} 个章节")
            return outline

        # 准备 PDF 文件（自动处理大文件）
        self.logger.info(f"正在读取PDF: {pdf_path}")
        pdf_file_path, pages_used = self._prepare_pdf_file(pdf_path)

        # 是否是临时文件
        is_temp_file = pdf_file_path != str(pdf_path)

        # 读取 PDF 为 base64
        with open(pdf_file_path, 'rb') as f:
            pdf_data = base64.b64encode(f.read()).decode('utf-8')

        # 生成大纲的提示词
        if pages_used > 0:
            # 使用了部分页面
            prompt = f"""请分析这份PDF文档的前 {pages_used} 页，生成JSON格式的文档大纲。

注意：由于文件较大，只提供了前 {pages_used} 页。请根据这些页面推断整个文档的结构。

要求：
1. 识别文档类型（research_report/journal_article/technical_document/book_chapter）
2. 提取章节结构（标题、页码范围）
3. 为每个章节生成简短摘要（50字内）
4. 提取每个章节的关键词（3-5个）

输出JSON格式：
{{
  "document_type": "research_report",
  "structure": [
    {{
      "level": 1,
      "title": "章节标题",
      "pages": [起始页, 结束页],
      "summary": "章节摘要（50字内）",
      "keywords": ["关键词1", "关键词2", "关键词3"]
    }}
  ]
}}

注意：
- 只需要提取文档结构信息
- 如果无法确定结束页码，可以留空或估算
- 直接返回JSON，不要添加任何解释"""
        else:
            # 使用完整文件
            prompt = """请分析这份PDF文档，生成JSON格式的文档大纲。

要求：
1. 识别文档类型（research_report/journal_article/technical_document/book_chapter）
2. 提取章节结构（标题、页码范围）
3. 为每个章节生成简短摘要（50字内）
4. 提取每个章节的关键词（3-5个）

输出JSON格式：
{
  "document_type": "research_report",
  "structure": [
    {
      "level": 1,
      "title": "章节标题",
      "pages": [起始页, 结束页],
      "summary": "章节摘要（50字内）",
      "keywords": ["关键词1", "关键词2", "关键词3"]
    }
  ]
}

注意：
- 只需要提取文档结构信息
- 直接返回JSON，不要添加任何解释"""

        # 调用 API (使用 Base64 编码方式)
        self.logger.info("正在调用API分析文档...")

        session = get_global_session()
        headers = {
            "Authorization": f"Bearer {self.config['api']['outline_api_key']}",
            "Content-Type": "application/json"
        }

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:application/pdf;base64,{pdf_data}"
                        }
                    }
                ]
            }
        ]

        payload = {
            "model": self.config['api']['outline_api_model'],
            "messages": messages,
            "temperature": self.config['api']['temperature'],
            "max_tokens": self.config['api']['max_tokens']
        }

        # ===== 调试模式：打印请求详情 =====
        api_url = f"{self.config['api']['outline_api_base_url']}/chat/completions"
        self.debugger.log_request(api_url, headers, payload, pdf_data)
        # ===== 调试模式结束 =====

        # 配置重试策略（从config读取）
        retry_config_dict = self.config.get('retry', {})
        retry_config = RetryConfig(
            max_retries=retry_config_dict.get('outline_max_retries', 3),
            initial_delay=retry_config_dict.get('outline_initial_delay', 3.0),
            max_delay=retry_config_dict.get('outline_max_delay', 30.0),
            exponential_base=retry_config_dict.get('outline_exponential_base', 2.0),
            retry_on_dns_error=retry_config_dict.get('retry_on_dns_error', True),
            retry_on_connection_error=retry_config_dict.get('retry_on_connection_error', True),
            retry_on_timeout=retry_config_dict.get('retry_on_timeout', True),
            retry_on_5xx=retry_config_dict.get('retry_on_5xx', True),
            retry_on_429=retry_config_dict.get('retry_on_429', True)
        )

        retry_handler = APIRetryHandler(retry_config, self.logger)

        def _make_request():
            resp = session.post(
                f"{self.config['api']['outline_api_base_url']}/chat/completions",
                headers=headers,
                json=payload,
                timeout=self.config['api']['timeout']
            )
            resp.raise_for_status()
            return resp.json()

        result = retry_handler.execute_with_retry(_make_request)
        response_text = result['choices'][0]['message']['content'].strip()

        # 清理临时文件
        if is_temp_file:
            self._delete_temp_file(Path(pdf_file_path), log_success=True)

        # 解析JSON（移除可能的markdown代码块标记）
        if response_text.startswith("```"):
            lines = response_text.split('\n')
            # 移除第一行和最后一行的代码块标记
            response_text = '\n'.join(lines[1:-1])

        outline = json.loads(response_text)

        # 保存大纲
        outline_path.parent.mkdir(parents=True, exist_ok=True)
        with open(outline_path, 'w', encoding='utf-8') as f:
            json.dump(outline, f, ensure_ascii=False, indent=2)

        self.logger.success(f"大纲已生成: {len(outline['structure'])} 个章节")
        self.logger.info(f"大纲已保存: {outline_path}")

        return outline
