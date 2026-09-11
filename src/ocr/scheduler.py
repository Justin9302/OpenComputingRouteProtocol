"""
OCR 调度器

对齐 RFC-002 SLA 分级和 RFC-005 算电协同：
- SLA 策略组调度（实时/交互/批处理/离线）
- 电价感知：低价/负电价时段优先执行低优任务
- 预算约束：任务总预算上限（price_ceiling）
- 租户优先级：gold/standard/economy 分级调度
- 数据敏感度约束：high/critical 强制 private_only
- 网络亲和性：优先同 zone 节点

调度器是 Hub 的核心，不直接执行任务，只决定"何时、在哪个节点执行"。
"""

import time
import logging
import heapq
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from enum import Enum

logger = logging.getLogger("ocr.scheduler")


class SLAStrategyGroup(Enum):
    """SLA 策略组（对齐 P2-1 改进项）"""
    REALTIME = "realtime"      # T0-T2: 立即执行
    INTERACTIVE = "interactive"  # T3-T5: 可短暂排队
    BATCH = "batch"            # T6-T7: 可被抢占
    OFFLINE = "offline"        # T8-T10: 仅低价时段


class TaskPriority(Enum):
    """任务优先级（综合 SLA Tier + 租户优先级）"""
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3


class ScheduledTask:
    """待调度任务"""

    def __init__(self, task_id: str, compute_intent: Dict,
                 sla_scheduling: Dict, data_governance: Optional[Dict] = None,
                 tenant_priority: str = "standard"):
        self.task_id = task_id
        self.compute_intent = compute_intent
        self.sla_scheduling = sla_scheduling
        self.data_governance = data_governance or {}
        self.tenant_priority = tenant_priority
        self.created_at = time.time()
        self.status = "pending"  # pending / scheduled / running / completed / failed
        self.scheduled_at = None
        self.assigned_node = None
        self.estimated_cost = 0.0
        self.priority_score = self._calculate_priority_score()

    def _calculate_priority_score(self) -> float:
        """
        计算调度优先级分数（越低越优先）
        综合 SLA Tier、租户优先级、等待时间
        """
        tier = self.sla_scheduling.get("tier", "T5")
        tier_num = int(tier.replace("T", "")) if tier.startswith("T") else 5

        # 租户优先级权重
        tenant_weight = {
            "gold": 0.5,      # gold 租户优先级高
            "standard": 1.0,
            "economy": 1.5,   # economy 租户优先级低
        }.get(self.tenant_priority, 1.0)

        # 基础分 = SLA Tier * 租户权重
        base_score = tier_num * tenant_weight

        # 等待时间加分（等待越久优先级越高，防止饿死）
        wait_time = time.time() - self.created_at
        aging_bonus = wait_time / 3600  # 每等待1小时减1分

        return base_score - aging_bonus

    def get_strategy_group(self) -> SLAStrategyGroup:
        """获取 SLA 策略组"""
        tier = self.sla_scheduling.get("tier", "T5")
        tier_num = int(tier.replace("T", "")) if tier.startswith("T") else 5
        if tier_num <= 2:
            return SLAStrategyGroup.REALTIME
        elif tier_num <= 5:
            return SLAStrategyGroup.INTERACTIVE
        elif tier_num <= 7:
            return SLAStrategyGroup.BATCH
        else:
            return SLAStrategyGroup.OFFLINE

    def get_price_ceiling(self) -> Optional[float]:
        """获取任务总预算上限（AUD）"""
        return self.sla_scheduling.get("price_ceiling")

    def get_max_completion_time(self) -> Optional[str]:
        """获取最大完成时间"""
        return self.sla_scheduling.get("max_completion_time")

    def get_data_sensitivity(self) -> str:
        """获取数据敏感度级别"""
        return self.data_governance.get("data_sensitivity", "low")

    def get_network_affinity(self) -> Optional[str]:
        """获取网络亲和性"""
        return self.data_governance.get("network_affinity")

    def to_dict(self) -> Dict:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "priority_score": round(self.priority_score, 2),
            "strategy_group": self.get_strategy_group().value,
            "sla_tier": self.sla_scheduling.get("tier"),
            "tenant_priority": self.tenant_priority,
            "price_ceiling": self.get_price_ceiling(),
            "data_sensitivity": self.get_data_sensitivity(),
            "created_at": datetime.fromtimestamp(self.created_at).isoformat(),
            "scheduled_at": datetime.fromtimestamp(self.scheduled_at).isoformat() if self.scheduled_at else None,
            "assigned_node": self.assigned_node,
            "estimated_cost": round(self.estimated_cost, 2),
        }


