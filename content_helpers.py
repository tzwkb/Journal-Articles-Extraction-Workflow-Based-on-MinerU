"""
内容处理辅助模块
提供图片处理、文本合并、图片分组等辅助功能
"""

import shutil
import re
from pathlib import Path
from PIL import Image
from typing import Dict, List


def process_images(
    content_list: list,
    extract_dir: str,
    output_paths: dict,
    logger,
    config: dict
) -> int:
    """
    处理图片：复制图片到HTML输出目录并更新路径

    Args:
        content_list: 内容列表
        extract_dir: MinerU解压目录
        output_paths: 输出路径字典
        logger: 日志记录器
        config: 配置字典

    Returns:
        复制的图片数量
    """
    extract_dir = Path(extract_dir)
    source_images_dir = extract_dir / "images"

    if not source_images_dir.exists():
        logger.warning(f"未找到图片目录: {source_images_dir}")
        return 0

    # 确定目标图片目录（统一放在 output/HTML/images/）
    output_base = Path(config['paths']['output_base'])
    html_folder = config['output']['html_folder']
    html_base_dir = output_base / html_folder

    if output_paths and 'html_original' in output_paths:
        # 使用与 HTML 文件相同的目录层级
        html_dir = Path(output_paths['html_original']).parent
    else:
        html_dir = html_base_dir

    target_images_dir = html_dir / "images"
    target_images_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"正在复制图片: {source_images_dir} -> {target_images_dir}")

    # 复制图片并更新路径（包括普通图片和表格图片）
    copied_count = 0
    for item in content_list:
        # 修复：同时处理 type=='image' 和 type=='table' 的图片
        if item.get('img_path') and item.get('type') in ['image', 'table']:
            img_rel_path = item['img_path']
            source_img = extract_dir / img_rel_path

            if source_img.exists():
                img_filename = Path(img_rel_path).name
                target_img = target_images_dir / img_filename

                # 复制图片
                shutil.copy2(source_img, target_img)

                # 读取图片尺寸并计算宽高比
                try:
                    with Image.open(target_img) as img:
                        width, height = img.size
                        aspect_ratio = width / height if height > 0 else 1.0
                        item['img_width'] = width
                        item['img_height'] = height
                        item['img_aspect_ratio'] = aspect_ratio

                        # 判断图片类型：窄长图(宽高比<0.6)、正常图、扁平图(宽高比>1.8)
                        if aspect_ratio < 0.6:
                            item['img_layout_type'] = 'narrow'  # 窄长图
                        elif aspect_ratio > 1.8:
                            item['img_layout_type'] = 'wide'  # 扁平图
                        else:
                            item['img_layout_type'] = 'normal'  # 正常图
                except Exception as e:
                    logger.warning(f"无法读取图片尺寸 {img_filename}: {str(e)}")
                    item['img_layout_type'] = 'normal'

                # 更新路径：
                # 1. 相对路径用于 HTML（images/xxx.jpg）
                # 2. 绝对路径用于 PDF/DOCX 转换（存储在 img_path_absolute）
                item['img_path'] = f"images/{img_filename}"
                # 修复：Windows路径转换为file://协议格式
                abs_path = target_img.absolute().as_posix()  # 统一使用正斜杠
                item['img_path_absolute'] = abs_path  # 不加file:///前缀，模板中处理
                copied_count += 1
            else:
                logger.warning(f"图片文件不存在: {source_img}")

    if copied_count > 0:
        logger.success(f"已复制 {copied_count} 张图片")
    else:
        logger.warning("未找到任何图片文件")

    return copied_count


