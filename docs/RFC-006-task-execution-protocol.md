# RFC-006: 任务执行与生命周期协议

- **状态**: Draft
- **作者**: Open Compute Router Contributors
- **创建日期**: 2026-09-11
- **最后更新**: 2026-09-11

## 1. 摘要

本 RFC 定义 Open Compute Router 协议中 **Hub 与 Server（算力节点）之间的任务执行与生命周期管理协议**。涵盖任务下发、状态上报、数据传输、抢占与断点续传、心跳与健康检查等核心交互。本协议填补了 RFC-001~005 中"调度前"定义完整、但"调度时与调度后"交互缺失的空白，是端到端任务跑通的基础。

## 2. 设计动机

- **执行协议缺失**：现有 RFC 定义了任务声明（RFC-001/002）、资源描述（RFC-005）、能源接入（RFC-004），但没有定义 Hub 如何把任务交给 Server、Server 如何回报状态。
- **长任务管理**：T6-T10 任务可能运行数小时至数十小时，需要明确的生命周期管理、心跳保活、异常恢复机制。
- **抢占语义**：RFC-002 定义了 interruptible 任务可被高优任务抢占，但抢占的消息交互、checkpoint 保存与恢复流程未定义。
- **数据传输**：训练任务涉及模型权重、数据集、结果文件，需要明确传输方式，避免协议实现各自为政。

## 3. 核心概念

### 3.1 角色

| 角色 | 说明 |
|------|------|
| **Hub** | 控制平面，负责任务调度、下发指令、收集状态。可以是中心化部署，也可以分布在设备上（见 RFC-005） |
| **Server / Worker** | 执行平面，接收任务、执行计算、上报状态与结果 |
| **Task** | 一次计算任务的完整生命周期实例，由 Compute Intent 创建（见 `spec/compute-intent.schema.json`） |

### 3.2 任务状态机

```
                    ┌──────────┐
                    │ pending  │  已提交，等待调度
                    └────┬─────┘
                         │ Hub 调度，选择节点
                         ▼
                    ┌──────────┐
              ┌────▶│ assigned │  已分配给节点，等待节点确认
              │     └────┬─────┘
              │          │ 节点确认接受
              │          ▼
              │     ┌──────────┐
              │     │ running  │  节点正在执行
              │     └────┬─────┘
              │          │
              │    ┌─────┼──────┐
              │    ▼     ▼      ▼
              │ ┌──────┐ ┌────┐ ┌───────────┐
              │ │completed│ │failed│ │preempted │
              │ └──────┘ └────┘ └─────┬─────┘
              │                       │ 保存 checkpoint，等待重新调度
              │                       ▼
              │                  ┌──────────┐
              └──────────────────│  pending │  重新进入待调度
                                 └──────────┘

  任意状态可转入 cancelled（用户取消）或 timeout（超时）
```

| 状态 | 说明 | 进入条件 | 退出条件 |
|------|------|---------|---------|
| `pending` | 已提交，等待 Hub 调度 | 用户提交任务 / 被抢占后重新入队 | Hub 选择节点并下发 |
| `assigned` | 已分配给节点，等待节点确认 | Hub 发送 AssignTask | 节点 Accept → running；节点 Reject → pending |
| `running` | 节点正在执行 | 节点 Accept | 完成 → completed；失败 → failed；被抢占 → preempted |
| `preempted` | 被高优任务抢占，已暂停 | Hub 发送 PreemptTask | checkpoint 保存完成 → pending（重新调度） |
| `completed` | 成功完成 | 节点上报完成 | 终态 |
| `failed` | 执行失败 | 节点上报失败 / 心跳超时 | 终态（可由用户重试） |
| `cancelled` | 用户取消 | 用户发送 CancelTask | 终态 |
| `timeout` | 超过 max_completion_time | Hub 检测超时 | 终态 |

### 3.3 通信模式

采用 **双向流式通信**，Hub 与 Server 之间维持长连接：

