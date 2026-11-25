# COORDINATOR_DESIGN — 最小协调器设计 v1

设计：Muse，2026-11-25（MULTI-AGENT-002 阶段 6）。实现：Codex（按 TASK-20261125-01）。
部署：/opt/ai-coordinator（与量化业务代码物理分离）。技术：Python 3 标准库 + SQLite + Git + systemd。不引入任何第三方依赖与重型组件。

## 1. 定位

协调器是 Muse 调度 Codex 的**登记与执行台账**，不是自治大脑：任务由 Muse 签发（协议见 protocol/TASK_PROTOCOL.md），协调器负责登记、按闸门放行、调用 codex exec、留存证据、跟踪状态、支持中断恢复与人工审批。审查结论由 Muse 通过 review 命令录入；Codex 的自评不改变审查状态。

## 2. 组成

- `coordinator/coordinator.py` —— 单文件 CLI（stdlib only）。
- `coordinator/coordinator.db` —— SQLite 台账（tasks + events 两表）。
- `tasks/<task_id>/` —— 每任务证据目录（task.md / exec.log / result.md / diff.patch），与协议留存规范一致。
- `systemd/ai-coordinator-recover.service` + `.timer` —— 每 5 分钟跑一次 `recover` 巡检（把失联的 running 任务标 interrupted 并记事件）。unit 文件由 Codex 编写、Muse 安装启用。

## 3. 数据模型

tasks：task_id（PK）、title、objective、workdir、allowed_files、forbidden_files、requirements、acceptance、tests、evidence_required、git_commit、deploy、permission_level（P0–P3）、approval_by、approval_note、status、rework_count、codex_pid、codex_exit、session_note、created_at、updated_at。
events：id、task_id、ts、actor（claude/codex/system）、event、detail。**所有状态迁移必须写 events**，这是审计链。

status 枚举：created / dispatched / running / returned / reviewing / rework / accepted / rejected / blocked / interrupted。

## 4. CLI

- `init` —— 建库（幂等）。
- `add --file task.json` —— 按信封 JSON 登记任务（字段与协议一一对应），写 tasks/<id>/task.md，状态 created，记事件。
- `run <task_id>` —— 闸门检查（见 §5）→ 状态 dispatched→running（记 codex_pid）→ 以 workdir 为 cwd 调用 `codex exec -s workspace-write <信封全文>`（stdin /dev/null，超时取信封 timeout）→ 输出全量写入 exec.log → 记录退出码 → 状态 returned；非零退出记 returned_failed 事件但仍置 returned（由复核决定返工还是拒绝）。若 workdir 是 git 仓库，顺带把 `git diff` 存 diff.patch。
- `status <task_id>` / `list` —— 查状态与列表。
- `review <task_id> --verdict accepted|rework|rejected --note ...` —— Muse 录入复核结论；rework 时 rework_count+1，超 3 轮拒绝再派并提示升级。
- `approve <task_id> --by Cl --note ...` —— 录入人工批准（P2/P3 放行凭据），记事件。
- `recover` —— 扫描 running 且 pid 已不存在的任务 → interrupted + 事件；供 systemd timer 周期调用，也可手动跑。

## 5. 安全闸门（系统层，不靠提示词）

- permission_level 为 P2/P3 且 approval_by 为空 → `run` **拒绝执行**（退出码非零 + 事件留痕）。
- deploy=yes 且无批准 → 同样拒绝。
- workdir 不存在 → 拒绝。
- 任务信封由 Muse 签发才可 add（actor 固定记 claude；本 v1 不做多用户鉴权，靠服务器账户边界）。

## 6. 恢复语义

- run 过程中协调器或机器中断：tasks 行停在 running + codex_pid；recover 巡检发现 pid 不存在 → interrupted；Muse 可对该任务重新 run（新一轮 exec，并在 session_note 注明重跑原因）或用 `codex exec resume` 人工续会话后补记。v1 不做自动续会话（避免无人值守的不可控续跑）。

## 7. 非目标（v1 不做）

- 不做守护进程式自动派活、不做 Web UI、不做多机、不做自动合并/部署、不做 Codex 输出的语义解析（result.md 由 Codex 按结果信封填写或由 run 生成骨架后 Muse 补齐）。

—— Muse，2026-11-25
