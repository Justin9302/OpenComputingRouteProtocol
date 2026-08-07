open-compute-router/
├── README.md                          # 项目主页
├── LICENSE                            # Apache License 2.0
├── CONTRIBUTING.md                    # 贡献指南
├── CODE_OF_CONDUCT.md                 # 行为准则
├── SECURITY.md                        # 安全漏洞披露政策
├── CHANGELOG.md                       # 版本变更日志
│
├── docs/
│   ├── architecture.md                # 三层架构说明
│   ├── RFC-001-protocol-overview.md   # 协议总览
│   ├── RFC-002-sla-tiering.md         # 时效分级调度（1min~24h）
│   ├── RFC-003-security-model.md      # 零信任安全模型
│   └── RFC-004-energy-integration.md  # 算电协同与电网数据接入
│
├── spec/
│   ├── compute-intent.schema.json     # MCP 算力意图 JSON Schema
│   ├── sla-tiers.yaml                 # SLA 分级定义表
│   └── energy-plugin.interface.yaml   # 能源插件接口定义
│
├── examples/
│   ├── mcp-intent-example.json        # MCP 意图调用示例
│   ├── hub-simulator.py               # Hub 极简模拟器（Python）
│   ├── energy-price-mock.py           # 模拟电价生成器
│   └── server-node-mock.py            # Server 节点模拟器
│
├── .github/
│   ├── ISSUE_TEMPLATE/
│   │   ├── bug_report.md
│   │   ├── feature_request.md
│   │   └── rfc_proposal.md
│   ├── PULL_REQUEST_TEMPLATE.md
│   └── workflows/
│       └── spec-lint.yml              # 协议规范自动检查
│
└── assets/
    └── logo.svg                       # 项目 Logo（占位）
