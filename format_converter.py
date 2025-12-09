"""
格式转换模块
负责 HTML → PDF/DOCX 的格式转换
"""

import subprocess
from pathlib import Path
from playwright.sync_api import sync_playwright


class FormatConverter:
    """格式转换器类"""

    def __init__(self, config: dict, logger, output_base: Path):
        """
        初始化格式转换器

        Args:
            config: 配置字典
            logger: 日志记录器实例
            output_base: 输出基础路径
        """
        self.config = config
        self.logger = logger
        self.output_base = output_base

    def export_formats(self, original_html: str, translated_html: str, output_paths: dict = None):
        """
        导出PDF和DOCX

        Args:
            original_html: 原文HTML
            translated_html: 译文HTML
            output_paths: 自定义输出路径字典（可选）
        """
        self.logger.info("\n>>> 步骤4: 导出PDF和DOCX...")

        formats = self.config['output']['formats']

        # 保存HTML
        if output_paths and 'html_original' in output_paths:
            html_original_path = Path(output_paths['html_original'])
            html_translated_path = Path(output_paths['html_translated'])
        else:
            html_dir = self.output_base / self.config['output']['html_folder']
            html_dir.mkdir(parents=True, exist_ok=True)
            html_original_path = html_dir / "original.html"
            html_translated_path = html_dir / "translated.html"

        html_original_path.write_text(original_html, encoding='utf-8')
        html_translated_path.write_text(translated_html, encoding='utf-8')
        self.logger.success(f"HTML已生成: {html_original_path.parent}")

        if 'pdf' in formats:
            self.logger.info("正在生成PDF...")
            if output_paths and 'pdf_original' in output_paths:
                self._html_to_pdf(original_html, output_paths['pdf_original'])
                self._html_to_pdf(translated_html, output_paths['pdf_translated'])
            else:
                self._html_to_pdf(original_html, "original.pdf")
                self._html_to_pdf(translated_html, "translated.pdf")
            self.logger.success("PDF已生成")

        if 'docx' in formats:
            self.logger.info("正在生成DOCX...")
            if output_paths and 'docx_original' in output_paths:
                self._html_to_docx(original_html, output_paths['docx_original'])
                self._html_to_docx(translated_html, output_paths['docx_translated'])
            else:
                self._html_to_docx(original_html, "original.docx")
                self._html_to_docx(translated_html, "translated.docx")
            self.logger.success("DOCX已生成")

    def _html_to_pdf(self, html_content: str, filename_or_path):
        """
        HTML转PDF（使用Playwright）

        Args:
            html_content: HTML内容
            filename_or_path: 输出文件名或完整路径
        """
        # 判断是文件名还是完整路径
        if isinstance(filename_or_path, (str, Path)) and (Path(filename_or_path).parent != Path('.')):
            output_path = Path(filename_or_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            pdf_dir = output_path.parent
            filename = output_path.name
        else:
            pdf_dir = self.output_base / self.config['output']['pdf_folder']
            pdf_dir.mkdir(parents=True, exist_ok=True)
            filename = filename_or_path
            output_path = pdf_dir / filename

        html_path = pdf_dir / f"{filename}.html"
        html_path.write_text(html_content, encoding='utf-8')

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                page.goto(f"file:///{html_path.absolute()}")
                page.pdf(
                    path=str(output_path),
                    format='A4',
                    print_background=True,
                    margin={
                        'top': '1cm',
                        'right': '1cm',
                        'bottom': '1cm',
                        'left': '1cm'
                    }
                )
                browser.close()

            # 删除临时HTML文件
            if html_path.exists():
                html_path.unlink()

        except Exception as e:
            self.logger.error(f"PDF生成失败: {str(e)}")
            # 即使失败也尝试清理临时文件
            if html_path.exists():
                html_path.unlink()
            self.logger.error("请确保已安装 Playwright: pip install playwright && playwright install chromium")

    def _html_to_docx(self, html_content: str, filename_or_path):
        """
        HTML转DOCX

        Args:
            html_content: HTML内容
            filename_or_path: 输出文件名或完整路径
        """
        # 判断是文件名还是完整路径
        if isinstance(filename_or_path, (str, Path)) and (Path(filename_or_path).parent != Path('.')):
            output_path = Path(filename_or_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            docx_dir = output_path.parent
            filename = output_path.name
        else:
            docx_dir = self.output_base / self.config['output']['docx_folder']
            docx_dir.mkdir(parents=True, exist_ok=True)
            filename = filename_or_path
            output_path = docx_dir / filename

        html_path = docx_dir / f"{filename}.html"
        html_path.write_text(html_content, encoding='utf-8')

        try:
            subprocess.run([
                'pandoc',
                str(html_path),
                '-o', str(output_path)
            ], check=True)

            # 删除临时HTML文件
            if html_path.exists():
                html_path.unlink()

        except subprocess.CalledProcessError as e:
            self.logger.error(f"DOCX生成失败: {str(e)}")
            # 即使失败也尝试清理临时文件
            if html_path.exists():
                html_path.unlink()
        except FileNotFoundError:
            self.logger.error("pandoc未安装，跳过DOCX生成")
            # 清理临时文件
            if html_path.exists():
                html_path.unlink()
