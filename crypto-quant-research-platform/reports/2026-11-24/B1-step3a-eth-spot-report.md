# Crypto Quant Research Platform — B1 第三步 A 实施报告（OKX ETH-USDT 现货回补）

## 1. 执行时间
- 施工前检查与备份：2026-11-24 20:25–20:26 CST
- 代码参数化：2026-11-24 20:27 CST
- ETH 回补执行：2026-11-24 20:28:00–20:32:22 CST（UTC 12:28:00–12:32:22Z，逐周期串行）
- 验收复核：2026-11-24 20:33 CST
- 服务器：阿里云香港 ECS 47.239.82.63，项目路径 /opt/okx-quant

## 2. 施工前环境检查
- A2 工程文件、B1 第一步文档（B1_FIELD_MAPPING / B1_IMPLEMENTATION_PLAN）、第二步代码（12 个 Python 文件）全部在位
- 独立环境 /opt/okx-quant/.venv-data 存在且健康；原 venv 未动
- 版本：Python 3.12.16、duckdb 2.0.1、pyarrow 26.0.1、requests 2.34.2（与 requirements-data.lock.txt 一致）
- 磁盘：40G 已用 8.3G、余 29G（远高于 3G 停止线）；内存可用 2.8G
- .env：size=0、mode=600、mtime=1795520239（仅 stat，未读内容）
- 原 venv pip freeze md5 = db004966e1b716bd05d03c57bef1d8f1（基线一致）
- DuckDB 主键实测（duckdb_constraints）：PRIMARY KEY(exchange, market_type, instrument_id, "interval", open_time_utc) — 五维唯一标识，ETH 与 BTC 天然隔离，无需结构调整
- 施工前 ETH 数据量：0 条（klines 中 instrument_id='ETH-USDT' 计数为 0）
- BTC 现有数据：1m 43,199 / 5m 8,640 / 15m 2,879 / 1h 719 / 4h 179，共 55,616 条；Parquet 154 个文件；Raw 562 个批次

## 3. 备份路径
/opt/okx-quant/backups/backup-B1-STEP3A-20261124-202630/
包含：inventory-before.txt（文件清单）、btc-snapshot-before.json（BTC 数据统计快照：各周期行数、open_time 集合 MD5、Parquet 文件+SHA 清单 MD5、Raw 清单 MD5、游标全量、jobs/catalog 计数）、state-snapshot.db（SQLite backup API 一致性快照）、klines-snapshot.duckdb（DuckDB 文件快照，取于无写入进程时）、code-and-docs-before.tar.gz（代码与文档）、两个 venv 的 pip freeze、env-stat-before.txt、BACKUP_NOTES.md。备份过程未停/改任何服务。

## 4. 实际执行命令
代码上传（scp，仅 4 个参数化文件）后，逐周期串行执行（每周期独立进程，完成回补+归档+质检后才启动下一周期）：
```
./.venv-data/bin/python scripts/run_step2.py --instrument ETH-USDT \
  --start-ms 1792930032251 --end-ms 1795522032251 --intervals <1m|5m|15m|1h|4h>
```
窗口与 BTC 第二步任务书窗口完全一致：起点 2026-10-25 12:07:12.251Z，终点 2026-11-24 12:07:12.251Z。
无常驻进程（执行后 ps 复核无残留）、无 API Key、无交易账户连接、无交易行为。

## 5. 代码版本与依赖版本
本阶段必要代码变更（仅参数化，不改已验收逻辑与默认值，BTC 路径行为不变）：
- app/storage/normalizer.py：normalize_okx_spot / archive_interval 增加 instrument_id 参数；insert_final 的重复计数查询增加 instrument_id 过滤（隔离正确性修复：否则 ETH 入库时会把 BTC 相同 open_time 误计为重复）；归档 SQL 增加 instrument_id 过滤（原版按 interval 全表归档，处理 ETH 时会连带重写 BTC 文件——本阶段靠此修复避免，BTC 文件因此未被触碰，见第 15 节校验）
- app/collectors/backfill.py：backfill_interval 增加 instrument_id 参数（原 INST 常量改为参数）
- app/data_quality/checks.py：run_checks 增加 instrument_id 参数与 scope 区分
- scripts/run_step2.py：新增 --instrument / --start-ms / --end-ms 参数；jobs/cursors/archive/checks 全链路透传 instrument
依赖版本同第 2 节，未新增、未升级任何依赖。

