#!/usr/bin/env python3
"""
Open Compute Router - Hub 极简模拟器
仅用于演示 SLA Tiering 调度逻辑，非生产用途。
"""

import json
import time
import random
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Optional

@dataclass
class Task:
    task_id: str
    tier: int
    max_completion: timedelta
    interruptible: bool
    submitted_at: datetime = field(default_factory=datetime.now)
    status: str = "pending"  # pending / scheduled / running / completed / preempted

@dataclass
class EnergyPriceOracle:
    """模拟电价生成器（实际可替换为 AEMO/PJM API）"""
    def get_price(self, at: datetime) -> float:
        hour = at.hour
        # 模拟：夜间低谷，白天高峰
        if 0 <= hour < 6:
            return random.uniform(20, 50)    # 低谷
        elif 6 <= hour < 18:
            return random.uniform(150, 300)  # 高峰
        else:
            return random.uniform(80, 120)   # 平段

class HubSimulator:
    def __init__(self):
        self.queue: List[Task] = []
        self.oracle = EnergyPriceOracle()

    def submit_task(self, intent: dict):
        tier_str = intent.get("sla_scheduling", {}).get("tier", "T1")
        tier = int(tier_str.lstrip("T"))
        max_ct = self._parse_duration(intent.get("sla_scheduling", {}).get("max_completion_time", "1m"))
        interruptible = intent.get("sla_scheduling", {}).get("interruptible", False)

        task = Task(
            task_id=intent["task_id"],
            tier=tier,
            max_completion=max_ct,
            interruptible=interruptible
        )
        self.queue.append(task)
        print(f"[HUB] Received task {task.task_id} | Tier=T{tier} | Interruptible={interruptible}")

    def schedule(self):
        """极简调度逻辑：按 Tier 排序，结合电价决定是否延迟"""
        self.queue.sort(key=lambda t: t.tier)
        now = datetime.now()
        current_price = self.oracle.get_price(now)

        print(f"[HUB] Current simulated price: ${current_price:.2f}/MWh")

        for task in self.queue:
            if task.status != "pending":
                continue

            # T0-T2 立即执行
            if task.tier <= 2:
                task.status = "running"
                print(f"[HUB] ▶️  Task {task.task_id} (T{task.tier}) dispatched IMMEDIATELY.")
            else:
                # 高 Tier 任务：若电价过高，延迟执行
                if current_price > 200:
                    task.status = "scheduled"
                    delay = timedelta(hours=random.randint(1, task.tier))
                    print(f"[HUB] ⏸️  Task {task.task_id} (T{task.tier}) DELAYED due to peak price. Retry in {delay}.")
                else:
                    task.status = "running"
                    print(f"[HUB] ▶️  Task {task.task_id} (T{task.tier}) dispatched.")

    def _parse_duration(self, s: str) -> timedelta:
        if s.endswith("s"):
            return timedelta(seconds=int(s[:-1]))
        elif s.endswith("m"):
            return timedelta(minutes=int(s[:-1]))
        elif s.endswith("h"):
            return timedelta(hours=int(s[:-1]))
        return timedelta(minutes=1)

if __name__ == "__main__":
    # 模拟提交三个任务
    tasks = [
        {
            "task_id": "task-001",
            "sla_scheduling": {"tier": "T1", "max_completion_time": "1m", "interruptible": False}
        },
        {
            "task_id": "task-002",
            "sla_scheduling": {"tier": "T6", "max_completion_time": "2h", "interruptible": True}
        },
        {
            "task_id": "task-003",
            "sla_scheduling": {"tier": "T9", "max_completion_time": "16h", "interruptible": True}
        }
    ]

    hub = HubSimulator()
    for t in tasks:
        hub.submit_task(t)

    print("\n--- Scheduling Round ---")
    hub.schedule()