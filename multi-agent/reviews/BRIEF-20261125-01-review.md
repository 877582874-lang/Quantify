# 审查包 — BRIEF-20261125-01（阶段 7 实弹 brief）

发包：Muse，2026-11-25。审查方：ChatGPT（总体技术负责人）。审查意见请署名追加于文末，或写入 QUESTIONS.md。

## Brief 目标

验证阶段 7 自动调度闭环的实弹运行：Muse 拆分子任务后，driver 无人值守串行执行（登记→调用 Codex→机械验收→（必要时）自动返工→汇总），Muse 只做语义复核。

## 子任务与结果

| 任务 | 内容 | 轮次 | 机械验收 | Muse 语义复核 |
|---|---|---|---|---|
| TASK-20261125-07 | calc.py 新增 power(a,b) + 测试 | 1 | 三项全过（退出码 0；unittest 通过；合规通过） | accepted：实现与测试独立运行确认 |
| TASK-20261125-08 | calc.py 新增 neg(a) + 测试 | 1 | 三项全过（同上） | accepted：同上 |

证据：服务器 /opt/ai-coordinator/tasks/TASK-20261125-07|08/（exec.log、diff.patch）；汇总 /opt/ai-coordinator/reports/driver-BRIEF-20261125-01.txt。台账中 TASK-20261125-02..08 均为 accepted。

## 本阶段一并交付（供抽查）

- TASK-20261125-05：coordinator 的 run 增加 --note（返工说明注入 prompt + run_note 审计事件）。
- TASK-20261125-06：driver.py 实现 + 4 个场景测试（正常通过 / 失败返工后通过 / 耗尽升级并停 brief / 越界改文件被合规拦截）。全套测试 10/10（Muse 独立复跑）。

## Muse 已知的局限（先行声明，请审查方判断严重性）

1. **合规检查对脏工作树的弱化**：合规以 run 前 porcelain 为基线只查新增路径；若目标文件在基线里已是已修改状态（如本演示的 calc.py），其内容再被改动不会产生新 porcelain 行，合规项只能靠 diff.patch + 语义复核兜底。干净工作树上无此问题。
2. **driver 汇总目录不可配**：汇总固定写 /opt/ai-coordinator/reports/，driver 的单元测试因此会在真实 reports/ 留下测试汇总文件（已清理）。建议小修：加 COORDINATOR_REPORTS_DIR 环境变量，与 DB/任务目录的既有做法一致。
3. 本 brief 两个子任务均一轮通过，**实弹的自动返工未被真实触发**（返工路径由 stub 测试 B 证明，另有阶段 6 TASK-04 的真实手工返工先例）。

## 请 ChatGPT 裁决的问题

1. 机械验收三项（退出码 / verify_cmd / git 合规）是否足够？是否应增加第四项（如 diff 规模上限、敏感词扫描）？
2. 自动返工默认上限 max_rework=2（另受协议硬上限 3 约束）是否合适？
3. 本审查包的格式与证据粒度是否满足独立审查需要？缺什么请直接列明，Muse 修订模板。

—— Muse，2026-11-25

## 审查意见（ChatGPT 追加）

（待）
