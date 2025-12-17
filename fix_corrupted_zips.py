"""
修复损坏的ZIP文件工具
用于检测并处理MinerU生成的损坏ZIP文件
"""

import os
import zipfile
import json
from pathlib import Path
from typing import List, Tuple
from logger import Logger


class ZipValidator:
    """ZIP文件验证器"""

    def __init__(self):
        self.logger = Logger()

    def validate_zip_file(self, zip_path: str) -> Tuple[bool, str]:
        """
        验证单个ZIP文件

        Args:
            zip_path: ZIP文件路径

        Returns:
            (是否有效, 错误信息)
        """
        if not os.path.exists(zip_path):
            return False, "文件不存在"

        # 检查文件大小
        file_size = Path(zip_path).stat().st_size
        if file_size == 0:
            return False, "文件为空(0 bytes)"

        # 检查是否为有效的ZIP文件
        if not zipfile.is_zipfile(zip_path):
            return False, f"不是有效的ZIP文件 (大小: {file_size} bytes)"

        # 尝试打开并读取文件列表
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                file_list = zf.namelist()
                if len(file_list) == 0:
                    return False, "ZIP文件为空，不包含任何文件"

                # 检查是否包含必要的文件
                has_md = any(f.endswith('.md') for f in file_list)
                has_json = any(f.endswith('.json') for f in file_list)

                if not has_md and not has_json:
                    return False, "ZIP文件缺少必要的.md或.json文件"

                return True, f"有效 (包含{len(file_list)}个文件)"

        except zipfile.BadZipFile as e:
            return False, f"ZIP文件损坏: {str(e)}"
        except Exception as e:
            return False, f"验证失败: {type(e).__name__} - {str(e)}"

    def validate_json_in_zip(self, zip_path: str) -> Tuple[bool, str]:
        """
        验证ZIP文件中的JSON文件格式

        Args:
            zip_path: ZIP文件路径

        Returns:
            (是否有效, 错误信息)
        """
        if not zipfile.is_zipfile(zip_path):
            return False, "不是有效的ZIP文件"

        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                # 查找JSON文件
                json_files = [f for f in zf.namelist() if f.endswith('.json')]

                if not json_files:
                    return True, "没有JSON文件需要验证"

                # 验证每个JSON文件
                for json_file in json_files:
                    content = zf.read(json_file).decode('utf-8')
                    try:
                        json.loads(content)
                    except json.JSONDecodeError as e:
                        return False, f"{json_file}: JSON格式错误 - {str(e)}"

                return True, f"所有JSON文件有效 ({len(json_files)}个)"

        except Exception as e:
            return False, f"JSON验证失败: {str(e)}"

    def scan_directory(self, directory: str, pattern: str = "**/*_result.zip") -> dict:
        """
        扫描目录中的所有ZIP文件并验证

        Args:
            directory: 目录路径
            pattern: 文件匹配模式

        Returns:
            验证结果字典
        """
        dir_path = Path(directory)
        if not dir_path.exists():
            self.logger.error(f"目录不存在: {directory}")
            return {}

        self.logger.info(f"正在扫描目录: {directory}")
        self.logger.info(f"匹配模式: {pattern}")

        zip_files = list(dir_path.glob(pattern))
        self.logger.info(f"找到 {len(zip_files)} 个ZIP文件")

        results = {
            'valid': [],
            'invalid_zip': [],
            'invalid_json': [],
            'total': len(zip_files)
        }

        for i, zip_file in enumerate(zip_files, 1):
            relative_path = zip_file.relative_to(dir_path)
            self.logger.info(f"\n[{i}/{len(zip_files)}] 验证: {relative_path}")

            # 验证ZIP文件
            is_valid_zip, zip_msg = self.validate_zip_file(str(zip_file))

            if not is_valid_zip:
                self.logger.error(f"  ✗ ZIP无效: {zip_msg}")
                results['invalid_zip'].append({
                    'path': str(zip_file),
                    'relative_path': str(relative_path),
                    'error': zip_msg
                })
                continue

            # 验证JSON
            is_valid_json, json_msg = self.validate_json_in_zip(str(zip_file))

            if not is_valid_json:
                self.logger.warning(f"  ⚠ JSON有问题: {json_msg}")
                results['invalid_json'].append({
                    'path': str(zip_file),
                    'relative_path': str(relative_path),
                    'error': json_msg
                })
            else:
                self.logger.success(f"  ✓ 有效: {zip_msg}, {json_msg}")
                results['valid'].append({
                    'path': str(zip_file),
                    'relative_path': str(relative_path)
                })

        return results

    def delete_corrupted_files(self, results: dict, auto_delete: bool = False):
        """
        删除损坏的ZIP文件

        Args:
            results: scan_directory的返回结果
            auto_delete: 是否自动删除（不询问）
        """
        corrupted_files = results['invalid_zip']

        if not corrupted_files:
            self.logger.success("\n没有发现损坏的ZIP文件！")
            return

        self.logger.warning(f"\n发现 {len(corrupted_files)} 个损坏的ZIP文件:")
        for item in corrupted_files:
            self.logger.warning(f"  - {item['relative_path']}")
            self.logger.warning(f"    错误: {item['error']}")

        if not auto_delete:
            response = input(f"\n是否删除这 {len(corrupted_files)} 个损坏的文件? (y/n): ").strip().lower()
            if response != 'y':
                self.logger.info("已取消删除操作")
                return

        deleted_count = 0
        for item in corrupted_files:
            try:
                Path(item['path']).unlink()
                self.logger.success(f"  ✓ 已删除: {item['relative_path']}")
                deleted_count += 1
            except Exception as e:
                self.logger.error(f"  ✗ 删除失败: {item['relative_path']} - {str(e)}")

        self.logger.success(f"\n删除完成！共删除 {deleted_count}/{len(corrupted_files)} 个文件")

    def generate_report(self, results: dict, output_file: str = "zip_validation_report.txt"):
        """
        生成验证报告

        Args:
            results: scan_directory的返回结果
            output_file: 输出文件路径
        """
        report_lines = []
        report_lines.append("=" * 80)
        report_lines.append("ZIP文件验证报告")
        report_lines.append("=" * 80)
        report_lines.append(f"\n总计: {results['total']} 个文件")
        report_lines.append(f"有效: {len(results['valid'])} 个")
        report_lines.append(f"ZIP损坏: {len(results['invalid_zip'])} 个")
        report_lines.append(f"JSON有问题: {len(results['invalid_json'])} 个")

        if results['invalid_zip']:
            report_lines.append("\n" + "=" * 80)
            report_lines.append("损坏的ZIP文件列表:")
            report_lines.append("=" * 80)
            for item in results['invalid_zip']:
                report_lines.append(f"\n文件: {item['relative_path']}")
                report_lines.append(f"错误: {item['error']}")

        if results['invalid_json']:
            report_lines.append("\n" + "=" * 80)
            report_lines.append("JSON有问题的文件列表:")
            report_lines.append("=" * 80)
            for item in results['invalid_json']:
                report_lines.append(f"\n文件: {item['relative_path']}")
                report_lines.append(f"错误: {item['error']}")

        report_content = "\n".join(report_lines)

        # 保存报告
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(report_content)

        self.logger.success(f"\n报告已保存到: {output_file}")

        # 同时打印到控制台
        print("\n" + report_content)


