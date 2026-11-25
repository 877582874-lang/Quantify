# PROJECT_CONTEXT — Crypto Quant Research Platform 项目规则与上下文

**本文件是本项目的统一规则文件。任何在本项目工作的 AI（Codex、Claude Code 或其他），开工前必须先读完本文件，并遵守其中的边界、禁改清单与验收标准。**

- 项目路径：/opt/okx-quant（阿里云香港 ECS 47.239.82.63）
- 维护：Muse（工头，负责派活与验收）；最终决策人：Cl（老板）
- 最后更新：2026-11-25（Muse；当日阶段 5 补齐 docs 知识库与文件索引）。本文件只写经核实的事实；更正须留痕、署名、注明日期。
- 延伸阅读：docs/ARCHITECTURE.md、docs/DATA_CONTRACT.md、docs/DEVELOPMENT_RULES.md；问题池在 Quantify 仓库 crypto-quant-research-platform/QUESTIONS.md；跨会话记忆库在 Quantify 仓库 memory/；知识库：docs/DECISIONS.md、docs/CURRENT_STATUS.md、docs/KNOWN_ISSUES.md、docs/AGENT_RULES.md；任务协议：/opt/ai-coordinator/protocol/TASK_PROTOCOL.md。

---

## 1. 项目目标和研究边界

**目标**：建设一个多策略加密量化**研究**平台。当前阶段（B1）只做一件事：把公开行情数据可靠地采集、校验、存储成可研究的数据资产（Raw 原始层 → 规范化层 → Parquet 历史层 + SQLite 台账）。

**研究边界（硬性）**：

- 只使用**公开行情 API**（OKX、Binance 的公开端点）。
- **不连接任何交易账户、不使用交易 API Key、不下单、不做任何交易功能。**
- B1 阶段**不做策略、不做回测**。未收到 Cl 的新任务书前，不得自行进入下一阶段。
- 数据范围：OKX / Binance，现货 + USDT 永续；首批 BTC、ETH；周期 1m / 5m / 15m / 1h / 4h。
- 内部时间一律 **UTC** 存储与计算；报告时间须注明时区。
- 已验收的历史数据**不可改写**。发现历史数据有问题时，只记录、报告，等 Cl 裁决，不得擅自回改。
- 统计与研究结论只描述数据，不得宣称预测能力或胜率。

## 2. 已完成阶段（均已验收，报告在 docs/ 与 Quantify 仓库 reports/）

- **A0 / A1 / A2**（2026-11-24）：服务器审计、项目盘点、工程骨架（目录结构 + ARCHITECTURE / DATA_CONTRACT / DEVELOPMENT_RULES 三份标准）。
- **B1 步骤 1**：OKX 与 Binance 连通性与字段映射实测（详见 docs/B1_FIELD_MAPPING.md、B1_IMPLEMENTATION_PLAN.md）。
- **B1 步骤 2**：OKX BTC-USDT 现货 30 天回补，共 55,616 根（1m 43,199 / 5m 8,640 / 15m 2,879 / 1h 719 / 4h 179），562 次请求 0 失败，Raw SHA 562/562，DuckDB = Parquet = SQLite 台账三方一致，质量异常 0。
- **B1 步骤 3A**：OKX ETH-USDT 现货，同窗回补 55,618 根，559 请求 0 失败；BTC 数据保护比对全等。
- **B1 步骤 3B1**：OKX BTC-USDT-SWAP 永续回补 55,618 根；合约规格实测存档（见第 4 节）；成交量全量交叉验证通过。期间发生接缝缺 4 根事故，已修复，异常记录保留并标记 resolved。
- **B1 步骤 3B1-R**：断点续传游标机制结构修复（见第 4 节），8 项回归测试全过，生产数据保护比对全过。
- **B1 步骤 4**：ETH-USDT-SWAP 规格核验 + 30 天回补 55,619 根；增量采集器 scripts/run_collector.py 建成（20 序列）；12 小时受控试运行于 2026-11-24 21:27:22 CST 启动、2026-11-25 09:27:33 CST 自停：**API 请求 3,712、失败 0、重试 0、入库 3,712 根、质量异常 0、重复 0**，各序列新增根数与 12 小时理论值逐一吻合（1m +721、5m +144、15m +48、1h +12、4h +3，每序列）。收尾全量核账报告见 docs/B1_STEP4_12H_RUN_REPORT.txt。

## 3. 技术架构

