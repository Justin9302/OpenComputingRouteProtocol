"""
OCR Worker 进程

对齐 RFC-006 任务执行协议：
- 向 Hub 注册并上报算力资源
- 接收任务并执行
- 上报执行状态和结果
- 心跳保活

原型阶段使用 HTTP 轮询，后续可升级为 gRPC 流式。
"""

import json
import time
import uuid
import logging
import argparse
import subprocess
import urllib.request
import urllib.error
from typing import Dict, Optional, List

logger = logging.getLogger("ocr.worker")


class OCRWorker:
    """
    OCR Worker 节点

    职责:
    1. 启动时向 Hub 注册，上报算力资源
    2. 定期轮询 Hub 获取待执行任务
    3. 执行任务（调用用户指定的命令/脚本）
    4. 上报执行状态和结果
    5. 定期心跳
    """

    def __init__(self, hub_url: str, worker_id: Optional[str] = None,
                 compute_resources: Optional[Dict] = None,
                 work_dir: str = "./worker-workspace"):
        self.hub_url = hub_url.rstrip("/")
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self.compute_resources = compute_resources or self._detect_resources()
        self.work_dir = work_dir
        self.running = False
        self.current_task: Optional[Dict] = None
        self.poll_interval = 10  # 轮询间隔（秒）
        self.heartbeat_interval = 30  # 心跳间隔（秒）

    def _detect_resources(self) -> Dict:
        """检测本地算力资源（简化版）"""
        import os
        cpu_cores = os.cpu_count() or 4
        # 简化：不检测 GPU，需要时手动配置
        return {
            "cpu_cores": cpu_cores,
            "ram_gb": 16,  # 简化，实际应读取系统内存
            "gpu_count": 0,
            "gpu_model": None,
        }

    def _http_request(self, method: str, path: str,
                      data: Optional[Dict] = None) -> Optional[Dict]:
        """发送 HTTP 请求到 Hub"""
        url = f"{self.hub_url}{path}"
        try:
            if data:
                body = json.dumps(data).encode("utf-8")
                req = urllib.request.Request(url, data=body, method=method)
                req.add_header("Content-Type", "application/json")
            else:
                req = urllib.request.Request(url, method=method)

            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            logger.warning(f"HTTP {e.code}: {path}")
            return None
        except Exception as e:
            logger.warning(f"Request failed: {path}: {e}")
            return None

    def register(self) -> bool:
        """向 Hub 注册（原型阶段：Hub 预配置节点，这里只做日志）"""
        logger.info(f"Worker {self.worker_id} registering with Hub {self.hub_url}")
        logger.info(f"  Resources: {self.compute_resources}")
        # 原型阶段 Hub 的节点是预配置的，这里只记录
        # 正式版应调用 Hub 的注册 API
        return True

    def heartbeat(self):
        """发送心跳"""
        # 原型阶段：日志记录
        logger.debug(f"Heartbeat: worker={self.worker_id}, "
                     f"status={'busy' if self.current_task else 'idle'}")

    def poll_for_task(self) -> Optional[Dict]:
        """轮询 Hub 获取任务"""
        # 原型阶段：Hub 的调度是模拟的，Worker 通过查询任务列表获取
        # 正式版应使用 RFC-006 的任务下发协议
        result = self._http_request("GET", "/v1/tasks")
        if not result or "tasks" not in result:
            return None

        # 找到分配给本节点的 scheduled 任务
        for task in result["tasks"]:
            if (task.get("status") == "scheduled" and
                    task.get("assigned_node") == self.worker_id):
                return task
        return None

    def execute_task(self, task: Dict) -> Dict:
        """
        执行任务

        原型阶段：支持执行指定的 shell 命令或脚本
        正式版：应支持容器化执行、资源隔离等
        """
        task_id = task["task_id"]
        compute_intent = task.get("compute_intent", {})

        logger.info(f"Executing task {task_id}: {compute_intent}")

        # 获取要执行的命令
        command = compute_intent.get("command")
        script = compute_intent.get("script")

        start_time = time.time()
        result = {
            "task_id": task_id,
            "worker_id": self.worker_id,
            "started_at": time.time(),
            "status": "running",
        }

        try:
            if command:
                # 执行 shell 命令
                logger.info(f"Running command: {command}")
                proc = subprocess.run(
                    command, shell=True, capture_output=True,
                    text=True, timeout=3600, cwd=self.work_dir
                )
                result["exit_code"] = proc.returncode
                result["stdout"] = proc.stdout[-2000:]  # 截断
                result["stderr"] = proc.stderr[-2000:]
                result["status"] = "completed" if proc.returncode == 0 else "failed"

            elif script:
                # 执行 Python 脚本
                logger.info(f"Running script: {script}")
                proc = subprocess.run(
                    ["python", script], capture_output=True,
                    text=True, timeout=3600, cwd=self.work_dir
                )
                result["exit_code"] = proc.returncode
                result["stdout"] = proc.stdout[-2000:]
                result["stderr"] = proc.stderr[-2000:]
                result["status"] = "completed" if proc.returncode == 0 else "failed"

            else:
                # 没有指定命令，模拟执行
                logger.info("No command/script specified, simulating execution")
                time.sleep(3)  # 模拟执行时间
                result["status"] = "completed"
                result["stdout"] = "simulated task execution"

        except subprocess.TimeoutExpired:
            result["status"] = "failed"
            result["error"] = "timeout"
        except Exception as e:
            result["status"] = "failed"
            result["error"] = str(e)
            logger.error(f"Task execution failed: {e}", exc_info=True)

        result["completed_at"] = time.time()
        result["duration_seconds"] = round(result["completed_at"] - start_time, 2)

        logger.info(f"Task {task_id} {result['status']} in {result['duration_seconds']}s")
        return result

    def report_result(self, result: Dict):
        """上报任务结果到 Hub"""
        # 原型阶段：Hub 的任务完成是模拟的，这里只记录日志
        # 正式版应调用 Hub 的结果上报 API（RFC-006 TaskResult）
        logger.info(f"Reporting result for task {result['task_id']}: {result['status']}")

    def run(self):
        """运行 Worker 主循环"""
        self.running = True
        self.register()

        logger.info(f"Worker {self.worker_id} started")
        logger.info(f"  Hub: {self.hub_url}")
        logger.info(f"  Poll interval: {self.poll_interval}s")

        last_heartbeat = 0

        while self.running:
            try:
                # 心跳
                if time.time() - last_heartbeat > self.heartbeat_interval:
                    self.heartbeat()
                    last_heartbeat = time.time()

                # 如果当前没有任务，轮询获取
                if not self.current_task:
                    task = self.poll_for_task()
                    if task:
                        self.current_task = task
                        logger.info(f"Received task: {task['task_id']}")

                # 执行当前任务
                if self.current_task:
                    result = self.execute_task(self.current_task)
                    self.report_result(result)
                    self.current_task = None

                # 等待下一轮
                time.sleep(self.poll_interval)

            except KeyboardInterrupt:
                logger.info("Received interrupt, shutting down...")
                self.running = False
            except Exception as e:
                logger.error(f"Worker loop error: {e}", exc_info=True)
                time.sleep(self.poll_interval)

        logger.info(f"Worker {self.worker_id} stopped")

    def stop(self):
        """停止 Worker"""
        self.running = False


