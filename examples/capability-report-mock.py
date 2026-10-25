#!/usr/bin/env python3
"""
capability-report-mock.py — RFC-007 供给侧能力上报 mock。

模拟一个机房（Server/Node）向 Hub 上报能力曲线：
- slots：时段 × 可用容量(avail/hard) × GPU × 价格下限 × 绿电 × 维护
- physical_profile：设施物理模型声明（RFC-012，用户配置，非 OCR 测算）
- 对齐 RFC-007 消息格式；粒度对齐 NEM 5 分钟（288 点/日，真实 AEMO 数据）

可独立运行打印/保存 capability_report，也可被 matching-engine-mock.py import。

用法：
  python examples/capability-report-mock.py            # 打印报告摘要 + 前 3 个时段
  python examples/capability-report-mock.py --save     # 保存为 examples/data/mock/capability_report.json
"""

import argparse
import csv
import glob
import json
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
OUT_JSON = os.path.join(DATA_DIR, "mock", "capability_report.json")


def find_price_csv() -> str:
    """优先用 data/ 下真实 AEMO 曲线（最新优先）；无则返回 None。"""
    candidates = sorted(
        glob.glob(os.path.join(DATA_DIR, "aemo_nsw1_*.csv")),
        key=os.path.getmtime, reverse=True,
    )
    return candidates[0] if candidates else None


# --------------------------------------------------------------------------- #
# 机房能力配置（供给侧，对应 RFC-007 §3 + RFC-012 §4 physical_profile）
# --------------------------------------------------------------------------- #
FACILITY = {
    "router_id": "nsw1-rack-a",
    "gpu_type": "H100",
    "gpu_count": 64,                # 机柜可用 H100 卡数
    "avail_kw_base": 1000,          # 基础可用功率（kW）
    "hard_kw": 500,                 # 硬容量（确定性，服务 T0-T2）
    "price_floor_aud_per_mwh": -40.0,  # 机房接受的最低结算电价（负电价倒贴接单，换绿电消纳）
    "maintenance_slots": {          # 维护时段（slot index 集合），按 5 分钟粒度
        "03:00": True, "03:05": True, "03:10": True,   # 03:00-03:15 维护
    },
    "physical_profile": {
        "declared_by": "config-engineer-a",
        "source": "estimate",
        "confidence": 0.6,
        "pue_nominal": 1.18,
        "pue_load_model": "inverse_overhead",
        "cooling_tau_min": 10,
        "thermal_storage_kwh": 700.0,
        "ramp_limit_pct_per_min": 10.0,
        "ramp_limit_thermal_kw_per_min": 80.0,
        "power_envelope_kw": 1200.0,
        "unit_power_kw": 4.5,
    },
}


def load_price_rows(csv_path: str) -> list:
    """读取 AEMO CSV（time, price_per_mwh, green_ratio）；green 空记 None。"""
    rows = []
    with open(csv_path) as f:
        for r in csv.DictReader(f):
            g = r.get("green_ratio", "").strip()
            rows.append({"time": r["time"],
                         "price": float(r["price_per_mwh"]),
                         "green": float(g) if g else None})
    return rows


def build_capability_report(csv_path: str, version: int = 1) -> dict:
    """按电价曲线生成 capability_report：每 5 分钟一个 slot。"""
    rows = load_price_rows(csv_path)
    granularity_min = 5 if len(rows) == 288 else (30 if len(rows) == 48 else None)
    slots = []
    for i, r in enumerate(rows):
        t = r["time"][:5]  # "HH:MM"
        maintenance = FACILITY["maintenance_slots"].get(t, False)
        # 维护时段不可用；否则基础可用 - 硬容量 = 软容量（T3-T10 弹性池）
        avail_kw = 0 if maintenance else FACILITY["avail_kw_base"]
        hard_kw = 0 if maintenance else FACILITY["hard_kw"]
        slots.append({
            "start": r["time"],
            "end": None,  # 由粒度推导，演示从简
            "avail_kw": avail_kw,
            "hard_kw": hard_kw,
            "gpu_type": FACILITY["gpu_type"],
            "gpu_count": FACILITY["gpu_count"],
            "price_floor_aud_per_mwh": FACILITY["price_floor_aud_per_mwh"],
            "green_ratio": r["green"],       # 真实数据无此列 → None，不编造
            "maintenance": maintenance,
        })
    return {
        "type": "capability_report",
        "router_id": FACILITY["router_id"],
        "report_id": "cr-20261026-001",
        "generated_at": "2026-10-26T00:00:00+11:00",
        "timezone": "Australia/Sydney",
        "granularity_min": granularity_min,
        "slots": slots,
        "committed": [],
        "physical_profile": FACILITY["physical_profile"],
        "version": version,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true", help="保存 JSON 到 examples/data/mock/capability_report.json")
    args = ap.parse_args()

    csv_path = find_price_csv()
    if not csv_path:
        print("❌ data/ 下无 aemo_nsw1_*.csv，请先放置真实 AEMO 数据或运行 energy-price-mock.py")
        return
    report = build_capability_report(csv_path)

    print("=== RFC-007 供给侧能力上报 mock ===")
    print(f"机房 {report['router_id']} | 电价曲线 {os.path.basename(csv_path)} | "
          f"{len(report['slots'])} 时段 @ {report['granularity_min']}min")
    print(f"GPU {FACILITY['gpu_type']} × {FACILITY['gpu_count']} | "
          f"avail {FACILITY['avail_kw_base']}kW / hard {FACILITY['hard_kw']}kW | "
          f"价格下限 {FACILITY['price_floor_aud_per_mwh']} AUD/MWh")
    print(f"物理模型: PUE_nom={report['physical_profile']['pue_nominal']} "
          f"τ={report['physical_profile']['cooling_tau_min']}min "
          f"包络={report['physical_profile']['power_envelope_kw']}kW "
          f"({report['physical_profile']['source']})")
    maint = sum(1 for s in report["slots"] if s["maintenance"])
    neg = sum(1 for r in load_price_rows(csv_path) if r["price"] < 0)
    print(f"维护时段: {maint} 个 | 电价负值时段: {neg} 个（来自 AEMO 电价数据）")
    print("\n前 3 个 slot 示例：")
    for s in report["slots"][:3]:
        print(f"  {s['start']}  avail={s['avail_kw']}kW  hard={s['hard_kw']}kW  "
              f"price_floor={s['price_floor_aud_per_mwh']}  green={s['green_ratio']}  maint={s['maintenance']}")

    if args.save:
        os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
        with open(OUT_JSON, "w") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n✅ 已保存: {OUT_JSON}")


if __name__ == "__main__":
    main()
