
# 安全政策

## 支持的版本

| 版本   | 支持状态 |
| :----- | :------: |
| latest |    ✅    |

## 漏洞披露

请勿在公开 Issue 中报告安全漏洞。请通过以下方式私密披露：

- 邮箱：justin9302@gmail.com
- 或使用 GitHub 的 [Private Vulnerability Reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability)

我们将在 72 小时内确认收到，并在 30 天内给出修复计划。

## 安全设计原则

- **零信任**：Hub 与 Server 之间默认不互信，必须通过 mTLS/SPIFFE 认证。
- **最小权限**：任务仅能访问其声明的算力资源，禁止跨租户访问。
- **审计透明**：所有调度日志可导出用于第三方安全审计。
