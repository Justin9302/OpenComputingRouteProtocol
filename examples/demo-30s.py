#!/usr/bin/env python3
"""
Open Compute Router - 30 秒 Demo 脚本（Phase 0 可录屏 pitch 素材）

故事线：提交一个 T9 低优训练任务 → OCR 调度器基于 AEMO 电价曲线
选择夜间负电价时段执行 → 展示"白天执行 vs 负电价执行"的电费差异。

配合 energy-price-mock.py 生成的电价曲线使用（缺失时自动生成）。

用法：
  python demo-30s.py
"""

import csv
import os
import subprocess
import sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
CSV_PATH = os.path.join(DATA_DIR, f"aemo_nsw1_{datetime.now().strftime('%Y%m%d')}.csv")

# 演示任务：T9 低优训练任务（可等待、可中断、追求最低成本）
DEMO_TASK = {
    "task_id": "train-llm-pretrain-002",
    "compute_intent": {"min_vram_gb": 640, "gpu_count": 8},
    "sla_scheduling": {
        "tier": "T9",
        "price_ceiling": 5.0,  # 任务总预算上限（AUD），对齐 RFC-002 §4.4 预算约束调度
        "currency": "AUD",
        "cost_optimization": "min_cost",
        "energy_preference": {"prefer_green": True},
    },
}

# 演示 GPU 集群规格（对应 server-node-mock 的节点）
CLUSTER = {"gpu": 8, "vram_gb": 640, "power_kw": 28}  # 8x H100 满载约 28kW

# 任务执行时长（小时），用于计算总成本
TASK_DURATION_HOURS = 2.0


def ensure_price_data() -> list:
    """确保电价曲线存在；缺失则调用 energy-price-mock 生成"""
    if not os.path.exists(CSV_PATH):
        subprocess.run([sys.executable, os.path.join(SCRIPT_DIR, "energy-price-mock.py")],
                       cwd=SCRIPT_DIR, check=True)
    rows = []
    with open(CSV_PATH) as f:
        for r in csv.DictReader(f):
            rows.append({"time": r["time"],
                         "price": float(r["price_per_mwh"]),
                         "green": float(r["green_ratio"])})
    return rows


def pick_execution_window(rows: list, budget_aud: float, power_kw: float,
                          duration_hours: float, min_len: int = 4,
                          optimize: str = "min_cost") -> list:
    """
    预算约束调度（对齐 RFC-002 §4.4）：
    对每个候选时段计算总成本 = 电价 × 功率 × 时长，
    筛选总成本 <= 预算的时段，按优化目标选择最优窗口。

    Args:
        rows: 电价曲线（time, price, green）
        budget_aud: 任务总预算（AUD）
        power_kw: 节点功率（kW）
        duration_hours: 任务执行时长（小时）
        min_len: 最小连续时段数
        optimize: 优化目标 min_cost / max_green / fastest
    """
    mwh = power_kw * duration_hours / 1000.0
    candidates = []
    window = []

    for r in rows:
        cost = r["price"] * mwh  # 该时段执行的电力成本（AUD，可为负）
        if cost <= budget_aud:
            window.append({**r, "cost": cost})
            if len(window) >= min_len:
                candidates.append(list(window))
        else:
            window = []

    if not candidates:
        return []

    if optimize == "min_cost":
        return min(candidates, key=lambda w: sum(r["cost"] for r in w) / len(w))
    elif optimize == "max_green":
        return max(candidates, key=lambda w: sum(r["green"] for r in w) / len(w))
    else:  # fastest
        return candidates[0]


def main():
    print("=" * 64)
    print("  OCR · 算电协同 30 秒 Demo：负电价时段跑 AI 训练")
    print("  （用时间换成本——T8-T10 低优任务的可延迟价值）")
    print("=" * 64)

    # 1. 电网数据（被动接收：设备配置接口地址即收到 AEMO 推送）
    rows = ensure_price_data()
    print(f"\n① 电网数据已接入（AEMO NSW1 模拟，48 个 NEM30 时段）")
    print(f"   负电价时段：{sum(1 for r in rows if r['price'] < 0)} 个")

    # 2. 提交任务
    task = DEMO_TASK
    print(f"\n② 提交任务：{task['task_id']}")
    print(f"   tier={task['sla_scheduling']['tier']}（低优，可延迟 24h）"
          f" min_vram={task['compute_intent']['min_vram_gb']}GB"
          f" budget={task['sla_scheduling']['price_ceiling']} AUD"
          f" optimize={task['sla_scheduling'].get('cost_optimization', 'min_cost')}")

    # 3. 调度决策：预算约束调度（RFC-002 §4.4）
    budget = task["sla_scheduling"]["price_ceiling"]
    optimize = task["sla_scheduling"].get("cost_optimization", "min_cost")
    window = pick_execution_window(
        rows, budget_aud=budget, power_kw=CLUSTER["power_kw"],
        duration_hours=TASK_DURATION_HOURS, optimize=optimize
    )
    if not window:
        print(f"\n⚠️ 今日无满足预算（{budget} AUD）的执行窗口，任务保持 PENDING（这正是'等电来'）")
        print("   建议：提高 price_ceiling 或放宽 SLA Tier 时间窗口")
        return

    exec_price = sum(r["price"] for r in window) / len(window)
    exec_green = sum(r["green"] for r in window) / len(window)
    exec_cost = sum(r["cost"] for r in window) / len(window)
    print(f"\n③ 调度决策（预算约束：{budget} AUD，优化目标={optimize}）：")
    print(f"   在 {window[0]['time']}–{window[-1]['time']} 执行"
          f"（均价 {exec_price:.1f} AUD/MWh，绿电 {exec_green:.0%}）")
    print(f"   预计电力成本：{exec_cost:+.2f} AUD（在预算 {budget} AUD 内）")

    # 4. 省钱对比：任务耗时 TASK_DURATION_HOURS、集群 28kW
    hours = TASK_DURATION_HOURS
    mwh = CLUSTER["power_kw"] * hours / 1000.0
    daytime_avg = 180.0  # 白昼高峰典型均价
    cost_window = mwh * exec_price
    cost_day = mwh * daytime_avg
    print(f"\n④ 成本对比（8xH100 × 2h ≈ {mwh:.2f} MWh）：")
    print(f"   白天高峰执行：{cost_day:+.2f} AUD（电价 {daytime_avg}）")
    print(f"   负电价时段：  {cost_window:+.2f} AUD（电价 {exec_price:.1f}，绿电 {exec_green:.0%}）")
    if exec_price < 0:
        print(f"   → 负电价执行 = 反向电费收入，比白天节省 {cost_day - cost_window:.2f} AUD")

    # 5. 资源表 / 独立性提示（承接 RFC-005）
    print("\n⑤ 全部调度基于设备本地资源表（RFC-005 设备级自治）：")
    print("   数据在设备层被动接收、独立维护资源表、无第三方平台聚合。")
    print("\n" + "=" * 64)
    print("  结束语：算力跟着电力走——等电来，用电省。")
    print("=" * 64)


if __name__ == "__main__":
    main()
