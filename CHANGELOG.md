# 变更日志 (Changelog)

本项目的所有重要变更都将记录在此文件中。
格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [未发布]

### 计划中

- 完善 `spec/sla-tiers.yaml` 校验与 CI 集成
- 补充 `examples/` 下的电价模拟器与 Server 节点模拟器
- 增加 T11-T20 更长周期任务支持

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