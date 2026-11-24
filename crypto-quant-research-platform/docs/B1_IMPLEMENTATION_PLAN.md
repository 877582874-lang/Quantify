# B1_IMPLEMENTATION_PLAN — 行情采集与存储系统实现方案

版本：v1.0（B1 第一步，2026-11-24）
状态：**待审核**。本文件经批准前，不写采集代码、不建 .venv-data、不启动任何采集进程。
上位规范：docs/ARCHITECTURE.md、docs/DATA_CONTRACT.md、docs/DEVELOPMENT_RULES.md、B1_FIELD_MAPPING.md。

## 1. 总体设计

单机单进程组（非多容器，遵任务书第九节）：一个 Python 3.12 程序，内部按模块分工，systemd 或 nohup 常驻的方式留到第四步评审时定；第二、三步只做可手动运行、可重复执行的回补与增量各一次验证。

```
app/exchanges/okx.py, binance.py   适配器：拼请求、翻页、限频、把原始数组解析为原始记录（不做标准化判断）
app/collectors/backfill.py         历史回补驱动（按标的×周期推进，写游标）
app/collectors/incremental.py      增量采集驱动（按周期节拍拉最新已收盘 K 线）
app/storage/raw_store.py           raw 不可变落存（JSON 压缩批次 + 批次清单）
app/storage/normalizer.py          原始→标准化（按 FIELD_MAPPING），只读 raw、写 normalized
app/storage/archive.py             normalized→historical Parquet 归档 + DuckDB 目录登记
app/storage/state.py               SQLite 状态库访问（任务/游标/运行/质量/文件目录）
app/data_quality/checks.py         十项质量检查（见第 6 节），结果写 SQLite，不改数据
scripts/                           回补、增量、质量检查、归档的命令行入口
```

## 2. 独立环境（第二步先行验证，未批准不建）

- 新建 `/opt/okx-quant/.venv-data`（Python 3.12），与现有 `venv` 完全隔离，现有 venv 一行不动。
- 拟新增依赖（先在 .venv-data 中安装并做导入/版本兼容验证，再锁定到 requirements-data.lock.txt）：
  - duckdb（版本在验证时以 PyPI 当前稳定版为准并锁定）
  - pyarrow（与 duckdb 兼容版本，验证时锁定）
  - requests、websockets：复用已验证版本（2.34.2 / 17.4）
  - 标准库 sqlite3、gzip、hashlib、json：不新增依赖
- 暂不用 CCXT、Polars、pandas 做管道核心：标准化用标准库 + pyarrow 类型，避免 pandas 3.x 在管道层引入额外变量；pandas 只留给后续研究层。

## 3. 存储设计

### 3.1 raw（不可变）
- 路径：`data/raw/exchange=<ex>/market_type=<mt>/data_type=kline/instrument_id=<id>/interval=<iv>/date=<YYYY-MM-DD>/batch-<received_ms>-<seq>.json.gz`
- 内容：交易所原始响应 JSON 原文 + 批次元数据（请求 URL 参数、HTTP 状态、received_at_utc、条数、SHA-256）。
- 规则：只追加新批次文件，永不改写/删除；同一批重复拉取也以新批次保存，去重发生在 normalized 层，raw 只负责留痕与可追溯。

### 3.2 normalized（标准化暂存与去重）
- 以 DuckDB 文件 `data/normalized/klines.duckdb` 承载一张 `klines` 表（字段即 DATA_CONTRACT 标准字段 + raw_batch_id 外键式追溯列）。
- 唯一键（去重键）：`(exchange, market_type, instrument_id, interval, open_time_utc)`；写入用 INSERT OR REPLACE 语义按主键合并，重复回补不产生重复行（验收 D 的机制保证）。
- 只接受 `is_final=true` 的记录进入正式表；未收盘 K 线只出现在 raw，不进 normalized（验收 F）。

