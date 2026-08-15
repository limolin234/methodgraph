# Agent 接入

核心服务只需部署一次。每台 Agent 客户端安装同一 Python 包和一份很薄的配置；客户端不接触 Git 仓库、SQLite 或 embedding 模型。

```bash
python -m pip install 'methodgraph[mcp]'
mkdir -p ~/.config/methodgraph
cp integrations/config.toml.example ~/.config/methodgraph/config.toml
```

客户端配置只需要 `[client].server_url`。从 `127.0.0.1` 切换实验室域名时不需要改 Hook 或 MCP 命令。

## Codex

在 `~/.codex/config.toml` 中加入：

```toml
[mcp_servers.methodgraph]
command = "methodgraph-mcp"
enabled_tools = ["methodology_search", "methodology_get", "methodology_neighbors"]

[mcp_servers.methodgraph_admin]
command = "methodgraph-admin-mcp"
```

在 `~/.codex/hooks.json` 中合并 `integrations/codex/hooks.json.example`。首次启用或命令变化后，使用 `/hooks` 审查并信任。Hook 失败时不阻断用户请求；主动 MCP 仍可在任务中途检索或沿图探索。

管理 MCP 用本机以下身份作为 Git commit Author；服务端固定为 Committer：

```bash
git config --global user.name "Your Name"
git config --global user.email "you@lab.example"
```

## Claude Code

Claude Code 支持 HTTP `UserPromptSubmit` Hook，可直接 POST 到：

```text
http://SERVER/v1/hooks/claude/user-prompt-submit
```

也可以把 `methodgraph-mcp` 注册为 stdio MCP，用于主动检索。写权限只给需要维护知识库的环境，不应默认向所有 Agent 暴露管理 MCP。

## 其他 Agent

优先使用 stdio MCP，配置命令为 `methodgraph-mcp`。不支持 MCP 但支持提交前 Hook 的客户端，可把 Hook 输入原样 POST 到 `/v1/hooks/retrieve`；响应为空对象时不注入，响应中的 `hookSpecificOutput.additionalContext` 是要加入模型上下文的文本。

服务 API key 与 embedding API key 是两件事。当前实验室内版本暂不做服务认证；embedding key 只存在服务端环境变量中，客户端配置不应包含它。
