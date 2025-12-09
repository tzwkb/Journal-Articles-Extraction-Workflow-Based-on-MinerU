"""
MinerU Result Parser
解析MinerU返回的zip压缩包，提取json、markdown、images等内容
"""

import os
import json
import zipfile
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict

# 导入现有工具
try:
    from logger import Logger
    from json_utils import parse_json_response, validate_json_structure
except ImportError:
    class Logger:
        @staticmethod
        def info(msg): print(f"[INFO] {msg}")
        @staticmethod
        def warning(msg): print(f"[WARNING] {msg}")
        @staticmethod
        def error(msg): print(f"[ERROR] {msg}")
        @staticmethod
        def success(msg): print(f"[SUCCESS] {msg}")

    def parse_json_response(text): return json.loads(text)
    def validate_json_structure(data, schema): return True


@dataclass
class ParsedContent:
    """解析后的内容结构"""
    # 基本信息
    source_file: str  # 原始PDF文件名
    zip_path: str  # zip文件路径

    # 提取的内容
    markdown_content: Optional[str] = None
    json_content: Optional[Dict] = None
    images: List[str] = None  # 图片文件路径列表

    # 可选格式
    html_content: Optional[str] = None
    docx_path: Optional[str] = None
    latex_content: Optional[str] = None

    # 元数据
    total_pages: Optional[int] = None
    has_images: bool = False
    has_tables: bool = False
    has_formulas: bool = False

    def __post_init__(self):
        if self.images is None:
            self.images = []


