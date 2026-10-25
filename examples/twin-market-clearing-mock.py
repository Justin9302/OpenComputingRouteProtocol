#!/usr/bin/env python3
"""twin-market-clearing-mock.py — RFC-012 算力×电力联合出清 mock。

演示"双子市场对敲 + 设施物理约束"的最小可运行实现：
- 用户声明的 physical_profile（PUE/冷却τ/热储能/爬坡/供电包络）
- 电力侧价格曲线（含负电价窗口，对齐 NEM 场景）
- 耦合约束：每时隙算力×单位功率 ≤ 供电包络 + 储能放电 + 光伏
- 爬坡限速 + 负电价死区（防振荡）
- 联立出清：在约束内选成本最优时隙，输出排程 + 影子价格

运行：python examples/twin-market-clearing-mock.py
"""
from __future__ import annotations
import json


# --------------------------------------------------------------------------- #
# 1. 用户声明的设施物理模型（非 OCR 测算，RFC-012 §4 / physical-profile.schema）
# --------------------------------------------------------------------------- #
PHYSICAL_PROFILE = {
    "declared_by": "config-engineer-a",
    "source": "estimate",           # estimate=经验公式初估，运行后按实测修正为 calibrated
    "confidence": 0.6,
    "pue_nominal": 1.18,            # 冷板液冷目标
    "pue_load_model": "inverse_overhead",
    "cooling_tau_min": 10,          # 冷却热时间常数（分钟）
    "thermal_storage_kwh": 700.0,   # 热储能（蓄冷/建筑热质量），第二种储能
    "ramp_limit_pct_per_min": 10.0, # 电爬坡上限（%额定功率/分钟）
    "ramp_limit_thermal_kw_per_min": 80.0,  # 热爬坡上限（kW/分钟）
    "power_envelope_kw": 1200.0,    # 供电包络 = min(合同/变压器/断路器) − 非计算负载预留
    "unit_power_kw": 4.5,           # 单位算力功率（kW/卡）
}

# --------------------------------------------------------------------------- #
# 2. 电力侧：价格曲线（AUD/MWh，含负电价窗口）+ 储能/光伏可用（自有资产集合）
# --------------------------------------------------------------------------- #
# 时隙粒度 30min；为演示取 24 时隙 = 12h。负电价集中在夜间（风电/光伏过剩，NEM 场景）。
PRICE_AUD_PER_MWH = [  # 每时隙电价
    40, 35, 30, 25, -20, -40, -55, -60,   # 00:00-04:00（负电价窗口）
    -45, -25, 10, 15, 40, 55, 70, 65,     # 04:00-08:00
    50, 45, 40, 35, 30, 35, 45, 55,       # 08:00-12:00
]
# 每时隙自有资产集合：储能可放(kW) + 光伏可发(kW)，构成电力侧"可对敲集合"
STORAGE_DISCHARGE_KW = [300, 300, 300, 300, 200, 200, 150, 100,
                        100, 150, 200, 250, 250, 250, 250, 250,
                        250, 250, 250, 250, 250, 250, 250, 250]
PV_KW = [0, 0, 0, 0, 0, 0, 10, 30, 80, 150, 250, 300,
         320, 320, 300, 250, 150, 80, 30, 10, 0, 0, 0, 0]

# --------------------------------------------------------------------------- #
# 3. 需求侧：一个 T8 批量训练任务（8h 窗口内跑完，等负电价）
# --------------------------------------------------------------------------- #
TASK = {
    "task_id": "t-batch-20261026-01",
    "tier": "T8",                    # 软计划，仅在低价/负电价窗口执行
    "gpu_count": 64,                 # 64 卡
    "power_kw": 64 * 4.5,            # 64 × 单位功率 = 288 kW
    "duration_slots": 4,             # 需要连跑 4 个时隙（2h）
    "max_cost_aud": 150.0,           # 预算上限（总成本）
}

N_SLOTS = len(PRICE_AUD_PER_MWH)
DELTA_H = 0.5                        # 每时隙小时数（30min）
RATED_KW = PHYSICAL_PROFILE["power_envelope_kw"]


# --------------------------------------------------------------------------- #
# 4. 设施物理约束
# --------------------------------------------------------------------------- #
def facility_available_kw(slot: int, prev_running_kw: float) -> float:
    """返回该时隙"算力可用的总功率"，受三层物理约束：供电包络、储能+光伏、爬坡。"""
    p = PHYSICAL_PROFILE

    # 4a. 耦合约束：可用电力 = 电网导入包络（按负载率打折，低负载 PUE 更差→可用更紧）
    #     PUE(L)=1+(PUE_nominal-1)/L → 低负载每瓦 IT 更贵，等价于可用功率收紧
    load_factor = max(prev_running_kw / RATED_KW, 0.1)
    pue = 1.0 + (p["pue_nominal"] - 1.0) / load_factor
    grid_capable_kw = RATED_KW / pue            # 扣除冷却/配电开销后的 IT 可用

    # 4b. 自有资产集合（储能 + 光伏）
    asset_kw = STORAGE_DISCHARGE_KW[slot] + PV_KW[slot]

    # 4c. 爬坡限速（电爬坡与热爬坡取小）
    ramp_e = RATED_KW * p["ramp_limit_pct_per_min"] / 100.0 * DELTA_H * 60.0  # 每时隙可爬功率
    ramp_t = p["ramp_limit_thermal_kw_per_min"] * DELTA_H * 60.0
    ramp = min(ramp_e, ramp_t)
    ramp_capable = prev_running_kw + ramp       # 相对上一时隙只能爬这么多

    return max(min(grid_capable_kw + asset_kw, ramp_capable), 0.0)


