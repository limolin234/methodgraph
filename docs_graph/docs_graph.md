# MethodGraph 项目图

MethodGraph 的目标是把成熟领域方法论作为外部程序性上下文注入长任务 Agent。它与事实记忆、自动摘要和工具 Skill 分离：方法论回答的是“当前问题应当怎样被看待、分解、验证和推进”。

本项目的稳定决策分为三块：

- [`methodology/methodology.md`](methodology/methodology.md)：方法卡的 `when -> why -> how -> philosophy -> boundary`、第二层 detail、来源和无类型加权关系。
- [`architecture/architecture.md`](architecture/architecture.md)：SQLite 权威存储、embedding/图检索、MCP、Codex Hook、权限和回滚。
- [`research/livingmemory_audit.md`](research/livingmemory_audit.md)：对早期 living-memory 原型的取舍记录。

默认模型上下文只包含高相关方法卡、少量关系说明和紧凑来源。完整细节和审计历史由 MCP 按需读取。运行时不允许 Agent 改写知识库；负责摄入论文、书籍、标准和论坛内容的 Agent 使用隔离的管理 MCP，并且其内容保持 `authority=agent`，等待人工审核提升。
