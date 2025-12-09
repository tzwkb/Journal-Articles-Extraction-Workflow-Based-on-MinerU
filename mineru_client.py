"""
MinerU API Client
提供MinerU API的完整封装，支持批量上传、任务轮询、结果下载
"""

import requests
import time
import os
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

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

        # 所有重试都失败
        raise Exception(f"请求失败，已重试{self.max_retries}次: {str(last_error)}")

    def batch_upload_files(
        self,
        file_tasks: List[FileTask],
        callback: Optional[str] = None,
        seed: Optional[str] = None,
        enable_formula: bool = True,
        enable_table: bool = True,
        language: str = "ch"
    ) -> Tuple[str, List[str]]:
        """
        批量上传文件并创建解析任务

        Args:
            file_tasks: 文件任务列表
            callback: 回调URL（可选）
            seed: 回调签名种子（使用callback时必须提供）
            enable_formula: 是否启用公式识别
            enable_table: 是否启用表格识别
            language: 文档语言

        Returns:
            (batch_id, file_urls): 批次ID和上传URL列表

        Raises:
            Exception: API调用失败时抛出异常
        """
        self.logger.info(f"准备批量上传 {len(file_tasks)} 个文件...")

        # 1. 申请上传链接
        url = f"{self.base_url}/file-urls/batch"

        files_data = []
        for task in file_tasks:
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
            raise Exception(f"申请上传链接失败: HTTP {response.status_code}, {response.text}")

        result = response.json()

        if result.get("code") != 0:
            error_msg = result.get("msg", "未知错误")
            raise Exception(f"申请上传链接失败: {error_msg}")

        batch_id = result["data"]["batch_id"]
        file_urls = result["data"]["file_urls"]

        self.logger.success(f"成功申请上传链接，batch_id: {batch_id}")

        # 2. 上传文件到对应的URL
        self.logger.info("开始上传文件...")
        for i, (task, upload_url) in enumerate(zip(file_tasks, file_urls), 1):
            self.logger.info(f"[{i}/{len(file_tasks)}] 上传: {task.file_name}")

            if not os.path.exists(task.file_path):
                raise FileNotFoundError(f"文件不存在: {task.file_path}")

            with open(task.file_path, 'rb') as f:
                # 上传文件时不需要设置Content-Type，但需要SSL验证参数
                upload_response = self._request_with_retry('PUT', upload_url, data=f)

                if upload_response.status_code != 200:
                    raise Exception(
                        f"上传文件失败: {task.file_name}, "
                        f"HTTP {upload_response.status_code}"
                    )

            self.logger.success(f"✓ {task.file_name} 上传成功")

        self.logger.success(f"所有文件上传完成！系统将自动开始解析...")

        return batch_id, file_urls

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
            raise Exception(f"查询任务状态失败: HTTP {response.status_code}, {response.text}")

        result = response.json()

        if result.get("code") != 0:
            error_msg = result.get("msg", "未知错误")
            raise Exception(f"查询任务状态失败: {error_msg}")

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
            raise Exception(f"下载失败: HTTP {response.status_code}")

        # 流式写入文件
        total_size = int(response.headers.get('content-length', 0))
        downloaded = 0

        with open(save_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)

                    # 打印进度（每10%）
                    if total_size > 0:
                        progress = int(downloaded / total_size * 100)
                        if progress % 10 == 0:
                            self.logger.info(f"  下载进度: {progress}%")

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
