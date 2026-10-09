# RFC-008: 撮合与市场出清模型 (Matching & Market Clearing)

- **状态**: Draft
- **作者**: Open Compute Router Contributors
- **创建日期**: 2026-10-09
- **最后更新**: 2026-10-09
- **依赖**: RFC-002（时效分级）、RFC-007（能力上报）、RFC-006（任务生命周期）、RFC-010（信任，P2）

## 1. 动机与目标

将"任务→资源"的单向分配升级为**双边市场撮合**：需求侧 intent（用户要什么）× 供给侧 capability（机房有什么、什么价给），按多维权重排序出清。与网络路由的本质差异：**机房有权拒绝（双向自愿），路由是尽力而为转发**——所以本协议是"路由思想 + 市场机制"的混合体。

## 2. 撮合输入

| 输入 | 来源 | 内容 |
|---|---|---|
| Intent | 用户/MCP（RFC-001） | 容量、时延、预算、绿电偏好、时效 Tier、GPU 类型 |
| Capability | 机房（RFC-007） | 时段×容量×价格下限×绿电×GPU×签约占用 |
| 电价信号 | 市场数据（RFC-004） | AEMO/ENTSO-E/PJM 实时与预测价格 |
| 信任评分 | 信誉机制（RFC-010，P2） | 历史履约率 |

## 3. 权重合成（撮合排序依据）

```
total_cost(slot) = power_price(slot) × energy_kwh + migration_cost + latency_penalty
green_bonus(slot) = green_ratio × green_weight          # max_green 模式反转为主权重
trust_factor = trust_score / 100                        # P2 启用，封闭环境默认 1.0

排序得分 = total_cost − green_bonus − trust_bonus
Tier 作为 QoS 过滤条件：T0-T2 只允许 hard_kw（确定性容量）；T3-T10 允许 soft_kw
```

| 字段 | 定义 |
|---|---|
| `power_price(slot)` | 该时段结算电价（可为负），来自 RFC-004 数据源 |
| `energy_kwh` | 任务在该时段的预估能耗（容量 × 时长 × 利用率系数） |
| `migration_cost` | 数据/模型迁移成本（跨机房执行时的惩罚项） |
| `latency_penalty` | 时延超标的惩罚（按 Tier 阈值线性/阶跃） |
| `green_ratio` | 机房该时段绿电比例（无数据时 `null`，**不编造**） |

## 4. 撮合算法（顺序）

1. **候选集过滤**（硬约束）：时段匹配 ∩ 容量足够（avail − committed ≥ 需求）∩ GPU 类型匹配 ∩ 价格 ≤ 预算 ∩ Tier 容量类别匹配（hard/soft）。
2. **权重排序**（软约束）：按 §3 得分升序取最优；同分取 trust 高者。
3. **模式选择**（对齐 demo-30s）：`min_cost`（总成本最低，可为负）/ `max_green`（绿电最高，无绿电数据时自动退化为 min_cost 并提示）/ `fastest`（最早满足预算的窗口，不优化电费）。
4. **无达标窗口 → PENDING**：预算低于当日最低可实现成本时任务保持 PENDING（"等电来"），对齐 README 预算约束示例（2026-01-24 NSW1 当日最低 2h 成本约 -0.54 AUD，预算设 -1.0 AUD 即触发）。

## 5. 防超卖（committed 锁定）

```
撮合成功 → reserve（锁定，TTL 默认 5 分钟）
  ├─ 任务确认 → committed（永久锁定，进入 RFC-006）
  └─ TTL 过期/机房拒绝 → 释放回 active（通知用户，可选降级到次优候选）
```

- 撮合引擎**不得**触碰已 committed 的容量（RFC-007 §4 状态机）。
- 软容量允许超售率（overbooking），由机房在 capability 中声明（默认 0 = 不允许超售）；超售收益/风险由机房承担。

## 6. 时序（撮合生命周期）

```
用户 intent ──→ Hub 撮合 ──reserve──→ 机房（RFC-007 状态机）
    │                                  │
    └────← 确认/拒绝 ←───────────────┘
Hub 确认 → committed → 生成预测推送（RFC-009）→ 执行（RFC-006）→ 结算（RFC-011）→ 释放
```

## 7. 结算钩子

- 每笔 committed 生成一条 `settlement_record`（task_id、slot、结算价、绿电信用、费用构成），交给 RFC-011 处理。
- 负电价结算：`power_price < 0` 时总成本可为负，收益分配规则见 RFC-011 §5。

## 8. 安全

- 撮合结果签名（HMAC，RFC-003 身份体系），防篡改。
- 拒绝/确认消息带机房签名，防伪造意图。
- 撮合引擎不持有任何设备控制权（控制流单向原则）。

## 9. 与现有 RFC 的关系

- **RFC-002**：Tier 从"调度分级"升级为"撮合 QoS 类 + 容量类别（hard/soft）"。
- **RFC-006**：本协议的 reserve/committed 是 RFC-006 生命周期的前置阶段（增补后闭环）。
- **RFC-010**（P2）：trust_factor 进权重；开放环境启用，封闭环境默认 1.0。

## 10. 未来工作

- 多轮撮合（用户可对候选集反报价，演进双向协商）。
- 组合撮合（多个任务打包进同一窗口以凑满机房容量）。
- 撮合结果的事后评估（出清价格 vs 实际成本的偏差统计，反馈给 RFC-010 信任分）。
