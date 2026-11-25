# DISPATCH_DESIGN — 自动调度与返工闭环设计（阶段 7）

设计：Muse，2026-11-25。实现：Codex（TASK-20261125-05 / 06）。前置：阶段 6 协调器（coordinator.py）。
一句话：Muse 把一份 brief 拆成子任务信封后交给 driver 无人值守执行；driver 只做**机械验收**，语义复核仍归 Muse，独立审查仍归 ChatGPT。

## 1. 组件

- `coordinator/driver.py` —— 调度驱动器（stdlib only），通过子进程调用 coordinator.py 的 CLI（add / run / review），不直改数据库。
- 前置小改（TASK-20261125-05）：coordinator 的 `run` 增加 `--note <text>`，把说明追加进给 Codex 的 prompt 并记 `run_note` 事件——返工轮次靠它传递失败证据。

## 2. Brief 格式（driver 的输入 JSON）

```json
{
  "brief_id": "BRIEF-20261125-01",
  "title": "……",
  "max_rework": 2,
  "subtasks": [ { 子任务信封（TASK_PROTOCOL 全部字段）, "verify_cmd": "可选，workdir 内执行的验收命令" } ]
}
```

子任务按数组顺序串行执行；任一子任务升级（见 §4），整份 brief 停止，后续子任务不启动。

## 3. 机械验收（driver 在每次 run 后执行，全部通过才算过）

1. Codex 退出码为 0（取 coordinator 记录的 codex_exit）。
2. `verify_cmd`（若给了）在 workdir 内执行退出码为 0。
3. Git 合规（workdir 是 git 仓库时）：以 run 前的 `git status --porcelain` 为基线，run 后**新增**的变更路径必须 ⊆ allowed_files（fnmatch 匹配）且与 forbidden_files 无交集。基线里已有的脏文件不背锅。

机械验收只判可机械判定的事：测试过没过、文件动没动错。**写得对不对、好不好，仍由 Muse 语义复核**（driver 绝不自动 accepted）。

## 4. 自动返工与升级

- 机械验收失败 → driver 以失败证据拼返工说明：先 `review --verdict rework --note "driver机械验收失败：<证据>"`（占用协议返工计数），再 `run --note <证据+修复要求>`，重新验收。
- 自动返工上限 = brief 的 max_rework（默认 2；且受协调器 3 轮硬上限约束）。
- 仍失败 → 该子任务标 ESCALATED，driver 停整份 brief、退出码非零，等 Muse 处置（语义判断或升级 Cl/ChatGPT）。

## 5. 产出

- 汇总：`/opt/ai-coordinator/reports/driver-<brief_id>.txt`（人读）+ 同名 `.json`（机读）：每子任务的轮次、每轮验收结果、最终状态、证据指针（exec.log / diff.patch 路径）。
- 子任务状态停在 returned（过机械验收）或 rework（升级时），等 Muse 逐个语义复核后 review accepted/rejected。

## 6. ChatGPT 审查包（步骤 9 的交接约定）

每份 brief 完成后，Muse 生成审查包 `reviews/<brief_id>-review.md`（入 Quantify 仓库 multi-agent/reviews/）：含 brief 目标、子任务清单与状态、关键 diff（或指针）、测试输出摘要、Muse 复核结论、待 ChatGPT 裁决的问题。ChatGPT 在其通道审阅后把意见追加到包尾（署名 ChatGPT + 日期）或写入 QUESTIONS.md。

## 7. 非目标

不并行（串行保一写者原则）、不自动合并/部署、不自动语义验收、不绕过 P2/P3 闸门（driver 对 P2/P3 子任务同样走 coordinator 的批准检查，无批准即拒跑并升级）。

—— Muse，2026-11-25