class Scheduler:
    """
    OCR 调度器

    核心逻辑:
    1. 任务按优先级分数排序（小顶堆）
    2. 每个调度周期，根据当前电价和任务约束决定是否执行
    3. 实时组立即执行，离线组仅在低价时段执行
    4. 预算约束：预测执行成本，超过预算则等待
    5. 敏感度约束：high/critical 只能选 private_only 节点
    """

    def __init__(self):
        self.task_queue: List[Tuple[float, str, ScheduledTask]] = []  # (priority_score, task_id, task)
        self.tasks: Dict[str, ScheduledTask] = {}
        self.current_price: Optional[float] = None  # 当前电价 AUD/MWh
        self.price_history: List[Dict] = []  # 电价历史
        self.available_nodes: List[Dict] = []  # 可用节点列表（从资源表获取）
        self.scheduling_interval = 30  # 调度周期（秒）

    def submit_task(self, task_id: str, compute_intent: Dict,
                    sla_scheduling: Dict, data_governance: Optional[Dict] = None,
                    tenant_priority: str = "standard") -> ScheduledTask:
        """提交任务到调度队列"""
        task = ScheduledTask(
            task_id=task_id,
            compute_intent=compute_intent,
            sla_scheduling=sla_scheduling,
            data_governance=data_governance,
            tenant_priority=tenant_priority,
        )
        self.tasks[task_id] = task
        heapq.heappush(self.task_queue, (task.priority_score, task_id, task))
        logger.info(f"Task {task_id} submitted: tier={sla_scheduling.get('tier')}, "
                    f"group={task.get_strategy_group().value}, "
                    f"priority={task.priority_score:.2f}")
        return task

    def update_price(self, price: float, region: str = "NSW1"):
        """更新当前电价"""
        self.current_price = price
        self.price_history.append({
            "time": datetime.now().isoformat(),
            "region": region,
            "price_per_mwh": price,
        })
        # 保留最近 1000 条
        if len(self.price_history) > 1000:
            self.price_history = self.price_history[-1000:]

    def update_nodes(self, nodes: List[Dict]):
        """更新可用节点列表（从资源表获取）"""
        self.available_nodes = nodes

    def schedule(self) -> List[ScheduledTask]:
        """
        执行一次调度，返回应该立即执行的任务列表

        调度决策:
        - 实时组: 总是执行（只要有可用节点）
        - 交互组: 电价不超过尖峰阈值时执行
        - 批处理组: 电价低于平均时执行，可被抢占
        - 离线组: 仅在低价/负电价时段执行
        """
        if self.current_price is None:
            logger.warning("No price data available, scheduling with default")
            self.current_price = 100.0  # 默认电价

        to_execute = []
        remaining = []

        while self.task_queue:
            priority, task_id, task = heapq.heappop(self.task_queue)

            if task.status != "pending":
                continue

            # 重新计算优先级（考虑等待时间老化）
            task.priority_score = task._calculate_priority_score()

            should_run, reason = self._should_execute(task)

            if should_run:
                # 分配节点
                node = self._select_node(task)
                if node:
                    task.status = "scheduled"
                    task.scheduled_at = time.time()
                    task.assigned_node = node.get("resource_id")
                    task.estimated_cost = self._estimate_cost(task, node)
                    to_execute.append(task)
                    logger.info(f"Task {task_id} scheduled on {node.get('resource_id')}: "
                                f"price={self.current_price:.0f}, reason={reason}")
                else:
                    # 没有合适节点，放回队列
                    remaining.append((task.priority_score, task_id, task))
                    logger.debug(f"Task {task_id} waiting: no suitable node")
            else:
                remaining.append((task.priority_score, task_id, task))
                logger.debug(f"Task {task_id} waiting: {reason}")

        # 把未执行的任务放回队列
        for item in remaining:
            heapq.heappush(self.task_queue, item)

        return to_execute

    def _should_execute(self, task: ScheduledTask) -> Tuple[bool, str]:
        """判断任务是否应该在当前时段执行"""
        group = task.get_strategy_group()
        price = self.current_price or 100.0

        # 预算约束检查
        ceiling = task.get_price_ceiling()
        if ceiling is not None:
            # 简化：如果当前电价过高，预估成本可能超预算
            # 实际应基于任务时长和节点功率计算，这里用电价阈值做简化判断
            if price > ceiling * 10:  # 粗略换算，实际需要更精确的成本模型
                return False, f"price {price:.0f} too high for budget {ceiling}"

        if group == SLAStrategyGroup.REALTIME:
            return True, "realtime group, always execute"

        elif group == SLAStrategyGroup.INTERACTIVE:
            # 交互组：避开尖峰（>300 AUD/MWh）
            if price > 300:
                return False, f"interactive group, price {price:.0f} > peak threshold 300"
            return True, "interactive group, price acceptable"

        elif group == SLAStrategyGroup.BATCH:
            # 批处理组：电价低于近期平均时执行
            avg_price = self._get_recent_avg_price()
            if price > avg_price:
                return False, f"batch group, price {price:.0f} > avg {avg_price:.0f}"
            return True, "batch group, price below average"

        elif group == SLAStrategyGroup.OFFLINE:
            # 离线组：仅在低价（<50）或负电价时段执行
            if price > 50:
                return False, f"offline group, price {price:.0f} > low-price threshold 50"
            return True, "offline group, low price period"

        return False, "unknown strategy group"

    def _select_node(self, task: ScheduledTask) -> Optional[Dict]:
        """为任务选择合适的节点"""
        if not self.available_nodes:
            return None

        candidates = self.available_nodes

        # 数据敏感度约束：high/critical 只能 private_only 节点
        sensitivity = task.get_data_sensitivity()
        if sensitivity in ("high", "critical"):
            candidates = [n for n in candidates
                          if n.get("security_level") in ("private_only", "confidential")]
            if not candidates:
                logger.warning(f"Task {task.task_id} requires private_only node but none available")
                return None

        # 网络亲和性：优先同 zone 节点
        affinity = task.get_network_affinity()
        if affinity:
            same_zone = [n for n in candidates
                         if n.get("network_zone") == affinity]
            if same_zone:
                candidates = same_zone

        # 简单选择：延迟最低的节点
        candidates.sort(key=lambda n: n.get("latency_ms", 9999))
        return candidates[0] if candidates else None

    def _estimate_cost(self, task: ScheduledTask, node: Dict) -> float:
        """
        估算任务执行成本（AUD）
        简化模型：成本 = 电价 * 节点功率 * 预估时长 / 1000
        实际需要更精确的模型（含算力服务费、数据传输成本等）
        """
        price = self.current_price or 100.0
        # 假设节点功率 1kW，任务执行 1 小时
        # 实际应从 compute_intent 获取 estimated_duration
        power_kw = 1.0
        duration_h = 1.0
        cost = price * power_kw * duration_h / 1000  # AUD/MWh * kW * h = AUD
        return round(cost, 4)

    def _get_recent_avg_price(self, window: int = 48) -> float:
        """获取近期平均电价（默认最近48条，约4小时@5min粒度）"""
        if not self.price_history:
            return self.current_price or 100.0
        recent = self.price_history[-window:]
        return sum(p["price_per_mwh"] for p in recent) / len(recent)

    def get_task_status(self, task_id: str) -> Optional[Dict]:
        """获取任务状态"""
        task = self.tasks.get(task_id)
        return task.to_dict() if task else None

    def get_pending_tasks(self) -> List[Dict]:
        """获取所有待处理任务"""
        return [t.to_dict() for t in self.tasks.values() if t.status == "pending"]

    def get_queue_summary(self) -> Dict:
        """获取调度队列摘要"""
        groups = {"realtime": 0, "interactive": 0, "batch": 0, "offline": 0}
        for task in self.tasks.values():
            if task.status == "pending":
                groups[task.get_strategy_group().value] += 1

        return {
            "total_pending": len([t for t in self.tasks.values() if t.status == "pending"]),
            "by_strategy_group": groups,
            "current_price": self.current_price,
            "recent_avg_price": self._get_recent_avg_price(),
            "available_nodes": len(self.available_nodes),
        }

    def mark_running(self, task_id: str):
        """标记任务为运行中"""
        task = self.tasks.get(task_id)
        if task:
            task.status = "running"

    def mark_completed(self, task_id: str):
        """标记任务完成"""
        task = self.tasks.get(task_id)
        if task:
            task.status = "completed"

    def mark_failed(self, task_id: str, reason: str = ""):
        """标记任务失败"""
        task = self.tasks.get(task_id)
        if task:
            task.status = "failed"
            logger.warning(f"Task {task_id} failed: {reason}")