def main():
    parser = argparse.ArgumentParser(description="OCR Worker 节点")
    parser.add_argument("--hub-url", default="http://127.0.0.1:8080", help="Hub 地址")
    parser.add_argument("--worker-id", default="", help="Worker ID（默认自动生成）")
    parser.add_argument("--work-dir", default="./worker-workspace", help="工作目录")
    parser.add_argument("--cpu-cores", type=int, default=0, help="CPU 核心数（默认自动检测）")
    parser.add_argument("--ram-gb", type=int, default=16, help="内存 GB")
    parser.add_argument("--gpu-count", type=int, default=0, help="GPU 数量")
    parser.add_argument("--gpu-model", default="", help="GPU 型号")
    parser.add_argument("--poll-interval", type=int, default=10, help="轮询间隔（秒）")
    parser.add_argument("--log-level", default="INFO", help="日志级别")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # 配置算力资源
    resources = {}
    if args.cpu_cores:
        resources["cpu_cores"] = args.cpu_cores
    if args.ram_gb:
        resources["ram_gb"] = args.ram_gb
    if args.gpu_count:
        resources["gpu_count"] = args.gpu_count
    if args.gpu_model:
        resources["gpu_model"] = args.gpu_model

    worker = OCRWorker(
        hub_url=args.hub_url,
        worker_id=args.worker_id or None,
        compute_resources=resources or None,
        work_dir=args.work_dir,
    )
    worker.poll_interval = args.poll_interval

    try:
        worker.run()
    except KeyboardInterrupt:
        worker.stop()


if __name__ == "__main__":
    main()
