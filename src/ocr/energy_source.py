"""
电网数据推送源（Energy Source）

对齐 RFC-004 §3.4 设备级被动接收：
数据源通过发布-订阅/推送模型到达设备，设备被动接收，无需主动轮询。

本模块模拟 AEMO 电价数据源，通过 UDP 向 OCR 节点推送电价和绿电比例。
节点收到后更新资源表的 energy_grid 类型表项，并向邻居扩散。
"""

import asyncio
import csv
import json
import logging
import argparse
import os
import random
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

from .protocol import Message, ResourceEntry, make_advertisement

logger = logging.getLogger("ocr.energy_source")

# 默认模拟电价曲线（NEM NSW1 特征，30分钟粒度）
DEFAULT_PROFILE = [
    # 00:00-04:00 深夜（负电价，绿电过剩）
    -30, -25, -20, -15, -10, -5, 0, 5,
    # 04:00-08:00 早峰
    20, 45, 80, 120, 160, 210, 280, 300,
    # 08:00-12:00 上午（光伏爬坡压低电价）
    260, 220, 180, 140, 110, 90, 75, 60,
    # 12:00-16:00 正午（光伏高峰）
    50, 40, 30, 25, 20, 15, 10, 8,
    # 16:00-20:00 晚峰
    30, 80, 150, 240, 320, 360, 340, 280,
    # 20:00-24:00 夜间回落
    200, 140, 90, 50, 20, -5, -15, -25,
]

DEFAULT_GREEN_PROFILE = [
    0.92, 0.90, 0.88, 0.85, 0.82, 0.78, 0.72, 0.65,
    0.55, 0.45, 0.38, 0.35, 0.32, 0.30, 0.28, 0.30,
    0.35, 0.40, 0.48, 0.55, 0.62, 0.70, 0.76, 0.80,
    0.82, 0.84, 0.86, 0.88, 0.90, 0.92, 0.94, 0.95,
    0.96, 0.95, 0.94, 0.92, 0.90, 0.88, 0.85, 0.80,
    0.72, 0.60, 0.50, 0.40, 0.32, 0.28, 0.25, 0.22,
]


class EnergySource:
    """
    模拟电网数据源，向 OCR 节点推送电价数据。
    通过 UDP 发送 ResourceAdvertisement 消息，节点被动接收。
    """

    def __init__(self, source_id: str = "aemo-nsw1", region: str = "NSW1"):
        self.source_id = source_id
        self.region = region
        self._index = 0
        self._transport = None
        self._running = False

    def load_csv(self, csv_path: str) -> List[Dict]:
        """从 CSV 加载电价数据"""
        rows = []
        if not os.path.exists(csv_path):
            logger.warning(f"CSV not found: {csv_path}, using default profile")
            return []
        with open(csv_path) as f:
            for r in csv.DictReader(f):
                rows.append({
                    "time": r.get("time", ""),
                    "price": float(r.get("price_per_mwh", 0)),
                    "green": float(r.get("green_ratio", 0)),
                })
        logger.info(f"Loaded {len(rows)} price points from {csv_path}")
        return rows

    def generate_mock(self, seed: int = 42) -> List[Dict]:
        """生成模拟电价曲线"""
        rng = random.Random(seed)
        rows = []
        base_time = datetime(2026, 1, 1, 0, 0)
        for i, (price, green) in enumerate(zip(DEFAULT_PROFILE, DEFAULT_GREEN_PROFILE)):
            jitter = rng.uniform(-15, 15)
            t = base_time + timedelta(minutes=30 * i)
            rows.append({
                "time": t.strftime("%H:%M"),
                "price": round(price + jitter, 1),
                "green": round(min(max(green + rng.uniform(-0.03, 0.03), 0), 1), 3),
            })
        return rows

    def make_entry(self, price: float, green: float) -> ResourceEntry:
        """构造 energy_grid 类型的资源表项"""
        return ResourceEntry(
            resource_id=f"energy_grid/{self.region}",
            resource_type="energy_grid",
            status="up",
            price_per_mwh=price,
            green_ratio=green,
            avail_compute={},
            latency_ms=5,
            ttl="300s",
            trust_anchor=f"spiffe://open-compute-router.org/grid/{self.region}",
            origin_node=self.source_id,
        )

    async def push_to(self, target_addr: str, price: float, green: float):
        """向目标节点推送一条电价数据"""
        # 确保 transport 已创建（允许单独调用 push_to 而不经过 run）
        if self._transport is None:
            loop = asyncio.get_event_loop()
            self._transport, _ = await loop.create_datagram_endpoint(
                lambda: asyncio.DatagramProtocol(),
                local_addr=("0.0.0.0", 0),
            )

        entry = self.make_entry(price, green)
        msg = make_advertisement(
            sender_id=self.source_id,
            entries=[entry],
            path_vector=[self.source_id],
        )
        if self._transport:
            try:
                host, port = target_addr.rsplit(":", 1)
                data = msg.to_json().encode("utf-8")
                self._transport.sendto(data, (host, int(port)))
                logger.info(f"[{self.source_id}] Pushed price={price:+.0f} green={green:.0%} to {target_addr}")
            except Exception as e:
                logger.warning(f"[{self.source_id}] Push failed: {e}")

    async def run(self, targets: List[str], data: List[Dict],
                  interval: float = 5.0, loop: bool = False):
        """
        运行推送源

        Args:
            targets: 目标节点地址列表 ["host:port", ...]
            data: 电价数据点列表
            interval: 推送间隔（秒）
            loop: 是否循环推送（模拟实时更新）
        """
        if not data:
            data = self.generate_mock()

        loop_obj = asyncio.get_event_loop()
        self._transport, _ = await loop_obj.create_datagram_endpoint(
            lambda: asyncio.DatagramProtocol(),
            local_addr=("0.0.0.0", 0),
        )
        self._running = True

        logger.info(f"[{self.source_id}] Energy source started. Targets: {targets}")
        logger.info(f"[{self.source_id}] {len(data)} data points, interval={interval}s, loop={loop}")

        idx = 0
        while self._running:
            if idx >= len(data):
                if loop:
                    idx = 0
                else:
                    break

            point = data[idx]
            for target in targets:
                await self.push_to(target, point["price"], point["green"])

            idx += 1
            await asyncio.sleep(interval)

        self._running = False
        if self._transport:
            self._transport.close()
        logger.info(f"[{self.source_id}] Energy source stopped")

    def stop(self):
        self._running = False


def main():
    parser = argparse.ArgumentParser(description="OCR 电网数据推送源（模拟 AEMO）")
    parser.add_argument("--source-id", default="aemo-nsw1", help="数据源标识")
    parser.add_argument("--region", default="NSW1", help="电网区域")
    parser.add_argument("--targets", required=True, help="目标节点地址，逗号分隔")
    parser.add_argument("--csv", default="", help="电价数据 CSV 文件路径")
    parser.add_argument("--interval", type=float, default=5.0, help="推送间隔（秒）")
    parser.add_argument("--loop", action="store_true", help="循环推送（模拟实时更新）")
    parser.add_argument("--log-level", default="INFO", help="日志级别")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    targets = [t.strip() for t in args.targets.split(",") if t.strip()]
    source = EnergySource(source_id=args.source_id, region=args.region)

    data = source.load_csv(args.csv) if args.csv else source.generate_mock()

    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(source.run(targets, data, args.interval, args.loop))
    except KeyboardInterrupt:
        source.stop()


if __name__ == "__main__":
    main()
