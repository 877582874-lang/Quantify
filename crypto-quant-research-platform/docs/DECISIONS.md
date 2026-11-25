# DECISIONS — 关键决策留档

规则：重要决策一行一记，含日期、决策、决定方、依据/影响。只记已拍板且已落地的；推测与讨论不入此册。维护：Muse，2026-11-25 建立。

| 日期 | 决策 | 决定方 | 依据 / 影响 |
|---|---|---|---|
| 2026-11-24 | 数据管线分层：Raw 不可变（JSON.gz+SHA）→ DuckDB 规范化 → Parquet 日分区 + SQLite 台账 | Cl 任务书 / Muse 实施 | 全项目数据架构基线，见 docs/ARCHITECTURE.md |
| 2026-11-24 | 规范化仅收已收盘（final）K 线；主键 (exchange, market_type, instrument_id, interval, open_time_utc)；内部时间一律 UTC | Muse（A2 标准） | 见 docs/DATA_CONTRACT.md |
| 2026-11-24 | B1 阶段边界：只做行情采集/存储；不做策略、回测、交易功能 | Cl | PROJECT_CONTEXT.md 第 1 节 |
| 2026-11-24 | 游标语义修复：ingest_cursor_utc = 实际入库的最老 open，仅在落盘→入库→单事务登记后推进 | Muse（B1-R） | 根治接缝缺根事故；8 项回归测试背书 |
| 2026-11-24 | 采集器铁律：只插新根、永不改写已存 K 线；迟到修正只记异常 | Muse（步骤 4） | 历史数据不可改写原则的执行机制 |
| 2026-11-24 | 留档三件套（代码快照+CHANGELOG+报告）+ 谁改谁署名 + 仓库优先汇报 | Cl | Quantify 仓库 README 与 memory/rules-and-preferences.md |
| 2026-11-24 | 全部后台巡查删除、Pause: all（额度教训） | Cl | 重建须 Cl 重新下令 |
| 2026-11-25 | ChatGPT 获 ECS root SSH 与 Quantify 可写部署钥（PKCS#8 一把，QY9hO2ZN…） | Cl 明确指令（00:29） | 撤销方法见 memory/infrastructure.md |
| 2026-11-25 | Codex 出网经 mihomo 借道新加坡（OpenAI 对香港 403）；订阅只存服务器、永不入仓 | Muse 实施 / Cl 提供订阅 | memory/infrastructure.md |
| 2026-11-25 | Claude Code 卸载、先不考虑（Anthropic 封号风险） | Cl | memory/claude-code.md |
| 2026-11-25 | 跨会话记忆库建于 Quantify 仓库 memory/，Muse 主动维护、条条署名 | Cl | memory/README.md |
| 2026-11-25 | 项目统一规则文件为项目根目录 PROJECT_CONTEXT.md（任务书建议的 docs/ 同名路径不另建，避免双份漂移） | Cl 定名 / Muse 建议保留根路径 | 本知识库其余文件置于 docs/ |
| 2026-11-25 | 三 AI 分工：ChatGPT 总体技术负责人、Muse 运维+Codex 调度者、Codex 主要开发者；Muse 直接指挥 Codex，不经用户转述 | Cl（MULTI-AGENT-002） | docs/AGENT_RULES.md |
| 2026-11-25 | Codex 调度机制选定 CLI `codex exec`（+ resume）；app-server 保留为 iPhone 交互通道 | Muse 实测后定（阶段 1–3） | 13 项调用链实测报告 |

—— Muse，2026-11-25
