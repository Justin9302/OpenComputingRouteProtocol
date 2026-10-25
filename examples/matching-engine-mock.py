#!/usr/bin/env python3
"""
matching-engine-mock.py — RFC-008/012 撮合与联合出清 mock（撮合闭环）。

把"供给侧能力上报 → 需求侧 intent → 撮合出清 → reserve/commit → 排程+影子价"跑成可演示闭环，
使用已下载的真实 AEMO 电价数据（examples/data/aemo_nsw1_*.csv）做演示，不依赖 AEMO 实时 API。

撮合逻辑对齐：
- RFC-008 §4：候选过滤(硬约束) → 权重排序(软约束) → 模式选择 → 无达标窗口→PENDING
- RFC-012：耦合约束(供电包络/PUE/爬坡) + 负电价死区(防振荡) + 影子价格产出
- RFC-007：供给侧能力上报（复用 capability-report-mock）

用法：
  python examples/matching-engine-mock.py            # 运行一个 T8 批量任务的撮合闭环
  python examples/matching-engine-mock.py --save    # 顺带保存 capability_report.json
"""

import argparse
import importlib.util
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 复用供给侧能力上报（文件名含连字符，用 importlib 按路径加载）
_cap_spec = importlib.util.spec_from_file_location(
    "capability_report_mock", os.path.join(SCRIPT_DIR, "capability-report-mock.py"))
cap = importlib.util.module_from_spec(_cap_spec)
_cap_spec.loader.exec_module(cap)


# --------------------------------------------------------------------------- #
# 需求侧：一个 T8 批量训练任务（等负电价，追求最低成本）
# --------------------------------------------------------------------------- #
INTENT = {
    "task_id": "t-batch-20261026-01",
    "compute_intent": {"gpu_count": 8, "preferred_arch": ["gpu_nvidia"]},
    "sla_scheduling": {
        "tier": "T8",               # 软计划，仅低价/负电价窗口执行
        "price_ceiling": 25.0,      # 任务总预算上限（AUD）
        "currency": "AUD",
        "cost_optimization": "min_cost",
    },
    "energy_preference": {"avoid_peak_price": True},
}

CLUSTER_POWER_KW = 8 * 4.5          # 8 卡 × 单位功率 4.5kW = 36kW（匹配 physical_profile.unit_power_kw）
DURATION_HOURS = 1.0                # 任务时长 1h = 12 个 5 分钟时隙

# 防振荡（RFC-012 §5）：负电价死区 + 迟滞
ENTER_PRICE = -5.0                  # 电价 < -5 AUD/MWh 才切入
EXIT_PRICE = 0.0                    # 电价 > 0 退出（迟滞带，避免抖振）


def facility_available_kw(prev_kw, pp, price):
    """RFC-012 §4 耦合约束：供电包络(按 PUE 收紧) + 自有资产 + 爬坡限速。"""
    rated = pp["power_envelope_kw"]
    load_factor = max(prev_kw / rated, 0.1)
    pue = 1.0 + (pp["pue_nominal"] - 1.0) / load_factor      # 低负载每瓦 IT 更贵
    grid_capable = rated / pue                               # 扣除冷却/配电后的 IT 可用
    # 储能/光伏按电价联动：负电价时段储能倾向放电供计算（自有资产集合，简化）
    asset = 200.0 if price < 0 else 0.0
    # 爬坡限速（电/热取小；ramp_limit_pct_per_min = 每分钟 %额定，5 分钟时隙 ×5）
    ramp_e = rated * pp["ramp_limit_pct_per_min"] / 100.0 * 5
    ramp_t = pp["ramp_limit_thermal_kw_per_min"] * 5
    ramp_capable = prev_kw + min(ramp_e, ramp_t)
    return max(min(grid_capable + asset, ramp_capable), 0.0)


