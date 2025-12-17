"""
MinerU API Client
提供MinerU API的完整封装，支持批量上传、任务轮询、结果下载
"""

import requests
import time
import os
import hashlib
import fitz  # PyMuPDF
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
from tqdm import tqdm

# 导入现有工具
try:
    from logger import Logger
except ImportError:
    # 如果logger不存在，使用简单的打印
    class Logger:
        @staticmethod
        def info(msg): print(f"[INFO] {msg}")
        @staticmethod
        def warning(msg): print(f"[WARNING] {msg}")
        @staticmethod
        def error(msg): print(f"[ERROR] {msg}")
        @staticmethod
        def success(msg): print(f"[SUCCESS] {msg}")


class TaskState(Enum):
    """任务状态枚举"""
    WAITING_FILE = "waiting-file"  # 等待文件上传
    PENDING = "pending"  # 排队中
    RUNNING = "running"  # 解析中
    CONVERTING = "converting"  # 格式转换中
    DONE = "done"  # 完成
    FAILED = "failed"  # 失败


@dataclass
class FileTask:
    """单个文件任务"""
    file_name: str
    file_path: str
    data_id: Optional[str] = None
    page_ranges: Optional[str] = None
    is_ocr: bool = False
    # 分割信息
    is_split_part: bool = False  # 是否是分割的部分
    original_file: Optional[str] = None  # 原始文件路径（如果是分割部分）
    part_index: int = 0  # 分割序号（0表示不是分割）
    page_offset: int = 0  # 页码偏移量


@dataclass
class TaskResult:
    """任务结果"""
    file_name: str
    state: TaskState
    full_zip_url: Optional[str] = None
    err_msg: Optional[str] = None
    data_id: Optional[str] = None
    extracted_pages: Optional[int] = None
    total_pages: Optional[int] = None
    start_time: Optional[str] = None


