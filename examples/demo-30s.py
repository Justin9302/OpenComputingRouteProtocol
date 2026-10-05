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
import glob
import os
import subprocess
import sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")


def find_price_csv() -> tuple:
    """优先使用 data/ 下已有的 AEMO 曲线文件（最新修改优先）；
    不存在时调用 energy-price-mock 生成。返回 (csv 路径, 是否现场生成)。"""
    candidates = sorted(
        glob.glob(os.path.join(DATA_DIR, "aemo_nsw1_*.csv")),
        key=os.path.getmtime, reverse=True,
    )
    if candidates:
        return candidates[0], False
    subprocess.run([sys.executable, os.path.join(SCRIPT_DIR, "energy-price-mock.py")],
                   cwd=SCRIPT_DIR, check=True)
    return os.path.join(DATA_DIR, f"aemo_nsw1_{datetime.now().strftime('%Y%m%d')}.csv"), True

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


def read_price_csv(csv_path: str) -> list:
    """读取 AEMO 格式 CSV（time, price_per_mwh, green_ratio）
    green_ratio 缺失或为空时记 None（真实数据源通常无此列，不编造）。"""
    rows = []
    with open(csv_path) as f:
        for r in csv.DictReader(f):
            g = r.get("green_ratio", "").strip()
            rows.append({"time": r["time"],
                         "price": float(r["price_per_mwh"]),
                         "green": float(g) if g else None})
    return rows


def pick_execution_window(rows: list, budget_aud: float, power_kw: float,
                          duration_hours: float,
                          optimize: str = "min_cost") -> list:
    """
    预算约束调度（对齐 RFC-002 §4.4）：
    任务时长确定窗口长度（30 分钟粒度，2 时段/小时），
    枚举全部连续窗口，选择任务总成本 <= 预算且满足优化目标的最优窗口。

    Args:
        rows: 电价曲线（time, price, green）
        budget_aud: 任务总预算（AUD）
        power_kw: 节点功率（kW）
        duration_hours: 任务执行时长（小时）
        optimize: 优化目标 min_cost / max_green / fastest
    """
    mwh = power_kw * duration_hours / 1000.0       # 任务总用电量（MWh）
    slots_per_hour = max(1, len(rows) // 24)       # 数据粒度：12=5分钟(NEM结算)，2=30分钟(旧)
    window_len = max(1, int(round(duration_hours * slots_per_hour)))
    candidates = []
    for i in range(len(rows) - window_len + 1):
        w = rows[i:i + window_len]
        total_cost = (sum(r["price"] for r in w) / len(w)) * mwh  # 任务总电力成本 = 均价 × 总电量
        if total_cost <= budget_aud:
            has_green = w[0]["green"] is not None
            total_green = sum(r["green"] for r in w) if has_green else None
            candidates.append((total_cost, total_green, w))
    if not candidates:
        return []
    if optimize == "fastest":
        return candidates[0][2]  # 最早满足预算的窗口（按时间序首个）
    if optimize == "max_green" and any(c[1] is not None for c in candidates):
        candidates.sort(key=lambda t: (-t[1], t[0]))
    else:
        candidates.sort(key=lambda t: (t[0], -(t[1] if t[1] is not None else 0)))
    return candidates[0][2]


def main():
    print("=" * 64)
    print("  OCR · 算电协同 30 秒 Demo：负电价时段跑 AI 训练")
    print("  （用时间换成本——T8-T10 低优任务的可延迟价值）")
    print("=" * 64)

    # 1. 电网数据（被动接收：设备配置接口地址即收到 AEMO 推送）
    csv_path, generated_now = find_price_csv()
    rows = read_price_csv(csv_path)
    slots_per_hour = max(1, len(rows) // 24)
    grain = "5 分钟" if slots_per_hour == 12 else ("30 分钟" if slots_per_hour == 2 else f"{60//slots_per_hour} 分钟")
    if generated_now:
        print(f"\n① 电价曲线：本地无数据文件，已临时生成（AEMO 形态模拟，{len(rows)} 个时段，{grain}粒度）")
    else:
        print(f"\n① 电价曲线：加载本地文件 {os.path.basename(csv_path)}（{len(rows)} 个时段，{grain}粒度）")
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
    if optimize == "max_green" and all(r["green"] is None for r in rows):
        optimize = "min_cost"
        print(f"   ⚠️ 数据无绿电比例列，max_green 退化为 min_cost")
    window = pick_execution_window(
        rows, budget_aud=budget, power_kw=CLUSTER["power_kw"],
        duration_hours=TASK_DURATION_HOURS, optimize=optimize
    )
    if not window:
        print(f"\n⚠️ 今日无满足预算（{budget} AUD）的执行窗口，任务保持 PENDING（这正是'等电来'）")
        print("   建议：提高 price_ceiling 或放宽 SLA Tier 时间窗口")
        return

    exec_price = sum(r["price"] for r in window) / len(window)
    has_green = window[0]["green"] is not None
    exec_green = sum(r["green"] for r in window) / len(window) if has_green else None
    mwh = CLUSTER["power_kw"] * TASK_DURATION_HOURS / 1000.0
    exec_cost = mwh * exec_price  # 任务总电力成本（AUD，可为负）
    green_txt = f"{exec_green:.0%}" if has_green else "n/a"
    print(f"\n③ 调度决策（预算约束：{budget} AUD，优化目标={optimize}）：")
    print(f"   在 {window[0]['time']}–{window[-1]['time']} 执行"
          f"（均价 {exec_price:.1f} AUD/MWh，绿电 {green_txt}）")
    print(f"   预计电力成本：{exec_cost:+.2f} AUD（任务 {TASK_DURATION_HOURS}h 总成本，预算 {budget} AUD 内）")

    # 4. 省钱对比：任务耗时 TASK_DURATION_HOURS、集群 28kW
    hours = TASK_DURATION_HOURS
    top_peak = sorted(rows, key=lambda r: r["price"], reverse=True)[:8]
    daytime_avg = sum(r["price"] for r in top_peak) / len(top_peak)  # 数据集高峰 8 时段均值，不再硬编码
    cost_window = mwh * exec_price
    cost_day = mwh * daytime_avg
    print(f"\n④ 成本对比（8xH100 × 2h ≈ {mwh:.2f} MWh）：")
    print(f"   白天高峰执行：{cost_day:+.2f} AUD（电价 {daytime_avg:.0f}，取数据集高峰均值）")
    print(f"   负电价时段：  {cost_window:+.2f} AUD（电价 {exec_price:.1f}，绿电 {green_txt}）")
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
