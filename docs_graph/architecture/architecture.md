# 实现架构

## 权威边界

Git HEAD 是来源、方法和关系的唯一权威。内容仓库一对象一文件：`sources/<id>.md`、`methods/<id>.md`、`relations/<id>.md`。Markdown frontmatter 保存结构化索引字段，正文保存来源原文或第二层 Detail。

SQLite 只保存当前结构化投影、embedding 向量、`indexed_commit`、激活与冷却事件。启动时若 Git HEAD 与 `indexed_commit` 不同，先校验引用再原子替换结构投影。同步失败时不能推进 `indexed_commit`，更不能让 SQLite 反写 Git。

## 写入与回滚

```text
admin MCP/HTTP
  -> validate identity, fields, references, expected revision
  -> serialize under one process lock
  -> Git commit (client Author, server Committer)
  -> optional fast-forward push
  -> synchronous structured projection refresh
  -> asynchronous embedding refresh
```

客户端不接触内容仓库。`expected_revision` 使用当前文件 blob revision 做乐观并发控制。删除文件代表退役；删除方法前必须先删除连接它的关系。恢复读取历史 commit 中的文件并产生新的 restore commit，禁止 reset、force-push 和让数据库覆盖 Git。

当前身份是审计归因，不是安全认证。实验室部署依赖可信网络与最小化管理 MCP 暴露；公网部署前必须另加认证和授权层。

## 读取

```text
current context
  -> lexical + optional embedding seed retrieval
  -> relevance threshold
  -> revision/session/semantic de-duplication
  -> bounded one-hop graph expansion
  -> compact cards + brief edges + sources
```

Hook 只向核心 HTTP 服务提交当前输入和会话信息，最近输入、检索冷却和 embedding 都在服务端处理。Hook 失败开放，不本地加载模型或数据库。MCP 是 stdio HTTP 薄代理，用于计划阶段主动搜索、读取细则和沿图探索。

embedding provider 由 TOML 配置为 `local`、`openai_compatible` 或 `none`。文档向量在后台生成；查询只编码当前 query 并读取已有 projection，缺失时退回词法检索。
