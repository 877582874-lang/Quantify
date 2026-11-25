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
- 阶段 6（2026-11-25 10:49 Cl 批准后完成）：最小协调器上线——设计 COORDINATOR_DESIGN.md（Muse），实现 coordinator.py + 5 项测试 + recover systemd timer（Codex 著，/opt/ai-coordinator 本地仓库 main 已合并 a0bae55）。端到端实证：TASK-02 真实任务、TASK-03 P2 闸门（无批准拒跑/批准放行）、TASK-04 返工闭环（修 Muse 复核查出的两处缺陷）均经协调器执行并 accepted。timer 每 5 分钟 recover 巡检，已启用且实际触发过（recovered=0）；VM 替换清 /etc/systemd/system 后按 /opt/ai-coordinator/systemd/ 源文件重装。报告 reports/2026-11-25/multi-agent-002-stage6-report.txt。—— Muse，2026-11-25
- **沙箱 .git 只读（重要环境事实）**：Codex workspace-write 沙箱把工作区根仓库的 .git 设为只读（init/add 均报 Read-only file system，实测复现）；子目录仓库不受限（QUESTIONS 审查能提交是因此）。分工定论：git init/分支/提交由 Muse 在沙箱外执行，提交以 author=ChatGPT / committer=Muse 双字段如实区分；Codex 只写代码。—— Muse，2026-11-25
- 阶段 7–10：自动分配与返工常态化、审计与审批加强（P0–P3，含生产数据目录系统层强隔离——仍未做，不得宣称已实现）、连续运行测试、总验收（交付物 A–I）——**待 Cl 批准**。—— Muse，2026-11-25

## 关键约定

- 权限四级 P0 只读 / P1 开发 / P2 生产（Cl 批准）/ P3 高风险（Cl 逐次批准）；能用系统层隔离就不用提示词自律——但现状是 Codex 对生产数据目录主要靠红线+任务信封约束，强隔离待阶段 6–8，**不得宣称已实现**。—— Muse，2026-11-25
- Codex 不得自审其代码：初核 Muse、独立审查 ChatGPT。返工上限 3 轮。—— Muse，2026-11-25

—— Muse，2026-11-25
