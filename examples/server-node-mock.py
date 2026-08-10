#!/usr/bin/env python3
"""
Open Compute Router - 算力节点状态模拟器（Phase 0 Demo 资产）

模拟一台真实算力节点（GPU 集群）周期性上报状态，输出 JSON 行（JSON Lines）。
状态字段对齐 spec/resource-table.schema.json 的 compute 资源表项。

可被 demo-30s.py / resource-table-mock.py 消费，作为供给侧"节点在线"证据。

用法：
  python server-node-mock.py                       # 上报 3 次后退出
  python server-node-mock.py --loop                # 持续上报（Ctrl-C 停止）
  python server-node-mock.py --gpu 8 --vram 640    # 自定义节点规格
"""

import argparse
import json
import random
import time
from datetime import datetime


class ServerNode:
    """模拟一个算力节点：本地持有电价/绿电/算力/储能状态，被动消费电网数据后上报"""

    def __init__(self, node_id: str, gpu: int, vram: int, seed: int = 42):
        self.node_id = node_id
        self.gpu = gpu
        self.vram = vram
        self.rng = random.Random(seed)
        # 本地感知：由 energy-price-mock 推送的电网数据（发布-订阅，见 RFC-004/005）
        self.price_per_mwh = 0.0
        self.green_ratio = 0.5
        self.soc = 0.9  # 本地储能剩余电量

    def ingest_market(self, price: float, green: float):
        """被动接收电网数据（模拟：设备配置接口地址后由数据源推送到达）"""
        self.price_per_mwh = price
        self.green_ratio = green

    def status(self) -> dict:
        """上报状态：对齐 resource-table 的 compute 条目"""
        # 模拟运行中 GPU 占用（不改变可用量，这里上报"可用"算力）
        return {
            "resource_id": f"server/{self.node_id}",
            "resource_type": "compute",
            "status": "up",
            "price_per_mwh": self.price_per_mwh,
            "green_ratio": self.green_ratio,
            "avail_compute": {"gpu_count": self.gpu, "vram_gb": self.vram},
            "battery_soc": self.soc,
            "latency_ms": round(self.rng.uniform(15, 40), 1),
            "ttl": "300s",
            "trust_anchor": f"spiffe://open-compute-router.org/server/{self.node_id}",
            "reported_at": datetime.now().isoformat(),
        }


def main():
    parser = argparse.ArgumentParser(description="算力节点状态模拟器")
    parser.add_argument("--node-id", default="ap-southeast-02")
    parser.add_argument("--gpu", type=int, default=8)
    parser.add_argument("--vram", type=int, default=640)
    parser.add_argument("--loop", action="store_true", help="持续上报（默认 3 次）")
    parser.add_argument("--interval", type=float, default=1.0, help="上报间隔（秒）")
    args = parser.parse_args()

    node = ServerNode(args.node_id, args.gpu, args.vram)

    # 场景：白昼高峰 → 夜间负电价（模拟 30 分钟粒度两拍）
    market_scenes = [
        (280, 0.20, "白天高峰"),
        (-25, 0.92, "夜间负电价"),
    ]

    print(f"=== 算力节点在线：{node.node_id}（GPU x{node.gpu} / VRAM {node.vram}GB）===\n")

    rounds = -1 if args.loop else 3
    count = 0
    while rounds == -1 or count < rounds:
        price, green, label = market_scenes[count % len(market_scenes)]
        node.ingest_market(price, green)
        st = node.status()
        print(f"[{label}] {json.dumps(st, ensure_ascii=False)}")
        count += 1
        if rounds != -1 and count < rounds:
            time.sleep(args.interval)

    print("\n✅ 节点状态上报完成：供给侧\"在线证据\"可接入 demo-30s.py 或 resource-table-mock.py。")


if __name__ == "__main__":
    main()