## 6. 五周期 API 请求统计（OKX history-candles，limit=100/页）
| 周期 | 请求次数 | 失败 | 重试 | 执行时段（UTC） |
|---|---|---|---|---|
| 1m | 433 | 0 | 0 | 12:28:00–12:31:22 |
| 5m | 87 | 0 | 0 | 12:31:22–12:32:03 |
| 15m | 29 | 0 | 0 | 12:32:03–12:32:17 |
| 1h | 8 | 0 | 0 | 12:32:17–12:32:21 |
| 4h | 2 | 0 | 0 | 12:32:21–12:32:22 |
| 合计 | 559 | 0 | 0 | — |
请求次数与 Raw 批次数严格相等（559 = 559，每页一批次）。

## 7. 五周期数据数量统计（DuckDB 实测）
| 周期 | 入库条数 | first_open（UTC） | last_open（UTC） | first_close | last_close |
|---|---|---|---|---|---|
| 1m | 43,200 | 2026-10-25 12:08 | 2026-11-24 12:07 | 12:08:59.999 | 12:07:59.999 |
| 5m | 8,640 | 2026-10-25 12:10 | 2026-11-24 12:05 | 12:14:59.999 | 12:09:59.999 |
| 15m | 2,880 | 2026-10-25 12:15 | 2026-11-24 12:00 | 12:29:59.999 | 12:14:59.999 |
| 1h | 719 | 2026-10-25 13:00 | 2026-11-24 11:00 | 13:59:59.999 | 11:59:59.999 |
| 4h | 179 | 2026-10-25 16:00 | 2026-11-24 08:00 | 19:59:59.999 | 11:59:59.999 |
ETH 合计 55,618 条。样本行（1m 最新）：open 1700.07、close 1699.63、volume_base 6.27395、volume_quote 10665.91、volume_contract = NULL（现货无张数，未造数）；volume_native 与原始 [5] 一致，原始行完整保存在 raw_row 与 Raw 批次中。

## 8. 时间覆盖完整性计算（任务书公式逐项代入）
公式：first_theoretical = ceil(start_ms/step)×step；last_theoretical = floor((received_at−step)/step)×step，其中 received_at 为本周期最新一页（尾页）实际接收时刻；同时受窗口终点约束 last ≤ 最后一个 open < end_ms 的对齐点，两者取小（窗口外数据不计入本任务覆盖）。
| 周期 | 最新页 received_at（UTC） | first_theoretical | last_theoretical（约束来源） | 理论数量 | 实际 | 头部缺失 | 尾部缺失 |
|---|---|---|---|---|---|---|---|
| 1m | 12:28:00.982Z | 10-25 12:08 | 11-24 12:07（窗口约束） | 43,200 | 43,200 | 0 | 0 |
| 5m | 12:31:22.685Z | 10-25 12:10 | 11-24 12:05（窗口约束） | 8,640 | 8,640 | 0 | 0 |
| 15m | 12:32:03.473Z | 10-25 12:15 | 11-24 12:00（窗口约束） | 2,880 | 2,880 | 0 | 0 |
| 1h | 12:32:17.489Z | 10-25 13:00 | 11-24 11:00（抓取约束） | 719 | 719 | 0 | 0 |
| 4h | 12:32:21.509Z | 10-25 16:00 | 11-24 08:00（抓取约束） | 179 | 179 | 0 | 0 |
差异解释（与朴素 30 天计数 43,200/8,640/2,880/720/180 对照）：1m/5m/15m 与朴素值相等——窗口终点虽为 12:07:12Z，但回补在 12:28–12:32Z 执行，窗口内所有 K 线到抓取时均已收盘，故全数收录，含 BTC 当时被排除的尾根（如 1m 的 12:07 线）；1h 少 1、4h 少 1，是因 open=12:00 的 1h 线（12:59:59.999 收盘）与 4h 线（15:59:59.999 收盘）在抓取时刻（12:32Z）仍未收盘（confirm=0，只留 Raw），与 BTC 审计同一机制。理论数量与实际数量五周期全部相等，无未解释差异。

## 9. 数据质量检查结果（五周期分别执行，SQLite quality_checks 已登记）
- 时间戳对齐：misaligned = 0（全部 open_time 为周期整数倍）
- 内部缺口：missing = 0（按各自时间轴逐点核对）
- 重复主键：0（DuckDB 分组复核 + 入库去重计数 duplicates=0）
- OHLC 合法性：bad_ohlc = 0；负成交量：0；未收盘混入：0（表中 is_final 全为 true）
- quality_anomalies 中 ETH 相关记录：0 条

## 10. Raw 文件数量及 SHA-256 检查
ETH Raw 批次 559 个（.json.gz，路径 data/raw/exchange=okx/market_type=spot/data_type=kline/instrument_id=ETH-USDT/interval=<周期>/date=<接收日>/，与 BTC 目录完全分离）；逐个复算 SHA-256 与 SQLite raw_batches 及 .manifest.json 清单比对：559/559 一致，0 不符。写入流程为临时文件→原子改名，未覆盖任何已有批次（文件名含毫秒+随机后缀）。

