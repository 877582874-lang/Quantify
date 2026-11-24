# Crypto Quant Research Platform — B1 第三步 B1 实施报告（OKX BTC-USDT-SWAP 永续回补）

配套文件：docs/B1_STEP3B1_CONTRACT_SPEC.md（合约规格与成交量语义，含原始响应与样本验证）

## 1. 施工前环境检查
- 时间：2026-11-24 20:45 CST 起；服务器 47.239.82.63，/opt/okx-quant
- A2 工程文件、B1 文档（含 B1_FIELD_MAPPING、DATA_CONTRACT、B1_STEP3A 报告）齐全；现货代码 12 个 Python 文件在位
- .venv-data：Python 3.12.16、duckdb 2.0.1、pyarrow 26.0.1、requests 2.34.2（与锁定文件一致）；原 venv freeze md5 = db004966e1b716bd05d03c57bef1d8f1 不变
- .env：size=0、mode=600、mtime=1795520239（仅 stat）
- 磁盘余 29G（停止线 3G 以上）；内存可用 2.8G
- 施工前数据：BTC 现货 55,616 条 / ETH 现货 55,618 条 / 永续 0 条；DuckDB 主键 PRIMARY KEY(exchange, market_type, instrument_id, interval, open_time_utc) 五维隔离，spot 与 swap、BTC-USDT 与 BTC-USDT-SWAP 天然独立，无需表结构迁移

## 2. 备份路径
/opt/okx-quant/backups/backup-B1-STEP3B1-20261124-204608/
含：inventory-before.txt、spot-snapshot-before.json（BTC+ETH 双快照：行数/open 集合 MD5/Parquet 清单 MD5/Raw 清单 MD5/cursors/jobs/catalog）、state-snapshot.db（SQLite backup API）、klines-snapshot.duckdb（无写入进程时取的文件快照）、raw/parquet 文件清单、code-and-docs-before.tar.gz、双 venv freeze、env stat、BACKUP_NOTES.md。备份未停改任何服务。

## 3. 合约规格原始响应（摘要，完整版见 CONTRACT_SPEC）
GET /api/v5/public/instruments?instType=SWAP&instId=BTC-USDT-SWAP，请求 2026-11-24 12:46:18.792Z、接收 12:46:18.943Z，HTTP 200。
原始响应已保存：data/raw/exchange=okx/market_type=swap/data_type=instrument_metadata/instrument_id=BTC-USDT-SWAP/instruments-1795524378943.json.gz，SHA-256 = 00d42e116a61c6dd0f6bc073118a9b8d02c21aed061245c28c2a7c5c18f8d21e。

## 4. 规格字段实测值
instId=BTC-USDT-SWAP、instType=SWAP、ctVal=0.01、ctValCcy=BTC、ctMult=1、ctType=linear、lotSz=0.01、minSz=0.01、tickSz=0.1、settleCcy=USDT、state=live、uly=BTC-USDT；expTime 返回空字符串（永续无到期），标记为不适用，未填充。

## 5. 成交量字段语义（实测，非猜测）
- 原始 [5] vol = 合约张数；[6] volCcy = 基础币数量（BTC）；[7] volCcyQuote = 计价币成交额（USDT）
- 一张合约 = 0.01 BTC（ctVal=0.01，ctValCcy=BTC）
- 标准化映射：volume_native=[5] 原值（张）、volume_contract=[5]（张）、volume_base=[6]（BTC，交易所原值，非换算生成）、volume_quote=[7]（USDT）

## 6. 换算公式及验证证据
公式：volume_base = volume_contract × ctVal × ctMult = 张数 × 0.01 × 1（仅限 ctType=linear、ctValCcy=基础币的线性永续）。
验证（两级）：
- 规格阶段样本 5 根 1m 线：vol×0.01 与 volCcy 5/5 精确相等；volCcyQuote/volCcy 均落在各自 [low, high] 内
- 正式回补后全量校验（全部 55,618 行，非抽样）：max |volume_contract×0.01 − volume_base| = 1.46e-11（浮点表示噪声量级）；volCcyQuote/volCcy 落在 [low, high] 外的行数 = 0

