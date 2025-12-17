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
        self.max_pdf_size_mb = pdf_config.get('max_pdf_size_mb', 20)

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

        # 先读取完整PDF并转为base64，检查编码后的大小
        with open(pdf_path, 'rb') as f:
            pdf_bytes = f.read()

        pdf_base64 = base64.b64encode(pdf_bytes).decode('utf-8')
        base64_size_mb = len(pdf_base64) / (1024 * 1024)

        original_size_mb = len(pdf_bytes) / (1024 * 1024)
        self.logger.info(f"PDF 文件大小: {original_size_mb:.2f} MB")
        self.logger.info(f"Base64 编码后: {base64_size_mb:.2f} MB")

        # 如果 base64 大小在限制内，直接返回原文件
        if base64_size_mb <= self.max_pdf_size_mb:
            self.logger.info(f"✓ Base64 大小合适，使用完整 PDF")
            return str(pdf_path), -1  # -1 表示所有页

        # base64 过大，需要自适应截断
        self.logger.warning(f"⚠ Base64 过大 ({base64_size_mb:.2f} MB > {self.max_pdf_size_mb} MB)")
        self.logger.info(f"→ 自动计算需要提取的页数以符合大小限制...")

        try:
            # 打开 PDF
            doc = fitz.open(pdf_path)
            total_pages = len(doc)

            # 估算需要的页数比例（base64大小和页数大致成正比）
            ratio = self.max_pdf_size_mb / base64_size_mb
            estimated_pages = max(1, int(total_pages * ratio * 0.95))  # 留5%余量

            self.logger.info(f"总页数: {total_pages}, 估算需要: {estimated_pages} 页")

            # 二分查找最优页数
            pages_to_extract = self._find_optimal_pages(
                doc,
                total_pages,
                estimated_pages
            )

            if pages_to_extract >= total_pages:
                # 如果计算出来需要全部页，直接返回原文件
                doc.close()
                self.logger.success(f"✓ 全部 {total_pages} 页都可使用")
                return str(pdf_path), -1

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

            # 检查提取后的PDF的base64大小
            with open(temp_pdf_path, 'rb') as f:
                extracted_bytes = f.read()
            extracted_base64 = base64.b64encode(extracted_bytes).decode('utf-8')
            extracted_base64_mb = len(extracted_base64) / (1024 * 1024)
            extracted_size_mb = len(extracted_bytes) / (1024 * 1024)

            self.logger.success(
                f"✓ 已自适应提取前 {pages_to_extract}/{total_pages} 页"
            )
            self.logger.info(f"  提取后文件: {extracted_size_mb:.2f} MB")
            self.logger.info(f"  提取后Base64: {extracted_base64_mb:.2f} MB (目标: ≤{self.max_pdf_size_mb} MB)")

            return str(temp_pdf_path), pages_to_extract

        except Exception as e:
            self.logger.error(f"✗ PDF 提取失败: {e}")
            self.logger.info("→ 尝试使用完整 PDF（可能会失败）")
            return str(pdf_path), -1

    def _find_optimal_pages(self, doc, total_pages: int, estimated_pages: int) -> int:
        """
        二分查找最优页数，使base64大小刚好在限制内

        Args:
            doc: PyMuPDF文档对象
            total_pages: 总页数
            estimated_pages: 估算的起始页数

        Returns:
            最优页数
        """
        import io

        # 真正的二分查找
        left = 1  # 最少1页
        right = min(total_pages, estimated_pages)  # 最多估算值或总页数
        best_pages = 1  # 至少保证1页

        self.logger.info(f"  二分查找范围: {left}-{right} 页")

        while left <= right:
            mid = (left + right) // 2

            # 创建临时PDF测试
            test_doc = fitz.open()
            for page_num in range(mid):
                test_doc.insert_pdf(doc, from_page=page_num, to_page=page_num)

            # 保存到内存并检查base64大小
            pdf_bytes = io.BytesIO()
            test_doc.save(pdf_bytes)
            test_doc.close()

            pdf_bytes.seek(0)
            test_base64 = base64.b64encode(pdf_bytes.read()).decode('utf-8')
            test_base64_mb = len(test_base64) / (1024 * 1024)

            self.logger.info(f"  测试 {mid} 页: Base64 = {test_base64_mb:.2f} MB")

            if test_base64_mb <= self.max_pdf_size_mb:
                # 符合条件，记录并尝试更多页
                best_pages = mid
                left = mid + 1
                self.logger.info(f"    ✓ 符合条件，尝试更多页...")
            else:
                # 超过限制，减少页数
                right = mid - 1
                self.logger.info(f"    ✗ 超过限制，减少页数...")

        self.logger.info(f"  → 选定 {best_pages} 页")
        return best_pages

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
2. 生成期刊/文档概述（100-200字，包括：文档主题、发布机构、目标读者、主要内容领域）
3. 提取章节结构（标题、页码范围）
4. 为每个章节生成简短摘要（50字内）
5. 提取每个章节的关键词（3-5个）

