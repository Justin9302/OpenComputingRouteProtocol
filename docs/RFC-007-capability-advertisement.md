# RFC-007: 供给侧能力上报协议 (Capability Advertisement)

- **状态**: Draft
- **作者**: Open Compute Router Contributors
- **创建日期**: 2026-10-09
- **最后更新**: 2026-10-09
- **依赖**: RFC-003（零信任）

## 1. 动机与目标

撮合引擎需要供给侧输入。当前协议（RFC-001\~006）只定义了需求侧 intent 与任务生命周期，机房的能力（有什么、什么时候有、什么价愿意给）没有标准化上行的通道 —— 没有供给侧输入，撮合就是盲配。

本 RFC 定义机房（Server/Node）向 Hub 上报能力曲线的协议：**能力上报 = RFC-005 资源表的对外广播版**。Router 层本地维护资源表（低延迟、真实），Hub 层聚合为全局能力视图（增量更新）。

## 2. 术语



| 术语                   | 定义                                       |
| -------------------- | ---------------------------------------- |
| Capability Report    | 机房发出的能力曲线消息                              |
| capacity\_profile    | 各时段的可用容量序列                               |
| committed            | 已撮合任务锁定的容量（防超卖）                          |
| available            | 剩余可撮合容量 = capacity - committed           |
| price\_floor         | 机房各时段愿意接受的最低结算电价（负电价时段可为负）               |
| soft / hard capacity | 软容量（可超售、计划内，T3-T10 用）vs 硬容量（确定性，T0-T2 用） |

## 3. 消息格式

### 3.1 CapabilityReport（机房 → Hub，上行遥测）



```
{
  "type": "capability_report",
  "router_id": "nsw1-rack-a",
  "report_id": "cr-20261009-001",
  "generated_at": "2026-10-09T10:30:00+11:00",
  "timezone": "Australia/Sydney",
  "granularity_min": 30,
  "slots": [
    {
      "start": "2026-10-09T11:00:00+11:00",
      "end": "2026-10-09T11:30:00+11:00",
      "avail_kw": 750,
      "hard_kw": 500,
      "gpu_type": "H100",
      "gpu_count": 32,
      "price_floor_aud_per_mwh": -9.6,
      "green_ratio": 0.62,
      "maintenance": false
    }
  ],
  "committed": [
    { "task_id": "t-20261009-003", "slot_start": "2026-10-09T11:00:00+11:00", "kw": 250 }
  ],
  "version": 7
}
```

### 3.2 字段说明



* `granularity_min`：30（48 行）或 5（288 行），复用 demo 的粒度自适应规则（288 行 = NEM 5 分钟实际结算粒度，2021-10-01 起；48 行 = 30 分钟旧 mock 兼容）。

* `hard_kw`：硬容量（确定性，可承诺给 T0-T2）；`avail_kw` 与 `hard_kw` 之差为软容量（T3-T10 可用的弹性池）。

* `price_floor_aud_per_mwh`：可为负（负电价窗口机房愿意倒贴接单，换取消纳与绿电配额）。

* `green_ratio`：真实数据无绿电比例字段时**留空不编造**（诚实约定，对齐 README 的 `n/a` 规则）。

* `version`：单调递增，防止乱序覆盖（增量更新的基础）。

## 4. 状态机（机房侧容量）



```
active ──(撮合 reserve)──→ reserved ──(任务确认)──→ committed ──(执行完成/超时)──→ released
   │                          │                          │
   └──(维护窗口)──→ maintenance ──(恢复)──→ active      (commit 后进入 RFC-006 生命周期)
```



* `reserved`：撮合引擎锁定但任务未确认（TTL 过期自动释放，防止占着不放）。

* `committed`：已确认签约，**任何新撮合不得触碰**（防超卖底线）。

* `released`：执行结束或结算完成，容量归还 `active`。

## 5. 更新规则（增量推送，链路状态思想）



1. **触发式增量**：签约变化（reserve/commit/release）与电价变化（外部信号，RFC-004）触发增量推送，**不全量重传**。

2. **周期性全量**：每 15 分钟一次全量快照（对账用）。

3. **版本协调**：Hub 丢弃 `version <= 已收版本` 的乱序报文；两次全量快照之间用增量拼接。

4. **收敛机制**：Router = 本地链路状态（LS）节点，Hub = 区域骨干（ABR），增量洪泛 + 版本号去重 ——**不采用距离矢量**（RIP 式计数到无穷问题在高频电价变化下收敛风险大）。

## 6. 安全



* 上行遥测为**只读通道**：机房 → Hub 单向数据流，不含任何下行指令（控制流单向原则见 RFC-003 增补）。

* 报文签名：HMAC-SHA256（密钥由 RFC-003 的零信任身份体系派生）。

* 敏感字段（价格下限、签约明细）可选加密；聚合级数据（Hub 对外视图）去标识化。

* IEC 62443 zone 映射：本协议运行在机房 "管理平面"（zone 2），不进入 PCS 控制区（zone 4）。

## 7. 示例（mock 数据）



```
{
  "type": "capability_report",
  "router_id": "nsw1-rack-b",
  "report_id": "cr-20261009-002",
  "generated_at": "2026-10-09T10:45:00+11:00",
  "granularity_min": 30,
  "slots": [
    { "start": "2026-10-09T11:30:00+11:00", "end": "2026-10-09T12:00:00+11:00",
      "avail_kw": 1000, "hard_kw": 700, "gpu_type": "H100", "gpu_count": 42,
      "price_floor_aud_per_mwh": 5.2, "green_ratio": null, "maintenance": false }
  ],
  "committed": [],
  "version": 3
}
```

## 8. 与现有 RFC 的关系



* **RFC-005**：本协议是 RFC-005 资源表的外部视图 —— 资源表字段（destination/capacity\_profile/committed/metrics/status）在本协议中以 `slots` + `committed` 形式对外广播。

* **RFC-002**：`hard_kw`/`soft_kw` 与 Tier 对应（硬容量服务 T0-T2，软容量服务 T3-T10）。

* **RFC-004**：电价信号（AEMO）驱动 `price_floor` 参考与推送触发。

## 9. 未来工作



* 多机房聚合视图的拓扑抽象（跨区域 Hub 间能力交换，对齐 BGP-LS 算力信息通告）。

* GPU 互连拓扑（NVLink 域）作为撮合匹配字段（当前版本仅 gpu\_type/gpu\_count）。