def merge_split_texts(items: list) -> list:
    """
    极简合并 - 只处理明确的TEXT分割

    规则1: 连字符断词 (如 "frig-" + "ates")
    规则2: 跨列无标点 (如左列 "...limestone" + 右列 "V pedestal")
    规则3: 同列分割 (如 "...Pound" + "force was...")

    Args:
        items: 单页的内容项列表

    Returns:
        合并后的内容项列表（保留original_items字段）
    """
    merged = []
    i = 0

    while i < len(items):
        current = items[i]

        # 只处理text类型
        if current.get('type') != 'text' or not current.get('text'):
            merged.append(current)
            i += 1
            continue

        # 检查是否与下一项合并
        should_merge = False
        if i + 1 < len(items):
            next_item = items[i + 1]

            # 下一项也必须是text
            if next_item.get('type') == 'text' and next_item.get('text'):
                # 同一页
                if current.get('page_idx') == next_item.get('page_idx'):
                    text1 = current['text'].strip()
                    bbox1 = current.get('bbox', [0, 0, 0, 0])
                    bbox2 = next_item.get('bbox', [0, 0, 0, 0])

                    # 规则1: 连字符结尾 (100%确定是断词)
                    if text1.endswith('-'):
                        should_merge = True
                    # 规则2: 跨列 + 无句末标点
                    elif bbox2[0] - bbox1[2] > 80:  # x间距 > 80像素（跨列）
                        if text1 and text1[-1] not in '.!?。！？':
                            should_merge = True
                    # 规则3: 同列内分割 - text1无标点结尾 + text2小写开头
                    else:
                        text2 = next_item['text'].strip()
                        # text1不以标点结尾 且 text2以小写字母开头
                        if (text1 and text1[-1] not in '.!?。！？,;:' and
                            text2 and text2[0].islower()):
                            should_merge = True

        if should_merge:
            # 合并两个TEXT块
            merged_item = current.copy()
            merged_item['text'] = current['text'].rstrip() + ' ' + next_item['text'].lstrip()
            merged_item['original_items'] = [current, next_item]
            merged_item['merged'] = True
            merged.append(merged_item)
            i += 2  # 跳过下一项
        else:
            merged.append(current)
            i += 1

    return merged


def group_narrow_images(pages: dict, logger) -> dict:
    """
    对连续的窄长图片进行分组，使其并排显示

    Args:
        pages: {page_idx: [items]} 页面内容字典
        logger: 日志记录器

    Returns:
        处理后的pages字典
    """
    total_groups = 0
    total_narrow_images = 0

    for page_idx, items in pages.items():
        grouped_items = []
        i = 0

        while i < len(items):
            item = items[i]

            # 如果是窄长图片，尝试找到连续的窄长图片
            if item.get('type') == 'image' and item.get('img_layout_type') == 'narrow':
                # 收集连续的窄长图片
                narrow_group = [item]
                j = i + 1

                # 最多合并4张窄长图片到一行
                while j < len(items) and len(narrow_group) < 4:
                    next_item = items[j]
                    # 检查下一项是否也是窄长图片
                    if next_item.get('type') == 'image' and next_item.get('img_layout_type') == 'narrow':
                        narrow_group.append(next_item)
                        j += 1
                    else:
                        break

                # 如果有2张及以上窄长图片，创建图片组
                if len(narrow_group) >= 2:
                    grouped_items.append({
                        'type': 'image_group',
                        'layout_type': 'narrow_row',  # 窄长图片横排
                        'images': narrow_group,
                        'page_idx': item.get('page_idx')
                    })
                    total_groups += 1
                    total_narrow_images += len(narrow_group)
                    i = j  # 跳过已处理的图片
                else:
                    # 只有1张窄长图片，正常处理
                    grouped_items.append(item)
                    i += 1
            else:
                # 非窄长图片，正常添加
                grouped_items.append(item)
                i += 1

        # 更新页面内容
        pages[page_idx] = grouped_items

    if total_groups > 0:
        logger.info(f"📐 图片智能排版: 创建了 {total_groups} 个图片组，包含 {total_narrow_images} 张窄长图片")

    return pages


def get_chapter_context(page_idx: int, outline: dict) -> dict:
    """
    获取页面对应的章节上下文

    Args:
        page_idx: 页码
        outline: 文档大纲

    Returns:
        包含章节标题、摘要、关键词的字典
    """
    # 基础上下文：包含文档级别的期刊概述
    context = {
        'journal_overview': outline.get('journal_overview', '')
    }

    # 确保 page_idx 是整数
    try:
        page_num = int(page_idx)
    except (ValueError, TypeError):
        return context

    # 查找对应的章节信息
    for chapter in outline.get('structure', []):
        pages = chapter.get('pages', [])
        if len(pages) >= 2:
            try:
                # 确保 start 和 end 也是整数
                start = int(pages[0])
                end = int(pages[1])
                if start <= page_num <= end:
                    context.update({
                        'chapter_title': chapter.get('title', ''),
                        'chapter_summary': chapter.get('summary', ''),
                        'keywords': chapter.get('keywords', [])
                    })
                    return context
            except (ValueError, TypeError, IndexError):
                continue

    return context