输出JSON格式：
{{
  "document_type": "research_report",
  "journal_overview": "期刊概述（100-200字）",
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
2. 生成期刊/文档概述（100-200字，包括：文档主题、发布机构、目标读者、主要内容领域）
3. 提取章节结构（标题、页码范围）
4. 为每个章节生成简短摘要（50字内）
5. 提取每个章节的关键词（3-5个）

输出JSON格式：
{
  "document_type": "research_report",
  "journal_overview": "期刊概述（100-200字）",
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
            "max_tokens": self.config['api'].get('outline_max_tokens', self.config['api']['max_tokens'])
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

        # 尝试解析JSON，添加详细的错误处理
        try:
            outline = json.loads(response_text)
        except json.JSONDecodeError as e:
            # JSON解析失败，记录详细信息
            self.logger.error(f"✗ 大纲JSON解析失败: {str(e)}")
            self.logger.error(f"  错误位置: line {e.lineno}, column {e.colno}, char {e.pos}")

            # 保存原始响应用于调试
            error_log_path = self.output_base / "cache" / f"outline_error_{Path(pdf_file_path).stem}.txt"
            error_log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(error_log_path, 'w', encoding='utf-8') as f:
                f.write("=== API响应内容 ===\n")
                f.write(response_text)
                f.write("\n\n=== 错误信息 ===\n")
                f.write(f"错误: {str(e)}\n")
                f.write(f"位置: line {e.lineno}, column {e.colno}, char {e.pos}\n")

                # 显示出错位置的上下文
                if e.pos is not None:
                    lines = response_text.split('\n')
                    if e.lineno > 0 and e.lineno <= len(lines):
                        f.write(f"\n=== 出错行 (第{e.lineno}行) ===\n")
                        start_line = max(0, e.lineno - 3)
                        end_line = min(len(lines), e.lineno + 2)
                        for i in range(start_line, end_line):
                            marker = ">>> " if i == e.lineno - 1 else "    "
                            f.write(f"{marker}{i+1}: {lines[i]}\n")

            self.logger.error(f"  原始响应已保存到: {error_log_path}")
            self.logger.error(f"  响应前500字符: {response_text[:500]}")

            # 生成默认大纲作为fallback
            self.logger.warning("  生成默认大纲作为后备方案")
            outline = {
                "title": Path(pdf_file_path).stem,
                "structure": [
                    {"level": 1, "title": "文档内容", "page": 1}
                ]
            }

            # 保存默认大纲
            outline_path.parent.mkdir(parents=True, exist_ok=True)
            with open(outline_path, 'w', encoding='utf-8') as f:
                json.dump(outline, f, ensure_ascii=False, indent=2)

            self.logger.warning(f"⚠ 使用默认大纲: {outline_path}")
            return outline

        # 保存大纲
        outline_path.parent.mkdir(parents=True, exist_ok=True)
        with open(outline_path, 'w', encoding='utf-8') as f:
            json.dump(outline, f, ensure_ascii=False, indent=2)

        self.logger.success(f"大纲已生成: {len(outline['structure'])} 个章节")
        self.logger.info(f"大纲已保存: {outline_path}")

        return outline