- **数据链路**：公开 REST API → 历史回补 → Raw 层（不可变 JSON.gz + SHA-256）→ 规范化层（DuckDB，仅收盘确认 K 线）→ Parquet 日分区（写入协议：临时文件 → DuckDB 回读校验 → 原子改名 → SQLite 单事务登记）+ SQLite state.db 台账。
- **规范化表 klines 主键**：(exchange, market_type, instrument_id, interval, open_time_utc)。字段含 OHLC、volume_native / volume_base / volume_quote / volume_contract、is_final、raw_batch_id、raw_row 等（以 docs/DATA_CONTRACT.md 为准）。
- **state.db 表**：collect_jobs、cursors、collect_runs、raw_batches、quality_checks、quality_anomalies、files_catalog。
- **Python 环境（两个，勿混淆）**：
  - 原始 venv：/opt/okx-quant/venv（Python 3.12.16；pandas / numpy / requests / websockets）——**永不修改**。
  - 数据 venv：/opt/okx-quant/.venv-data（duckdb 2.0.1 / pyarrow 26.0.1 / requests 2.34.2，锁定于 requirements-data.lock.txt）。采集与数据脚本用这个。
- **采集器**（scripts/run_collector.py）：单进程；每序列在其 K 线收盘 + 8 秒安全延迟后轮询；首次轮询可向前翻页（≤10 页）补锚点之后的缺口；**只插入 open 大于该序列当前最大值的新根，永不改写已存 K 线**；迟到/被修正的已存 K 线只记 late_correction_detected 异常，不落库修改；每 60 秒质量检查与心跳；每小时 Parquet 归档 + 关机时归档；磁盘可用 < 3GB 自动停采；SIGTERM/SIGINT 优雅退出并写 logs/continuous/summary.json。
- **日志**：logs/continuous/ 下六类轮转日志（collector / api_requests / ingest / errors / quality / heartbeat，各 20MB×5）。
- **项目 git**：/opt/okx-quant 已 git init（分支 main）。提交纪律见第 7 节。

## 4. 已验证事实（可直接引用，不要重新质疑；如实测推翻，须报告并留痕更正）

1. **OKX 成交量字段陷阱**：现货 volume_base 是字段 [5]；永续字段 [5] 是**张数**，volume_base 是字段 [6]（volCcy）。取错列会把永续成交量放大 100 倍。
2. **合约规格**：BTC-USDT-SWAP ctVal = 0.01 BTC/张；ETH-USDT-SWAP ctVal = **0.1** ETH/张（两者不同！）；均为 ctMult=1、线性、settleCcy=USDT、state=live。原始响应 + SHA-256 存于 data/raw/.../instrument_metadata/。
3. **OKX bar 参数须大写**：1H / 4H；小写 1h/4h 报 51000。参数类错误码（51000/51001/51008）不可重试。
4. **UA 陷阱**：OKX/Cloudflare 会 403 Python urllib 的默认 User-Agent；API 代码须带浏览器 UA 或用 requests。
5. **规范回补窗**（步骤 2/3A/3B1/ETH 永续共用）：[1792930032251, 1795522032251)，即 2026-10-25 12:07:12.251Z 起 30 天，左闭右开。
6. **游标语义（3B1-R 后）**：cursors.ingest_cursor_utc = 实际已入库的最老 open，仅在「Raw 落盘 → 规范化入库 → SQLite 单事务登记」全部成功后推进；续传起点 = ingest + 步长。backfill_after_utc 只是抓取进度（诊断用），不得当作入库依据。
7. **Binance 从本机可达**：现货与 USDT-M 合约 klines 端点实测 200（香港机房未被地域封锁）；Binance 无 is_final 标志（由 closeTime 推导）、无张数成交量（volume_contract 按设计留空）。
8. **采集器 12 小时实测**：3,712 请求 0 失败、0 异常、0 重复；RSS 约 180MB；磁盘 12 小时消耗可忽略（余量约 29.6GB）。详见第 2 节步骤 4 与收尾报告。

## 5. 未解决问题（开放项，不得自行处理，等待 Cl 裁决或新任务书）

1. **Raw 孤儿文件**：入库失败页产生的 Raw 文件可由对账识别，但未做自动清理/复用。不得擅自删除 Raw 文件。
2. **存量系列 ingest 游标为 NULL**：步骤 3B1-R 之前入库的系列（BTC 现货、ETH 现货、BTC 永续共 15 个序列）ingest_cursor_utc 仍为 NULL，待各自下一次回补时回填（ETH 永续已回填）。在此之前对这些系列做续传类操作须先报告。
3. **门禁中的后续工作**（未获任务书不得启动）：Binance 现货/期货采集；网络中断/重启恢复测试；策略与回测；任何交易相关功能。
4. **问题池**：Quantify 仓库 crypto-quant-research-platform/QUESTIONS.md 中的待审查问题，以该文件为准持续维护。

