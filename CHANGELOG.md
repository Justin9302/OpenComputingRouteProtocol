# 变更日志 (Changelog)

本项目的所有重要变更都将记录在此文件中。
格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [未发布]

### 新增

- **阶段二参考实现**：新增 Hub/Worker/调度器/AEMO 客户端/Manifest 验证器
  - `aemo_client.py`：AEMO 真实电价数据接入（公开 CSV + 模拟数据 fallback）
  - `scheduler.py`：SLA 策略组调度器（实时/交互/批处理/离线）+ 电价感知 + 预算约束 + 敏感度约束
  - `hub.py`：Hub HTTP 服务（REST API + 后台电价更新 + 调度循环）
  - `worker.py`：Worker 进程（注册/轮询/执行/上报）
  - `manifest_verifier.py`：Manifest 第一层验证（签名/版本/时间戳/哈希抽样 + 信任分级）
- **Schema**：新增 `spec/data-manifest.schema.json` 客户端敏感度清单格式
- **参考实现**：新增 `src/ocr/` 包，包含设备级自治多节点原型
  - `protocol.py`：消息格式定义与序列化（ResourceAdvertisement / Request / Response）
  - `resource_table.py`：设备级资源表核心类（upsert / 老化 / 查询）
  - `node.py`：P2P 节点实现（UDP 通信、路径向量防环路、分裂水平、HTTP 查询接口）
  - `energy_source.py`：电网数据推送源（模拟 AEMO，被动推送电价）
- **脚本**：新增 `scripts/run-demo-autonomy.sh`（一键启动 3 节点+电价源）、`scripts/verify-autonomy.py`（自动验证扩散与收敛）
- **协议文档**：新增 RFC-006《任务执行与生命周期协议》（草案），定义 Hub↔Server 间的任务下发、状态上报、抢占与断点续传、心跳等 12 种消息
- **规范文件**：新增 `spec/task-execution.messages.json`（任务执行协议消息 Schema）、`spec/task-lifecycle.yaml`（任务生命周期状态机）
- **项目治理**：新增 `docs/PROTOCOL_IMPROVEMENTS.md`（协议改进待办清单，含 P0-P3 共 16 项）、`docs/DEVELOPMENT_PLAN_2026Q3-Q4.md`（四阶段发展计划）
- **规范字段**：`spec/compute-intent.schema.json` 新增 `cost_optimization`、`currency`、`tenant_priority`（预留）、`network_affinity`（预留）、`data_governance`（预留，含 data_sensitivity / data_masking_required / data_residency）
- **规范字段**：`spec/resource-table.schema.json` 扩充 `avail_compute`（新增 gpu_model / compute_capability / network_bandwidth_gbps / cpu_cores / ram_gb），新增 `network_zone`（预留）

### 变更

- **price_ceiling 语义澄清**：从"最高单价"明确为"任务总预算上限（本币）"，调度器基于时间+资金双约束做资源匹配；电价为内部输入，不直接暴露给用户
- **RFC-001**：澄清 Hub 为逻辑角色而非物理中心，补充中心化/分布式/嵌入式三种部署形态
- **RFC-002**：新增 §4.4 预算约束调度；修正任务提交示例中 price_ceiling 的值与含义
- **RFC-005**：澄清"无中央控制器"指反对第三方平台，而非 Hub 逻辑角色本身；重写与三层架构的关系；资源表项扩充算力描述字段
- **docs/architecture.md**：更新项目目录结构，反映 RFC-005/006、新 spec 文件、发展计划等
- **examples/demo-30s.py**：调度逻辑从"电价低于阈值"改为"总成本低于预算"的预算约束调度，支持 min_cost / max_green / fastest 三种优化目标

### 计划中

- 完善 `spec/sla-tiers.yaml` 校验与 CI 集成
- 增加 T11-T20 更长周期任务支持
- 阶段一：设备级自治多节点原型开发

## [0.1.0] - 2026-08-07

### 新增

- **协议文档**：新增 RFC-001（协议总览与三层架构）、RFC-002（时效分级调度模型）、RFC-003（零信任安全模型）、RFC-004（算电协同与电网数据接入）
- **规范文件**：新增 `spec/compute-intent.schema.json`（MCP 算力意图 Schema）、`spec/sla-tiers.yaml`（SLA 分级定义）、`spec/energy-plugin.interface.yaml`（能源插件接口）
- **参考实现**：新增 `examples/hub-simulator.py`（Hub 极简调度模拟器）
- **社区治理**：新增 CONTRIBUTING.md、CODE_OF_CONDUCT.md、SECURITY.md、CHANGELOG.md
- **GitHub 模板**：新增 Issue 模板（Bug 报告 / 功能请求 / RFC 提案）与 PR 模板
- **CI**：新增 `spec-lint` 工作流（协议规范自动检查）

### 文档

- README 大幅更新：新增 SLA 时效分级表、三层架构说明、快速开始示例
- 协议文档重组：RFC 文档移至 `docs/` 目录

### 基础设施

- 项目 Logo 占位文件（`assets/logo.svg`）

[0.1.0]: https://github.com/Justin9302/OpenComputingRouteProtocol/releases/tag/v0.1.0