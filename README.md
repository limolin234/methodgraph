# MethodGraph

MethodGraph 是给长任务 Agent 使用的外部方法论层。它保存“什么时候用、为什么用、怎样处理、哲学基础和边界”，不是事实记忆，也不是工具 Skill。

```text
Codex/Claude Hook ─┐
                    ├─> JSON HTTP service ─> SQLite query projection
thin read MCP ──────┤                         ├─ embedding projection
thin admin MCP ─────┘                         └─ activation/cooldown state
                              |
                              └─ Git content repository (only authority)
```

Git 仓库按对象存放 `methods/<id>.md`、`relations/<id>.md` 和 `sources/<id>.md`。SQLite 只保存可重建的结构化/embedding 投影和运行时冷却状态，永远不能覆盖 Git。写入顺序是校验、Git commit、可选 push、同步结构投影、后台补 embedding；查询不会临时重建全库索引。

## 安装

```bash
micromamba activate methodgraph
python -m pip install -e '.[runtime]'
cp integrations/config.toml.example ~/.config/methodgraph/config.toml
methodgraph-server
```

不需要本地 embedding 时：

```toml
[embedding]
provider = "none"
model = "none"
```

使用 OpenAI-compatible `/embeddings` API 时：

```toml
[embedding]
provider = "openai_compatible"
model = "text-embedding-3-large"
base_url = "https://api.openai.com/v1"
api_key_env = "METHODGRAPH_EMBEDDING_API_KEY"
batch_size = 32
```

配置文件只记录环境变量名，密钥放在服务进程环境中。完整示例见 [`integrations/config.toml.example`](integrations/config.toml.example)。

## 迁移与运行

首次从旧 SQLite 迁移：

```bash
methodgraph --db /path/to/old.db migrate-git --content-repo /srv/methodgraph/content
methodgraph --db /srv/methodgraph/runtime.db sync-git --content-repo /srv/methodgraph/content
```

迁移会把当前有效来源、方法和关系导出为一个 Git commit，再从 Git 重建投影。旧数据库应保留为迁移备份；历史回退使用新的 restore commit，不 reset 或 force-push。

健康检查：

```bash
curl http://127.0.0.1:8765/healthz
```

## 模型侧接口

只读 MCP 有三个工具：

- `methodology_search`：语义/词法召回、有限一跳图扩展、去重、低相关过滤和会话冷却。
- `methodology_get`：批量读取方法、关系和来源的 detail/full/audit 内容。
- `methodology_neighbors`：沿图主动探索。

管理 MCP 提供来源、方法、关系的增改删、Git history 和 restore。每次写入使用客户端 `git config user.name/user.email` 作为 Author，服务身份作为 Committer；身份用于归因而非认证。当前版本适用于可信实验室网络，不应暴露到公网。

Codex、Claude Code 和其他 Agent 的安装方式见 [`integrations/README.md`](integrations/README.md)。

## 测试

```bash
PYTHONPYCACHEPREFIX=/tmp/methodgraph-pycache \
PYTHONPATH=src \
python3 -m unittest discover -s tests -v
```

基础环境没有 `server` extra 时 HTTP 用例会跳过；安装 `runtime` extra 的部署环境应运行全部测试。
