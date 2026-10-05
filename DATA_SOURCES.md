# DATA_SOURCES

本项目数据文件来源登记（数据溯源）。原则：每个文件必须能回答"从哪来、什么口径、谁生成"。

## 真实数据（AEMO 官方）

### `examples/data/aemo_nsw1_20260124.csv`

- **来源**：AEMO「Price and Demand」月度数据集（`PRICE_AND_DEMAND_202601_NSW1.csv`，AEMO 官网下载）
- **区域 / 日期**：NSW1，2026-01-24
- **粒度**：5 分钟（288 点/日，NEM 自 2021-10-01 起的实际结算粒度）
- **字段**：`time`（时段起点）、`price_per_mwh`（RRP，AUD/MWh）、`green_ratio`（源数据无此字段，留空）
- **提取口径**：AEMO `SETTLEMENTDATE` 为时段结束时刻，本文件时间取其减 5 分钟的时段起点；RRP 原样保留，未做平滑或拟合
- **下载日期**：2026-10-06
- **版权**：AEMO 数据按 [AEMO Copyright Permissions Notice](https://www.aemo.com.au/privacy-and-legal-notices/copyright-permissions) 使用，衍生作品须注明"来源：AEMO"

## Mock / 生成数据

以下文件为 `examples/energy-price-mock.py` 的输出（`seed=42`，确定性生成），**不是 AEMO 实测数据**，仅用于无网络时的演示与回归测试：

| 文件 | 说明 |
|---|---|
| `examples/data/mock/aemo_nsw1_20260810.csv` | mock 输出，48 点 30 分钟模拟曲线 |
| `examples/data/mock/aemo_nsw1_20260911.csv` | mock 输出，与上文件逐点相同（同 seed 同 PROFILE） |
| `examples/data/mock/aemo_nsw1_20261006.csv` | mock 输出，同上 |

> ⚠️ 若将 mock 文件用于对外演示，请勿标注为"真实数据"；真实演示一律使用本文件第一节登记的 AEMO 数据。

## 约定

- 真实文件命名 `aemo_nsw1_YYYYMMDD.csv`，置于 `examples/data/`；
- 新增真实数据文件时，在本文件登记来源 URL、下载日期与提取口径；
- mock 文件建议统一移入 `examples/data/mock/`，避免与真实文件混淆。
