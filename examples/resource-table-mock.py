#!/usr/bin/env python3
"""
Open Compute Router - 设备级资源表模拟器（RFC-005 参考实现）

验证三个核心设计：
  1. 设备级被动接收：设备配置接口地址，数据源以发布-订阅/推送方式被动到达（无第三方、无主动轮询）
  2. 资源表维护：每台设备独立维护资源表（与路由表同构的「算力 x 能源」视图），过期表项自动老化
  3. 调度决策：基于本地资源表 + SLA Tier（对齐 RFC-002），低优任务只在负电价/绿电过剩时执行

仅用于演示，非生产用途。
"""

import json
import random
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional


# ============================================================
# 1. 资源表（Resource Table）：与路由表同构
#    路由表回答"数据包往哪走"，资源表回答"任务何时/在哪儿/用什么电跑"
# ============================================================
@dataclass
class ResourceEntry:
    resource_id: str          # 资源唯一标识
    resource_type: str        # compute / energy_grid / battery
    status: str               # up / degraded / down
    price_per_mwh: float      # 电价（本币/MWh，可为负）
    green_ratio: float        # 绿电比例（0~1）
    avail_compute: Dict       # 可用算力 {"gpu_count":..,"vram_gb":..}
    battery_soc: Optional[float]  # 本地储能剩余电量（0~1）
    latency_ms: float
    ttl: str                  # 表项有效期（过期即失效，类比路由老化）
    trust_anchor: str         # 身份来源（SPIFFE ID，RFC-003 零信任验证）
    received_at: datetime = field(default_factory=datetime.now)

    def is_expired(self) -> bool:
        ttl_sec = self._parse_duration(self.ttl)
        return datetime.now() - self.received_at > timedelta(seconds=ttl_sec)

    @staticmethod
    def _parse_duration(s: str) -> int:
        if s.endswith("s"):
            return int(s[:-1])
        elif s.endswith("m"):
            return int(s[:-1]) * 60
        elif s.endswith("h"):
            return int(s[:-1]) * 3600
        return 300


class ResourceTable:
    """设备本地资源表：独立维护，无任何第三方参与"""

    def __init__(self, device_id: str):
        self.device_id = device_id
        self.entries: Dict[str, ResourceEntry] = {}
        self.version = 1

    def upsert(self, entry: ResourceEntry):
        """被动接收数据后更新表项（来源身份已在入口校验）"""
        self.entries[entry.resource_id] = entry
        self.version += 1
        print(f"  [RT:{self.device_id}] + {entry.resource_type}:{entry.resource_id} "
              f"price={entry.price_per_mwh:+.0f} green={entry.green_ratio:.2f}")

    def prune_expired(self):
        """清理过期表项（路由老化）"""
        expired = [rid for rid, e in self.entries.items() if e.is_expired()]
        for rid in expired:
            del self.entries[rid]
            print(f"  [RT:{self.device_id}] - expired:{rid}")

    def query(self, resource_type: Optional[str] = None) -> List[ResourceEntry]:
        return [e for e in self.entries.values()
                if resource_type is None or e.resource_type == resource_type]

    def to_dict(self) -> Dict:
        entries = []
        for e in self.entries.values():
            d = asdict(e)
            d["received_at"] = d["received_at"].isoformat()
            entries.append(d)
        return {
            "device_id": self.device_id,
            "version": self.version,
            "updated_at": datetime.now().isoformat(),
            "entries": entries,
        }


# ============================================================
# 2. 数据源：发布-订阅推送
#    设备接入网络后配置一个接口地址（listen endpoint）即可被动接收
# ============================================================
class EnergyGridSource:
    """电网开放数据源（模拟 AEMO 推送：电价 / 绿电比例）"""

    def __init__(self, region: str, listeners: List[ResourceTable]):
        self.region = region
        self.listeners = listeners  # 订阅了本数据源的设备（已配置接口地址）

    def publish(self, price: float, green: float):
        for rt in self.listeners:
            rt.upsert(ResourceEntry(
                resource_id=f"energy_grid/{self.region}",
                resource_type="energy_grid",
                status="up",
                price_per_mwh=price,
                green_ratio=green,
                avail_compute={},
                battery_soc=None,
                latency_ms=5,
                ttl="300s",
                trust_anchor=f"spiffe://open-compute-router.org/grid/{self.region}",
            ))


class ComputeNodeSource:
    """算力节点状态推送（GPU / 显存 / 节点电价）"""

    def __init__(self, node_id: str, listeners: List[ResourceTable]):
        self.node_id = node_id
        self.listeners = listeners

    def publish(self, price: float, green: float, gpu: int, vram: int, soc: Optional[float] = None):
        for rt in self.listeners:
            rt.upsert(ResourceEntry(
                resource_id=f"server/{self.node_id}",
                resource_type="compute",
                status="up",
                price_per_mwh=price,
                green_ratio=green,
                avail_compute={"gpu_count": gpu, "vram_gb": vram},
                battery_soc=soc,
                latency_ms=20,
                ttl="300s",
                trust_anchor=f"spiffe://open-compute-router.org/server/{self.node_id}",
            ))