# --------------------------------------------------------------------------- #
# 5. 负电价死区 + 迟滞（防振荡）：只有显著负电价才切入，电价回升才退出
# --------------------------------------------------------------------------- #
ENTER_PRICE = -10.0   # 低于 -10 AUD/MWh 才切入
EXIT_PRICE = -5.0     # 高于 -5 AUD/MWh 退出（迟滞带，避免抖振）


def eligible(slot: int) -> bool:
    return PRICE_AUD_PER_MWH[slot] < ENTER_PRICE


# --------------------------------------------------------------------------- #
# 6. 联立出清：滑动窗口找"成本最优且满足物理约束的连续时段"
# --------------------------------------------------------------------------- #
def clear():
    schedule = []      # [(slot, price, energy_kwh, cost)]
    prev_kw = 0.0
    best = None

    for start in range(N_SLOTS - TASK["duration_slots"] + 1):
        window = list(range(start, start + TASK["duration_slots"]))
        if not all(eligible(s) for s in window):
            continue
        # 逐时隙校验物理约束（爬坡累计）
        ok = True
        cost = 0.0
        energy_kwh_total = 0.0
        kw_track = 0.0
        for i, s in enumerate(window):
            cap = facility_available_kw(s, kw_track if i else 0.0)
            if cap < TASK["power_kw"]:
                ok = False
                break
            e_kwh = TASK["power_kw"] * DELTA_H
            # 负电价：能量成本 = 电价 × 电量（负值 = 倒贴，即赚钱）
            cost += PRICE_AUD_PER_MWH[s] / 1000.0 * e_kwh
            energy_kwh_total += e_kwh
            kw_track = TASK["power_kw"]
        if not ok or cost > TASK["max_cost_aud"]:
            continue
        # 影子价格 ≈ 该窗口的最低结算价（稀缺度信号）
        shadow = min(PRICE_AUD_PER_MWH[s] for s in window)
        cand = (start, cost, energy_kwh_total, shadow)
        if best is None or cand[1] < best[1]:
            best = cand

    if best is None:
        return None
    start, cost, e, shadow = best
    prev = 0.0
    for s in range(start, start + TASK["duration_slots"]):
        cap = facility_available_kw(s, prev)
        schedule.append({
            "slot": s,
            "start": f"{s*30//60:02d}:{s*30%60:02d}",
            "price_aud_per_mwh": PRICE_AUD_PER_MWH[s],
            "power_kw": TASK["power_kw"],
            "facility_capable_kw": round(cap, 1),
            "energy_kwh": TASK["power_kw"] * DELTA_H,
        })
        prev = TASK["power_kw"]
    return {"schedule": schedule, "total_cost_aud": round(cost, 2),
            "energy_kwh": round(e, 1), "shadow_price_aud_per_mwh": shadow}


if __name__ == "__main__":
    print("=== RFC-012 联合出清 mock：算力 × 电力 × 设施物理 ===")
    print(f"任务: {TASK['task_id']} | Tier {TASK['tier']} | {TASK['gpu_count']}卡 = {TASK['power_kw']:.0f}kW | 需 {TASK['duration_slots']} 时隙")
    print(f"物理模型(用户声明, {PHYSICAL_PROFILE['source']}): PUE_nom={PHYSICAL_PROFILE['pue_nominal']} τ={PHYSICAL_PROFILE['cooling_tau_min']}min "
          f"包络={PHYSICAL_PROFILE['power_envelope_kw']}kW 爬坡={PHYSICAL_PROFILE['ramp_limit_pct_per_min']}%/min")
    print(f"负电价死区: 切入<{ENTER_PRICE} AUD/MWh, 退出>{EXIT_PRICE} AUD/MWh\n")

    r = clear()
    if r is None:
        print("❌ 无满足物理约束且低于预算的负电价窗口（任务保持 PENDING，等电来）")
    else:
        print(f"✅ 出清窗口: 共 {len(r['schedule'])} 时隙 | 总成本 {r['total_cost_aud']} AUD (负=倒贴赚钱) "
              f"| 能耗 {r['energy_kwh']} kWh | 影子价格 {r['shadow_price_aud_per_mwh']} AUD/MWh\n")
        print(f"{'时隙':<4}{'时刻':<8}{'电价(AUD/MWh)':<16}{'算力(kW)':<12}{'设施可用(kW)':<16}{'能耗(kWh)'}")
        for s in r["schedule"]:
            print(f"{s['slot']:<6}{s['start']:<10}{s['price_aud_per_mwh']:<18}{s['power_kw']:<14}"
                  f"{s['facility_capable_kw']:<18}{s['energy_kwh']:<10}")

    # 输出一份 capability_report 参考 JSON（含 physical_profile），供 schema 校验
    print("\n=== capability_report 参考（含 physical_profile）===")
    report = {
        "type": "capability_report",
        "router_id": "nsw1-rack-a",
        "report_id": "cr-20261026-001",
        "generated_at": "2026-10-26T00:00:00+11:00",
        "granularity_min": 30,
        "slots": [{"start": "2026-10-26T00:00:00+11:00", "end": "2026-10-26T00:30:00+11:00",
                   "avail_kw": 800, "hard_kw": 500, "gpu_type": "H100", "gpu_count": 32,
                   "price_floor_aud_per_mwh": -60.0, "green_ratio": 0.62, "maintenance": False}],
        "committed": [],
        "physical_profile": PHYSICAL_PROFILE,
        "version": 1,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