## 7. 小规模试运行结果（第四阶段）
范围：BTC-USDT-SWAP 5m，窗口内最近 1 天 [2026-11-23 12:07:12.251Z, 2026-11-24 12:07:12.251Z)。
结果：3 次请求、288 条入库（理论 288 = 恰满）、归档 2 个 Parquet 文件、质检全零。
逐字段对照（10 条，节选 3 条，全部 10 条均 all_fields_match=True 且 vol×ctVal=volCcy）：
| ts | raw o/h/l/c | raw vol | raw volCcy | raw volCcyQuote | confirm | std native | std base | std quote | std contract |
|---|---|---|---|---|---|---|---|---|---|
| 1795521900000 | 60296.2/60311.3/60247.4/60278.8 | 21765.7 | 217.657 | 13119947.65756 | 1 | 21765.7 | 217.657 | 13119947.65756 | 21765.7 |
| 1795521600000 | 60394.1/60416.2/60243.6/60296.1 | 63704.5 | 637.045 | 38419167.93817 | 1 | 63704.5 | 637.045 | 38419167.93817 | 63704.5 |
| 1795520700000 | 59961.3/60357.3/59926.4/60293.9 | 254514.75 | 2545.1475 | 153110493.86704 | 1 | 254514.75 | 2545.1475 | 153110493.86704 | 254514.75 |
试运行 288 行全量换算校验：max |张数×0.01 − volume_base| = 9.09e-13。试运行通过后才进入正式回补（遵任务书门禁）。

## 8. 五周期正式回补统计（第五阶段，串行 1m→5m→15m→1h→4h）
窗口：start_ms=1792930032251，end_ms=1795522032251，open_time ∈ [start, end)。
| 周期 | 请求 | 失败/重试 | 入库 | 执行时段（UTC） |
|---|---|---|---|---|
| 1m | 433 | 0/0 | 43,200 | 12:48:05–12:51:26 |
| 5m | 84（续传，见第 17 节）+ 87（--fresh 重跑） | 0/0 | 8,640 | 12:51:26–12:52:06，重跑 ~12:54 |
| 15m | 29 | 0/0 | 2,880 | 12:52:06–12:52:20 |
| 1h | 8 | 0/0 | 719 | 12:52:20–12:52:24 |
| 4h | 2 | 0/0 | 179 | 12:52:24–12:52:25 |
永续合计入库 55,618 条；Raw 批次总计 646 个（试运行 3 + 正式 556 + 5m 重跑 87）。

## 9. 时间覆盖完整性（任务书公式逐周期代入，received_at 取各周期最新页实际接收时刻）
| 周期 | first_theoretical | last_theoretical | 理论数量 | 实际 | 头部缺失 | 尾部缺失 |
|---|---|---|---|---|---|---|
| 1m | 10-25 12:08Z | 11-24 12:07Z | 43,200 | 43,200 | 0 | 0 |
| 5m | 10-25 12:10Z | 11-24 12:05Z | 8,640 | 8,640 | 0 | 0 |
| 15m | 10-25 12:15Z | 11-24 12:00Z | 2,880 | 2,880 | 0 | 0 |
| 1h | 10-25 13:00Z | 11-24 11:00Z | 719 | 719 | 0 | 0 |
| 4h | 10-25 16:00Z | 11-24 08:00Z | 179 | 179 | 0 | 0 |
与朴素计数差异仅 1h/4h 各 −1：open=12:00 的 1h 线（12:59:59 收盘）与 4h 线（15:59:59 收盘）在抓取时刻（12:52Z）仍未收盘，confirm=0 只留 Raw，与现货各阶段同一机制，有 Raw 原文为证。

## 10. 数据质量检查（五周期分别执行）
对齐 misaligned=0、内部缺口 missing=0（5m 首轮曾检出 4 条接缝缺口，已修复并复检为 0，见第 17 节）、重复主键 0、OHLC 非法 0、负成交量 0、未收盘混入 0（表中 is_final 全 true）。

## 11. Raw SHA-256 检查
永续 Raw 646 个批次逐个复算 SHA-256 与 SQLite raw_batches 及 manifest 比对：646/646 一致，0 不符。 instruments 元数据响应 SHA 见第 3 节。

## 12. Raw 与 DuckDB 集合比对（遍历全部永续 Raw 批次，confirm=1 且 open∈[start,end)）
五周期 raw-only = ∅、duckdb-only = ∅（1m 43,200 / 5m 8,640 / 15m 2,880 / 1h 719 / 4h 179，集合逐一相等，无具体差异记录可列）。

