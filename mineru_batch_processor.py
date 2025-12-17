"""
MinerU 批处理模块
负责 MinerU 文件上传、状态监控、结果下载和分割文件合并
"""

import os
import time
import queue
import shutil
import hashlib
import threading
from pathlib import Path
from mineru_client import FileTask, TaskState


class MinerUBatchProcessor:
    """MinerU 批量处理器 - 处理上传、监控、下载、合并"""

    def __init__(self, mineru_client, logger, config, path_manager):
        """
        初始化 MinerU 批处理器

        Args:
            mineru_client: MinerU 客户端实例
            logger: 日志记录器
            config: 配置字典
            path_manager: 路径管理器实例
        """
        self.mineru = mineru_client
        self.logger = logger
        self.config = config
        self.path_mgr = path_manager

    def upload_and_monitor(
        self,
        files_to_upload: list,
        translation_queue: queue.Queue,
        stop_event: threading.Event,
        failed_files: list,
        failed_files_lock: threading.Lock
    ) -> None:
        """
        MinerU 上传和监控线程（生产者）
        分批上传到 MinerU，实时监控状态，完成一个文件立即下载并加入翻译队列
        自动处理大文件分割和合并

        Args:
            files_to_upload: [(relative_path, pdf_path, output_paths), ...] 文件列表
            translation_queue: 翻译任务队列
            stop_event: 停止事件，完成后设置
            failed_files: 失败文件列表
            failed_files_lock: 失败文件锁
        """
        try:
            # 1. 分批上传（每批200个）
            BATCH_SIZE = 200
            batch_jobs = []  # [(batch_id, batch_files, batch_file_map, split_info)]

            # 全局分割信息管理
            all_split_info = {}  # {original_file: {"total_parts": N, "parts": [(task_idx, start, end, output_paths)], ...}}

            for batch_idx in range(0, len(files_to_upload), BATCH_SIZE):
                batch_files = files_to_upload[batch_idx:batch_idx + BATCH_SIZE]
                batch_num = batch_idx // BATCH_SIZE + 1
                total_batches = (len(files_to_upload) + BATCH_SIZE - 1) // BATCH_SIZE

                self.logger.info(f"\n[MinerU] 上传批次 {batch_num}/{total_batches} (共 {len(batch_files)} 个文件)")

                # 创建 FileTask 列表（先过滤不存在的文件）
                file_tasks = []
                valid_batch_files = []  # 只包含存在的文件
                batch_file_map = {}  # {expanded_task_index: (relative_path, pdf_path, output_paths, is_split, original_file, page_offset, part_idx)}

                task_idx = 0
                for i, (relative_path, pdf_path, output_paths) in enumerate(batch_files):
                    # 检查文件是否存在
                    if not os.path.exists(pdf_path):
                        self.logger.warning(f"⚠ 文件不存在，跳过: {pdf_path}")
                        with failed_files_lock:
                            failed_files.append(relative_path)
                        continue

                    valid_batch_files.append((relative_path, pdf_path, output_paths))

                    # 使用文件路径的MD5哈希前16位作为 data_id（保证不超过128字符）
                    data_id = hashlib.md5(pdf_path.encode('utf-8')).hexdigest()[:16]

                    file_task = FileTask(
                        file_name=Path(pdf_path).name,
                        file_path=pdf_path,
                        data_id=data_id
                    )
                    file_tasks.append(file_task)

                # 如果没有有效文件，跳过这个批次
                if not file_tasks:
                    self.logger.warning(f"[MinerU] 批次 {batch_num} 没有有效文件，跳过")
                    continue

                try:
                    # 批量上传（自动处理分割）
                    result = self.mineru.batch_upload_files(file_tasks)

                    # batch_upload_files内部也会再次检查，但这里已经过滤过了
                    if result is None or result[0] is None:
                        self.logger.warning(f"[MinerU] 批次 {batch_num} 上传失败")
                        continue

                    batch_id, _, split_info = result
                    self.logger.success(f"[MinerU] 批次 {batch_num} 上传成功，batch_id: {batch_id}")

                    # 处理分割信息
                    for original_file, parts_info in split_info.items():
                        # parts_info = [(task_idx, start_page, end_page), ...]
                        if original_file not in all_split_info:
                            all_split_info[original_file] = {
                                "total_parts": len(parts_info),
                                "parts": [],
                                "downloaded_parts": {}
                            }

                        # 查找对应的 output_paths
                        for relative_path, pdf_path, output_paths in valid_batch_files:
                            if pdf_path == original_file:
                                all_split_info[original_file]["relative_path"] = relative_path
                                all_split_info[original_file]["output_paths"] = output_paths
                                break

                    # 构建 batch_file_map（考虑分割后的索引）
                    expanded_task_idx = 0
                    for relative_path, pdf_path, output_paths in valid_batch_files:
                        if pdf_path in split_info:
                            # 这个文件被分割了
                            for part_idx, (_, start_page, end_page) in enumerate(split_info[pdf_path], 1):
                                batch_file_map[expanded_task_idx] = (
                                    relative_path,
                                    pdf_path,
                                    output_paths,
                                    True,  # is_split
                                    pdf_path,  # original_file
                                    start_page,  # page_offset
                                    part_idx  # part编号（从1开始）
                                )
                                expanded_task_idx += 1
                        else:
                            # 未分割
                            batch_file_map[expanded_task_idx] = (
                                relative_path,
                                pdf_path,
                                output_paths,
                                False,  # is_split
                                None,  # original_file
                                0,  # page_offset
                                0  # part_idx（0表示未分割）
                            )
                            expanded_task_idx += 1

                    batch_jobs.append((batch_id, valid_batch_files, batch_file_map, split_info))

                except Exception as e:
                    self.logger.error(f"[MinerU] 批次 {batch_num} 上传失败: {str(e)}")
                    continue

            # 2. 实时监控所有批次
            self.logger.info(f"\n[MinerU] 开始实时监控 {len(batch_jobs)} 个批次...")

            # 跟踪每个文件的状态
            completed_files = set()  # 已完成的文件（已下载并加入队列）
            downloaded_files = {}  # {(batch_id, file_index): True}
            # 修正：total_to_monitor 应该是展开后的任务数，不是原始文件数
            total_to_monitor = sum(len(batch_file_map) for _, _, batch_file_map, _ in batch_jobs)

            poll_interval = 10  # 轮询间隔
            first_iteration = True  # 第一次迭代标记
            last_status = None  # 上次的状态摘要

            while len(completed_files) < total_to_monitor:
                # 第一次迭代立即查询，后续等待10秒
                if not first_iteration:
                    time.sleep(poll_interval)
                first_iteration = False

                for batch_id, batch_files, batch_file_map, split_info in batch_jobs:
                    try:
                        # 查询批次状态
                        results = self.mineru.get_batch_status(batch_id)

                        # 统计当前状态
                        status_summary = {}
                        for result in results:
                            state_name = result.state.value
                            status_summary[state_name] = status_summary.get(state_name, 0) + 1

                        # 只在状态变化时显示
                        status_str = ", ".join(sorted([f"{state}: {count}" for state, count in status_summary.items()]))
                        if status_str != last_status:
                            self.logger.info(f"[MinerU] 状态: {status_str}")
                            last_status = status_str

                        for i, result in enumerate(results):
                            file_key = (batch_id, i)

                            # 跳过已处理的文件
                            if file_key in downloaded_files:
                                continue

                            if i not in batch_file_map:
                                continue

                            relative_path, pdf_path, output_paths, is_split, original_file, page_offset, part_idx = batch_file_map[i]

                            # 检查是否完成
                            if result.state == TaskState.DONE and result.full_zip_url:
                                self.logger.info(f"[MinerU] 下载: {relative_path} (Part {part_idx})" if is_split else f"[MinerU] 下载: {relative_path}")

                                try:
                                    if is_split:
                                        # 分割部分：下载到临时位置
                                        temp_dir = Path(output_paths['mineru']).parent / "temp_parts"
                                        temp_dir.mkdir(parents=True, exist_ok=True)

                                        part_name = f"{Path(original_file).stem}_part{part_idx}_result.zip"
                                        zip_path = self.mineru.download_result(
                                            result.full_zip_url,
                                            str(temp_dir),
                                            part_name
                                        )

                                        # 记录下载的部分（使用part_idx作为key，保证顺序）
                                        all_split_info[original_file]["downloaded_parts"][part_idx] = (zip_path, page_offset)
                                        downloaded_files[file_key] = True
                                        completed_files.add(file_key)

                                        self.logger.success(f"[MinerU] ✓ 已下载 Part {part_idx}")

                                        # 检查是否所有部分都下载完
                                        if len(all_split_info[original_file]["downloaded_parts"]) == all_split_info[original_file]["total_parts"]:
                                            self.logger.info(f"[MinerU] 所有部分已下载，开始合并: {all_split_info[original_file]['relative_path']}")

                                            # 合并所有部分
                                            part_paths = []
                                            page_offsets = []
                                            for part_idx in sorted(all_split_info[original_file]["downloaded_parts"].keys()):
                                                zip_path, offset = all_split_info[original_file]["downloaded_parts"][part_idx]
                                                part_paths.append(zip_path)
                                                page_offsets.append(offset)

                                            # 合并
                                            expected_zip = Path(all_split_info[original_file]["output_paths"]['mineru'])
                                            expected_zip.parent.mkdir(parents=True, exist_ok=True)

                                            self.mineru._merge_mineru_results(
                                                part_paths,
                                                str(expected_zip),
                                                page_offsets
                                            )

                                            # 清理临时文件
                                            for part_path, _ in all_split_info[original_file]["downloaded_parts"].values():
                                                try:
                                                    Path(part_path).unlink()
                                                except:
                                                    pass

                                            # 加入翻译队列
                                            translation_queue.put((
                                                all_split_info[original_file]["relative_path"],
                                                original_file,
                                                str(expected_zip)
                                            ))

                                            self.logger.success(f"[MinerU] ✓ {all_split_info[original_file]['relative_path']} 已合并并加入翻译队列")

                                    else:
                                        # 未分割：直接下载
                                        expected_zip = Path(output_paths['mineru'])
                                        expected_zip.parent.mkdir(parents=True, exist_ok=True)

                                        base_name = Path(pdf_path).stem
                                        zip_name = f"{base_name}_result.zip"

                                        zip_path = self.mineru.download_result(
                                            result.full_zip_url,
                                            str(expected_zip.parent),
                                            zip_name
                                        )

                                        # 移动到目标位置
                                        if Path(zip_path) != expected_zip:
                                            shutil.move(zip_path, str(expected_zip))
                                            zip_path = str(expected_zip)

                                        # 加入翻译队列
                                        translation_queue.put((relative_path, pdf_path, zip_path))
                                        completed_files.add(file_key)
                                        downloaded_files[file_key] = True

                                        self.logger.success(f"[MinerU] ✓ {relative_path} 已加入翻译队列")

                                except Exception as e:
                                    self.logger.error(f"[MinerU] 下载失败: {relative_path} - {str(e)}")
                                    downloaded_files[file_key] = True
                                    with failed_files_lock:
                                        failed_files.append((relative_path, f"下载失败: {str(e)}"))
                                    completed_files.add(file_key)

                            elif result.state == TaskState.FAILED:
                                # 失败的文件也标记为已处理
                                error_msg = result.err_msg or "未知错误"
                                self.logger.error(f"[MinerU] 解析失败: {relative_path} - {error_msg}")
                                downloaded_files[file_key] = True
                                completed_files.add(file_key)
                                with failed_files_lock:
                                    failed_files.append((relative_path, f"MinerU解析失败: {error_msg}"))

                    except Exception as e:
                        self.logger.warning(f"[MinerU] 查询批次 {batch_id} 状态失败: {str(e)}")
                        continue

                # 显示进度
                if len(completed_files) > 0 and len(completed_files) % 10 == 0:
                    self.logger.info(f"[MinerU] 进度: {len(completed_files)}/{total_to_monitor}")

            self.logger.success(f"[MinerU] 所有文件处理完成！{len(completed_files)}/{total_to_monitor}")

        except Exception as e:
            self.logger.error(f"[MinerU] 监控线程异常: {str(e)}")
            import traceback
            traceback.print_exc()

        finally:
            # 设置停止事件
            stop_event.set()
            self.logger.info("[MinerU] 监控线程退出")