def main():
    """主函数"""
    print("=" * 80)
    print("ZIP文件验证和修复工具")
    print("=" * 80)

    validator = ZipValidator()

    # 扫描output/MinerU目录
    output_dir = "output/MinerU"

    if not Path(output_dir).exists():
        print(f"\n错误: 目录不存在: {output_dir}")
        print("请确认MinerU输出目录路径是否正确")
        input("\n按回车键退出...")
        return

    # 扫描并验证
    results = validator.scan_directory(output_dir)

    # 生成报告
    validator.generate_report(results)

    # 显示摘要
    print("\n" + "=" * 80)
    print("验证摘要")
    print("=" * 80)
    print(f"总计: {results['total']} 个ZIP文件")
    print(f"✓ 有效: {len(results['valid'])} 个")
    print(f"✗ ZIP损坏: {len(results['invalid_zip'])} 个")
    print(f"⚠ JSON有问题: {len(results['invalid_json'])} 个")

    # 询问是否删除损坏的文件
    if results['invalid_zip']:
        print("\n" + "=" * 80)
        validator.delete_corrupted_files(results, auto_delete=False)

        print("\n建议:")
        print("1. 删除损坏的ZIP文件后，重新运行批量处理")
        print("2. 如果问题持续出现，检查网络连接和MinerU服务状态")
        print("3. 考虑减小批次大小或增加下载超时时间")

    input("\n按回车键退出...")


if __name__ == "__main__":
    main()
