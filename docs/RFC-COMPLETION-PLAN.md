# OCRP 协议补全：新增 RFC 与增补说明

> 本目录为 OpenComputingRouteProtocol 的 RFC 补全草案，对应仓库 `docs/` 目录结构。
> 状态：Draft · 起草日期：2026-10-09 · 起草人：AI 协助，Justin9302 审阅

## 背景

现有 RFC-001~006 覆盖了"调度器"形态：三层架构、时效分级、零信任、算电协同（AEMO 电价接入）、设备级自治（资源表）、任务生命周期。
本轮补全的目标是把 OCRP 从"调度器"升级为"市场协议"：增加供给侧能力上报、撮合出清、预测推送、信任机制、结算模型，并修订现有 RFC 的安全边界与生命周期闭环。

## 新增 RFC 清单

| 编号 | 标题 | 一句话内容 | 优先级 | 对应 mock |
|---|---|---|---|---|
| RFC-007 | 供给侧能力上报协议 | 机房→Hub 上行能力曲线（时段×容量×价格×绿电×签约占用），Router 本地维护、Hub 增量聚合 | **P0** | `capability-report-mock.py`（基于 `resource-table-mock.py` 扩展） |
| RFC-008 | 撮合与市场出清模型 | intent × capability 双边匹配、多维权重合成、防超卖锁定（committed）、Tier 作 QoS | **P0** | `matching-engine-mock.py`（基于 `demo-30s.py` 扩展） |
| RFC-009 | 净负荷预测单向推送 | 撮合成功后预测下行（时间序列+置信度），机房 EMS 前瞻调度；无反向控制通道 | P1 | `forecast-push-mock.py` |
| RFC-010 | 信任与信誉机制 | 开放环境信息可信性：履约率、预测准确度、结算准时率；质押与惩罚 | P2 | `trust-registry-mock.py` |
| RFC-011 | 结算与经济模型 | 预测即服务 / 撮合分成 / 免费聚合三种模式；负电价结算；三方分账 | P2 | `settlement-mock.py` |

## 现有 RFC 增补清单

| RFC | 增补内容 | 位置 |
|---|---|---|
| RFC-002 | Tier 与置信度挂钩（T0-T2 硬承诺、T3-T10 软计划）；Tier 明确为撮合 QoS 类 | §4 新增"置信度分级"小节 |
| RFC-003 | IT/OT 安全分层：信息流双向、控制流单向；协议永不直接控 PCS；IEC 62443 zone 映射 | §新增"控制边界"小节 |
| RFC-005 | 资源表字段标准化（destination/capacity_profile/committed/metrics/status）；收敛机制=链路状态+增量更新 | §字段定义与 §更新规则 |
| RFC-006 | 撮合后生命周期闭环：intent→撮合→reserve→confirm→committed→预测→执行→结算→释放 | §状态机 |

详见 `amendments-existing-rfcs.md`。

## 落地顺序（代码渐进）

1. **P0 撮合闭环**：`capability-report-mock.py` + `matching-engine-mock.py`——把已有的 `resource-table-mock.py` 和 `demo-30s.py` 规范化成 RFC-007/008 的可演示实现（真实 AEMO 电价对拍）。
2. **P1 单向接口**：`forecast-push-mock.py`——模拟机房接收端（无反向通道）。
3. **P2 信任与结算**：`trust-registry-mock.py` + `settlement-mock.py`——开放市场前提。

## 与行业标准的对齐

| OCRP 组件 | 行业对应 |
|---|---|
| RFC-007 能力上报 | 电力市场机组可用容量申报 · Akash provider manifest · BGP-LS 算力信息通告 |
| RFC-008 撮合 | 订单簿撮合 · 电力现货市场出清 |
| RFC-009 预测推送 | OpenADR 报告方向 · OCP POM 功率编排 · IEC 61850 负荷预测结构 |
| RFC-010 信任 | 信用评分 · 质押/仲裁 |
| RFC-011 结算 | 电力现货结算 · 分成模式 |
| 安全分层（RFC-003 增补） | IEC 62443 zone/conduit · IT/OT 隔离 |