- **Hub → Server**：任务下发、抢占指令、取消指令
- **Server → Hub**：任务确认、状态上报、心跳、结果通知
- 连接断开后，Server 侧任务状态保留，重连后同步状态

> **实现说明**：协议语义与传输层解耦。参考实现可使用 gRPC 双向流、WebSocket 或 HTTP 长轮询。原型阶段推荐 WebSocket（Python 生态成熟，调试方便）。

## 4. 协议消息定义

### 4.1 消息总览

| 方向 | 消息 | 说明 |
|------|------|------|
| Hub → Server | `AssignTask` | 下发任务到节点 |
| Hub → Server | `PreemptTask` | 抢占正在执行的任务 |
| Hub → Server | `CancelTask` | 取消任务 |
| Hub → Server | `HeartbeatAck` | 心跳响应 |
| Server → Hub | `TaskAccept` | 节点接受任务 |
| Server → Hub | `TaskReject` | 节点拒绝任务（资源不足等） |
| Server → Hub | `TaskStatus` | 任务状态更新（进度、日志） |
| Server → Hub | `TaskComplete` | 任务完成（含结果引用） |
| Server → Hub | `TaskFail` | 任务失败（含错误信息） |
| Server → Hub | `PreemptAck` | 抢占确认（含 checkpoint 引用） |
| Server → Hub | `Heartbeat` | 节点心跳（含资源状态） |
| Server → Hub | `RegisterNode` | 节点注册（首次连接） |

### 4.2 RegisterNode（节点注册）

Server 首次连接 Hub 时发送，声明自身能力。

```json
{
  "message_type": "RegisterNode",
  "node_id": "spiffe://open-compute-router.org/server/ap-southeast-02",
  "capabilities": {
    "compute": {
      "gpu_model": "H100-80GB",
      "gpu_count": 8,
      "vram_gb": 640,
      "cpu_cores": 64,
      "ram_gb": 512,
      "network_bandwidth_gbps": 100
    },
    "supported_task_types": ["training", "fine_tuning", "batch_processing", "inference"],
    "max_concurrent_tasks": 4
  },
  "location": {
    "region": "NSW1",
    "grid_price_per_mwh": -25.0,
    "green_ratio": 0.92
  },
  "protocol_version": "0.2.0"
}
```

> 注册信息与 RFC-005 资源表的 `compute` 表项对齐。Hub 收到注册后更新本地资源表。

### 4.3 AssignTask（任务下发）

Hub 选择节点后下发任务。

```json
{
  "message_type": "AssignTask",
  "task_id": "train-llm-001",
  "compute_intent": {
    "type": "fine_tuning",
    "min_vram_gb": 640,
    "preferred_arch": ["gpu_nvidia"],
    "security_level": "internal"
  },
  "sla_scheduling": {
    "tier": "T6",
    "max_completion_time": "2h",
    "price_ceiling": 50.0,
    "interruptible": true,
    "energy_preference": {
      "prefer_green": true,
      "avoid_peak_price": true
    }
  },
  "execution": {
    "command": "python train.py --config config.yaml",
    "working_dir": "/opt/ocr/tasks/train-llm-001",
    "estimated_duration": "1h30m",
    "env": {
      "CUDA_VISIBLE_DEVICES": "0,1,2,3"
    }
  },
  "data": {
    "mode": "reference",
    "inputs": [
      {
        "name": "dataset",
        "url": "s3://ocr-tasks/train-llm-001/dataset.tar.gz",
        "checksum": "sha256:abc123...",
        "size_bytes": 10737418240
      },
      {
        "name": "base_model",
        "url": "s3://ocr-models/llama-3-8b/",
        "checksum": "sha256:def456...",
        "size_bytes": 16000000000
      }
    ],
    "output": {
      "upload_url": "s3://ocr-results/train-llm-001/",
      "expected_files": ["checkpoint-final/", "metrics.json"]
    }
  },
  "checkpoint": {
    "resume_from": null,
    "upload_url": "s3://ocr-checkpoints/train-llm-001/"
  },
  "deadline": "2026-09-12T08:00:00Z"
}
```