class BatterySource:
    """本地储能状态推送（为 T0-T2 高优任务提供兜底）"""

    def __init__(self, battery_id: str, listeners: List[ResourceTable]):
        self.battery_id = battery_id
        self.listeners = listeners

    def publish(self, soc: float, price: float):
        for rt in self.listeners:
            rt.upsert(ResourceEntry(
                resource_id=f"battery/{self.battery_id}",
                resource_type="battery",
                status="up",
                price_per_mwh=price,
                green_ratio=0.0,
                avail_compute={},
                battery_soc=soc,
                latency_ms=2,
                ttl="300s",
                trust_anchor=f"spiffe://open-compute-router.org/battery/{self.battery_id}",
            ))


# ============================================================
# 3. 调度决策：基于本地资源表 + SLA Tier（对齐 RFC-002）
# ============================================================
def decide(rt: ResourceTable, task: Dict) -> str:
    """
    极简决策逻辑：
    - T0-T2（高优）：立即执行，容忍峰值电价，本地储能兜底
    - T3-T7（中优）：避开尖峰，等待电价回落（price > ceiling 则等待）
    - T8-T10（低优）：仅在负电价/绿电过剩时段执行（近似零成本算力）
    """
    tier = int(task["sla_scheduling"]["tier"].lstrip("T"))
    ceiling = task["sla_scheduling"].get("price_ceiling", 200)
    min_vram = task.get("compute_intent", {}).get("min_vram_gb", 1)
    prefer_green = task["sla_scheduling"].get("energy_preference", {}).get("prefer_green", False)

    rt.prune_expired()
    nodes = [n for n in rt.query("compute")
             if n.avail_compute.get("vram_gb", 0) >= min_vram]

    if not nodes:
        return f"[DECIDE] {task['task_id']} (T{tier}): no capable node in local table -> deferred"

    best = min(nodes, key=lambda n: n.price_per_mwh)
    cur_price, green = best.price_per_mwh, best.green_ratio
    battery = rt.query("battery")
    soc = max((b.battery_soc for b in battery), default=0.0)

    if tier <= 2:
        action = (f"RUN NOW (high priority"
                  f"{'; battery backup SOC=' + f'{soc:.0%}' if soc > 0.5 else ''})")
    elif tier <= 7:
        action = "WAIT (price above ceiling, avoid peak)" if cur_price > ceiling else "RUN (mid priority, price OK)"
    else:
        if cur_price < 0 or (prefer_green and green >= 0.6):
            action = "RUN NOW (low priority: NEGATIVE PRICE / green surplus)"
        else:
            action = f"WAIT for cheap/green window (price={cur_price:+.0f}, green={green:.2f})"

    return (f"[DECIDE] {task['task_id']} (T{tier}) -> {best.resource_id} | "
            f"price={cur_price:+.0f} green={green:.2f} | {action}")


def print_resources(rt: ResourceTable, title: str):
    print(f"\n-- hub-a 本地资源表（{title}；独立维护，无第三方）--")
    print(json.dumps(rt.to_dict(), ensure_ascii=False, indent=2))


def main():
    print("=== OCR RFC-005 设备级自治与资源表 模拟器 ===\n")

    # 两台设备（Hub/Switch/Router），各自独立维护资源表
    hub_a = ResourceTable("hub/edge-01")
    hub_b = ResourceTable("hub/edge-02")

    # 数据源发布-订阅：设备配置接口地址即被动接收
    aemo_nsw = EnergyGridSource("NSW1", [hub_a, hub_b])
    node1 = ComputeNodeSource("ap-southeast-01", [hub_a])
    node2 = ComputeNodeSource("ap-southeast-02", [hub_a, hub_b])
    batt = BatterySource("edge-01-batt", [hub_a])

    # 任务定义
    t_high = {
        "task_id": "task-001",
        "compute_intent": {"min_vram_gb": 32},
        "sla_scheduling": {"tier": "T1", "price_ceiling": 500},
    }
    t_low = {
        "task_id": "task-002",
        "compute_intent": {"min_vram_gb": 16},
        "sla_scheduling": {
            "tier": "T9",
            "price_ceiling": 20,
            "energy_preference": {"prefer_green": True},
        },
    }

    # ---- 场景 1：白天高峰 ----
    print("-- 场景 1：白天高峰（电价高、绿电低）--")
    aemo_nsw.publish(price=280, green=0.20)
    node1.publish(price=285, green=0.15, gpu=4, vram=320, soc=0.80)
    node2.publish(price=290, green=0.18, gpu=8, vram=640)
    batt.publish(soc=0.85, price=280)

    print()
    print(decide(hub_a, t_high))
    print(decide(hub_a, t_low))

    # ---- 场景 2：夜间负电价 ----
    print("\n-- 场景 2：夜间负电价（绿电过剩）--")
    aemo_nsw.publish(price=-25, green=0.90)
    node1.publish(price=-20, green=0.88, gpu=4, vram=320, soc=0.50)
    node2.publish(price=-25, green=0.92, gpu=8, vram=640)
    batt.publish(soc=0.45, price=-25)

    print()
    print(decide(hub_a, t_low))

    # 资源表 dump（hub-a）
    print_resources(hub_a, "场景 2 结束后")

    print("\n-- 独立性验证：hub-b 仅订阅了 NSW1 电网与 node2 --")
    print(json.dumps(
        {rid: {"type": e.resource_type, "price": e.price_per_mwh}
         for rid, e in hub_b.entries.items()},
        ensure_ascii=False, indent=2))
    print("\n注：资源表由设备独立维护；无第三方平台聚合、无单点故障、数据主权不旁落。")


if __name__ == "__main__":
    random.seed(42)
    main()