class MinerUClient:
    """MinerU API客户端"""

    def __init__(
        self,
        api_token: str,
        base_url: str = "https://mineru.net/api/v4",
        model_version: str = "vlm",
        extra_formats: Optional[List[str]] = None,
        verify_ssl: bool = True,
        max_retries: int = 3
    ):
        """
        初始化MinerU客户端

        Args:
            api_token: MinerU API Token
            base_url: API基础URL
            model_version: 模型版本 (pipeline/vlm)
            extra_formats: 额外输出格式 (docx/html/latex)
            verify_ssl: 是否验证SSL证书
            max_retries: 请求失败时的最大重试次数
        """
        self.api_token = api_token
        self.base_url = base_url
        self.model_version = model_version
        self.extra_formats = extra_formats or []
        self.verify_ssl = verify_ssl
        self.max_retries = max_retries
        self.logger = Logger()

        # 请求头
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_token}",
            "Accept": "*/*"
        }

        # 创建session以复用连接
        self.session = requests.Session()
        if not verify_ssl:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            self.logger.warning("SSL验证已禁用（仅用于测试）")

    def _request_with_retry(self, method: str, url: str, **kwargs) -> requests.Response:
        """
        带重试机制的HTTP请求

        Args:
            method: HTTP方法 (GET/POST/PUT)
            url: 请求URL
            **kwargs: requests参数

        Returns:
            Response对象

        Raises:
            Exception: 所有重试都失败时抛出异常
        """
        kwargs['verify'] = self.verify_ssl

        last_error = None
        for attempt in range(self.max_retries):
            try:
                if method.upper() == 'GET':
                    response = self.session.get(url, **kwargs)
                elif method.upper() == 'POST':
                    response = self.session.post(url, **kwargs)
                elif method.upper() == 'PUT':
                    response = self.session.put(url, **kwargs)
                else:
                    raise ValueError(f"不支持的HTTP方法: {method}")

                return response

            except requests.exceptions.SSLError as e:
                last_error = e
                self.logger.warning(f"SSL错误 (尝试 {attempt + 1}/{self.max_retries}): {str(e)}")
                if attempt < self.max_retries - 1:
                    wait_time = 2 ** attempt  # 指数退避
                    self.logger.info(f"等待 {wait_time} 秒后重试...")
                    time.sleep(wait_time)

            except requests.exceptions.ConnectionError as e:
                last_error = e
                self.logger.warning(f"连接错误 (尝试 {attempt + 1}/{self.max_retries}): {str(e)}")
                if attempt < self.max_retries - 1:
                    wait_time = 2 ** attempt
                    self.logger.info(f"等待 {wait_time} 秒后重试...")
                    time.sleep(wait_time)

            except Exception as e:
                last_error = e
                self.logger.error(f"请求失败: {str(e)}")
                raise

        # All retries failed
        raise Exception(f"Request failed after {self.max_retries} retries: {str(last_error)}")

    def _split_large_pdf(self, file_path: str, max_size_mb: int = 200) -> List[Tuple[str, int, int]]:
        """
        检查 PDF 文件大小，如果超过限制则分割为多个部分

        Args:
            file_path: PDF 文件路径
            max_size_mb: 最大文件大小（MB）

        Returns:
            [(part_path, start_page, end_page), ...] 列表
            如果不需要分割，返回 [(original_path, 0, total_pages)]
        """
        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)

        if file_size_mb <= max_size_mb:
            return [(file_path, 0, -1)]  # -1 表示所有页

        self.logger.warning(f"⚠ 文件大小 {file_size_mb:.2f} MB 超过 {max_size_mb} MB 限制")
        self.logger.info(f"→ 正在智能分割 PDF...")

        try:
            # 打开 PDF
            doc = fitz.open(file_path)
            total_pages = len(doc)

            # 估算每部分的页数（按比例）
            pages_per_part = int(total_pages * (max_size_mb / file_size_mb) * 0.9)  # 留10%余量
            pages_per_part = max(1, pages_per_part)  # 至少1页

            self.logger.info(f"总页数: {total_pages}, 每部分约 {pages_per_part} 页")

            parts = []
            part_num = 1
            start_page = 0

            # 创建临时目录
            temp_dir = Path(file_path).parent / "temp_splits"
            temp_dir.mkdir(parents=True, exist_ok=True)

            while start_page < total_pages:
                end_page = min(start_page + pages_per_part, total_pages)

                # 创建分割的 PDF
                new_doc = fitz.open()
                for page_idx in range(start_page, end_page):
                    new_doc.insert_pdf(doc, from_page=page_idx, to_page=page_idx)

                # 保存（使用临时文件名）
                file_stem = Path(file_path).stem
                part_path = temp_dir / f"{file_stem}_part{part_num}.pdf"
                new_doc.save(str(part_path))
                new_doc.close()

                part_size_mb = os.path.getsize(part_path) / (1024 * 1024)
                self.logger.info(
                    f"  Part {part_num}: 页 {start_page+1}-{end_page} "
                    f"({end_page - start_page} 页, {part_size_mb:.2f} MB)"
                )

                parts.append((str(part_path), start_page, end_page))

                start_page = end_page
                part_num += 1

            doc.close()
            self.logger.success(f"✓ PDF 已分割为 {len(parts)} 部分")
            return parts

        except Exception as e:
            self.logger.error(f"✗ PDF 分割失败: {e}")
            self.logger.warning(f"→ 将尝试使用原始文件上传（可能被 MinerU 拒绝）")
            return [(file_path, 0, -1)]

    def _merge_mineru_results(self, zip_paths: List[str], output_path: str, page_offsets: List[int]):
        """
        合并多个 MinerU 结果 ZIP 为一个（完整复刻 MinerU 返回结构）

        Args:
            zip_paths: ZIP 文件路径列表
            output_path: 合并后的输出路径
            page_offsets: 每个部分的页码偏移量列表
        """
        import zipfile
        import json

        self.logger.info(f"正在合并 {len(zip_paths)} 个 MinerU 结果...")

        try:
            # 准备合并数据
            all_content = []
            all_images = {}
            image_counter = 0
            all_markdown = []
            all_model = []
            merged_layout = {"pdf_info": {}, "_backend": None, "_version_name": None}
            origin_pdfs = []

            for idx, (zip_path, page_offset) in enumerate(zip(zip_paths, page_offsets)):
                self.logger.info(f"  处理 Part {idx + 1}/{len(zip_paths)} (页码偏移: {page_offset})...")

                with zipfile.ZipFile(zip_path, 'r') as zf:
                    # 列出所有文件
                    all_files = zf.namelist()

                    # 1. 处理 content_list.json
                    content_json_file = None
                    for file in all_files:
                        if file.endswith('.json') and ('content' in file.lower() or 'auto' in file.lower()):
                            content_json_file = file
                            break

                    if not content_json_file:
                        json_files = [f for f in all_files if f.endswith('.json') and 'layout' not in file and 'model' not in file]
                        if json_files:
                            content_json_file = json_files[0]
                            self.logger.warning(f"⚠ 未找到标准的content_list.json，使用: {content_json_file}")

                    if not content_json_file:
                        self.logger.warning(f"⚠ Part {idx + 1} 缺少 JSON 文件，跳过")
                        continue

                    content_json = zf.read(content_json_file).decode('utf-8')
                    content = json.loads(content_json)
                    self.logger.info(f"  - content_list.json: {len(content)} 个内容块")

                    # 调整 page_idx
                    for item in content:
                        if 'page_idx' in item:
                            item['page_idx'] += page_offset

                        # 重命名图片路径
                        if item.get('type') == 'image' and item.get('img_path'):
                            old_img_path = item['img_path']
                            img_ext = Path(old_img_path).suffix
                            new_img_name = f"image_{image_counter:04d}{img_ext}"
                            item['img_path'] = f"images/{new_img_name}"

                            # 提取图片
                            try:
                                img_data = zf.read(old_img_path)
                                all_images[f"images/{new_img_name}"] = img_data
                                image_counter += 1
                            except KeyError:
                                self.logger.warning(f"⚠ 图片不存在: {old_img_path}")

                    all_content.extend(content)

                    # 2. 处理 full.md
                    if 'full.md' in all_files:
                        md_content = zf.read('full.md').decode('utf-8')
                        all_markdown.append(md_content)
                        self.logger.info(f"  - full.md: {len(md_content)} 字符")

                    # 3. 处理 layout.json
                    if 'layout.json' in all_files:
                        layout_content = json.loads(zf.read('layout.json').decode('utf-8'))

                        # 合并 pdf_info（调整页码键）
                        if 'pdf_info' in layout_content:
                            pdf_info = layout_content['pdf_info']
                            # 检查 pdf_info 是字典还是列表
                            if isinstance(pdf_info, dict):
                                for page_idx_str, page_data in pdf_info.items():
                                    new_page_idx = int(page_idx_str) + page_offset
                                    merged_layout['pdf_info'][str(new_page_idx)] = page_data
                            elif isinstance(pdf_info, list):
                                # 如果是列表，尝试将其转换为字典格式
                                self.logger.warning(f"⚠ Part {idx + 1} 的 pdf_info 是列表格式，正在转换...")
                                for page_idx, page_data in enumerate(pdf_info):
                                    new_page_idx = page_idx + page_offset
                                    merged_layout['pdf_info'][str(new_page_idx)] = page_data

                        # 保留第一个部分的 backend 和 version
                        if idx == 0:
                            merged_layout['_backend'] = layout_content.get('_backend')
                            merged_layout['_version_name'] = layout_content.get('_version_name')

                        pdf_info_len = len(layout_content.get('pdf_info', {})) if isinstance(layout_content.get('pdf_info', {}), dict) else len(layout_content.get('pdf_info', []))
                        self.logger.info(f"  - layout.json: {pdf_info_len} 页")

                    # 4. 处理 model.json
                    model_files = [f for f in all_files if 'model.json' in f]
                    if model_files:
                        model_content = json.loads(zf.read(model_files[0]).decode('utf-8'))
                        if isinstance(model_content, list):
                            all_model.extend(model_content)
                            self.logger.info(f"  - model.json: {len(model_content)} 个元素")

                    # 5. 处理 origin.pdf
                    origin_files = [f for f in all_files if 'origin.pdf' in f]
                    if origin_files:
                        origin_pdf_data = zf.read(origin_files[0])
                        origin_pdfs.append(origin_pdf_data)
                        self.logger.info(f"  - origin.pdf: {len(origin_pdf_data) / 1024:.1f} KB")

            # 创建合并的 ZIP
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)

            # 生成唯一UUID（用于文件命名）
            import uuid
            merged_uuid = str(uuid.uuid4())

            with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                # 写入 content_list.json
                zf.writestr(
                    f'{merged_uuid}_content_list.json',
                    json.dumps(all_content, ensure_ascii=False, indent=2)
                )

                # 写入 full.md
                if all_markdown:
                    merged_md = '\n\n---\n\n'.join(all_markdown)
                    zf.writestr('full.md', merged_md)

                # 写入 layout.json
                if merged_layout['pdf_info']:
                    zf.writestr(
                        'layout.json',
                        json.dumps(merged_layout, ensure_ascii=False, indent=2)
                    )

                # 写入 model.json
                if all_model:
                    zf.writestr(
                        f'{merged_uuid}_model.json',
                        json.dumps(all_model, ensure_ascii=False, indent=2)
                    )

                # 写入 origin.pdf（合并多个PDF）
                if origin_pdfs:
                    merged_pdf = self._merge_pdfs(origin_pdfs)
                    zf.writestr(f'{merged_uuid}_origin.pdf', merged_pdf)

                # 写入所有图片
                for img_path, img_data in all_images.items():
                    zf.writestr(img_path, img_data)

            self.logger.success(f"✓ 已合并为: {output_path}")
            self.logger.info(f"  总内容块: {len(all_content)}, 图片: {len(all_images)}")
            self.logger.info(f"  Full.md: {len(all_markdown)} 部分, Layout: {len(merged_layout['pdf_info'])} 页")
            self.logger.info(f"  Model: {len(all_model)} 元素, Origin PDF: {len(origin_pdfs)} 部分")

        except Exception as e:
            self.logger.error(f"✗ 合并失败: {str(e)}")
            raise

    def _merge_pdfs(self, pdf_bytes_list: List[bytes]) -> bytes:
        """
        合并多个 PDF 字节流为一个

        Args:
            pdf_bytes_list: PDF 字节流列表

        Returns:
            合并后的 PDF 字节流
        """
        import io

        # 创建新的 PDF
        merged_doc = fitz.open()

        for pdf_bytes in pdf_bytes_list:
            # 从字节流打开 PDF
            pdf_stream = io.BytesIO(pdf_bytes)
            doc = fitz.open(stream=pdf_stream, filetype="pdf")

            # 插入所有页
            merged_doc.insert_pdf(doc)
            doc.close()

        # 保存到字节流
        output_stream = io.BytesIO()
        merged_doc.save(output_stream)
        merged_doc.close()

        return output_stream.getvalue()

    def batch_upload_files(
        self,
        file_tasks: List[FileTask],
        callback: Optional[str] = None,
        seed: Optional[str] = None,
        enable_formula: bool = True,
        enable_table: bool = True,
        language: str = "ch"
    ) -> Tuple[str, List[str], Dict]:
        """
        批量上传文件并创建解析任务（自动处理大文件分割）

        Args:
            file_tasks: 文件任务列表
            callback: 回调URL（可选）
            seed: 回调签名种子（使用callback时必须提供）
            enable_formula: 是否启用公式识别
            enable_table: 是否启用表格识别
            language: 文档语言

        Returns:
            (batch_id, file_urls, split_info):
                - batch_id: 批次ID
                - file_urls: 上传URL列表
                - split_info: 分割信息 {original_file_path: [(part_idx, start_page, end_page), ...]}

        Raises:
            Exception: API调用失败时抛出异常
        """
        self.logger.info(f"准备批量上传 {len(file_tasks)} 个文件...")

        # 1. 预处理：检测并分割大文件，同时过滤不存在的文件
        expanded_tasks = []
        split_info = {}  # {original_file: [(task_idx, start_page, end_page), ...]}
        temp_files_to_cleanup = []  # 临时文件列表（用于最后清理）
        skipped_files = []  # 跳过的文件列表

        for task in file_tasks:
            # 检查文件是否存在
            if not os.path.exists(task.file_path):
                self.logger.warning(f"⚠ 文件不存在，跳过: {task.file_path}")
                skipped_files.append(task.file_name)
                continue

            # 检查文件大小
            parts = self._split_large_pdf(task.file_path, max_size_mb=200)

            if len(parts) == 1 and parts[0][2] == -1:
                # 不需要分割，直接添加
                expanded_tasks.append(task)
            else:
                # 需要分割，创建多个任务
                self.logger.info(f"文件 {task.file_name} 已分割为 {len(parts)} 部分")
                split_info[task.file_path] = []

                for part_idx, (part_path, start_page, end_page) in enumerate(parts, 1):
                    # 创建分割任务
                    part_task = FileTask(
                        file_name=f"{Path(task.file_name).stem}_part{part_idx}.pdf",
                        file_path=part_path,
                        data_id=f"{task.data_id or hashlib.md5(task.file_path.encode()).hexdigest()[:16]}_p{part_idx}",
                        page_ranges=task.page_ranges,
                        is_ocr=task.is_ocr,
                        is_split_part=True,
                        original_file=task.file_path,
                        part_index=part_idx,
                        page_offset=start_page
                    )

                    task_idx = len(expanded_tasks)
                    expanded_tasks.append(part_task)
                    split_info[task.file_path].append((task_idx, start_page, end_page))

                    # 记录临时文件
                    if part_path != task.file_path:
                        temp_files_to_cleanup.append(part_path)

        self.logger.info(f"展开后共 {len(expanded_tasks)} 个上传任务")

        # 如果跳过了一些文件，显示警告
        if skipped_files:
            self.logger.warning(f"已跳过 {len(skipped_files)} 个不存在的文件")

        # 如果没有任何有效文件，返回空结果
        if len(expanded_tasks) == 0:
            self.logger.warning("没有有效的文件可上传")
            return None, [], split_info

        # 2. 申请上传链接
        url = f"{self.base_url}/file-urls/batch"

        files_data = []
        for task in expanded_tasks:
            file_info = {"name": task.file_name}
            if task.data_id:
                file_info["data_id"] = task.data_id
            if task.page_ranges:
                file_info["page_ranges"] = task.page_ranges
            if task.is_ocr:
                file_info["is_ocr"] = task.is_ocr
            files_data.append(file_info)

        request_data = {
            "files": files_data,
            "model_version": self.model_version,
            "enable_formula": enable_formula,
            "enable_table": enable_table,
            "language": language
        }

        if self.extra_formats:
            request_data["extra_formats"] = self.extra_formats

        if callback:
            request_data["callback"] = callback
            if not seed:
                raise ValueError("使用callback时必须提供seed参数")
            request_data["seed"] = seed

        # 发送请求申请上传链接
        self.logger.info("正在申请文件上传链接...")
        response = self._request_with_retry('POST', url, headers=self.headers, json=request_data)

        if response.status_code != 200:
            raise Exception(f"Upload link request failed: HTTP {response.status_code}, {response.text}")

        result = response.json()

        if result.get("code") != 0:
            error_msg = result.get("msg", "Unknown error")
            raise Exception(f"Upload link request failed: {error_msg}")

        batch_id = result["data"]["batch_id"]
        file_urls = result["data"]["file_urls"]

        self.logger.success(f"成功申请上传链接，batch_id: {batch_id}")

        # 3. 上传文件到对应的URL
        self.logger.info("开始上传文件...")
        for i, (task, upload_url) in enumerate(zip(expanded_tasks, file_urls), 1):
            self.logger.info(f"[{i}/{len(expanded_tasks)}] 上传: {task.file_name}")

            # 文件存在性已在前面检查，这里直接上传
            with open(task.file_path, 'rb') as f:
                # 上传文件时不需要设置Content-Type，但需要SSL验证参数
                upload_response = self._request_with_retry('PUT', upload_url, data=f)

                if upload_response.status_code != 200:
                    raise Exception(
                        f"上传文件失败: {task.file_name}, "
                        f"HTTP {upload_response.status_code}"
                    )

            self.logger.success(f"✓ {task.file_name} 上传成功")

        # 4. 清理临时分割文件
        for temp_file in temp_files_to_cleanup:
            try:
                Path(temp_file).unlink()
            except:
                pass

        # 清理临时目录
        for task in file_tasks:
            temp_dir = Path(task.file_path).parent / "temp_splits"
            if temp_dir.exists():
                try:
                    import shutil
                    shutil.rmtree(temp_dir)
                except:
                    pass

        self.logger.success(f"所有文件上传完成！系统将自动开始解析...")

        return batch_id, file_urls, split_info

    def get_batch_status(self, batch_id: str) -> List[TaskResult]:
        """
        查询批量任务状态

        Args:
            batch_id: 批次ID

        Returns:
            任务结果列表

        Raises:
            Exception: API调用失败时抛出异常
        """
        url = f"{self.base_url}/extract-results/batch/{batch_id}"

        response = self._request_with_retry('GET', url, headers=self.headers)

        if response.status_code != 200:
            raise Exception(f"Task status query failed: HTTP {response.status_code}, {response.text}")

        result = response.json()

        if result.get("code") != 0:
            error_msg = result.get("msg", "Unknown error")
            raise Exception(f"Task status query failed: {error_msg}")

        # 解析任务结果
        extract_results = result["data"]["extract_result"]
        task_results = []

        for item in extract_results:
            state = TaskState(item["state"])

            task_result = TaskResult(
                file_name=item["file_name"],
                state=state,
                full_zip_url=item.get("full_zip_url"),
                err_msg=item.get("err_msg"),
                data_id=item.get("data_id")
            )

            # 如果正在运行，添加进度信息
            if "extract_progress" in item:
                progress = item["extract_progress"]
                task_result.extracted_pages = progress.get("extracted_pages")
                task_result.total_pages = progress.get("total_pages")
                task_result.start_time = progress.get("start_time")

            task_results.append(task_result)

        return task_results

    def wait_for_completion(
        self,
        batch_id: str,
        poll_interval: int = 10,
        max_wait_time: int = 3600,
        progress_callback=None
    ) -> List[TaskResult]:
        """
        轮询等待批量任务完成

        Args:
            batch_id: 批次ID
            poll_interval: 轮询间隔（秒）
            max_wait_time: 最大等待时间（秒）
            progress_callback: 进度回调函数 callback(task_results)

        Returns:
            最终任务结果列表

        Raises:
            TimeoutError: 超时时抛出异常
        """
        self.logger.info(f"开始轮询任务状态 (batch_id: {batch_id})...")

        start_time = time.time()

        while True:
            elapsed = time.time() - start_time
            if elapsed > max_wait_time:
                raise TimeoutError(f"任务超时 ({max_wait_time}秒)")

            # 查询状态
            task_results = self.get_batch_status(batch_id)

            # 统计状态
            status_count = {}
            for task in task_results:
                status_count[task.state] = status_count.get(task.state, 0) + 1

            # 打印进度
            status_str = ", ".join([f"{state.value}: {count}" for state, count in status_count.items()])
            self.logger.info(f"[{int(elapsed)}s] {status_str}")

            # 如果有进度回调，调用它
            if progress_callback:
                progress_callback(task_results)

            # 检查是否全部完成或失败
            all_finished = all(
                task.state in [TaskState.DONE, TaskState.FAILED]
                for task in task_results
            )

            if all_finished:
                success_count = sum(1 for task in task_results if task.state == TaskState.DONE)
                failed_count = sum(1 for task in task_results if task.state == TaskState.FAILED)

                self.logger.success(
                    f"所有任务完成！成功: {success_count}, 失败: {failed_count}"
                )

                # 打印失败任务详情
                if failed_count > 0:
                    self.logger.warning("失败任务详情:")
                    for task in task_results:
                        if task.state == TaskState.FAILED:
                            self.logger.error(f"  - {task.file_name}: {task.err_msg}")

                return task_results

            # 等待下一次轮询
            time.sleep(poll_interval)

    def download_result(
        self,
        zip_url: str,
        save_dir: str,
        file_name: Optional[str] = None
    ) -> str:
        """
        下载解析结果zip文件

        Args:
            zip_url: zip文件URL
            save_dir: 保存目录
            file_name: 保存文件名（可选，默认从URL提取）

        Returns:
            保存的文件路径

        Raises:
            Exception: 下载失败时抛出异常
        """
        # 创建保存目录
        Path(save_dir).mkdir(parents=True, exist_ok=True)

        # 确定文件名
        if not file_name:
            file_name = os.path.basename(zip_url.split("?")[0])

        save_path = os.path.join(save_dir, file_name)

        self.logger.info(f"正在下载: {file_name}")

        # 下载文件 - 使用stream=True进行流式下载
        response = self._request_with_retry('GET', zip_url, stream=True)

        if response.status_code != 200:
            raise Exception(f"Download failed: HTTP {response.status_code}")

        # 流式写入文件（使用进度条）
        total_size = int(response.headers.get('content-length', 0))

        with open(save_path, 'wb') as f:
            with tqdm(total=total_size, unit='B', unit_scale=True,
                     desc=f"  下载中", ncols=80, leave=False) as pbar:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        pbar.update(len(chunk))

        file_size_mb = os.path.getsize(save_path) / (1024 * 1024)
        self.logger.success(f"下载完成: {save_path} ({file_size_mb:.2f} MB)")

        return save_path

    def download_all_results(
        self,
        task_results: List[TaskResult],
        save_dir: str
    ) -> Dict[str, str]:
        """
        批量下载所有成功任务的结果

        Args:
            task_results: 任务结果列表
            save_dir: 保存目录

        Returns:
            {file_name: zip_path} 字典
        """
        self.logger.info(f"准备下载解析结果到: {save_dir}")

        downloaded = {}

        for i, task in enumerate(task_results, 1):
            if task.state != TaskState.DONE:
                self.logger.warning(f"[{i}/{len(task_results)}] 跳过: {task.file_name} (状态: {task.state.value})")
                continue

            if not task.full_zip_url:
                self.logger.warning(f"[{i}/{len(task_results)}] 跳过: {task.file_name} (无下载链接)")
                continue

            self.logger.info(f"[{i}/{len(task_results)}] 下载: {task.file_name}")

            # 生成保存文件名（原文件名_result.zip）
            base_name = Path(task.file_name).stem
            zip_name = f"{base_name}_result.zip"

            try:
                zip_path = self.download_result(
                    task.full_zip_url,
                    save_dir,
                    zip_name
                )
                downloaded[task.file_name] = zip_path
            except Exception as e:
                self.logger.error(f"下载失败: {task.file_name}, 错误: {str(e)}")

        self.logger.success(f"批量下载完成！共下载 {len(downloaded)} 个文件")

        return downloaded


if __name__ == "__main__":
    # 简单测试
    print("MinerU Client 模块")
    print("使用示例:")
    print("""
    from mineru_client import MinerUClient, FileTask

    # 初始化客户端
    client = MinerUClient(
        api_token="your_token_here",
        model_version="vlm",
        extra_formats=["html"]
    )

    # 准备文件任务
    file_tasks = [
        FileTask(
            file_name="example.pdf",
            file_path="/path/to/example.pdf",
            data_id="example_001"
        )
    ]

    # 批量上传
    batch_id, _ = client.batch_upload_files(file_tasks)

    # 等待完成
    results = client.wait_for_completion(batch_id)

    # 下载结果
    downloaded = client.download_all_results(results, "./results")
    """)
