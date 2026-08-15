# 实现架构

## 两种入口，一套检索

`methodgraph-mcp` 运行只读 MCP，默认支持 stdio，也支持绑定本机的 streamable HTTP。Codex 的 `UserPromptSubmit` Hook 通过同一个 HTTP 服务搜索并把文本放进 `hookSpecificOutput.additionalContext`；HTTP 不可用时 Hook 自动降级为本地无 embedding 词法检索并失败开放。MCP 用于任务中途的主动搜索、细则读取和邻图探索。

常驻 HTTP 服务让 Hook 与 MCP 共用 embedding 模型和 SQLite 连接。跨平台环境也可以分别启动 stdio MCP 和本地 Hook；transport、数据库和模型都由环境变量配置。

## 权威存储

SQLite 的 `mg_methods`、`mg_relations` 是当前投影；`mg_revisions` 保存每次 create/update/retire/restore 的完整快照、事务号、actor、权限、原因和时间；`mg_sources` 按 SHA-256 内容去重且不原地修改；`mg_activation_events` 保存检索/注入账本。embedding 表是可重建投影，revision 改变后旧投影自动失效。

每次写入在数据库事务中完成。没有物理删除接口。恢复是从历史快照产生一次新的 update/restore，因此审计链不会被改写；严重事故使用 SQLite/WAL 的备份恢复。批量导入可以在上层复用 transaction_ref 做整批追踪。

## 检索

```text
current context
  -> lexical + optional local embedding seed retrieval
  -> relevance threshold
  -> exact/revision/semantic/session de-duplication
  -> bounded one-hop weighted graph expansion
  -> compact cards + brief edges + compact citations
```

默认最多六张卡和两条邻边预算，实际不足不凑数。Hook 使用更严格阈值；相同 session、method、revision 的近期注入默认冷却，版本更新或 `exclude_recent=false` 才重新返回。模型永远看不到向量分数、边权、relation ID 以外的内部索引细节或检索调试原因。

## 接入配置

Codex 项目配置位于 `.codex/config.toml`，Hook 位于 `.codex/hooks.json`。Codex 官方要求首次运行非托管命令 Hook 时在 `/hooks` 审查和信任。管理 MCP 作为独立 server 默认注册，但进程固定为 `agent` 身份；它通过独立命令和权限环境供摄入 Agent 使用，不会因为模型传入参数而获得人工权限。
