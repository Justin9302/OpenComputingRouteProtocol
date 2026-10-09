# 项目架构与目录结构

> **最后更新**: 2026-09-11

## 目录结构

```
open-compute-router/
├── README.md                              # 项目主页（愿景、架构、快速开始）
├── ROADMAP.md                             # 项目路线图与里程碑
├── LICENSE                                # Apache License 2.0
├── AGENT_CONTEXT.md                       # AI 工作上下文（项目身份、提交规范）
├── CONTRIBUTING.md                        # 贡献指南
├── CODE_OF_CONDUCT.md                     # 行为准则
├── SECURITY.md                            # 安全漏洞披露政策
├── CHANGELOG.md                           # 版本变更日志
│
├── docs/
│   ├── architecture.md                    # 本文档：项目架构与目录结构
│   ├── DEVELOPMENT_PLAN_2026Q3-Q4.md      # 发展计划（2026 Q3-Q4）
│   ├── PROTOCOL_IMPROVEMENTS.md           # 协议改进待办清单（P0-P3）
│   ├── RFC-001-protocol-overview.md       # 协议总览与三层架构
│   ├── RFC-002-sla-tiering.md             # 时效分级调度模型（T0-T10）
│   ├── RFC-003-security-model.md          # 零信任安全模型
│   ├── RFC-004-energy-integration.md      # 算电协同与电网数据接入
│   ├── RFC-005-resource-table-and-device-level-autonomy.md  # 设备级自治与资源表
│   ├── RFC-006-task-execution-protocol.md # 任务执行与生命周期协议
│   ├── RFC-007-capability-advertisement.md # 供给侧能力上报协议
│   ├── RFC-008-matching-and-clearing.md   # 撮合与市场出清模型
│   ├── RFC-009-forecast-push.md           # 净负荷预测单向推送
│   ├── RFC-010-trust-reputation.md        # 信任与信誉机制
│   ├── RFC-011-settlement.md              # 结算与经济模型
│   ├── RFC-AMENDMENTS-2026-10.md          # 现有 RFC 增补说明（002/003/005/006）
│   └── RFC-COMPLETION-PLAN.md             # 市场协议补全索引与优先级
│
├── spec/
│   ├── compute-intent.schema.json         # MCP 算力意图 JSON Schema
│   ├── resource-table.schema.json         # 设备级资源表 JSON Schema
│   ├── task-execution.messages.json       # 任务执行协议消息格式（RFC-006）
│   ├── task-lifecycle.yaml                # 任务生命周期状态机（RFC-006）
│   ├── sla-tiers.yaml                     # SLA 分级定义表（T0-T10）
│   └── energy-plugin.interface.yaml       # 能源插件接口定义
│
├── examples/
│   ├── hub-simulator.py                   # Hub 极简调度模拟器
│   ├── energy-price-mock.py               # AEMO 电价模拟器（含负电价）
│   ├── server-node-mock.py                # 算力节点状态模拟器
│   ├── resource-table-mock.py             # RFC-005 设备级自治验证
│   ├── demo-30s.py                        # 30 秒 Demo：负电价时段跑训练
│   └── data/
│       └── aemo_nsw1_*.csv                # 模拟/真实电价数据
│
├── assets/
│   └── logo.svg                           # 项目 Logo
│
└── .github/
    ├── ISSUE_TEMPLATE/
    │   ├── bug_report.md
    │   ├── feature_request.md
    │   └── rfc_proposal.md
    ├── PULL_REQUEST_TEMPLATE.md
    └── workflows/
        └── spec-lint.yaml                 # 协议规范自动检查（CI）
```

## 三层架构

详见 [RFC-001](RFC-001-protocol-overview.md)。

```text
[用户层 / AI Agent]          声明算力意图（MCP）
       │
       ▼
[Hub 层 / 控制平面]          逻辑角色，可中心化或分布式部署
       │                       · 验证身份（mTLS/SPIFFE）
       │                       · 解析意图，匹配能源策略
       │                       · 基于本地资源表做调度决策
       │                       · 下发任务，维护生命周期
       ▼
[Server 层 / 执行平面]       执行计算任务，上报状态
       │
       ▼
[算力与电力资源池]            GPU/NPU/微电网/储能
```

> Hub 是逻辑角色，不是物理中心。三种部署形态：中心化（小规模）、分布式（大规模）、嵌入式（边缘设备）。

## 核心协议文档索引

| RFC | 标题 | 核心内容 |
|-----|------|---------|
| RFC-001 | 协议总览与三层架构 | 整体架构、设计目标、核心概念 |
| RFC-002 | 时效分级调度模型 | T0-T10 分级、预算约束调度、抢占与降级 |
| RFC-003 | 零信任安全模型 | mTLS/SPIFFE、数据分级、审计透明 |
| RFC-004 | 算电协同与电网数据接入 | 能源插件、价格驱动调度、需求响应 |
| RFC-005 | 设备级自治与资源表 | 资源表（类比路由表）、被动接收、BGP式扩散、字段标准化+链路状态收敛 |
| RFC-006 | 任务执行与生命周期协议 | Hub↔Server 消息定义、状态机、抢占与断点续传、撮合后闭环 |
| RFC-007 | 供给侧能力上报协议 | 能力曲线（时段×容量×价格×绿电×签约占用）、增量更新、防超卖锁定 |
| RFC-008 | 撮合与市场出清模型 | intent × capability 双边匹配、多维权重排序、PENDING（等电来） |
| RFC-009 | 净负荷预测单向推送 | 预测下行（时间序列+置信度）、无反向控制通道、机房前瞻调度 |
| RFC-010 | 信任与信誉机制 | 履约率/预测准确度/结算准时率、质押与惩罚、Tier 权限绑定 |
| RFC-011 | 结算与经济模型 | SettlementRecord、三种商业模式、负电价结算规则 |

## 规范文件（spec）

| 文件 | 用途 |
|------|------|
| `compute-intent.schema.json` | 用户层向 Hub 声明算力需求的格式规范 |
| `resource-table.schema.json` | 设备级资源表的格式规范（RFC-005） |
| `sla-tiers.yaml` | T0-T10 分级参数定义（RFC-002） |
| `energy-plugin.interface.yaml` | 电网数据插件接口定义（RFC-004） |

## 设计原则

1. **协议先行，代码渐进**：先以 RFC 固化设计，再实现参考代码
2. **中立**：不绑定任何芯片厂商、云厂商或 AI 框架
3. **算电协同**：电力价格是调度的一等公民，不是附加因素
4. **设备级自治**：资源表分布式维护，无第三方中央平台
5. **安全与调度解耦**：安全由数据分级决定，调度由时效/成本决定
6. **向后兼容**：新增字段不得破坏已有消息解析
