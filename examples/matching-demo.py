#!/usr/bin/env python3
"""
matching-demo.py — OCR 撮合闭环 30 秒 Pitch（可录屏素材）。

把"供给侧能力上报 → 需求 intent → 联合出清 → 负电价窗口执行 → 省钱对比"串成一个完整叙事，
用已下载的真实 AEMO 电价（examples/data/aemo_nsw1_*.csv）演示，不依赖实时 API。

故事主线：一个 T8 批量训练任务，OCR 在物理约束内自动找到夜间负电价窗口执行，
白天要付的钱 → 变成反向电费收入。一句话：算力跟着电力走，用时间换成本。

用法：
  python examples/matching-demo.py
"""

import importlib.util
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 复用供给侧能力上报 + 撮合核心（文件名含连字符，用 importlib 加载）
def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPT_DIR, fname))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

cap = _load("capability_report_mock", "capability-report-mock.py")
eng = _load("matching_engine_mock", "matching-engine-mock.py")


def main():
    csv_path = cap.find_price_csv()
    if not csv_path:
        print("❌ data/ 下无 aemo_nsw1_*.csv")
        return
    price_rows = cap.load_price_rows(csv_path)
    report = cap.build_capability_report(csv_path)
    pp = report["physical_profile"]
    slots = report["slots"]
    task = eng.INTENT
    kw = eng.CLUSTER_POWER_KW
    dur = eng.DURATION_HOURS

    r = eng.find_best_window(price_rows, slots, pp, kw, dur,
                             task["sla_scheduling"]["price_ceiling"])
    if r is None:
        print("今日无负电价窗口满足约束 → 任务 PENDING（等电来）")
        return
    start, cost = r["start"], r["cost"]
    mwh = r["mwh"]
    window = price_rows[start:start + r["window_len"]]
    window_avg = sum(p["price"] for p in window) / len(window)

    # 白天高峰对比：取全天最高 8 个时隙均值（对齐 demo-30s 口径）
    peak = sorted(price_rows, key=lambda p: p["price"], reverse=True)[:8]
    peak_avg = sum(p["price"] for p in peak) / len(peak)
    cost_peak = mwh * peak_avg

    print("=" * 64)
    print("  OCR · 撮合闭环 30 秒 Pitch")
    print("  算力跟着电力走——用时间换成本")
    print("=" * 64)

    # 1 能力上报
    print(f"\n① 机房上报能力（RFC-007）")
    print(f"   机房 {report['router_id']}：H100 × {cap.FACILITY['gpu_count']}，"
          f"包络 {pp['power_envelope_kw']}kW，PUE {pp['pue_nominal']}")
    print(f"   市场标准（AER/AEMC）：电价下限 MFP={cap.MARKET_PRICE_FLOOR:.0f}，"
          f"上限 MPC={cap.MARKET_PRICE_CAP:.0f} AUD/MWh")
    print(f"   今日 {len(price_rows)} 个时隙，负电价 {sum(1 for p in price_rows if p['price']<0)} 个"
          f"（全天最长负电价 {self_longest(price_rows):.1f}h）")

    # 2 需求
    print(f"\n② 提交任务（RFC-001/002）")
    print(f"   Tier {task['sla_scheduling']['tier']} 批量训练｜{task['compute_intent']['gpu_count']} 卡"
          f" ≈ {kw:.0f}kW｜预算 {task['sla_scheduling']['price_ceiling']} AUD")

    # 3 撮合
    print(f"\n③ 联合出清（RFC-008 × RFC-012）")
    print(f"   候选窗口 {r['candidate_count']} 个，含物理约束（PUE/爬坡/包络）+ 负电价死区防振荡")
    print(f"   → 选定 {window[0]['time']}–{window[-1]['time']} 执行，"
          f"均价 {window_avg:+.1f} AUD/MWh，影子价 {r['shadow']} AUD/MWh")
    print(f"   已 reserve→committed 锁定 {r['window_len']} 时隙（防超卖）")

    # 4 省钱对比
    print(f"\n④ 成本对比（{kw:.0f}kW × {dur}h ≈ {mwh:.2f} MWh）")
    print(f"   白天高峰执行：{cost_peak:+.2f} AUD（均价 {peak_avg:+.0f}）")
    print(f"   负电价窗口：  {cost:+.2f} AUD（均价 {window_avg:+.0f}）")
    if cost < 0:
        print(f"   → 白天省 {cost_peak - cost:.2f} AUD，且负电价执行 = 反向电费收入 {cost:+.2f} AUD")

    # 5 收口
    print(f"\n⑤ 数据源：{os.path.basename(csv_path)}（AEMO NSW1 真实电价，登记于 DATA_SOURCES.md）")
    print("\n" + "=" * 64)
    print("  一句话：等电来，用电省——算力跟着电力走。")
    print("=" * 64)


def self_longest(price_rows):
    """全天最长连续负电价时长（小时）。"""
    best = cur = 0
    for p in price_rows:
        cur = cur + 1 if p["price"] < 0 else 0
        best = max(best, cur)
    return best * 5 / 60.0


if __name__ == "__main__":
    main()
