# OpenComputingRouteProtocol
This is a Protocol for Distributed AI Computing Router.

> 一个开源、中立、安全的分布式算力路由协议。  
> 让算力像互联网一样自由流动，让数据主权回归企业，让计算与绿色能源协同。

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![RFC Status](https://img.shields.io/badge/RFC-Draft-orange)](docs/)

---

## 🌱 项目愿景

在 AI 算力需求爆炸与能源转型瓶颈交织的时代，集中式云算力正面临**电力墙、安全墙、信任墙**的三重约束。

**Open Compute Router (OCR)** 旨在定义一套轻量级、可演进的分布式算力路由协议：

- **中立**：不绑定任何芯片厂商、云厂商或 AI 框架。
- **安全**：基于零信任架构，支持私有算力隔离与端到端加密。
- **算电协同**：将计算任务与电力价格、绿电供给动态匹配，实现"算力跟着电力走"。
- **时效分级**：从 1 分钟实时底座到 24 小时离线批处理，用时间换取成本与能源的最优解。

## 🏗️ 三层架构

```text
[用户层 / AI Agent]
       │  绑定 MCP，声明算力意图（显存、延迟、成本、安全、时效）
       ▼
[Hub 层 / 控制平面]
       │  验证身份，解析意图，匹配能源策略，下发调度指令
       ▼
[Server 层 / 执行平面]
       │  执行计算任务，上报状态，本地对接电网开放数据
       ▼
[算力与电力资源池]  (GPU/NPU/微电网/储能)
