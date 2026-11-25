# TASK_PROTOCOL — Muse → Codex 统一任务协议 v1

依据：MULTI-AGENT-002 任务书第三节。制定：Muse，2026-11-25（阶段 4）。
适用：一切由 Muse 向 Codex 下达的任务；ChatGPT 的任务书经 Muse 拆分转下时同样适用。
原则：**所有任务与结果必须落盘留存，不依赖任何 AI 的对话上下文。**

## 1. 任务信封（下达时必填）

| 字段 | 说明 |
|---|---|
| task_id | 格式 `TASK-YYYYMMDD-NN`，全局唯一，返工沿用同一 id 并加轮次 |
| title | 一句话标题 |
| objective | 任务目标（要达成什么，不写实现细节除非必要） |
| workdir | Codex 的工作目录（绝对路径） |
| allowed_files | 允许修改/新增的文件或目录清单（清单外只读） |
| forbidden_files | 明确禁止触碰的文件/目录（与 PROJECT_CONTEXT.md 红线叠加生效） |
| requirements | 技术要求（语言、依赖、风格、接口约束） |
| acceptance | 验收标准（可逐项判定的条目，不写"做好"这类不可测措辞） |
| tests | 测试要求（要运行的命令/要新增的测试；无测试须说明理由） |
| evidence_required | 须提交的证据（测试输出、diff、日志片段、SHA 等） |
| git_commit | `none` / `branch`（含分支名与署名要求）；直推 main 须 Cl 批准并在此注明 |
| deploy | `no` / `yes`（yes 仅限已获批的 P2 操作，注明批准凭据） |
| permission_level | P0 只读 / P1 开发 / P2 生产（须 Cl 批准）/ P3 高风险（须 Cl 逐次批准），定义见 docs/AGENT_RULES.md |
| approval | P2/P3 必填：批准人 + 时间 + 原话要点；P0/P1 留空 |
| timeout | 单次执行超时（秒）；长任务须同时约定心跳/进度回报方式 |

## 2. 结果信封（Codex 完成时必填，缺项视为未交付）

- 修改文件清单 / 新增文件清单 / 删除文件清单（与 git status/diff 可对账）
- 关键实现说明（做了什么、为什么这样做）
- 测试命令与测试结果（原始输出，不得只写"通过"）
- Git diff（原文或以文件留存并在结果中给出路径）
- 未解决问题（没有则写"无"）
- 风险说明（对现有数据/服务/后续工作的影响）
- 是否达到验收标准（逐项对照自评）——**注意：Codex 自评不等于验收；Muse 复核通过才算 accepted。Codex 不得为自己的代码出具独立审计结论。**

## 3. 生命周期

`created → dispatched → running → returned → reviewing → accepted`
支线：`reviewing → rework（注明不达标项）→ running …`；`blocked（等待批准/外部条件）`；`rejected（方案不成立，关闭并记录原因）`。

- 返工默认上限 **3 轮**；超限停止并升级给 Cl / ChatGPT 裁决。
- 任务被中断（进程被杀、超时、机器重启）：凭 task_id 与留存目录恢复，优先 `codex exec resume` 续会话；不可续时以原信封重发，并在结果中注明。

## 4. 留存规范

每任务一个目录：`/opt/ai-coordinator/tasks/<task_id>/`，至少包含：

- `task.md` —— 任务信封全文
- `exec.log` —— 每次调用的完整输出（含退出码、token 用量）
- `result.md` —— 结果信封
- `diff.patch` —— 本任务的 git diff（如有代码变更）

阶段 6 协调器建成后，本目录结构与 SQLite 任务登记互相映射，字段以本协议为准。

## 5. 签署

- 任务由 Muse 签发（信封末尾署名 + 日期）。
- 结果由执行者署名（Codex 的提交与结果署名 ChatGPT/Codex 按仓库约定）。
- 复核结论由 Muse 署名写入 `result.md` 末尾（accepted / rework / rejected + 理由）。

—— Muse，2026-11-25
