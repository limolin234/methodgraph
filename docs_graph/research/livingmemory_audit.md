# LivingMemory 审计与选型

## 审计对象

- 仓库：<https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory>
- 审计 commit：`8fe9bb1d2128678ee10105a3948751e0c84952cd`
- commit 时间：2026-08-14T15:50:37+08:00
- 许可证：AGPL-3.0

## 值得借鉴的工程设计

LivingMemory 已实现不少成熟的长期记忆工程能力：

- SQLite 权威存储；
- BM25、FAISS、图关键词和图向量的双路四模式检索；
- RRF 融合；
- 原始消息来源保留；
- 记忆原子、TTL、衰减和访问强化；
- 写操作日志；
- 迁移前与版本变化备份；
- 影子索引构建、验证后切换和失败保留旧代际；
- AstrBot 自动钩子和 Agent 主动工具；
- WebUI 图浏览和生命周期管理。

这些设计证明“检索、自动触发、主动查询、图扩展、索引重建和恢复保护”能够组合在一个实际插件中。

## 不直接 fork 的原因

1. 它的数据本体是事实、事件、偏好、计划和实体关系；MethodGraph 的本体是人类整理的方法论。
2. 它的运行边界直接依赖 AstrBot 事件、Provider、工具和页面 API，不适合作为 Codex/ChatGPT 共用核心。
3. 图边会跨记忆按相同实体关系合并并累加权重，这不等价于带独立版本和来源的方法关系。
4. 备份与删除回滚主要保护存储和索引一致性，尚不是方法层与图层各自可回退的历史模型。
5. AGPL-3.0 对修改和网络服务分发有明确传染性要求；除非项目主动选择 AGPL，否则应借鉴设计而不复制实现。

## 采用决定

MethodGraph 独立实现小内核，复用思想而不复用代码：

- 保留 SQLite、混合检索、影子投影和来源审计思想；
- 将方法与图改为当前投影 + 修订快照 + 审计日志，不复刻 Git commit DAG；
- 将来源设为内容寻址且不可变；
- 将运行事件设为只追加；
- 使用 MCP 作为 Codex/ChatGPT 的首要适配器；
- 使用 Codex UserPromptSubmit Hook 与同一只读 MCP 检索服务组合；
- AstrBot 后续只通过相同 service API 增加薄适配器。
