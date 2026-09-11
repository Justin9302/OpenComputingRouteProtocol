
# RFC-002: 时效分级调度模型 (SLA Tiering)

- **状态**: Draft
- **作者**: Open Compute Router Contributors
- **创建日期**: 2026-08-07
- **最后更新**: 2026-09-11

## 1. 摘要

本 RFC 定义了 Open Compute Router 协议中的**时效分级调度模型（SLA Tiering）**。该模型将计算任务按延迟容忍度划分为 11 个等级（T0-T10），时间窗口从实时底座到 24 小时不等，并配套逐级下调的价格机制，实现"用时间换成本、用时间换绿电"的算电协同调度。

## 2. 设计动机

- **电力峰值错配**：AI 算力需求与电网负荷高峰往往重叠，导致电价飙升与绿电浪费并存。
- **业务 SLA 差异化**：实时推理与离线训练对延迟的敏感度天差地别，不应支付相同的算力成本。
- **需求侧响应**：将可延迟任务作为"可转移负荷"，参与电网调峰，获取需求响应补贴。

## 3. SLA 分级定义

| Tier | 时间窗口 | 最大完成时间 | 可中断 | 典型场景               | 电力策略                       |
| :--: | :------- | :----------- | :----: | :--------------------- | :----------------------------- |
|  T0  | 实时底座 | < 1s         |   否   | 工业控制、安全拦截     | 基载电力 + 本地储能兜底        |
|  T1  | 1 分钟   | 60s          |   否   | 实时推理、语音交互     | 优先本地节点，容忍峰值电价     |
|  T2  | 5 分钟   | 300s         |   否   | 交互式 Agent、实时客服 | 本地/边缘节点，可短暂排队      |
|  T3  | 10 分钟  | 600s         |   是   | 轻量批处理、文档摘要   | 可跨机房调度，避开尖峰         |
|  T4  | 30 分钟  | 1800s        |   是   | 知识库更新、中等推理   | 可跨区域调度，等待电价回落     |
|  T5  | 1 小时   | 3600s        |   是   | 日报表生成、数据清洗   | 匹配平段电价                   |
|  T6  | 2 小时   | 7200s        |   是   | 模型微调、批量翻译     | 优先绿电充裕时段               |
|  T7  | 4 小时   | 14400s       |   是   | 数据预处理、向量库构建 | 可被高优任务抢占，支持断点续传 |
|  T8  | 8 小时   | 28800s       |   是   | 大规模训练、科学计算   | 匹配夜间低谷电价/风电高峰      |
|  T9  | 16 小时  | 57600s       |   是   | 超大规模离线训练       | 仅在绿电过剩/负电价时段执行    |
| T10 | 24 小时  | 86400s       |   是   | 归档、冷数据挖掘       | 纯粹"等电来"，作为虚拟负荷池   |

## 4. 调度语义

### 4.1 任务提交

用户层通过 MCP 意图声明 `sla_tier` 字段，Hub 据此决定调度策略。

```json
{
  "sla_scheduling": {
    "tier": "T6",
    "max_completion_time": "2h",
    "price_ceiling": 50.0,
    "currency": "AUD",
    "cost_optimization": "min_cost",
    "interruptible": true,
    "energy_preference": {
      "prefer_green": true,
      "avoid_peak_price": true
    }
  }
}
```

**字段说明**：
- `tier`：SLA 分级，决定时间窗口和可中断性
- `max_completion_time`：任务必须完成的时间上限
- `price_ceiling`：**任务总预算上限**（本币），调度器在该预算内寻找最优执行方案
- `currency`：预算货币代码，默认 AUD
- `cost_optimization`：优化目标（最低成本 / 最高绿电 / 最快完成）
- `interruptible`：是否允许被高优任务抢占


### 4.2 抢占与降级


当 T0-T2 高优任务到达且算力不足时，Hub 可抢占 interruptible: true 的低优任务。
被抢占任务自动降级至下一 Tier（如 T6 → T7），并记录断点以便续传。

### 4.3 绿电套利

Hub 定期拉取电网开放数据（如 AEMO Price Forecast）。
若预测未来 N 小时内电价处于低位且绿电比例达标，Hub 将对应 Tier 的任务提前唤醒执行。绿电套利是预算约束调度（§4.4）的一种特殊情形——当 `cost_optimization=max_green` 时，调度器优先选择绿电比例最高的时段，而非单纯成本最低。