class MinerUParser:
    """MinerU结果解析器"""

    def __init__(self, output_dir: str = "./mineru_output"):
        """
        初始化解析器

        Args:
            output_dir: 解析后内容的输出目录
        """
        self.output_dir = Path(output_dir)
        self.logger = Logger()

    def extract_zip(self, zip_path: str, extract_to: Optional[str] = None) -> str:
        """
        解压zip文件

        Args:
            zip_path: zip文件路径
            extract_to: 解压目标目录（可选，默认在output_dir下创建）

        Returns:
            解压后的目录路径

        Raises:
            Exception: 解压失败时抛出异常
        """
        if not os.path.exists(zip_path):
            raise FileNotFoundError(f"Zip文件不存在: {zip_path}")

        # 确定解压目录
        if not extract_to:
            zip_name = Path(zip_path).stem
            extract_to = self.output_dir / zip_name

        extract_to = Path(extract_to)
        extract_to.mkdir(parents=True, exist_ok=True)

        self.logger.info(f"正在解压: {zip_path}")

        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                # 获取zip中的文件列表
                file_list = zip_ref.namelist()
                self.logger.info(f"  包含 {len(file_list)} 个文件")

                # 解压所有文件
                zip_ref.extractall(extract_to)

            self.logger.success(f"解压完成: {extract_to}")
            return str(extract_to)

        except Exception as e:
            raise Exception(f"解压失败: {str(e)}")

    def analyze_directory_structure(self, dir_path: str) -> Dict[str, Any]:
        """
        分析解压后的目录结构

        Args:
            dir_path: 目录路径

        Returns:
            目录结构信息
        """
        structure = {
            "total_files": 0,
            "markdown_files": [],
            "json_files": [],
            "image_files": [],
            "html_files": [],
            "docx_files": [],
            "latex_files": [],
            "other_files": []
        }

        dir_path = Path(dir_path)

        for root, dirs, files in os.walk(dir_path):
            for file in files:
                file_path = Path(root) / file
                rel_path = file_path.relative_to(dir_path)
                structure["total_files"] += 1

                # 根据扩展名分类
                ext = file_path.suffix.lower()

                if ext == ".md":
                    structure["markdown_files"].append(str(rel_path))
                elif ext == ".json":
                    structure["json_files"].append(str(rel_path))
                elif ext in [".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg"]:
                    structure["image_files"].append(str(rel_path))
                elif ext in [".html", ".htm"]:
                    structure["html_files"].append(str(rel_path))
                elif ext == ".docx":
                    structure["docx_files"].append(str(rel_path))
                elif ext in [".tex", ".latex"]:
                    structure["latex_files"].append(str(rel_path))
                else:
                    structure["other_files"].append(str(rel_path))

        return structure

    def read_markdown(self, file_path: str) -> str:
        """
        读取markdown文件

        Args:
            file_path: markdown文件路径

        Returns:
            markdown内容
        """
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()

    def read_json(self, file_path: str) -> Dict:
        """
        读取并解析JSON文件

        Args:
            file_path: JSON文件路径

        Returns:
            解析后的JSON对象
        """
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            return parse_json_response(content)

    def read_html(self, file_path: str) -> str:
        """
        读取HTML文件

        Args:
            file_path: HTML文件路径

        Returns:
            HTML内容
        """
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()

    def parse_zip_result(
        self,
        zip_path: str,
        source_file_name: Optional[str] = None
    ) -> ParsedContent:
        """
        解析MinerU返回的zip结果

        Args:
            zip_path: zip文件路径
            source_file_name: 原始文件名（可选）

        Returns:
            ParsedContent对象

        Raises:
            Exception: 解析失败时抛出异常
        """
        self.logger.info(f"开始解析MinerU结果: {zip_path}")

        # 1. 解压zip
        extract_dir = self.extract_zip(zip_path)

        # 2. 分析目录结构
        structure = self.analyze_directory_structure(extract_dir)

        self.logger.info("目录结构:")
        self.logger.info(f"  Markdown文件: {len(structure['markdown_files'])}")
        self.logger.info(f"  JSON文件: {len(structure['json_files'])}")
        self.logger.info(f"  图片文件: {len(structure['image_files'])}")
        self.logger.info(f"  HTML文件: {len(structure['html_files'])}")
        self.logger.info(f"  DOCX文件: {len(structure['docx_files'])}")
        self.logger.info(f"  LaTeX文件: {len(structure['latex_files'])}")

        # 3. 创建ParsedContent对象
        if not source_file_name:
            source_file_name = Path(zip_path).stem.replace("_result", "")

        parsed = ParsedContent(
            source_file=source_file_name,
            zip_path=zip_path
        )

        # 4. 读取markdown（通常是主文件）
        if structure['markdown_files']:
            # 优先查找auto目录下的.md文件
            md_file = None
            for md in structure['markdown_files']:
                if 'auto' in md.lower() or md.endswith('.md'):
                    md_file = md
                    break

            if not md_file:
                md_file = structure['markdown_files'][0]

            md_path = Path(extract_dir) / md_file
            parsed.markdown_content = self.read_markdown(md_path)
            self.logger.success(f"✓ 读取Markdown: {md_file} ({len(parsed.markdown_content)} 字符)")

        # 5. 读取JSON
        if structure['json_files']:
            # 优先查找content_list.json或类似的主JSON文件
            json_file = None
            for jf in structure['json_files']:
                if 'content' in jf.lower() or 'auto' in jf.lower():
                    json_file = jf
                    break

            if not json_file:
                json_file = structure['json_files'][0]

            json_path = Path(extract_dir) / json_file
            parsed.json_content = self.read_json(json_path)
            self.logger.success(f"✓ 读取JSON: {json_file}")

            # 尝试提取元数据
            self._extract_metadata(parsed)

        # 6. 收集图片
        if structure['image_files']:
            parsed.images = [str(Path(extract_dir) / img) for img in structure['image_files']]
            parsed.has_images = True
            self.logger.success(f"✓ 找到 {len(parsed.images)} 张图片")

        # 7. 读取可选格式
        if structure['html_files']:
            html_path = Path(extract_dir) / structure['html_files'][0]
            parsed.html_content = self.read_html(html_path)
            self.logger.success(f"✓ 读取HTML: {structure['html_files'][0]}")

        if structure['docx_files']:
            parsed.docx_path = str(Path(extract_dir) / structure['docx_files'][0])
            self.logger.success(f"✓ 找到DOCX: {structure['docx_files'][0]}")

        if structure['latex_files']:
            latex_path = Path(extract_dir) / structure['latex_files'][0]
            with open(latex_path, 'r', encoding='utf-8') as f:
                parsed.latex_content = f.read()
            self.logger.success(f"✓ 读取LaTeX: {structure['latex_files'][0]}")

        self.logger.success("解析完成！")

        return parsed

    def _extract_metadata(self, parsed: ParsedContent):
        """
        从JSON中提取元数据

        Args:
            parsed: ParsedContent对象
        """
        if not parsed.json_content:
            return

        json_data = parsed.json_content

        # 尝试提取总页数
        if isinstance(json_data, list):
            parsed.total_pages = len(json_data)
        elif isinstance(json_data, dict):
            if 'pages' in json_data:
                parsed.total_pages = len(json_data['pages'])
            elif 'page_count' in json_data:
                parsed.total_pages = json_data['page_count']

        # 检测是否包含表格和公式
        json_str = json.dumps(json_data)
        parsed.has_tables = 'table' in json_str.lower()
        parsed.has_formulas = 'formula' in json_str.lower() or 'equation' in json_str.lower()

    def generate_analysis_report(self, parsed: ParsedContent) -> str:
        """
        生成详细的分析报告

        Args:
            parsed: ParsedContent对象

        Returns:
            Markdown格式的分析报告
        """
        report = []
        report.append(f"# MinerU解析结果分析报告")
        report.append(f"\n## 基本信息")
        report.append(f"- **原始文件**: {parsed.source_file}")
        report.append(f"- **Zip路径**: {parsed.zip_path}")

        if parsed.total_pages:
            report.append(f"- **总页数**: {parsed.total_pages}")

        report.append(f"\n## 内容概览")

        # Markdown
        if parsed.markdown_content:
            lines = parsed.markdown_content.split('\n')
            chars = len(parsed.markdown_content)
            report.append(f"- **Markdown**: ✓ ({len(lines)} 行, {chars} 字符)")
        else:
            report.append(f"- **Markdown**: ✗")

        # JSON
        if parsed.json_content:
            report.append(f"- **JSON**: ✓")
            if isinstance(parsed.json_content, dict):
                report.append(f"  - 顶级字段: {list(parsed.json_content.keys())}")
            elif isinstance(parsed.json_content, list):
                report.append(f"  - 数组长度: {len(parsed.json_content)}")
        else:
            report.append(f"- **JSON**: ✗")

        # 图片
        if parsed.has_images:
            report.append(f"- **图片**: ✓ ({len(parsed.images)} 张)")
        else:
            report.append(f"- **图片**: ✗")

        # 可选格式
        report.append(f"\n## 可选格式")
        report.append(f"- **HTML**: {'✓' if parsed.html_content else '✗'}")
        report.append(f"- **DOCX**: {'✓' if parsed.docx_path else '✗'}")
        report.append(f"- **LaTeX**: {'✓' if parsed.latex_content else '✗'}")

        # 特性检测
        report.append(f"\n## 特性检测")
        report.append(f"- **包含表格**: {'✓' if parsed.has_tables else '✗'}")
        report.append(f"- **包含公式**: {'✓' if parsed.has_formulas else '✗'}")

        # Markdown预览
        if parsed.markdown_content:
            report.append(f"\n## Markdown预览（前500字符）")
            report.append("```markdown")
            report.append(parsed.markdown_content[:500])
            if len(parsed.markdown_content) > 500:
                report.append("...")
            report.append("```")

        # JSON结构预览
        if parsed.json_content:
            report.append(f"\n## JSON结构预览")
            report.append("```json")
            json_str = json.dumps(parsed.json_content, indent=2, ensure_ascii=False)
            report.append(json_str[:1000])
            if len(json_str) > 1000:
                report.append("...")
            report.append("```")

        return "\n".join(report)

    def save_analysis_report(self, parsed: ParsedContent, output_path: Optional[str] = None):
        """
        保存分析报告到文件

        Args:
            parsed: ParsedContent对象
            output_path: 输出文件路径（可选）
        """
        if not output_path:
            output_path = self.output_dir / f"{parsed.source_file}_analysis.md"

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        report = self.generate_analysis_report(parsed)

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(report)

        self.logger.success(f"分析报告已保存: {output_path}")


if __name__ == "__main__":
    # 简单测试
    print("MinerU Parser 模块")
    print("使用示例:")
    print("""
    from mineru_parser import MinerUParser

    # 初始化解析器
    parser = MinerUParser(output_dir="./output")

    # 解析zip结果
    parsed = parser.parse_zip_result(
        zip_path="./results/example_result.zip",
        source_file_name="example.pdf"
    )

    # 生成分析报告
    parser.save_analysis_report(parsed)

    # 访问解析后的内容
    print(f"Markdown长度: {len(parsed.markdown_content)}")
    print(f"图片数量: {len(parsed.images)}")
    print(f"JSON结构: {parsed.json_content.keys()}")
    """)