def main():
    """命令行测试"""
    import argparse
    parser = argparse.ArgumentParser(description="OCR 调度器测试")
    parser.add_argument("--price", type=float, default=-20.0, help="模拟当前电价 AUD/MWh")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    scheduler = Scheduler()
    scheduler.update_price(args.price)

    # 模拟可用节点
    scheduler.update_nodes([
        {"resource_id": "node-01", "latency_ms": 10, "security_level": "public", "network_zone": "office"},
        {"resource_id": "node-02", "latency_ms": 50, "security_level": "private_only", "network_zone": "home"},
    ])

    # 提交不同优先级的任务
    tasks = [
        ("task-realtime", {"type": "inference"}, {"tier": "T1", "price_ceiling": 100}),
        ("task-interactive", {"type": "training"}, {"tier": "T4", "price_ceiling": 50}),
        ("task-batch", {"type": "batch_processing"}, {"tier": "T6", "price_ceiling": 30}),
        ("task-offline", {"type": "batch_processing"}, {"tier": "T9", "price_ceiling": 20}),
        ("task-sensitive", {"type": "training"}, {"tier": "T5"},
         {"data_sensitivity": "high", "network_affinity": "home"}),
    ]

    for task_info in tasks:
        task_id, intent, sla = task_info[0], task_info[1], task_info[2]
        governance = task_info[3] if len(task_info) > 3 else None
        scheduler.submit_task(task_id, intent, sla, governance)

    print(f"\n当前电价: {args.price} AUD/MWh")
    print(f"队列摘要: {scheduler.get_queue_summary()}")

    # 执行调度
    scheduled = scheduler.schedule()
    print(f"\n本次调度执行 {len(scheduled)} 个任务:")
    for task in scheduled:
        print(f"  - {task.task_id}: tier={task.sla_scheduling.get('tier')}, "
              f"node={task.assigned_node}, cost={task.estimated_cost}")

    print(f"\n剩余待处理: {scheduler.get_pending_tasks()}")


if __name__ == "__main__":
    main()