### 4.4 预算约束调度

`price_ceiling` 定义任务的**总预算上限**，与 `max_completion_time` 共同构成调度的双约束。调度器在时间窗口内寻找满足预算的最优执行方案。

**调度逻辑**：

```
输入：
  - 任务预计执行时长（estimated_duration）
  - 节点功率（来自资源表）
  - 未来 N 小时电价预测（来自能源插件，RFC-004）
  - 算力服务费（节点报价）
  - price_ceiling（总预算）
  - cost_optimization（优化目标）

计算：
  对时间窗口内每个候选时段：
    电力成本 = 电价 × 节点功率 × 执行时长
    算力成本 = 算力单价 × 执行时长
    总成本 = 电力成本 + 算力成本
    若 总成本 <= price_ceiling → 该时段可行

选择：
  - min_cost：选总成本最低的可行时段
  - max_green：选绿电比例最高的可行时段
  - fastest：选最早开始的可行时段
```

**关键原则**：
- 电价是调度器的**内部输入**，不直接暴露给用户。用户只关心"总共花多少钱"
- 若时间窗口内无任何时段满足预算，任务保持 `pending`，并通知用户"预算不足，建议提高 price_ceiling 或放宽时间窗口"
- `price_ceiling` 为可选字段；未设置时调度器按 `cost_optimization` 默认策略选择最优时段，不设预算上限
- 预算仅约束算力+电力的直接成本，不含数据传输费（后续版本可扩展）

### 5. 安全与合规


所有 Tier 的任务均受 RFC-003 安全模型约束。
security_level: private_only 的任务禁止路由至公有节点，无论 Tier 高低。

### 6. 向后兼容


未声明 sla_tier 的任务默认视为 T1（1 分钟），按最高优先级调度。
未来可扩展 T11-T20 支持更长周期（如 72h、168h）的科研任务。

```

---

### 4. `spec/compute-intent.schema.json`

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "https://open-compute-router.org/spec/compute-intent.schema.json",
  "title": "ComputeIntent",
  "description": "MCP 算力意图描述规范，用于用户层向 Hub 层声明计算任务需求。",
  "type": "object",
  "required": ["task_id", "compute_intent"],
  "properties": {
    "task_id": {
      "type": "string",
      "format": "uuid",
      "description": "全局唯一任务标识"
    },
    "mcp_context": {
      "type": "string",
      "description": "关联的 MCP 会话上下文 ID"
    },
    "compute_intent": {
      "type": "object",
      "required": ["type"],
      "properties": {
        "type": {
          "type": "string",
          "enum": ["inference", "training", "fine_tuning", "batch_processing", "data_cleaning"],
          "description": "计算任务类型"
        },
        "min_vram_gb": {
          "type": "integer",
          "minimum": 1,
          "description": "最小显存需求（GB）"
        },
        "preferred_arch": {
          "type": "array",
          "items": {
            "type": "string",
            "enum": ["gpu_nvidia", "gpu_amd", "npu_huawei", "tpu", "asic", "cpu"]
          },
          "description": "偏好的硬件架构列表"
        },
        "security_level": {
          "type": "string",
          "enum": ["public", "internal", "private_only", "confidential"],
          "description": "安全隔离级别"
        }
      }
    },
    "sla_scheduling": {
      "type": "object",
      "properties": {
        "tier": {
          "type": "string",
          "pattern": "^T([0-9]|10)$",
          "description": "SLA 分级（T0-T10）"
        },
        "max_completion_time": {
          "type": "string",
          "pattern": "^\\d+(s|m|h)$",
          "description": "最大完成时间（如 30s, 5m, 2h）"
        },
        "price_ceiling": {
          "type": "number",
          "minimum": 0,
          "description": "愿意支付的最高单价（货币单位/任务）"
        },
        "interruptible": {
          "type": "boolean",
          "default": false,
          "description": "是否允许被高优任务抢占"
        },
        "energy_preference": {
          "type": "object",
          "properties": {
            "prefer_green": {
              "type": "boolean",
              "description": "优先使用绿电"
            },
            "avoid_peak_price": {
              "type": "boolean",
              "description": "自动避开电价高峰"
            },
            "use_local_battery": {
              "type": "boolean",
              "description": "是否强制使用本地储能兜底"
            }
          }
        }
      }
    }
  }
}
```