### 3.3 historical（Parquet 正式归档）
- 路径：`data/historical/exchange=<ex>/market_type=<mt>/instrument_id=<id>/interval=<iv>/year=<YYYY>/month=<MM>/klines-<YYYYMMDD>.parquet`（按日文件，避免小文件泛滥：单日单组合 1m 也仅 1440 行，合并写出）。
- DuckDB 直接以 `read_parquet(..., hive_partitioning=true)` 查询 historical，无需常驻数据库进程（验收 G/H）。

### 3.4 DuckDB 与 Parquet 一致性机制（任务书第七节硬要求）
归档事务顺序（崩溃安全）：
1. 从 normalized 读出待归档批次 → 写 Parquet 到同目录临时文件 `*.tmp.parquet`；
2. 用 DuckDB 打开临时文件校验：行数、min/max open_time、主键唯一性，与待归档批次比对，三项全等才继续；
3. 原子 rename 为正式文件名；
4. 在 SQLite `files_catalog` 中以单事务登记（路径、行数、时间范围、SHA-256、归档批次 id）；
5. 仅在第 4 步成功后，才在 normalized 侧把该批次标记 archived。
任一步失败：临时文件保留待人工处理、状态不前进、游标不越过未归档数据；重启时先对账（catalog vs 实际文件 vs normalized 标记），不一致即报警并停在对账环节，不静默继续。

### 3.5 SQLite 状态库 `data/state.db`
表（第二步建表时以此为据）：
- `collect_jobs`：job_id、exchange、market_type、instrument_id、interval、kind(backfill/incremental)、range_start_utc、range_end_utc、status、created/updated
- `cursors`：每 (exchange, market_type, instrument_id, interval) 一行：last_final_open_time_utc、last_success_at_utc、total_fetched、total_missing、total_duplicates、request_count、failure_count（即任务书第六节要求的回补统计口径，增量同样适用）
- `collect_runs`：每次运行一行：run_id、job_id、started/finished、fetched、inserted、duplicates、failures、error_summary
- `quality_checks`：check_id、scope(exchange/market/instrument/interval)、check_type、checked_range、anomaly_count、detail_json、created_at
- `quality_anomalies`：异常明细：type、open_time_utc、detail、raw_batch_id（可追溯）、status(open/backfilled/resolved)
- `files_catalog`：见 3.4
全部时间 UTC；SQLite 开 WAL 与事务，写入批量提交。

## 4. 回补设计（30 天）

- 规模估算（已按周期算清）：单组合 30 天条数 1m=43,200、5m=8,640、15m=2,880、1h=720、4h=180，合计 55,620 条；8 个组合共 **444,960 条**。
- 请求估算：Binance（limit 1000）约 450 次；OKX history-candles（limit 100）约 4,450 次。按自限速（OKX 每请求间隔 ≥0.35s、Binance ≥0.12s，另加抖动）OKX 回补约需 30–45 分钟，可断点续跑，不追求速度。
- 分页：OKX 以 after=已取最旧 open_time 继续向历史翻；Binance 以 endTime=已取最旧 openTime−1 翻页；每页落 raw 后即标准化入库并更新游标，进程中断后从游标续跑（验收 I 的回补侧）。
- 回补完成判据：每组合实际条数 vs 理论条数对账，差额必须全部能被 quality_anomalies 中的缺口记录解释（验收 C/D/E）。

## 5. 增量采集设计

- 节拍：每分钟第 8 秒后触发一轮（避开整点收盘瞬间），一轮内对 40 个序列各拉最近 3 根 K 线，只取其中 is_final 的入库；4h/1h 等大周期同样每轮检查，靠去重键保证不重复。
- 重试：单请求失败指数退避（1s→2s→4s→…上限 60s，带抖动），单序列连续失败不阻塞其他序列（每序列独立 try 与独立游标）；整轮失败只记 runs 与日志，不退出进程。
- 限频：全局令牌桶按所分别限速（OKX ≤ 8 req/s 保守值、Binance ≤ 10 req/s 保守值，远低于官方上限），429/418 立即降速并记录。
- 健康与自检：每轮写心跳到 cursors.last_success_at_utc；启动时先做磁盘空间检查（剩余 < 3GB 拒绝启动并报警）、对账（3.4 第 5 步）、游标连续性检查。
- 日志：logs/collector.log 按日轮转（logging RotatingFileHandler，单文件 ≤10MB ×5），与宿主机 Docker 日志轮转无关（本阶段不用容器跑采集）。

