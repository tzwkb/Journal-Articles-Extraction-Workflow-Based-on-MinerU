"""
断点续传管理模块
负责检查文件处理进度，智能跳过已完成的步骤
"""

from pathlib import Path
from typing import List, Tuple, Dict
from dataclasses import dataclass
from enum import Enum


class ProcessStage(Enum):
    """处理阶段"""
    NOT_STARTED = "未开始"
    MINERU_PARSED = "已解析"
    HTML_GENERATED = "HTML已生成"
    FORMATS_PARTIAL = "格式部分完成"
    COMPLETED = "已完成"


@dataclass
class FileStatus:
    """文件处理状态"""
    relative_path: str
    pdf_path: str
    output_paths: dict
    stage: ProcessStage
    missing_outputs: List[str]  # 缺失的输出（如 ["PDF", "DOCX"]）


class ResumeManager:
    """断点续传管理器"""

    def __init__(self, logger):
        """
        初始化断点续传管理器

        Args:
            logger: 日志记录器实例
        """
        self.logger = logger

    def check_file_status(
        self,
        relative_path: str,
        pdf_path: str,
        output_paths: dict
    ) -> FileStatus:
        """
        检查单个文件的处理状态

        Args:
            relative_path: 相对路径
            pdf_path: PDF 绝对路径
            output_paths: 输出路径字典

        Returns:
            FileStatus: 文件状态对象
        """
        # 检查各阶段输出文件是否存在
        mineru_zip = Path(output_paths['mineru'])
        html_original = Path(output_paths['html_original'])
        html_translated = Path(output_paths['html_translated'])
        pdf_translated = Path(output_paths['pdf_translated'])
        docx_translated = Path(output_paths['docx_translated'])

        missing_outputs = []

        # 情况1：所有最终输出都存在 = 完全完成
        if html_translated.exists() and pdf_translated.exists() and docx_translated.exists():
            return FileStatus(
                relative_path=relative_path,
                pdf_path=pdf_path,
                output_paths=output_paths,
                stage=ProcessStage.COMPLETED,
                missing_outputs=[]
            )

        # 情况2：HTML 存在但格式转换未完成
        if html_translated.exists():
            if not pdf_translated.exists():
                missing_outputs.append("PDF")
            if not docx_translated.exists():
                missing_outputs.append("DOCX")

            return FileStatus(
                relative_path=relative_path,
                pdf_path=pdf_path,
                output_paths=output_paths,
                stage=ProcessStage.FORMATS_PARTIAL if missing_outputs else ProcessStage.HTML_GENERATED,
                missing_outputs=missing_outputs
            )

        # 情况3：MinerU 完成但未翻译
        if mineru_zip.exists():
            return FileStatus(
                relative_path=relative_path,
                pdf_path=pdf_path,
                output_paths=output_paths,
                stage=ProcessStage.MINERU_PARSED,
                missing_outputs=["HTML", "PDF", "DOCX"]
            )

        # 情况4：完全未处理
        return FileStatus(
            relative_path=relative_path,
            pdf_path=pdf_path,
            output_paths=output_paths,
            stage=ProcessStage.NOT_STARTED,
            missing_outputs=["MinerU", "HTML", "PDF", "DOCX"]
        )

    def categorize_files(
        self,
        file_list: List[Tuple[str, str]],
        path_manager
    ) -> Dict[str, List]:
        """
        将文件列表按处理状态分类

        Args:
            file_list: [(relative_path, pdf_path), ...] 文件列表
            path_manager: 路径管理器实例

        Returns:
            分类结果字典:
            {
                'completed': [...],           # 已完成
                'need_formats': [...],        # 需要格式转换
                'need_translation': [...],    # 需要翻译
                'need_mineru': [...]         # 需要 MinerU 解析
            }
        """
        self.logger.info(f"\n>>> 检查已有结果（智能断点续传）...")

        completed = []
        need_formats = []
        need_translation = []
        need_mineru = []

        for relative_path, pdf_path in file_list:
            output_paths = path_manager.get_output_paths(relative_path)
            status = self.check_file_status(relative_path, pdf_path, output_paths)

            if status.stage == ProcessStage.COMPLETED:
                self.logger.info(f"✓ 已完成: {relative_path}")
                completed.append(status)

            elif status.stage == ProcessStage.FORMATS_PARTIAL:
                missing_str = ", ".join(status.missing_outputs)
                self.logger.info(f"⚠ 需补全格式: {relative_path} (缺: {missing_str})")
                need_formats.append(status)

            elif status.stage == ProcessStage.HTML_GENERATED:
                self.logger.info(f"✓ HTML已生成（待格式转换）: {relative_path}")
                need_formats.append(status)

            elif status.stage == ProcessStage.MINERU_PARSED:
                self.logger.info(f"✓ 已解析（待翻译）: {relative_path}")
                need_translation.append(status)

            else:  # NOT_STARTED
                self.logger.info(f"○ 待解析: {relative_path}")
                need_mineru.append(status)

        # 输出统计
        self.logger.info("\n--- 断点续传统计 ---")
        self.logger.info(f"✓ 已完成: {len(completed)} 个文件")
        self.logger.info(f"⚠ 需补全格式: {len(need_formats)} 个文件")
        self.logger.info(f"→ 需翻译: {len(need_translation)} 个文件")
        self.logger.info(f"○ 需解析: {len(need_mineru)} 个文件")
        self.logger.info(f"总计: {len(file_list)} 个文件")

        return {
            'completed': completed,
            'need_formats': need_formats,
            'need_translation': need_translation,
            'need_mineru': need_mineru
        }

    def prepare_processing_lists(
        self,
        categorized: Dict[str, List]
    ) -> Tuple[List, List]:
        """
        准备处理列表：需要上传的文件 + 需要翻译的文件

        Args:
            categorized: 分类结果

        Returns:
            (files_to_upload, ready_to_translate)
        """
        files_to_upload = []
        ready_to_translate = []

        # 需要 MinerU 解析的文件
        for status in categorized['need_mineru']:
            files_to_upload.append((
                status.relative_path,
                status.pdf_path,
                status.output_paths
            ))

        # 需要翻译的文件（MinerU 已完成）
        for status in categorized['need_translation']:
            mineru_zip = str(Path(status.output_paths['mineru']))
            ready_to_translate.append((
                status.relative_path,
                status.pdf_path,
                mineru_zip
            ))

        # 需要格式转换的文件（HTML 已存在）
        for status in categorized['need_formats']:
            mineru_zip = str(Path(status.output_paths['mineru']))
            ready_to_translate.append((
                status.relative_path,
                status.pdf_path,
                mineru_zip
            ))

        return files_to_upload, ready_to_translate

    def is_all_completed(self, categorized: Dict[str, List]) -> bool:
        """
        检查是否所有文件都已完成

        Args:
            categorized: 分类结果

        Returns:
            bool: 是否全部完成
        """
        total_incomplete = (
            len(categorized['need_formats']) +
            len(categorized['need_translation']) +
            len(categorized['need_mineru'])
        )
        return total_incomplete == 0


if __name__ == "__main__":
    print("断点续传管理模块")
    print("用法示例:")
    print("""
    from resume_manager import ResumeManager
    from logger import Logger

    logger = Logger()
    resume_mgr = ResumeManager(logger)

    # 分类文件
    categorized = resume_mgr.categorize_files(file_list, path_manager)

    # 检查是否全部完成
    if resume_mgr.is_all_completed(categorized):
        print("所有文件已处理完成！")

    # 准备处理列表
    files_to_upload, ready_to_translate = resume_mgr.prepare_processing_lists(categorized)
    """)
