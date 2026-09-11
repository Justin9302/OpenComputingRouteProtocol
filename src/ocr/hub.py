"""
OCR Hub HTTP 服务

整合 AEMO 电价客户端、调度器、Manifest 验证器，提供 REST API。
对齐 RFC-001 Hub 角色和 RFC-006 任务执行协议。

零外部依赖，使用 Python 内置 http.server。

API:
  POST /v1/tasks          - 提交算力意图
  GET  /v1/tasks/{id}     - 查询任务状态
  GET  /v1/tasks          - 列出所有任务
  GET  /v1/price          - 当前电价
  GET  /v1/queue          - 调度队列摘要
  GET  /v1/resource-table - 资源表（从阶段一节点获取，或本地维护）
  GET  /health            - 健康检查
"""

import json
import time
import uuid
import logging
import threading
import argparse
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, Optional

from .aemo_client import AEMOClient
from .scheduler import Scheduler
from .manifest_verifier import ManifestVerifier

logger = logging.getLogger("ocr.hub")


class HubState:
    """Hub 全局状态"""

    def __init__(self, region: str = "NSW1", data_dir: str = "./data"):
        self.region = region
        self.aemo = AEMOClient(region=region)
        self.scheduler = Scheduler()
        self.manifest_verifier = ManifestVerifier(data_dir=data_dir)
        self.task_results: Dict[str, Dict] = {}  # task_id -> result
        self.start_time = time.time()
        self._price_update_interval = 300  # 5分钟更新一次电价
        self._schedule_interval = 30  # 30秒调度一次

    def start_background_tasks(self):
        """启动后台线程"""
        # 电价更新线程
        price_thread = threading.Thread(target=self._price_update_loop, daemon=True)
        price_thread.start()

        # 调度线程
        schedule_thread = threading.Thread(target=self._schedule_loop, daemon=True)
        schedule_thread.start()

        logger.info("Background tasks started")

    def _price_update_loop(self):
        """定期更新电价"""
        while True:
            try:
                price = self.aemo.get_current_price()
                if price is not None:
                    self.scheduler.update_price(price, self.region)
                    logger.debug(f"Price updated: {price:.1f} AUD/MWh")
            except Exception as e:
                logger.warning(f"Price update failed: {e}")
            time.sleep(self._price_update_interval)

    def _schedule_loop(self):
        """定期执行调度"""
        while True:
            try:
                scheduled = self.scheduler.schedule()
                for task in scheduled:
                    # 这里应该通过 RFC-006 协议下发给 Worker
                    # 原型阶段：标记为 running，模拟执行
                    self.scheduler.mark_running(task.task_id)
                    logger.info(f"Task {task.task_id} dispatched to {task.assigned_node}")
                    # 模拟任务完成（实际应由 Worker 上报）
                    threading.Thread(
                        target=self._simulate_task_completion,
                        args=(task.task_id,),
                        daemon=True
                    ).start()
            except Exception as e:
                logger.warning(f"Scheduling failed: {e}")
            time.sleep(self._schedule_interval)

    def _simulate_task_completion(self, task_id: str):
        """模拟任务执行完成（原型阶段，实际应由 Worker 上报）"""
        time.sleep(5)  # 模拟执行5秒
        self.scheduler.mark_completed(task_id)
        self.task_results[task_id] = {
            "status": "completed",
            "completed_at": time.time(),
            "result": "simulated completion",
        }
        logger.info(f"Task {task_id} completed (simulated)")

    def submit_task(self, payload: Dict) -> Dict:
        """提交任务"""
        task_id = payload.get("task_id") or f"task-{uuid.uuid4().hex[:8]}"
        compute_intent = payload.get("compute_intent", {})
        sla_scheduling = payload.get("sla_scheduling", {})
        data_governance = payload.get("data_governance")
        tenant_priority = payload.get("tenant_priority", "standard")

        # 如果有数据目录，验证 manifest
        data_dir = payload.get("data_dir")
        manifest_result = None
        if data_dir:
            try:
                verifier = ManifestVerifier(data_dir=data_dir)
                manifest_result = verifier.verify()
                logger.info(f"Manifest verification: trust={manifest_result.trust_level}, "
                           f"score={manifest_result.trust_score}")
                # 如果 manifest 声明了敏感度，使用它
                if manifest_result.overall_sensitivity != "low":
                    if not data_governance:
                        data_governance = {}
                    data_governance["data_sensitivity"] = manifest_result.overall_sensitivity
            except Exception as e:
                logger.warning(f"Manifest verification failed: {e}")

        task = self.scheduler.submit_task(
            task_id=task_id,
            compute_intent=compute_intent,
            sla_scheduling=sla_scheduling,
            data_governance=data_governance,
            tenant_priority=tenant_priority,
        )

        return {
            "task_id": task_id,
            "status": task.status,
            "priority_score": round(task.priority_score, 2),
            "strategy_group": task.get_strategy_group().value,
            "manifest_verification": manifest_result.to_dict() if manifest_result else None,
        }

    def get_task(self, task_id: str) -> Optional[Dict]:
        """获取任务状态"""
        task = self.scheduler.get_task_status(task_id)
        if not task:
            return None
        # 合并结果
        if task_id in self.task_results:
            task["result"] = self.task_results[task_id]
        return task

    def get_all_tasks(self) -> list:
        """获取所有任务"""
        return [self.get_task(tid) for tid in self.scheduler.tasks.keys()]


class HubHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器"""

    state: HubState = None  # 由 server 设置

    def _send_json(self, data: Dict, status: int = 200):
        """发送 JSON 响应"""
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> Dict:
        """读取请求体 JSON"""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            return {}
        body = self.rfile.read(content_length)
        return json.loads(body.decode("utf-8"))

    def do_GET(self):
        if self.path == "/health":
            self._send_json({
                "status": "ok",
                "uptime_seconds": round(time.time() - self.state.start_time, 1),
                "region": self.state.region,
            })
        elif self.path == "/v1/price":
            price = self.state.scheduler.current_price
            stats = self.state.aemo.get_price_stats(days=7)
            self._send_json({
                "current_price": price,
                "region": self.state.region,
                "stats": stats,
            })
        elif self.path == "/v1/queue":
            self._send_json(self.state.scheduler.get_queue_summary())
        elif self.path == "/v1/tasks":
            self._send_json({"tasks": self.state.get_all_tasks()})
        elif self.path == "/v1/resource-table":
            # 原型阶段：返回模拟的资源表
            # 实际应从阶段一的 P2P 节点获取
            self._send_json({
                "nodes": self.state.scheduler.available_nodes,
                "note": "prototype: nodes configured manually",
            })
        elif self.path.startswith("/v1/tasks/"):
            task_id = self.path.split("/")[-1]
            task = self.state.get_task(task_id)
            if task:
                self._send_json(task)
            else:
                self._send_json({"error": "task not found"}, 404)
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path == "/v1/tasks":
            try:
                payload = self._read_json()
                result = self.state.submit_task(payload)
                self._send_json(result, 201)
            except Exception as e:
                logger.error(f"Task submission failed: {e}", exc_info=True)
                self._send_json({"error": str(e)}, 400)
        else:
            self._send_json({"error": "not found"}, 404)

    def log_message(self, format, *args):
        logger.debug(f"HTTP: {format % args}")


def main():
    parser = argparse.ArgumentParser(description="OCR Hub HTTP 服务")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8080, help="监听端口")
    parser.add_argument("--region", default="NSW1", help="NEM 区域")
    parser.add_argument("--data-dir", default="./data", help="数据目录（用于 manifest 验证）")
    parser.add_argument("--log-level", default="INFO", help="日志级别")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # 初始化状态
    state = HubState(region=args.region, data_dir=args.data_dir)

    # 预加载电价数据
    logger.info("Loading AEMO price data...")
    try:
        price = state.aemo.get_current_price()
        if price is not None:
            state.scheduler.update_price(price, args.region)
            logger.info(f"Initial price: {price:.1f} AUD/MWh")
    except Exception as e:
        logger.warning(f"Initial price load failed: {e}")

    # 配置模拟节点（原型阶段）
    state.scheduler.update_nodes([
        {"resource_id": "worker-01", "latency_ms": 10,
         "security_level": "public", "network_zone": "cloud",
         "avail_compute": {"cpu_cores": 16, "ram_gb": 64}},
        {"resource_id": "worker-02", "latency_ms": 50,
         "security_level": "private_only", "network_zone": "office",
         "avail_compute": {"cpu_cores": 32, "ram_gb": 128, "gpu_count": 2}},
    ])

    # 启动后台任务
    state.start_background_tasks()

    # 设置 handler 的 state
    HubHandler.state = state

    # 启动 HTTP 服务
    server = HTTPServer((args.host, args.port), HubHandler)
    logger.info(f"OCR Hub listening on http://{args.host}:{args.port}")
    logger.info(f"API: POST /v1/tasks, GET /v1/tasks/{{id}}, GET /v1/price, GET /v1/queue")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