def run_closed_loop(save: bool = False):
    csv_path = cap.find_price_csv()
    if not csv_path:
        print("❌ data/ 下无 aemo_nsw1_*.csv")
        return
    price_rows = cap.load_price_rows(csv_path)
    report = cap.build_capability_report(csv_path)
    pp = report["physical_profile"]
    slots = report["slots"]

    # 校验报价/死区阈值在市场界限内（国家标准 MFP=-1000, MPC=20300）
    for name, v in [("机房 price_floor", cap.FACILITY["price_floor_aud_per_mwh"]),
                    ("死区 ENTER", ENTER_PRICE), ("死区 EXIT", EXIT_PRICE)]:
        if not (cap.MARKET_PRICE_FLOOR <= v <= cap.MARKET_PRICE_CAP):
            raise ValueError(f"{name}={v} 超出 AEMO 市场价格界限 "
                             f"[{cap.MARKET_PRICE_FLOOR}, {cap.MARKET_PRICE_CAP}] AUD/MWh")

    print("=" * 66)
    print("  OCR 撮合闭环 mock：能力上报 → intent → 撮合出清 → commit")
    print("  （RFC-007 供给侧 × RFC-008 撮合 × RFC-012 联合出清，真实 AEMO 电价演示）")
    print("=" * 66)

    # ① 供给侧能力上报
    print(f"\n① 能力上报（RFC-007）：机房 {report['router_id']} | {len(slots)} 时段 @ {report['granularity_min']}min")
    print(f"   市场标准（AER/AEMC）: 价格下限 MFP={cap.MARKET_PRICE_FLOOR:.0f} AUD/MWh, "
          f"价格上限 MPC={cap.MARKET_PRICE_CAP:.0f} AUD/MWh")
    print(f"   GPU {cap.FACILITY['gpu_type']}×{cap.FACILITY['gpu_count']} | 包络 {pp['power_envelope_kw']}kW | "
          f"机房愿受价格下限 {cap.FACILITY['price_floor_aud_per_mwh']} AUD/MWh | 物理模型({pp['source']})")

    # ② 需求侧 intent
    t = INTENT
    print(f"\n② 需求 intent（RFC-001/002）：{t['task_id']}")
    print(f"   Tier {t['sla_scheduling']['tier']} | {t['compute_intent']['gpu_count']} 卡 ≈ {CLUSTER_POWER_KW:.0f}kW | "
          f"预算 {t['sla_scheduling']['price_ceiling']} AUD | 目标 {t['sla_scheduling']['cost_optimization']}")

    # ③ 撮合出清：候选过滤 → 物理约束 → 负电价死区 → 权重排序
    mwh = CLUSTER_POWER_KW * DURATION_HOURS / 1000.0
    window_len = int(DURATION_HOURS * 12)   # 5 分钟粒度，1h=12 时隙
    candidates = []
    for i in range(len(slots) - window_len + 1):
        w = slots[i:i + window_len]
        ok = True
        prev_kw = 0.0
        cost = 0.0
        for k in range(window_len):
            s = w[k]
            price = price_rows[i + k]["price"]
            if s["maintenance"]:
                ok = False; break
            if s["avail_kw"] < CLUSTER_POWER_KW:
                ok = False; break
            if price < cap.FACILITY["price_floor_aud_per_mwh"]:
                ok = False; break                      # 机房不愿接
            cap_kw = facility_available_kw(prev_kw, pp, price)
            if cap_kw < CLUSTER_POWER_KW:
                ok = False; break                      # 物理约束不足（RFC-012 耦合约束）
            cost += price / 1000.0 * (CLUSTER_POWER_KW * 5 / 60.0)
            prev_kw = CLUSTER_POWER_KW
        if not ok:
            continue
        if not all(price_rows[j]["price"] < ENTER_PRICE for j in range(i, i + window_len)):
            continue                                   # 负电价死区：整个窗口都在死区下才切入
        if cost > t["sla_scheduling"]["price_ceiling"]:
            continue                                   # 预算约束
        candidates.append((cost, i))

    # ④ 无达标窗口 → PENDING（等电来）
    if not candidates:
        print("\n⚠️ 今日无满足物理约束 + 负电价死区 + 预算的执行窗口 → 任务 PENDING（'等电来'）")
        print("   建议：提高 price_ceiling / 放宽死区阈值 / 缩短任务时长")
        return

    candidates.sort(key=lambda c: c[0])                 # min_cost
    cost, start = candidates[0]
    shadow = min(price_rows[j]["price"] for j in range(start, start + window_len))

    # ⑤ reserve → commit（RFC-008 §5 防超卖）
    reserved = slots[start:start + window_len]
    for s in reserved:
        s["committed_kw"] = CLUSTER_POWER_KW
    print(f"\n③ 撮合出清（RFC-008 + RFC-012 联合出清）:")
    print(f"   候选窗口 {len(candidates)} 个 → 最优: 在 {price_rows[start]['time']}–"
          f"{price_rows[start+window_len-1]['time']} 执行")
    print(f"   总电力成本 {cost:+.2f} AUD（预算 {t['sla_scheduling']['price_ceiling']} AUD 内）| "
          f"影子价格 {shadow} AUD/MWh")
    print(f"   防超卖：已 reserve→committed 锁定 {len(reserved)} 时隙（新撮合不得触碰）")

    # ⑥ 结算钩子（RFC-008 §7 / RFC-011 接口）
    neg_win = [price_rows[j]["price"] for j in range(start, start + window_len)]
    print(f"\n④ 结算钩子（计量回传中心化结算中心，RFC-012 §8）：")
    print(f"   settlement: task={t['task_id']} slots={len(reserved)} "
          f"avg_price={sum(neg_win)/len(neg_win):+.1f} AUD/MWh energy={mwh:.3f} MWh cost={cost:+.2f} AUD")
    if cost < 0:
        print(f"   → 负电价执行 = 反向电费收入 {cost:+.2f} AUD（算力跟着电力走，用时间换成本）")

    # ⑦ 数据源标注
    print(f"\n⑤ 数据源: {os.path.basename(csv_path)}（AEMO NSW1 真实电价，登记于 DATA_SOURCES.md）")

    if save:
        os.makedirs(os.path.dirname(cap.OUT_JSON), exist_ok=True)
        import json
        with open(cap.OUT_JSON, "w") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"✅ capability_report 已保存: {cap.OUT_JSON}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true", help="顺带保存 capability_report.json")
    args = ap.parse_args()
    run_closed_loop(save=args.save)