## 13. DuckDB 与 Parquet 一致性
永续 Parquet 154 个文件（pyarrow 独立读取）：1m 43,200 / 5m 8,640 / 15m 2,880 / 1h 719 / 4h 179 = 55,618 行，与 DuckDB 计数、SQLite files_catalog 登记（154 条 / 55,618 行）三方一致。路径：data/historical/exchange=okx/market_type=swap/instrument_id=BTC-USDT-SWAP/interval=<周期>/year=/month=/，与现货完全隔离。归档走既有五步协议。

## 14. SQLite 登记结果
raw_batches 新增永续 646 条；collect_jobs 新增永续 6 条（试运行 1 + 正式 5，均 complete）；cursors 新增永续 5 行；files_catalog 新增永续 154 条；quality_anomalies 中永续 4 条接缝缺口记录在修复复检后已标记 resolved（记录保留，未删除）。

## 15. BTC/ETH 现货数据保护结果（与 spot-snapshot-before.json 逐项比对）
- BTC 现货：五周期行数（43,199/8,640/2,879/719/179）不变；open 主键集合 MD5 五周期全等；Parquet 清单（路径+SHA）MD5 五周期全等；Raw 清单 MD5 相等（562 批次）；cursors 逐字相同；jobs 6 不变；catalog 154/55,616 不变
- ETH 现货：同上全部相等（行数 43,200/8,640/2,880/719/179；Raw 559 批次；jobs 5；catalog 154/55,618）
- 汇总标志：SPOT_ALL_OK = True。现货数据与元数据零变化。

## 16. 资源占用
data/raw 9.1MB→15MB、normalized 23MB→37MB、historical 16MB→24MB（永续增量约 28MB）；磁盘已用 8.4G、余 29G；内存可用 2.8G；执行后复核无残留进程；Docker/Caddy 状态不变、容器 0；.env stat 与原 venv freeze md5 与基线一致。

## 17. 代码修改清单与事件记录
代码修改（仅参数化与永续映射，现货默认行为不变）：
- app/storage/normalizer.py：新增 normalize_okx_swap（永续成交量映射，volume_base 取交易所 volCcy 原值）；archive_interval 增加 market_type 参数，归档 SQL 同时过滤 instrument_id 与 market_type，路径按 market 分区
- app/collectors/backfill.py：backfill_interval 增加 market_type 参数，按 market 选择标准化函数；cursors/raw_batches 全链路写 market_type
- app/data_quality/checks.py：run_checks 增加 market_type 参数，SQL 过滤与 scope 均含 market
- scripts/run_step2.py：新增 --market-type 参数并全链路透传（jobs/cursors/回补/归档/质检）
事件（如实记录）：正式回补首轮 5m 采用游标续传衔接试运行，质检当场检出 4 条接缝缺口（open=1795434600000/1795434900000/1795435200000/1795435500000，即 2026-11-23 11:50–12:05Z）。根因：试运行的回补游标记录的是最后一页的最早“抓取”时间戳，该页有 4 根窗口外溢出行已被抓取但被试运行窗口过滤未入库，正式续传从游标严格向旧翻页，恰好跳过这 4 根。处置：对 5m 永续以 --fresh 全窗口重拉（87 请求，8,636 条主键替换、4 条补入），复检 missing=0、集合比对双向为空；异常记录保留并标记 resolved。设计改进项（留待后续阶段评审）：回补游标应记录“最早已入库时间戳”而非“最早抓取时间戳”，或续传时回退一页重叠抓取靠主键去重吸收——本阶段未擅自更改已验收的续传语义，仅记录。

## 18. 未解决问题
- 续传游标接缝问题已用 --fresh 绕过并记录改进项，根治方案待后续阶段评审决定
- 其余无

## 19. 最终验收自检结论
第三步 B1 全部验收项达成：合约规格经公开接口实测确认（ctVal=0.01 BTC/张），成交量语义经样本 + 全量 55,618 行双重交叉验证，标准化映射正确（张数未冒充 BTC、BTC 未冒充 USDT）；五周期时间覆盖理论=实际、头尾缺失 0；Raw/DuckDB/Parquet/SQLite 四方一致、SHA 全量通过；BTC/ETH 现货数据经施工前后快照比对零变化。
本报告生成后立即停步：无常驻进程、无交易账户连接、无交易行为；未扩展 ETH-USDT-SWAP、Binance、增量采集等任何后续阶段，等待审核。
