
# 贡献指南

欢迎参与 Open Compute Router！我们遵循"协议先行，代码渐进"的原则。

## 贡献方式

1. **RFC 提案**：在 `docs/` 下新建 `RFC-XXX-title.md`，遵循现有 RFC 格式。
2. **协议规范修订**：修改 `spec/` 下的 JSON Schema 或 YAML，并附上变更理由。
3. **参考实现**：用任何语言实现 Hub/Server 模拟器，放入 `examples/`。
4. **电网数据适配器**：接入真实电网开放 API（AEMO、PJM、ENTSO-E 等）。

## 开发流程

1. Fork 本仓库。
2. 创建特性分支：`git checkout -b rfc/sla-tiering-update`。
3. 提交 PR，清晰描述变更动机与影响。
4. 至少获得 1 名维护者 Review 后方可合并。

## 行为准则

请阅读 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。我们致力于维护一个开放、中立、尊重的社区。

## 许可证

您的贡献将自动遵循 [Apache License 2.0](LICENSE)。提交 PR 即表示您同意该许可。