## 11. DuckDB 数据统计
klines 总行数 111,234 = BTC 55,616 + ETH 55,618；ETH 按周期统计见第 7 节；查询、min/max、样本行均实测正常。

## 12. Parquet 文件数量及独立读取结果
ETH Parquet 154 个文件（1m/5m/15m/1h 各 31 天文件、4h 30 个），路径 data/historical/exchange=okx/market_type=spot/instrument_id=ETH-USDT/interval=<周期>/year=/month=/klines-<日期>.parquet；仅用 pyarrow 独立读取元数据汇总：55,618 行，与 DuckDB、SQLite 登记三方一致。归档全程走既有五步协议（临时文件→行数/min/max 校验→原子改名→SQLite 单事务登记→标记）。

## 13. SQLite 文件目录登记结果
files_catalog 中 ETH 登记 154 条、登记行数合计 55,618，与实际文件逐项对应；collect_jobs 新增 ETH 任务 5 条（每周期一条，status=complete）；cursors 新增 ETH 五行；collect_runs 新增 5 条（failures 全 0）。

## 14. Raw 与 DuckDB 全量集合比对（遍历 ETH 全部 559 个 Raw 批次）
取 confirm=1 且 open_time ∈ [1792930032251, 1795522032251) 的 open 集合与 DuckDB 对应周期全部 open 集合做差集：
| 周期 | Raw 已收盘（窗口内） | DuckDB | raw-only | duckdb-only |
|---|---|---|---|---|
| 1m | 43,200 | 43,200 | ∅ | ∅ |
| 5m | 8,640 | 8,640 | ∅ | ∅ |
| 15m | 2,880 | 2,880 | ∅ | ∅ |
| 1h | 719 | 719 | ∅ | ∅ |
| 4h | 179 | 179 | ∅ | ∅ |
两向差集均为空集（逐条列举即无记录可列）。

## 15. BTC 数据施工前后对比（与 btc-snapshot-before.json 逐项比对）
- 各周期行数：43,199 / 8,640 / 2,879 / 719 / 179，前后不变
- 各周期 open_time 主键集合 MD5：五周期全部与施工前相等
- BTC Parquet 文件清单（路径+SHA-256）MD5：五周期全部与施工前相等（154 个文件未被修改、未被重写）
- BTC Raw 清单 MD5：与施工前相等（562 个批次未动）
- BTC cursors 五 行全量字段：与施工前逐字相同；BTC collect_jobs 计数 6→6；BTC files_catalog 154 条 / 55,616 行不变
- 结论：BTC 数据与元数据零变化（BTC_ALL_OK = True）

## 16. 磁盘空间及内存变化
- 磁盘：已用 8.3G → 8.4G（余 29G）；data/raw 4.6MB→9.1MB、normalized 12MB→23MB、historical 7.8MB→16MB（ETH 增量约 24MB，另含备份快照 12MB 在 backups 下）
- 内存：执行期为 IO 等待主导，事后可用内存 2.8G，与施工前一致；无 OOM、无锁冲突

## 17. 异常、失败、重试和未解决问题
- API 失败 0、重试 0、程序异常 0、质量异常 0
- 过程小插曲（已当场纠正、不影响数据）：备份前进程检查首次用 pgrep -f 匹配到 SSH 会话自身命令行造成假阳性，改用 ps 精确匹配复核确认无回补进程后才取 DuckDB 快照；参数化时 run_step2.py 的 jobs 插入语句曾出现一处占位符笔误，本地 py_compile 与人工复读发现后在上传前已修正（未在服务器上运行过错误版本）
- 未解决问题：无

## 18. 数据风险
无新增数据风险。ETH 与 BTC 在主键、Raw 目录、Parquet 目录、SQLite 记录四个层面均按 instrument_id 隔离且经施工前后快照比对实证；成交量映射沿用现货口径（volume_base=[5]、volume_quote=[7]、volume_contract 留空），原始行完整保留可追溯。

## 19. 最终验收自检结论
第三步 A 全部验收项达成：ETH-USDT 现货五周期在固定窗口内头尾完整（理论=实际、头尾缺失 0）、内部无缺口无重复、Raw/DuckDB/Parquet/SQLite 四方一致、SHA-256 全量通过、BTC 既有数据零变化。同一套系统已证明可在不破坏既有数据的前提下正确处理第二种资产。
本报告生成后立即停步：未启动任何常驻进程，未进入永续/Binance/增量采集等后续阶段，等待审核。
