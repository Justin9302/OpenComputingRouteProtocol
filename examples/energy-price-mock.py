#!/usr/bin/env python3
"""
Open Compute Router - AEMO 电价模拟器（Phase 0 Demo 资产）

生成一天 48 个 NEM30 时段（30 分钟粒度）的电价曲线，
包含澳洲 NEM 市场真实特征：
  - 夜间负电价（光伏/风电过剩、需求低谷）
  - 早/晚双峰（需求高峰）
  - 白天光伏压低中午电价（鸭型曲线）

输出：examples/data/aemo_nsw1_<yyyymmdd>.csv
      time,price_per_mwh,green_ratio

用法：
  python energy-price-mock.py            # 生成模拟电价曲线（含负电价）
  python energy-price-mock.py --real     # 预留：替换为 AEMO 真实历史数据
"""

import argparse
import csv
import os
import random
from datetime import datetime, timedelta

# 典型 NEM NSW1 冬季工作日曲线（30 分钟粒度，24h * 2 = 48 点）
# 单位 AUD/MWh；负值 = 负电价（绿电过剩时段）
# 由 AEMO 真实历史曲线抽象：深夜负电价、早峰、午间光伏压低、晚峰
PROFILE = [
    # 00:00-04:00 深夜（负电价，绿电过剩）
    -30, -25, -20, -15, -10, -5, 0, 5,
    # 04:00-08:00 早峰（需求启动）
    20, 45, 80, 120, 160, 210, 280, 300,
    # 08:00-12:00 上午（光伏爬坡压低电价）
    260, 220, 180, 140, 110, 90, 75, 60,
    # 12:00-16:00 正午（光伏高峰，价格压低甚至转负）
    50, 40, 30, 25, 20, 15, 10, 8,
    # 16:00-20:00 晚峰（光伏退坡 + 需求高峰）
    30, 80, 150, 240, 320, 360, 340, 280,
    # 20:00-24:00 夜间回落
    200, 140, 90, 50, 20, -5, -15, -25,
]

# 对应时段绿电比例（可再生发电占比，0~1）
GREEN_PROFILE = [
    0.92, 0.90, 0.88, 0.85, 0.82, 0.78, 0.72, 0.65,
    0.55, 0.45, 0.38, 0.35, 0.32, 0.30, 0.28, 0.30,
    0.35, 0.40, 0.48, 0.55, 0.62, 0.70, 0.76, 0.80,
    0.82, 0.84, 0.86, 0.88, 0.90, 0.92, 0.94, 0.95,
    0.96, 0.95, 0.94, 0.92, 0.90, 0.88, 0.85, 0.80,
    0.72, 0.60, 0.50, 0.40, 0.32, 0.28, 0.25, 0.22,
]


def generate_profile(seed: int = 42) -> list:
    """为曲线加入少量随机扰动，模拟真实市场波动"""
    rng = random.Random(seed)
    noisy = []
    for price, green in zip(PROFILE, GREEN_PROFILE):
        jitter = rng.uniform(-15, 15)
        noisy.append((round(price + jitter, 1), round(min(max(green + rng.uniform(-0.03, 0.03), 0), 1), 3)))
    return noisy


def write_csv(times: list, profile: list, date: str) -> str:
    out_dir = os.path.join(os.path.dirname(__file__), "data")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"aemo_nsw1_{date}.csv")
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["time", "price_per_mwh", "green_ratio"])
        for t, (price, green) in zip(times, profile):
            writer.writerow([t.strftime("%H:%M"), price, green])
    return path


def main():
    parser = argparse.ArgumentParser(description="AEMO 电价模拟器")
    parser.add_argument("--real", action="store_true",
                        help="预留：从 AEMO 开放数据接入真实历史电价（Phase 1 替换 mock）")
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"),
                        help="数据日期（用于输出文件名）")
    args = parser.parse_args()

    if args.real:
        print("⚠️  --real 尚未实现：Phase 1 将从 AEMO REST API 拉取真实历史电价并替换本 mock。")
        print("   接入点：https://aemo.com.au/energy-data/electricity/aggregated-data（PRICE_DISPATCH 表）")
        return

    start = datetime(2026, 8, 10, 0, 0)
    times = [start + timedelta(minutes=30 * i) for i in range(48)]
    profile = generate_profile()

    path = write_csv(times, profile, args.date)
    print(f"✅ 已生成 AEMO 模拟电价曲线：{path}（48 个 NEM30 时段）")
    print(f"   负电价时段：{sum(1 for p, _ in profile if p < 0)} 个")
    print(f"   最高价：{max(p for p, _ in profile):.0f} AUD/MWh，最低价：{min(p for p, _ in profile):.0f} AUD/MWh")

    print("\n   时段曲线摘要（每 6 个时段一行）：")
    for i in range(0, 48, 6):
        chunk = profile[i:i + 6]
        seg = "  ".join(f"{p:>6.0f}" for p, _ in chunk)
        print(f"   {times[i].strftime('%H:%M')} | {seg}")

    print("\n   用法提示：供 demo-30s.py / server-node-mock.py 消费。")


if __name__ == "__main__":
    main()