**字段说明**:
- `execution.command`：节点执行的命令。协议不限制具体运行时（Python、Docker、裸机均可）。
- `data.mode`：`reference`（引用对象存储，节点自行下载）或 `inline`（小数据直接内嵌在消息中）。
- `checkpoint.resume_from`：若非 null，表示这是被抢占后恢复的任务，节点应从该 checkpoint 继续。
- `deadline`：基于 SLA Tier 计算的绝对截止时间。

### 4.4 TaskAccept / TaskReject（任务确认）

节点收到 AssignTask 后，检查资源是否满足，回复接受或拒绝。

```json
// TaskAccept
{
  "message_type": "TaskAccept",
  "task_id": "train-llm-001",
  "node_id": "spiffe://open-compute-router.org/server/ap-southeast-02",
  "accepted_at": "2026-09-11T22:00:00Z",
  "estimated_start": "2026-09-11T22:05:00Z"
}

// TaskReject
{
  "message_type": "TaskReject",
  "task_id": "train-llm-001",
  "node_id": "spiffe://open-compute-router.org/server/ap-southeast-02",
  "reason": "insufficient_vram",
  "detail": "可用显存 480GB < 需求 640GB"
}
```

**拒绝原因枚举**：`insufficient_vram`、`insufficient_gpu`、`node_busy`、`security_policy_violation`、`unsupported_task_type`、`other`。

### 4.5 TaskStatus（状态上报）

节点执行期间定期上报进度。建议频率：T0-T2 每 5s，T3-T5 每 30s，T6-T10 每 5min。

```json
{
  "message_type": "TaskStatus",
  "task_id": "train-llm-001",
  "node_id": "spiffe://open-compute-router.org/server/ap-southeast-02",
  "status": "running",
  "progress": {
    "percent": 45.0,
    "current_step": 4500,
    "total_steps": 10000,
    "elapsed_time": "27m",
    "estimated_remaining": "33m"
  },
  "resource_usage": {
    "gpu_utilization": 0.92,
    "vram_used_gb": 580,
    "power_kw": 26.5,
    "power_price_per_mwh": -20.0
  },
  "logs_tail": "step 4500/10000, loss=2.34, lr=1e-5",
  "reported_at": "2026-09-11T22:32:00Z"
}
```

> `progress` 字段为可选，节点无法精确报告进度时可只上报 `status=running`。`resource_usage.power_price_per_mwh` 用于算电协同的成本核算。

### 4.6 TaskComplete（任务完成）

```json
{
  "message_type": "TaskComplete",
  "task_id": "train-llm-001",
  "node_id": "spiffe://open-compute-router.org/server/ap-southeast-02",
  "completed_at": "2026-09-11T23:05:00Z",
  "duration": "1h5m",
  "result": {
    "output_ref": "s3://ocr-results/train-llm-001/",
    "files": ["checkpoint-final/", "metrics.json", "train.log"],
    "checksum": "sha256:ghi789..."
  },
  "metrics": {
    "final_loss": 1.87,
    "gpu_hours": 8.5,
    "energy_kwh": 28.3,
    "energy_cost_aud": -0.57
  },
  "exit_code": 0
}
```

### 4.7 TaskFail（任务失败）

```json
{
  "message_type": "TaskFail",
  "task_id": "train-llm-001",
  "node_id": "spiffe://open-compute-router.org/server/ap-southeast-02",
  "failed_at": "2026-09-11T22:45:00Z",
  "error": {
    "code": "OOM",
    "message": "CUDA out of memory at step 4500",
    "retryable": true
  },
  "logs_tail": "...RuntimeError: CUDA out of memory...",
  "partial_result_ref": "s3://ocr-results/train-llm-001/partial/",
  "exit_code": 1
}
```

**错误码枚举**：`OOM`、`DATA_DOWNLOAD_FAILED`、`EXECUTION_ERROR`、`NODE_FAILURE`、`TIMEOUT`、`SECURITY_VIOLATION`、`OTHER`。

### 4.8 PreemptTask / PreemptAck（抢占）