## 6. 数据质量检查（对应任务书第八节 10 项）

时间戳合法性（UTC、单调、与周期对齐）/ 重复（主键）/ 缺口（理论时间轴差分，缺口逐条入 anomalies）/ OHLC 合法性（low ≤ o,c ≤ high 等 4 组不等式）/ 成交量非负 / 未收盘过滤复核（normalized 内 is_final 必须全 true）/ 分交易所分别出报告 / 采集延迟（received_at − close_time 分布，超阈值标记）。
异常处置：只记录、不删除、不改 raw；缺口支持按 anomalies 清单定向回补（复用回补驱动按时间窗模式）。

## 7. 资源预算（2 vCPU / 3.5 GiB / 盘余 30 GB）

- 内存：单进程，批量页大小 ≤1000 行，常驻目标 < 600 MB；DuckDB 设 memory_limit=512MB。
- 磁盘估算：raw JSON.gz 约 445k 条 × ~0.1KB 压缩 ≈ 50–100 MB（30 天回补）；Parquet 约 445k 行 ≈ 20–40 MB；增量每日新增 8 组合 ×（1440+288+96+24+6）≈ 14,832 行/日，raw+parquet 年增量约 2–3 GB，30 GB 可用空间足够 1 年以上；仍设磁盘检查阈值（第 5 节）。
- CPU：回补为 IO 等待主导；标准化与质量检查为分钟级批处理，不与增量抢占（增量优先）。

## 8. 测试映射（任务书第十节 13 项 → 执行阶段）

第二步（OKX BTC 现货单点打通）：回补 30 天 5 周期 → 对应测试 1 的现货部分 + 6/7/8/11/12/13 的现货子集。
第三步（扩展）：其余 7 个组合全量回补 → 测试 2–5 全项 + 6–8 全量。
第三步末尾：网络失败恢复（回补中拔线/拦域名后恢复续跑）、程序重启恢复（kill 后从游标续跑）→ 测试 9/10。
全部以真实公开行情做集成测试；不调用任何交易接口（本项目无交易接口调用代码）。

## 9. 分步与验收门（遵任务书第十三节）

- 第一步（本步）：检查 ✔、备份 ✔、本映射表与本方案 → **提交审核，停**。
- 第二步：建 .venv-data 并验证依赖 → 实现 OKX BTC 现货回补+存储+质量检查 → 小报告 + 验收子集 → 停，等审核。
- 第三步：扩展 OKX 永续、Binance 现货/永续、ETH → 全量 30 天回补与 13 项测试 → 小报告 → 停，等审核。
- 第四步：启动长期增量采集（含常驻方式评审）→ B1 总报告（十五节内容）→ B1 验收 A–M。

## 10. 风险与待确认项（请审核时留意）

1. OKX 永续合约面值 ctVal（BTC/ETH 永续 0.01）需在第二步用 instruments 接口实测确认后写入映射表 v1.1，不凭记忆。
2. Binance 永续 volume_contract 留空是刻意取舍（接口无张数口径）；若后续研究需要张数，需另立换算规范，不在本阶段造数。
3. DuckDB 与 pyarrow 的具体锁定版本在第二步隔离验证时确定；若与 Python 3.12.16 出现兼容问题，回退方案是仅用 pyarrow + 标准库实现 normalized 暂存（DuckDB 只做查询），不改动现有 venv。
4. 长期增量采集的常驻方式（systemd unit vs 其他）涉及新增系统服务，第四步单独报批，本方案不预设已批准。
