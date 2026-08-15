# MethodGraph

MethodGraph 是给长任务 Agent 使用的外部方法论层。它保存的是“面对什么问题、为什么采用某种处理方式、怎样处理、哲学基础和边界”，不是事实记忆，也不是工具 Skill。方法卡只要求 `title`；其余字段和第二层 `detail` 只有证据支持时才填写。

## 运行时结构

同一个只读服务同时服务两个入口：

```text
Codex UserPromptSubmit Hook ─┐
                              ├─> MethodGraph HTTP/SQLite ─> embedding + graph retrieval
Codex/ChatGPT MCP tools ──────┘
```

运行时 MCP 只有三个工具：

- `methodology_search(context, method_limit, neighbor_limit, exclude_recent, ...)`：语义/词法召回、有限一跳图扩展、去重、低相关过滤和会话冷却；返回方法卡、简短关系和紧凑来源。
- `methodology_get(items, mode)`：批量读取 `method`、`relation`、`source`。`detail` 只给新增细则，`full` 给完整内容，`audit` 给来源和修订历史。
- `methodology_neighbors(method, context, limit, cursor)`：主动沿图探索邻居。

管理 MCP (`methodgraph-admin-mcp`) 与运行时 MCP 分开。它提供来源、方法、关系的增改退役、历史、差异和恢复。进程环境中的 `METHODGRAPH_ACTOR_AUTHORITY=human|agent` 决定权限；Agent 不能修改或退役人工内容，来源永远按内容哈希不可变。退役是软删除，恢复会产生新修订。SQLite 事务、修订快照和审计日志负责追溯，不自造 Git DAG。

## 安装与测试

推荐使用已有的 micromamba 环境：

```bash
micromamba activate methodgraph
python -m pip install -e '.[mcp,embedding]'
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

无 embedding 依赖也可以使用词法检索：

```bash
METHODGRAPH_DB=/path/to/methodgraph.db METHODGRAPH_EMBEDDING_MODEL=none methodgraph-mcp
```

启用本地 Qwen embedding。服务会在后台线程中生成缺失或过期的 projection，查询阶段只读取已经生成的 projection；因此查询不会同步扫描全库或临时生成全部索引：

```bash
METHODGRAPH_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-4B \
METHODGRAPH_EMBEDDING_DEVICE=cuda \
METHODGRAPH_DB=/path/to/methodgraph.db methodgraph-mcp
```

默认每 60 秒检查一次新增和修订内容。可用环境变量调整：

```bash
METHODGRAPH_INDEX_INTERVAL=60       # 秒，最小 10
METHODGRAPH_INDEX_MODE=off          # off/manual/false 可关闭后台索引
```

也可以在部署前显式预生成索引：

```bash
methodgraph index --db /path/to/methodgraph.db \
  --model Qwen/Qwen3-Embedding-4B --device cuda
```

后台索引失败不会阻塞服务；在 projection 尚未准备好时，检索自动退回词法检索，后台下一周期继续重试。

HTTP 服务和 Codex 配置示例在 [`integrations/codex/`](integrations/codex/)；项目内的 `.codex/config.toml` 和 `.codex/hooks.json` 已指向 `127.0.0.1:8765` 与 Hook 命令。需要先启动 HTTP 服务，例如使用 `methodgraph.service.example` 的 user service。Codex 首次使用项目 Hook 时，在 `/hooks` 中审查并信任该命令。

没有 user service 的环境可以直接前台启动：

```bash
METHODGRAPH_DB=/path/to/methodgraph.db \
METHODGRAPH_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-4B \
METHODGRAPH_EMBEDDING_DEVICE=cuda \
METHODGRAPH_TRANSPORT=streamable-http methodgraph-mcp
```

管理 MCP 现在也默认注册，但默认进程身份固定为 `ingestion-agent`/`agent`。它可供负责收集论文、书籍和论坛方法论的 Agent 使用；人工维护时应显式启动一个 `METHODGRAPH_ACTOR_AUTHORITY=human` 的管理进程。

详细的字段、来源、关系、检索和回滚约束见 [`docs_graph/`](docs_graph/)。
