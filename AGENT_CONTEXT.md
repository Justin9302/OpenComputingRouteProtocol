# AGENT_CONTEXT.md — AI 工作上下文

> 本文件用于帮助 AI 助手在会话开始时快速识别项目身份与工作模式。
> **开始本项目工作前，请先阅读本文件。**

---

## 1. 项目身份

| 项目 | 值 |
|------|-----|
| 项目名称 | OpenComputingRouteProtocol (OCR) |
| 项目定位 | 分布式算力路由协议（算电协同） |
| 工作目录 | `work/projects/OpenComputingRouteProtocol/` |
| GitHub 远程 | `https://github.com/Justin9302/OpenComputingRouteProtocol.git` |
| 主分支 | `main`（直接推送 GitHub） |

## 2. 工作模式（关键！）

> ⚠️ **本项目是独立 Git 仓库，不遵循外层仓库的双轨制（harness/work）。**

| 规则 | 说明 |
|------|------|
| **独立仓库** | 有自己的 `.git`，所有提交、推送都直接在本项目内完成 |
| **直接推送** | 提交后直接 `git push origin main` 到 GitHub |
| **无 harness 分支** | 不适用外层 `git sync-harness`、`git harness-commit` |
| **无 work 前缀** | 提交信息**不要**加 `work(...)` 前缀 |
| **外层忽略** | 外层 `.gitignore` 已忽略此目录，外层不会跟踪它 |

## 3. 提交规范

```
<类型>(<范围>): <简短描述>
```

| 类型 | 用途 |
|------|------|
| `feat` | 新功能/新文件 |
| `docs` | 文档变更 |
| `chore` | 配置/工具变更 |
| `fix` | 缺陷修复 |

示例：
```bash
git commit -m "docs: 完善 RFC-002 时效分级调度模型"
git commit -m "feat: 新增 energy-price-mock 模拟器"
```

## 4. 当前进度

> 详见 [ROADMAP.md](ROADMAP.md)

- **阶段**: Phase 0（补齐可演示的最小资产）
- **已完成**: 协议文档、规范文件、模拟器、社区文档、CI
- **进行中**: `examples/` 下缺 `energy-price-mock.py`、`server-node-mock.py`、`mcp-intent-example.json`
- **聚焦场景**: 用 T8-T10 低优任务在澳洲 NEM 负电价时段跑训练任务

## 5. 会话启动流程

当用户表示"开始 OCR / 算力路由 / 本项目工作"时，AI 应执行：

```bash
cd work/projects/OpenComputingRouteProtocol
git branch --show-current        # 确认在 main
git status --short               # 查看未提交变更
git log --oneline -5             # 查看最近历史
git ls-remote origin main        # 确认远程同步状态（可选）
```

然后根据用户意图，结合 `ROADMAP.md` 判断当前应推进的任务。

## 6. 常用命令速查

```bash
# 提交并推送
git add <files> && git commit -m "<type>(<scope>): <desc>"
git push origin main

# 同步远程（若有网页端编辑）
git fetch origin
git rebase origin/main

# 查看规范文件
cat spec/compute-intent.schema.json
cat spec/sla-tiers.yaml
```

## 7. 文档导航

| 文件 | 用途 |
|------|------|
| [README.md](README.md) | 项目门面（愿景、架构、快速开始） |
| [ROADMAP.md](ROADMAP.md) | 路线图与当前阶段 |
| [docs/RFC-001.md](docs/RFC-001-protocol-overview.md) | 协议总览与三层架构 |
| [docs/RFC-002.md](docs/RFC-002-sla-tiering.md) | 时效分级调度模型 |
| [docs/RFC-003.md](docs/RFC-003-security-model.md) | 零信任安全模型 |
| [docs/RFC-004.md](docs/RFC-004-energy-integration.md) | 算电协同与电网数据接入 |
| [docs/RFC-005.md](docs/RFC-005-resource-table-and-device-level-autonomy.md) | 设备级自治与资源表 |
| [docs/RFC-006.md](docs/RFC-006-task-execution-protocol.md) | 任务执行与生命周期协议 |
| [docs/PROTOCOL_IMPROVEMENTS.md](docs/PROTOCOL_IMPROVEMENTS.md) | 协议改进待办清单 |
| [docs/DEVELOPMENT_PLAN_2026Q3-Q4.md](docs/DEVELOPMENT_PLAN_2026Q3-Q4.md) | 发展计划（2026 Q3-Q4） |
| [spec/](spec/) | 协议规范（Schema / YAML） |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 贡献指南 |
