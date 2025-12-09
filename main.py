"""
主流程脚本
完成整个文档翻译流程：
1. 生成文档大纲
2. 调用MinerU解析PDF
3. 按页处理内容并翻译
4. 生成HTML
5. 转换为PDF/DOCX
"""

import yaml
import sys
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from jinja2 import Template

from mineru_client import MinerUClient, FileTask
from mineru_parser import MinerUParser
from article_translator import ArticleTranslator
from logger import Logger
from format_converter import FormatConverter
from outline_generator import OutlineGenerator
from path_manager import PathManager

try:
    from openpyxl import load_workbook
except ImportError:
    load_workbook = None

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None


class DocumentProcessor:
    """文档处理主类"""

    def __init__(self, config_path="config.yaml"):
        """
        初始化文档处理器

        Args:
            config_path: 配置文件路径
        """
        # 加载配置
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)

        self.logger = Logger()
        self.output_base = Path(self.config['paths']['output_base'])

        # 初始化MinerU客户端
        self.mineru = MinerUClient(
            api_token=self.config['api']['mineru_token'],
            verify_ssl=False,
            max_retries=5
        )

        # 初始化解析器
        self.parser = MinerUParser()

        # 初始化格式转换器
        self.converter = FormatConverter(self.config, self.logger, self.output_base)

        # 初始化大纲生成器
        self.outline_gen = OutlineGenerator(self.config, self.logger, self.output_base)

        # 初始化路径管理器
        self.path_mgr = PathManager(self.config, self.logger)

        # 初始化文件夹结构
        self._init_directories()

    def _init_directories(self):
        """初始化所需的文件夹结构"""
        input_base = Path(self.config['paths']['input_base'])
        output_base = Path(self.config['paths']['output_base'])
        terminology_folder = Path(self.config['paths']['terminology_folder'])

        # 输出文件夹名称
        mineru_folder = self.config['output']['mineru_folder']
        html_folder = self.config['output']['html_folder']
        pdf_folder = self.config['output']['pdf_folder']
        docx_folder = self.config['output']['docx_folder']
        cache_folder = self.config['output']['cache_folder']

        # 创建所有必要的目录
        folders = [
            input_base,
            terminology_folder,
            output_base / mineru_folder,
            output_base / html_folder,
            output_base / pdf_folder,
            output_base / docx_folder,
            output_base / cache_folder / 'outlines',
        ]

        for folder in folders:
            folder.mkdir(parents=True, exist_ok=True)

        self.logger.info(f"文件夹结构初始化完成")

    def load_terminology_from_excel(self) -> dict:
        """
        从 terminology 文件夹下的 Excel 文件加载术语库

        Returns:
            术语字典 {"English": "中文"}
        """
        terminology_folder = Path(self.config['paths']['terminology_folder'])

        if not terminology_folder.exists():
            self.logger.warning(f"术语库文件夹不存在: {terminology_folder}")
            return {}

        if not load_workbook:
            self.logger.warning("openpyxl 未安装，无法读取 Excel 术语库")
            return {}

        glossary = {}
        excel_files = list(terminology_folder.glob("*.xlsx")) + list(terminology_folder.glob("*.xls"))

        if not excel_files:
            self.logger.warning(f"术语库文件夹中没有 Excel 文件: {terminology_folder}")
            return {}

        self.logger.info(f"正在加载术语库，共 {len(excel_files)} 个 Excel 文件...")

        for excel_file in excel_files:
            try:
                workbook = load_workbook(excel_file, read_only=True, data_only=True)

                # 遍历所有 sheet
                for sheet_name in workbook.sheetnames:
                    sheet = workbook[sheet_name]

                    # 跳过空 sheet
                    if sheet.max_row <= 1:
                        continue

                    # 假设第一列是英文，第二列是中文（跳过标题行）
                    for row in sheet.iter_rows(min_row=2, values_only=True):
                        if len(row) >= 2 and row[0] and row[1]:
                            english_term = str(row[0]).strip()
                            chinese_term = str(row[1]).strip()

                            if english_term and chinese_term:
                                glossary[english_term] = chinese_term

                workbook.close()
                self.logger.info(f"  已加载: {excel_file.name} - {len(glossary)} 个术语")

            except Exception as e:
                self.logger.error(f"加载 Excel 文件失败: {excel_file.name} - {str(e)}")

        self.logger.success(f"术语库加载完成，共 {len(glossary)} 个术语")
        return glossary

    def batch_process(self):
        """
        批量处理 input 文件夹中的所有 PDF 文件（多文件并发）
        """
        self.logger.info("=" * 60)
        self.logger.info("批量处理模式")
        self.logger.info("=" * 60)

        # 1. 扫描输入文件
        file_list = self.path_mgr.scan_input_files()

        if not file_list:
            self.logger.error("没有找到要处理的 PDF 文件")
            return

        # 2. 加载全局术语库（从 Excel）
        excel_glossary = self.load_terminology_from_excel()

        # 3. 多文件并发处理
        max_workers = self.config['concurrency']['max_files']
        self.logger.info(f"开始并发处理，并发数: {max_workers}")

        # 使用 ProcessPoolExecutor 进行多文件并发
        success_count = 0
        failure_count = 0
        results = []

        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            # 提交所有任务
            future_to_file = {
                executor.submit(self._process_single_file, relative_path, pdf_path, excel_glossary):
                (relative_path, pdf_path)
                for relative_path, pdf_path in file_list
            }

            # 使用 tqdm 显示进度（如果可用）
            if tqdm:
                future_iterator = tqdm(as_completed(future_to_file), total=len(file_list), desc="处理进度")
            else:
                future_iterator = as_completed(future_to_file)

            # 收集结果
            for future in future_iterator:
                relative_path, pdf_path = future_to_file[future]
                try:
                    result = future.result()
                    if result['success']:
                        success_count += 1
                        self.logger.success(f"✓ 完成: {relative_path}")
                    else:
                        failure_count += 1
                        self.logger.error(f"✗ 失败: {relative_path} - {result.get('error', 'Unknown error')}")
                    results.append(result)
                except Exception as e:
                    failure_count += 1
                    self.logger.error(f"✗ 失败: {relative_path} - {str(e)}")
                    results.append({'success': False, 'file': relative_path, 'error': str(e)})

        # 4. 输出汇总
        self.logger.info("=" * 60)
        self.logger.info(f"批量处理完成！")
        self.logger.info(f"  成功: {success_count} 个文件")
        self.logger.info(f"  失败: {failure_count} 个文件")
        self.logger.info("=" * 60)

        return results

    def _process_single_file(self, relative_path: str, pdf_path: str, excel_glossary: dict) -> dict:
        """
        处理单个 PDF 文件（用于多进程调用）

        Args:
            relative_path: 相对路径
            pdf_path: PDF 绝对路径
            excel_glossary: Excel 术语库

        Returns:
            处理结果字典
        """
        try:
            # 生成输出路径
            output_paths = self.path_mgr.get_output_paths(relative_path)

            # 调用单文件处理流程
            self.run(pdf_path, output_paths, excel_glossary)

            return {
                'success': True,
                'file': relative_path,
                'output_paths': {k: str(v) for k, v in output_paths.items()}
            }
        except Exception as e:
            return {
                'success': False,
                'file': relative_path,
                'error': str(e)
            }

    def run(self, pdf_path: str, output_paths: dict = None, excel_glossary: dict = None):
        """
        运行完整流程

        Args:
            pdf_path: PDF文件路径
            output_paths: 自定义输出路径字典（可选）
            excel_glossary: Excel术语库（可选）
        """
        self.logger.info("=" * 60)
        self.logger.info("开始处理文档")
        self.logger.info("=" * 60)

        try:
            # 步骤1: 生成大纲
            outline = self.outline_gen.generate_outline(pdf_path, output_paths)

            # 步骤2: MinerU解析
            content_list = self.parse_with_mineru(pdf_path, output_paths)

            # 步骤3: 合并术语库（Excel + AI生成）
            combined_glossary = {}
            if excel_glossary:
                combined_glossary.update(excel_glossary)
            combined_glossary.update(outline.get('glossary', {}))

            self.logger.info(f"术语库合并完成: {len(combined_glossary)} 个术语")

            # 步骤4: 初始化翻译器（带合并后的术语表）
            translator = ArticleTranslator(
                api_key=self.config['api']['translation_api_key'],
                api_url=self.config['api']['translation_api_base_url'],
                model=self.config['api']['translation_api_model'],
                glossary=combined_glossary,
                case_sensitive=False,
                whole_word_only=True,
                config=self.config  # 传递config，用于读取API参数和并发配置
            )

            # 步骤5: 处理内容并翻译
            original_html, translated_html = self.process_content(
                content_list, outline, translator
            )

            # 步骤6: 导出格式
            self.converter.export_formats(original_html, translated_html, output_paths)

            self.logger.info("=" * 60)
            self.logger.success("处理完成！")
            self.logger.info("=" * 60)

        except Exception as e:
            self.logger.error(f"处理失败: {str(e)}")
            import traceback
            traceback.print_exc()
            raise  # 抛出异常而不是exit，以便批处理能继续

    def parse_with_mineru(self, pdf_path: str, output_paths: dict = None) -> list:
        """
        使用MinerU解析PDF

        Args:
            pdf_path: PDF文件路径
            output_paths: 自定义输出路径字典（可选）

        Returns:
            content_list.json内容
        """
        self.logger.info("\n>>> 步骤2: 使用MinerU解析PDF...")

        # 确定缓存路径
        if output_paths and 'mineru' in output_paths:
            expected_zip = Path(output_paths['mineru'])
            cache_dir = expected_zip.parent
        else:
            cache_dir = self.output_base / "cache/mineru_results"
            pdf_name = Path(pdf_path).stem
            expected_zip = cache_dir / f"{pdf_name}_result.zip"

        # 检查是否已有解析结果
        if expected_zip.exists():
            self.logger.info("发现已有MinerU解析结果，直接加载...")
            parsed = self.parser.parse_zip_result(
                str(expected_zip),
                source_file_name=Path(pdf_path).name
            )
            self.logger.success(f"解析结果已加载: {len(parsed.json_content)} 个内容块")
            return parsed.json_content

        # 上传并解析
        file_task = FileTask(
            file_name=Path(pdf_path).name,
            file_path=pdf_path,
            data_id=Path(pdf_path).stem
        )

        self.logger.info("正在上传PDF到MinerU...")
        batch_id, _ = self.mineru.batch_upload_files([file_task])

        self.logger.info("等待MinerU解析完成...")
        results = self.mineru.wait_for_completion(batch_id, poll_interval=10)

        # 下载结果
        cache_dir.mkdir(parents=True, exist_ok=True)
        downloaded = self.mineru.download_all_results(results, str(cache_dir))

        # 解析ZIP并移动到目标位置
        zip_path = list(downloaded.values())[0]

        # 如果有自定义路径，移动文件
        if output_paths and 'mineru' in output_paths:
            import shutil
            shutil.move(zip_path, str(expected_zip))
            zip_path = str(expected_zip)

        parsed = self.parser.parse_zip_result(
            zip_path,
            source_file_name=Path(pdf_path).name
        )

        self.logger.success(f"解析完成: {len(parsed.json_content)} 个内容块")
        return parsed.json_content

    def process_content(
        self,
        content_list: list,
        outline: dict,
        translator: ArticleTranslator
    ) -> tuple:
        """
        处理内容并翻译（使用批量并发翻译）

        Args:
            content_list: MinerU返回的content_list
            outline: 文档大纲
            translator: 翻译器实例

        Returns:
            (original_html, translated_html) 元组
        """
        self.logger.info("\n>>> 步骤3: 处理内容并翻译...")

        # 按页分组
        pages = {}
        for item in content_list:
            page_idx = item.get('page_idx', 0)
            if page_idx not in pages:
                pages[page_idx] = []
            pages[page_idx].append(item)

        self.logger.info(f"共 {len(pages)} 页")

        # 处理图片：复制图片到输出目录并更新路径
        self._process_images(content_list)

        # 收集所有翻译任务
        tasks = []  # [(item, field_name, text, context), ...]
        total_items = sum(len(items) for items in pages.values())

        for page_idx in sorted(pages.keys()):
            items = pages[page_idx]
            context = self._get_chapter_context(page_idx, outline)

            for item in items:
                # 跳过header/footer/page_number
                if item['type'] in ['header', 'footer', 'page_number']:
                    continue

                # 收集文本翻译任务
                if item['type'] == 'text' and item.get('text'):
                    tasks.append((item, 'text_zh', item['text'], context))

                # 收集图片说明翻译任务
                if item['type'] == 'image' and item.get('image_caption'):
                    caption_text = ' '.join(item['image_caption'])
                    tasks.append((item, 'caption_zh', caption_text, context))

        self.logger.info(f"共收集 {len(tasks)} 个翻译任务，开始并发翻译...")

        # 批量并发翻译
        translation_tasks = [(text, context) for _, _, text, context in tasks]
        translations = translator.translate_batch(translation_tasks)

        # 将翻译结果赋值回item（线程安全，主线程执行）
        for i, (item, field_name, _, _) in enumerate(tasks):
            item[field_name] = translations[i]

            # 每10%显示进度
            if (i + 1) % max(1, len(tasks) // 10) == 0:
                progress = (i + 1) * 100 // len(tasks)
                self.logger.info(f"  翻译进度: {i + 1}/{len(tasks)} ({progress}%)")

        self.logger.success(f"翻译完成: {len(tasks)} 个内容块")

        # 生成HTML
        self.logger.info("正在生成HTML...")
        original_html = self._render_html(pages, language='en')
        translated_html = self._render_html(pages, language='zh')

        # 保存HTML
        html_dir = self.output_base / "html"
        html_dir.mkdir(parents=True, exist_ok=True)

        (html_dir / "original.html").write_text(original_html, encoding='utf-8')
        (html_dir / "translated.html").write_text(translated_html, encoding='utf-8')

        self.logger.success(f"HTML已生成: {html_dir}")

        return original_html, translated_html

    def _process_images(self, content_list: list):
        """
        处理图片：复制图片到HTML输出目录并更新路径

        Args:
            content_list: 内容列表
        """
        import shutil

        # 确定MinerU解压目录
        mineru_folder = self.config['output']['mineru_folder']
        mineru_dir = self.output_base / mineru_folder

        if not mineru_dir.exists():
            self.logger.warning(f"未找到MinerU输出目录: {mineru_dir}，跳过图片处理")
            return

        # 查找最新的解压目录（包含images子目录的目录）
        extract_dirs = []
        for item in mineru_dir.rglob("*"):
            if item.is_dir() and (item / "images").exists():
                extract_dirs.append(item)

        if not extract_dirs:
            self.logger.warning("未找到包含images目录的MinerU解压结果")
            return

        # 使用最新的目录
        latest_dir = max(extract_dirs, key=lambda d: d.stat().st_mtime)
        source_images_dir = latest_dir / "images"

        self.logger.info(f"找到MinerU图片目录: {source_images_dir}")

        # 创建目标图片目录（HTML/images）
        html_folder = self.config['output']['html_folder']
        html_dir = self.output_base / html_folder
        target_images_dir = html_dir / "images"
        target_images_dir.mkdir(parents=True, exist_ok=True)

        # 复制图片并更新路径
        copied_count = 0
        for item in content_list:
            if item.get('type') == 'image' and item.get('img_path'):
                img_rel_path = item['img_path']  # 例如: "images/xxx.jpg"

                # 构建源文件路径
                source_img = latest_dir / img_rel_path

                if source_img.exists():
                    # 提取文件名
                    img_filename = Path(img_rel_path).name

                    # 复制到目标目录
                    target_img = target_images_dir / img_filename
                    shutil.copy2(source_img, target_img)

                    # 更新item中的路径（相对于HTML文件）
                    item['img_path'] = f"images/{img_filename}"
                    copied_count += 1

        if copied_count > 0:
            self.logger.success(f"已复制 {copied_count} 张图片到 {target_images_dir}")
        else:
            self.logger.warning("未找到任何图片文件")

    def _get_chapter_context(self, page_idx: int, outline: dict) -> dict:
        """
        获取页面对应的章节上下文

        Args:
            page_idx: 页面索引
            outline: 文档大纲

        Returns:
            章节上下文字典
        """
        for chapter in outline.get('structure', []):
            pages = chapter.get('pages', [])
            if len(pages) >= 2:
                start, end = pages[0], pages[1]
                if start <= page_idx <= end:
                    return {
                        'chapter_title': chapter.get('title', ''),
                        'chapter_summary': chapter.get('summary', ''),
                        'keywords': chapter.get('keywords', [])
                    }
        return {}

    def _render_html(self, pages: dict, language: str) -> str:
        """
        渲染HTML

        Args:
            pages: 按页分组的内容
            language: 语言（'en'或'zh'）

        Returns:
            HTML字符串
        """
        with open('page_template.html', 'r', encoding='utf-8') as f:
            template = Template(f.read())

        return template.render(pages=pages, language=language)


def main():
    """命令行入口"""
    # 如果没有参数，进入交互模式
    if len(sys.argv) == 1:
        interactive_mode()
        return

    # 批处理模式
    if sys.argv[1] in ["--batch", "-b", "--interactive", "-i"]:
        interactive_mode()
    else:
        # 如果提供了参数但不是已知选项，显示错误
        print(f"❌ 未知参数: {sys.argv[1]}")
        print("使用 'python main.py -h' 查看帮助")
        sys.exit(1)

def interactive_mode():
    """交互式命令行界面"""
    processor = DocumentProcessor()

    while True:
        print("\n" + "="*60)
        print("  MinerU 文档翻译工具 - 交互模式")
        print("="*60)
        print("\n请选择操作：")
        print("  [1] 批量处理（递归扫描 input/ 文件夹）")
        print("  [2] 查看配置信息")
        print("  [3] 查看输入文件列表")
        print("  [4] 清除缓存")
        print("  [0] 退出")
        print()

        choice = input("请输入选项 [0-4]: ").strip()

        if choice == "0":
            print("\n再见！")
            break
        elif choice == "1":
            batch_mode_interactive(processor)
        elif choice == "2":
            show_config(processor)
        elif choice == "3":
            show_input_files(processor)
        elif choice == "4":
            clear_cache(processor)
        else:
            print("❌ 无效选项，请重新选择")


def batch_mode_interactive(processor):
    """批量处理交互模式"""
    print("\n" + "-"*60)
    print("  批量处理模式")
    print("-"*60)

    # 扫描文件
    file_list = processor.path_mgr.scan_input_files()

    if not file_list:
        print("\n❌ input/ 文件夹中没有找到 PDF 文件")
        print("   请先将 PDF 文件放入 input/ 文件夹")
        input("\n按回车键继续...")
        return

    print(f"\n找到 {len(file_list)} 个 PDF 文件:")
    for i, (rel_path, abs_path) in enumerate(file_list[:10], 1):
        print(f"  {i}. {rel_path}")

    if len(file_list) > 10:
        print(f"  ... 还有 {len(file_list) - 10} 个文件")

    print(f"\n并发配置:")
    print(f"  - 文件并发数: {processor.config['concurrency']['max_files']}")
    print(f"  - 翻译并发数: {processor.config['concurrency']['initial_translation_workers']} (初始)")

    confirm = input(f"\n确认开始批量处理？[y/N]: ").strip().lower()

    if confirm != 'y':
        print("已取消")
        return

    try:
        print("\n开始批量处理...")
        processor.batch_process()
        print("\n✓ 批量处理完成！")
    except Exception as e:
        print(f"\n❌ 批量处理失败: {str(e)}")

    input("\n按回车键继续...")


def show_config(processor):
    """显示配置信息"""
    print("\n" + "-"*60)
    print("  当前配置信息")
    print("-"*60)

    config = processor.config

    print("\n📡 API 配置:")
    print(f"  MinerU Token: {'已配置' if config['api']['mineru_token'] != 'YOUR_MINERU_TOKEN' else '❌ 未配置'}")
    print(f"  Outline API Key: {'已配置' if config['api']['outline_api_key'] != 'YOUR_GEMINI_KEY' else '❌ 未配置'}")
    print(f"  Outline API URL: {config['api']['outline_api_base_url']}")
    print(f"  Outline API Model: {config['api']['outline_api_model']}")
    print(f"  Translation API Key: {'已配置' if config['api']['translation_api_key'] else '❌ 未配置'}")
    print(f"  Translation API URL: {config['api']['translation_api_base_url']}")
    print(f"  Translation API Model: {config['api']['translation_api_model']}")

    print("\n⚙️ API 参数:")
    print(f"  Temperature: {config['api']['temperature']}")
    print(f"  Max Tokens: {config['api']['max_tokens']}")
    print(f"  Timeout: {config['api']['timeout']}s")

    print("\n🔄 并发配置:")
    print(f"  文件并发数: {config['concurrency']['max_files']}")
    print(f"  初始翻译并发: {config['concurrency']['initial_translation_workers']}")
    print(f"  最大翻译并发: {config['concurrency']['max_translation_workers']}")
    print(f"  最小翻译并发: {config['concurrency']['min_translation_workers']}")

    print("\n📂 路径配置:")
    print(f"  输入目录: {config['paths']['input_base']}")
    print(f"  输出目录: {config['paths']['output_base']}")
    print(f"  术语库目录: {config['paths']['terminology_folder']}")

    print("\n📄 输出格式:")
    print(f"  格式: {', '.join(config['output']['formats'])}")

    input("\n按回车键继续...")


def show_input_files(processor):
    """显示输入文件列表"""
    print("\n" + "-"*60)
    print("  输入文件列表")
    print("-"*60)

    file_list = processor.path_mgr.scan_input_files()

    if not file_list:
        print("\n❌ input/ 文件夹中没有找到 PDF 文件")
        print("   请先将 PDF 文件放入 input/ 文件夹")
    else:
        print(f"\n找到 {len(file_list)} 个 PDF 文件:\n")
        for i, (rel_path, abs_path) in enumerate(file_list, 1):
            file_size = Path(abs_path).stat().st_size / (1024 * 1024)  # MB
            print(f"  {i:3d}. {rel_path:50s} ({file_size:.1f} MB)")

    input("\n按回车键继续...")


def clear_cache(processor):
    """清除缓存"""
    print("\n" + "-"*60)
    print("  清除缓存")
    print("-"*60)

    cache_dir = processor.output_base / "cache"

    if not cache_dir.exists():
        print("\n没有缓存需要清除")
        input("\n按回车键继续...")
        return

    print("\n缓存目录:")
    print(f"  {cache_dir}")

    # 统计缓存大小
    total_size = 0
    file_count = 0
    for file in cache_dir.rglob("*"):
        if file.is_file():
            total_size += file.stat().st_size
            file_count += 1

    print(f"\n缓存统计:")
    print(f"  文件数: {file_count}")
    print(f"  总大小: {total_size / (1024 * 1024):.1f} MB")

    confirm = input("\n确认清除所有缓存？[y/N]: ").strip().lower()

    if confirm != 'y':
        print("已取消")
        input("\n按回车键继续...")
        return

    try:
        import shutil
        shutil.rmtree(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        print("\n✓ 缓存已清除")
    except Exception as e:
        print(f"\n❌ 清除失败: {str(e)}")

    input("\n按回车键继续...")


if __name__ == "__main__":
    main()
