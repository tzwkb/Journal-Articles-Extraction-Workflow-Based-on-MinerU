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
                self._html_to_pdf(html_original_path, output_paths['pdf_original'])
                self._html_to_pdf(html_translated_path, output_paths['pdf_translated'])
            else:
                self._html_to_pdf(html_original_path, "original.pdf")
                self._html_to_pdf(html_translated_path, "translated.pdf")
            self.logger.success("PDF已生成")

        if 'docx' in formats:
            self.logger.info("正在生成DOCX...")
            if output_paths and 'docx_original' in output_paths:
                self._html_to_docx(html_original_path, output_paths['docx_original'])
                self._html_to_docx(html_translated_path, output_paths['docx_translated'])
            else:
                self._html_to_docx(html_original_path, "original.docx")
                self._html_to_docx(html_translated_path, "translated.docx")
            self.logger.success("DOCX已生成")

    def _html_to_pdf(self, html_path, output_path):
        """
        HTML转PDF（使用Playwright）- 增加超时和优化

        Args:
            html_path: HTML文件路径（Path对象或字符串）
            output_path: 输出PDF路径（Path对象或字符串）
        """
        # 确保是Path对象
        html_path = Path(html_path)
        
        # 判断是文件名还是完整路径
        if isinstance(output_path, (str, Path)) and (Path(output_path).parent != Path('.')):
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            pdf_dir = self.output_base / self.config['output']['pdf_folder']
            pdf_dir.mkdir(parents=True, exist_ok=True)
            output_path = pdf_dir / output_path

        try:
            with sync_playwright() as p:
                # 启动浏览器（禁用超时）
                browser = p.chromium.launch(
                    headless=True,
                    args=['--disable-web-security', '--disable-features=IsolateOrigins,site-per-process']
                )
                page = browser.new_page()
                
                # 设置更长的超时时间和等待策略
                page.set_default_timeout(180000)
                
                # 使用 file:// 协议加载本地HTML
                file_url = f"file:///{html_path.absolute().as_posix()}"
                self.logger.info(f"  加载HTML: {file_url}")
                
                # 导航到页面，等待网络空闲（图片加载完成）
                page.goto(
                    file_url,
                    wait_until='networkidle',  # 等待网络空闲
                    timeout=180000
                )
                
                # 额外等待，确保所有图片加载完成
                page.wait_for_timeout(2000)
                
                # 生成PDF
                self.logger.info(f"  生成PDF: {output_path}")
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
                self.logger.success(f"  ✓ PDF已生成: {output_path.name}")

        except Exception as e:
            self.logger.error(f"PDF生成失败: {str(e)}")
            self.logger.error("请确保已安装 Playwright: pip install playwright && playwright install chromium")

    def _html_to_docx(self, html_path, output_path):
        """
        HTML转DOCX（使用pandoc）

        Args:
            html_path: HTML文件路径（Path对象或字符串）
            output_path: 输出DOCX路径（Path对象或字符串）
        """
        # 确保是Path对象
        html_path = Path(html_path)
        
        # 判断是文件名还是完整路径
        if isinstance(output_path, (str, Path)) and (Path(output_path).parent != Path('.')):
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            docx_dir = self.output_base / self.config['output']['docx_folder']
            docx_dir.mkdir(parents=True, exist_ok=True)
            output_path = docx_dir / output_path

        try:
            self.logger.info(f"  转换DOCX: {html_path} -> {output_path}")
            
            # 使用pandoc转换，添加 --extract-media 参数以提取图片
            result = subprocess.run([
                'pandoc',
                str(html_path),
                '-o', str(output_path),
                '--extract-media', str(output_path.parent),  # 提取图片到同目录
                '--resource-path', str(html_path.parent)  # 指定资源查找路径
            ], 
            check=True,
            capture_output=True,
            text=True,
            timeout=120  # 120秒超时
            )
            
            self.logger.success(f"  ✓ DOCX已生成: {output_path.name}")

            # 清理Pandoc产生的散落图片文件
            docx_dir = output_path.parent
            cleaned_count = 0

            # 清理 .jpg 文件
            for img_file in docx_dir.glob("*.jpg"):
                try:
                    img_file.unlink()
                    cleaned_count += 1
                except Exception as e:
                    self.logger.warning(f"  ⚠ 无法删除图片: {img_file.name} - {e}")

            # 清理 .png 文件
            for img_file in docx_dir.glob("*.png"):
                try:
                    img_file.unlink()
                    cleaned_count += 1
                except Exception as e:
                    self.logger.warning(f"  ⚠ 无法删除图片: {img_file.name} - {e}")

            if cleaned_count > 0:
                self.logger.info(f"  ✓ 已清理 {cleaned_count} 个DOCX散落图片")

        except subprocess.TimeoutExpired:
            self.logger.error(f"DOCX生成超时（120秒）")
        except subprocess.CalledProcessError as e:
            self.logger.error(f"DOCX生成失败: {e.stderr}")
        except FileNotFoundError:
            self.logger.error("pandoc未安装，跳过DOCX生成")
            self.logger.error("安装方法：")
            self.logger.error("  Windows: choco install pandoc")
            self.logger.error("  Mac: brew install pandoc")
            self.logger.error("  Linux: apt-get install pandoc")