Hub 需要将节点资源让给高优任务时，向正在执行低优任务的节点发送抢占指令。

```json
// Hub → Server
{
  "message_type": "PreemptTask",
  "task_id": "train-llm-001",
  "reason": "high_priority_task_arrived",
  "grace_period": "5m",
  "checkpoint_upload_url": "s3://ocr-checkpoints/train-llm-001/"
}

// Server → Hub
{
  "message_type": "PreemptAck",
  "task_id": "train-llm-001",
  "node_id": "spiffe://open-compute-router.org/server/ap-southeast-02",
  "checkpoint_saved": true,
  "checkpoint_ref": "s3://ocr-checkpoints/train-llm-001/ckpt-step-4500/",
  "progress_at_preempt": {
    "percent": 45.0,
    "current_step": 4500
  },
  "acknowledged_at": "2026-09-11T22:48:00Z"
}
```

**抢占流程**:
1. Hub 发送 `PreemptTask`，携带宽限时间（grace_period）
2. Server 在宽限时间内保存 checkpoint，上传到指定位置
3. Server 回复 `PreemptAck`，携带 checkpoint 引用
4. Hub 将任务状态改为 `preempted`，随后转为 `pending` 重新调度
5. 重新调度时，`AssignTask.checkpoint.resume_from` 指向上次的 checkpoint

> 若任务不支持 checkpoint（如无状态推理），Server 可直接终止并回复 `checkpoint_saved: false`，任务重新从头执行。

### 4.9 CancelTask（取消）

```json
// Hub → Server
{
  "message_type": "CancelTask",
  "task_id": "train-llm-001",
  "reason": "user_requested"
}
```

节点收到后终止任务执行，回复 `TaskStatus` 状态改为 `cancelled`。

### 4.10 Heartbeat（心跳）

Server 定期发送心跳，同时上报资源状态。心跳间隔建议 30s，连续 3 次未收到视为节点离线。

```json
{
  "message_type": "Heartbeat",
  "node_id": "spiffe://open-compute-router.org/server/ap-southeast-02",
  "status": "up",
  "resources": {
    "avail_gpu": 4,
    "avail_vram_gb": 320,
    "running_tasks": 1,
    "cpu_load": 0.35,
    "power_kw": 15.2
  },
  "grid": {
    "region": "NSW1",
    "price_per_mwh": -20.0,
    "green_ratio": 0.90
  },
  "sent_at": "2026-09-11T22:30:00Z"
}
```

> 心跳中的资源与电网信息用于 Hub 更新本地资源表（RFC-005），是设备级自治中"算力节点状态上报"的具体实现。

## 5. 数据传输规范

### 5.1 传输模式

| 模式 | 适用场景 | 说明 |
|------|---------|------|
| `reference` | 大数据（数据集、模型、checkpoint） | 任务消息中携带 URL，节点自行下载/上传。推荐使用 S3 兼容对象存储 |
| `inline` | 小数据（配置文件、脚本、<10MB） | 数据直接内嵌在消息的 `data.content` 字段中，base64 编码 |

### 5.2 引用模式的 URL 规范

- 支持 `s3://`、`https://`、`http://` 协议
- 节点需具备访问该 URL 的权限（通过预签名 URL 或节点身份授权）
- 输入文件必须携带 `checksum`（sha256），节点下载后校验
- 输出位置由 Hub 在 AssignTask 中指定 `upload_url`，节点完成后上传

### 5.3 数据生命周期

- **输入数据**：节点下载到本地工作目录，任务完成后可清理（缓存策略由节点自行决定）
- **输出数据**：节点上传到 `output.upload_url`，Hub 通知用户下载
- **Checkpoint**：抢占时上传到 `checkpoint.upload_url`，任务最终完成后可由 Hub 决定保留或清理

## 6. 异常处理

### 6.1 节点离线

- Hub 连续 3 次心跳超时（约 90s），标记节点为 `down`
- 节点上 running 状态的任务：
  - interruptible 任务 → 转为 `pending`，重新调度到其他节点
  - 非 interruptible 任务 → 标记为 `failed`，错误码 `NODE_FAILURE`
