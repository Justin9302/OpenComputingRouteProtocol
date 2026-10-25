#!/usr/bin/env python3
"""
plot-matching-window.py — 撮合闭环 30 秒 Pitch 的价格曲线录屏素材。

与 matching-demo.py 用同一份真实 AEMO 电价（examples/data/aemo_nsw1_*.csv）：
画全天 RRP 曲线 → 高亮撮合选定的负电价调度窗口 → 标注"白天高峰 vs 负电价窗口"省钱对比。
生成 assets/matching-window-YYYYMMDD.png，可作录屏背景素材。

用法：
  python examples/plot-matching-window.py             # 打印关键数字
  python examples/plot-matching-window.py --save     # 生成 PNG
"""

import argparse
import importlib.util
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)
ASSETS_DIR = os.path.join(REPO_DIR, "assets")

# 中文字体（macOS 常见 CJK 字体，按可用性回退）
plt.rcParams["font.sans-serif"] = ["PingFang SC", "Arial Unicode MS", "Heiti TC", "Heiti SC", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPT_DIR, fname))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cap = _load("capability_report_mock", "capability-report-mock.py")
eng = _load("matching_engine_mock", "matching-engine-mock.py")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true", help="生成 assets/matching-window-*.png")
    args = ap.parse_args()

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
        print("今日无负电价窗口满足约束 → 无图可画")
        return
    start, cost = r["start"], r["cost"]
    mwh = r["mwh"]
    window = price_rows[start:start + r["window_len"]]
    window_avg = sum(p["price"] for p in window) / len(window)

    peak = sorted(price_rows, key=lambda p: p["price"], reverse=True)[:8]
    peak_avg = sum(p["price"] for p in peak) / len(peak)
    cost_peak = mwh * peak_avg

    prices = [p["price"] for p in price_rows]
    times = [p["time"] for p in price_rows]
    n = len(prices)

    # ---- 画图 ----
    fig, ax = plt.subplots(figsize=(14, 6), dpi=110)
    fig.patch.set_facecolor("white")

    # 负电价区域底纹（浅红）
    below_zero = [i for i, v in enumerate(prices) if v < 0]
    if below_zero:
        # 聚成连续段
        segs, seg = [], []
        prev = -2
        for i in below_zero:
            if i != prev + 1:
                if seg:
                    segs.append(seg)
                seg = [i]
            else:
                seg.append(i)
            prev = i
        if seg:
            segs.append(seg)
        for seg in segs:
            ax.axvspan(seg[0], seg[-1], facecolor="#fde9e9", zorder=0)

    # 撮合选定的调度窗口（浅绿 + 边框）
    ax.axvspan(start, start + r["window_len"] - 1, facecolor="#e2f3e2",
               edgecolor="#2e9e4f", linewidth=1.5, zorder=1)

    ax.plot(range(n), prices, color="#1f6fb2", linewidth=1.8, zorder=3, label="RRP 电价")

    # 峰值对比点
    peak_idx = sorted(range(n), key=lambda i: prices[i], reverse=True)[:8]
    ax.scatter(peak_idx, [prices[i] for i in peak_idx], color="#c0392b",
               s=28, zorder=4, label=f"白天高峰均价 {peak_avg:+.0f} AUD/MWh")

    # 窗口标注
    mid = start + r["window_len"] // 2
    ax.annotate(f"调度窗口 {window[0]['time']}–{window[-1]['time']}\n"
                f"均价 {window_avg:+.1f} AUD/MWh\n"
                f"省钱 {cost_peak - cost:+.2f} AUD",
                xy=(mid, prices[mid]), xytext=(mid, min(prices) + (max(prices) - min(prices)) * 0.35),
                arrowprops=dict(arrowstyle="->", color="#2e9e4f"),
                fontsize=11, color="#1d6f42", ha="center",
                bbox=dict(boxstyle="round,pad=0.4", facecolor="#e2f3e2", edgecolor="#2e9e4f"))

    ax.axhline(0, color="#888", linewidth=1, linestyle="--", zorder=2)
    ax.set_ylim(min(prices) - 8, max(prices) + 8)

    # x 轴时间刻度
    step = 12  # 每小时一个刻度
    ax.set_xticks(range(0, n, step))
    ax.set_xticklabels([times[i] for i in range(0, n, step)], fontsize=10)
    ax.set_xlim(0, n - 1)

    ax.set_ylabel("RRP (AUD/MWh)", fontsize=11)
    ax.set_xlabel("时间（5 分钟粒度）", fontsize=11)
    ax.set_title(f"OCR 撮合闭环｜NSW1 {os.path.basename(csv_path).split('_')[-1][:8]} "
                 f"批发电价 + 负电价调度窗口\n"
                 f"算力跟着电力走——用时间换成本（真实 AEMO 电价）",
                 fontsize=13, fontweight="bold", loc="left")
    ax.grid(True, alpha=0.25, zorder=0)
    ax.legend(loc="upper left", fontsize=10, framealpha=0.9)

    fig.tight_layout()

    # ---- 打印关键数字 + 保存 ----
    print(f"调度窗口: {window[0]['time']}–{window[-1]['time']} | 均价 {window_avg:+.1f} AUD/MWh")
    print(f"白天高峰均价 {peak_avg:+.0f} | 窗口执行 {cost:+.2f} AUD vs 高峰 {cost_peak:+.2f} AUD")
    print(f"省钱 {cost_peak - cost:+.2f} AUD（{kw:.0f}kW × {dur}h ≈ {mwh:.3f} MWh）")

    if args.save:
        os.makedirs(ASSETS_DIR, exist_ok=True)
        out = os.path.join(ASSETS_DIR, f"matching-window-{os.path.basename(csv_path).split('_')[-1][:8]}.png")
        fig.savefig(out, bbox_inches="tight", facecolor="white")
        print(f"✅ 已生成: {out}")
    plt.close(fig)


if __name__ == "__main__":
    main()