## 6. 禁止擅自修改的内容（红线）

以下事项**任何情况下**不得擅自执行；确有必要时，先写方案、报告 Cl、获批后再动：

- **.env**：不读内容、不改、不输出（只允许 stat 看大小/权限/时间）。
- **原始 venv** /opt/okx-quant/venv：不装、不卸、不升级任何包。
- **data/ 下的既有行情数据与数据库**（Raw 文件、DuckDB、Parquet、state.db）：不删除、不改写、不回填；新增数据只能走既定管线。
- **服务器基础设施**：Caddy、Docker、SSH 配置、防火墙/安全组、mihomo 代理配置与订阅文件——不改。
- **凭据类文件**：/root/.codex/auth.json、任何私钥/令牌文件——不读、不输出、不复制。
- **运行中的服务/进程**：不停止、不重启（包括采集器、mihomo、Codex app-server），除非任务书明确要求。
- **Git**：不把 .env、数据文件、密钥提交进任何仓库；不强推；不改写他人署名的提交。
- **本文件**：不得静默改写既有事实与结论；更正须注明日期、依据与署名。

## 7. 每次任务的验收标准（通用）

1. **阶段门禁**：只读审计 → 方案 → Cl 批准 → 执行 → 验收 → 报告。未获批准不得执行有副作用的步骤；任务书没写的事不顺手做。
2. **证据优先**：一切结论（数量、状态、成功/失败）必须来自真实命令输出、日志或文件实况，并在报告中给出原始数字；禁止凭记忆或推测下结论。外部说法与服务器实况冲突时，以服务器实况为准。
3. **保护比对**：凡涉及数据的任务，执行前后必须做受保护对象比对（如历史行数/集合一致性、基线指纹），并在报告中给出比对结果。
4. **失败如实报**：失败、异常、中断都要写进报告并保留现场（日志、异常记录），不得掩盖、不得自动修复掩盖痕迹；「自动修复」只在任务书明确授权时才可做。
5. **停止条件**：遇到与任务书不符的实况（状态异常、数字对不上、权限不足），立即停止并报告原始错误，不得自行绕行或扩大操作范围。
6. **报告格式**：每个任务交付**一份 .txt 汇总报告**（含目标、步骤、真实数字、异常、开放项），并按留档规则入 Quantify 仓库：报告 + CHANGELOG 条目 + 代码快照（如有代码变更），三者齐全。
7. **Git 纪律**：谁提交谁署名（Codex 的提交署名 ChatGPT，Muse 署名 Muse）；首次提交、向 Quantify 仓库推送前，先报告 git status 并获 Cl 确认；工作分支与 main 分开，不得直推 main（除非任务书明确要求）。
8. **汇报方式**：向 Cl 的汇报走 Quantify 仓库（报告与问题池）；聊天里只发短讯：改了什么、为什么、结论一句、提交哈希。

—— Muse 整理，2026-11-25。Cl 确立的规则与当晚实测事实为准；后续维护者按第 6 节末条规则更新本文件。


## 8. Codex 协作记录规则

- 每次对仓库内容进行新增、修改或删除，都必须附带本次变更的说明与署名；说明应能指出改了什么、为什么改、依据/验证是什么（适用时）。
- 署名只标记实际作者。Codex 的修改署名为 **ChatGPT（Codex）**；Muse 署名为 **Muse**。不得沿用或覆盖他人的署名，也不得把他人已写内容改成自己的署名。
- 文档中已有 Muse 署名的记录应保持原样；后续追加独立条目，注明日期、作者和变更内容。

—— ChatGPT（Codex）补充，2026-11-25。依据：Cl 在对话中明确要求每次增删改仓库内容均附修改说明和署名。

- 项目长期记忆的正式来源是 GitHub 上 Quantify 仓库根目录的 `memory/`，入口 `memory/README.md`；其中包括 README、rules-and-preferences、infrastructure、project-status、codex-remote、claude-code、timeline 七份记忆文件。开始相关工作时先读 README 并按索引查阅适用文件；工作过程中及阶段结束时，主动判断是否需要整理更新项目状态、时间线、规则或设备/远程环境信息，避免上下文遗失。所有增删改必须写明修改内容、原因/依据并署实际作者；保留 Muse 既有署名，不代署。
