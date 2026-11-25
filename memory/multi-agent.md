# 三 AI 协作体系（MULTI-AGENT-002）

最后更新：2026-11-25（Muse）。任务书由 Cl 于 2026-11-25 10:34 下达。

## 分工（Cl 定立）

- **ChatGPT**：总体技术负责人——架构、技术路线、任务拆分与任务书、研究与统计审查、关键决策、Codex 代码独立审查、最终验收建议。
- **Muse**：服务器运维负责人 + Codex 调度者——ECS 运维、环境与服务、Codex 运行维护；**直接指挥 Codex**（不经 Cl 转述）、跟踪、返工、初步复核、向 ChatGPT 交执行报告；原则上不写大量业务代码。
- **Codex**：主要代码开发者——全部开发、测试、文档、分支提交。
- **Cl**：资金、安全、不可逆操作与 P2/P3 的唯一批准人。

## 进度

- 阶段 1–3（2026-11-25 上午）：盘点 + 调用链 13 项实测全过 + 机制选定 CLI `codex exec`（+resume）。见 `codex-remote.md` 补充段与报告 reports/2026-11-25/multi-agent-002-stage1-3-report.txt。—— Muse，2026-11-25
- 阶段 4–5（2026-11-25 10:44 Cl 批准后完成）：任务协议 TASK_PROTOCOL.md + 任务/结果模板部署于服务器 /opt/ai-coordinator/protocol/（留存目录 /opt/ai-coordinator/tasks/<task_id>/）；知识库四文件（DECISIONS / CURRENT_STATUS / KNOWN_ISSUES / AGENT_RULES）部署于 /opt/okx-quant/docs/，PROJECT_CONTEXT.md 保留根目录并已补索引。报告 reports/2026-11-25/multi-agent-002-stage4-5-report.txt。—— Muse，2026-11-25
- 阶段 6–10：最小协调器（Python+SQLite+systemd，/opt/ai-coordinator）、自动分配与返工、审计与审批（P0–P3，含系统层隔离加固）、连续运行测试、总验收（交付物 A–I）——**待 Cl 批准**。—— Muse，2026-11-25

## 关键约定

- 权限四级 P0 只读 / P1 开发 / P2 生产（Cl 批准）/ P3 高风险（Cl 逐次批准）；能用系统层隔离就不用提示词自律——但现状是 Codex 对生产数据目录主要靠红线+任务信封约束，强隔离待阶段 6–8，**不得宣称已实现**。—— Muse，2026-11-25
- Codex 不得自审其代码：初核 Muse、独立审查 ChatGPT。返工上限 3 轮。—— Muse，2026-11-25

—— Muse，2026-11-25