- 节点重新上线后，通过 RegisterNode 重新注册，同步本地任务状态

### 6.2 任务超时

- Hub 跟踪每个任务的 `deadline`（基于 SLA Tier 的 max_completion_time）
- 超过 deadline 未完成 → 发送 CancelTask，标记为 `timeout`
- 节点收到后终止执行，已产生的部分结果按 `partial_result_ref` 上传

### 6.3 连接断开重连

- Server 侧保留任务状态，重连后发送 RegisterNode + 各 running 任务的 TaskStatus
- Hub 根据 Server 上报的状态 reconcile 本地视图
- 若 Hub 侧已因心跳超时将任务重新调度，Server 应终止重复执行的任务

## 7. 与其他 RFC 的关系

| RFC | 关系 |
|-----|------|
| RFC-001（三层架构） | 本协议定义 Hub 层与 Server 层之间的执行平面交互 |
| RFC-002（SLA Tiering） | `sla_scheduling` 字段直接来自 Compute Intent；抢占语义基于 interruptible 标志；deadline 基于 max_completion_time |
| RFC-003（零信任安全） | 所有消息通过 mTLS 传输，node_id 使用 SPIFFE ID；`security_level` 决定任务可调度的节点范围；任务数据传输遵循端到端加密要求 |
| RFC-004（算电协同） | `resource_usage.power_price_per_mwh` 和 `metrics.energy_cost_aud` 用于算电协同的成本核算；心跳中的 grid 信息驱动调度决策 |
| RFC-005（设备级自治） | RegisterNode 和 Heartbeat 是资源表 `compute` 表项的来源；Hub 角色为逻辑角色，可分布在设备上 |

## 8. 安全约束

- **传输安全**：所有协议消息通过 TLS 1.3 传输，双向认证（mTLS），身份以 SPIFFE ID 表达（见 RFC-003）
- **任务隔离**：Server 侧必须对不同任务进行资源隔离（cgroup / 容器 / 虚拟机），`security_level: confidential` 的任务需 TEE 支持
- **数据安全**：`reference` 模式下，输入/输出 URL 应使用预签名 URL 或节点身份授权，避免公开暴露；`inline` 模式下数据随 TLS 加密传输
- **最小权限**：任务仅能访问其声明的资源和数据目录，禁止跨任务访问
- **审计**：Hub 记录所有任务生命周期事件（提交、调度、完成、失败、抢占），支持导出用于第三方审计（见 RFC-003 §3.3）

## 9. 演进原则

- **协议先行，代码渐进**：本 RFC 定义消息语义，参考实现逐步补齐
- **向后兼容**：新增字段不得破坏已有消息解析；节点通过 `protocol_version` 声明支持的版本
- **传输层无关**：消息格式与具体传输协议（gRPC/WebSocket/HTTP）解耦，实现可选择
- **先中心化，后分布式**：原型阶段 Hub 为中心化部署；后续基于 RFC-005 设备级自治，Hub 功能可分布到多节点
- **未定义事项**：结算与计量（P3-4）、结果验证机制、TEE 远程验证等留待后续 RFC

---

## 附录 A：消息 JSON Schema 索引

本 RFC 中定义的消息格式已整理为独立的 spec 文件：

- `spec/task-execution.messages.json` — 所有消息的 JSON Schema（✅ 已创建）
- `spec/task-lifecycle.yaml` — 状态机定义（✅ 已创建）

## 附录 B：原型实现建议

阶段二开发最小 Hub/Worker 时，建议按以下顺序实现：

1. **RegisterNode + Heartbeat** — 节点注册与心跳，资源表更新
2. **AssignTask + TaskAccept/Reject** — 任务下发与确认
3. **TaskStatus + TaskComplete + TaskFail** — 执行与结果上报
4. **数据传输（reference 模式）** — 节点下载输入、上传输出
5. **PreemptTask + PreemptAck** — 抢占与 checkpoint（可延后）
6. **CancelTask + timeout** — 取消与超时（可延后）
