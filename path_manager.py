"""
路径管理模块
负责文件扫描和输出路径映射
"""

from pathlib import Path


class PathManager:
    """路径管理器"""

    def __init__(self, config: dict, logger):
        """
        初始化路径管理器

        Args:
            config: 配置字典
            logger: 日志记录器实例
        """
        self.config = config
        self.logger = logger

    def scan_input_files(self) -> list:
        """
        递归扫描 input 文件夹下的所有 PDF 文件

        Returns:
            [(相对路径, 绝对路径), ...] 例如: [('project1/doc.pdf', 'input/project1/doc.pdf'), ...]
        """
        input_base = Path(self.config['paths']['input_base'])

        if not input_base.exists():
            self.logger.error(f"输入文件夹不存在: {input_base}")
            return []

        # 递归查找所有 PDF 文件
        pdf_files = list(input_base.rglob("*.pdf"))

        if not pdf_files:
            self.logger.warning(f"输入文件夹中没有 PDF 文件: {input_base}")
            return []

        # 计算相对路径
        file_list = []
        for pdf_file in pdf_files:
            relative_path = pdf_file.relative_to(input_base)
            file_list.append((str(relative_path), str(pdf_file)))

        self.logger.info(f"扫描到 {len(file_list)} 个 PDF 文件")
        return file_list

    def get_output_paths(self, relative_path: str) -> dict:
        """
        根据输入文件的相对路径，生成所有输出文件的路径（复刻 input 层级）

        Args:
            relative_path: 相对于 input 的路径，例如 'project1/research/paper.pdf'

        Returns:
            输出路径字典
        """
        output_base = Path(self.config['paths']['output_base'])

        # 提取文件名（不含扩展名）和目录结构
        path_obj = Path(relative_path)
        file_stem = path_obj.stem  # 例如 'paper'
        dir_structure = path_obj.parent  # 例如 'project1/research'

        # 生成输出文件夹名称
        mineru_folder = self.config['output']['mineru_folder']
        html_folder = self.config['output']['html_folder']
        pdf_folder = self.config['output']['pdf_folder']
        docx_folder = self.config['output']['docx_folder']
        cache_folder = self.config['output']['cache_folder']

        # 构建完整路径（复刻层级）
        paths = {
            'mineru': output_base / mineru_folder / dir_structure / f"{file_stem}_result.zip",
            'html_original': output_base / html_folder / dir_structure / f"{file_stem}_original.html",
            'html_translated': output_base / html_folder / dir_structure / f"{file_stem}_translated.html",
            'pdf_original': output_base / pdf_folder / dir_structure / f"{file_stem}_original.pdf",
            'pdf_translated': output_base / pdf_folder / dir_structure / f"{file_stem}_translated.pdf",
            'docx_original': output_base / docx_folder / dir_structure / f"{file_stem}_original.docx",
            'docx_translated': output_base / docx_folder / dir_structure / f"{file_stem}_translated.docx",
            'outline': output_base / cache_folder / 'outlines' / f"{str(dir_structure).replace('/', '_').replace('\\', '_')}_{file_stem}.json"
        }

        # 创建所有必要的目录
        for key, path in paths.items():
            path.parent.mkdir(parents=True, exist_ok=True)

        return paths
