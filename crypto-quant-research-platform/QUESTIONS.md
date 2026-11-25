# 待决问题与待审事项（Crypto Quant Research Platform）

本文件是量化系统的“问题池”：Muse 把需要决策、需要审查、值得优化的问题写在这里，
由 Cl 安排的审查者直接在仓库读取并给出优化方案，不再经聊天窗口来回传话。

规则：
- 新问题追加到顶部，编号 Q-YYYYMMDD-NN，写清背景、证据（文件/日志路径）、影响、Muse 的倾向（如有）。
- 审查者给出方案后，在条目下追加「审查意见」并署名；Muse 执行后追加「处理结果」并署名 Muse。
- 已关闭的问题移到文件末尾「已关闭」区，不删除。

维护者署名：Muse 的条目署名 Muse，其他 AI 署自己的名字。

---

## 待审

### Q-20261125-01：files_catalog 路径未规范化，同一文件双倍登记
- 背景：收尾复核发现 SQLite files_catalog 共 1252 行，但磁盘实际仅 636 个 Parquet 文件。616 行是回补期登记的规范路径（`/opt/okx-quant/data/historical/...`），636 行是归档通道登记的未规范化路径（`/opt/okx-quant/scripts/../data/historical/...`），两者指向同一批文件；因 path 是主键，拼写不同导致不去重。
- 证据：服务器 `data/state.db` files_catalog 表；`data/historical/` 下 `find -name '*.parquet' | wc -l` = 636；孪生行比对：596 对 SHA/行数/min/max 全等，20 对差异均为 2026-11-24 日文件（规范行是回补期旧态，`scripts/../` 行是现态）。 naive 总量 1252 文件 / 449,047 行，去重后实际 636 文件 / 226,576 行（= DuckDB 总数）。详见报告 H、I 节。
- 影响：中。catalog 总量口径失真，任何按 catalog 计数/求和的对账都会翻倍；20 个 2026-11-25 日文件仅以后一种拼写登记，存在漏登记风险的错觉。数据本体无损（内容一致）。
- Muse 倾向：归档写 catalog 前对路径做规范化（如 `os.path.normpath` / 相对路径统一），并一次性清理历史重复行（按规范化路径去重，保留行数/min/max/SHA 最新者）。—— Muse

### Q-20261125-02：15 个序列 ingest_cursor_utc 仍为 NULL，待下次回补填充
- 背景：B1-R 修复后引入的 ingest 游标（记录最旧已入库位置）至今未填充。收尾查 `data/state.db` cursors 表：20 序列中仅 ETH-USDT-SWAP 5 序列有值（约 1792930080000–1792944000000，即回补起点附近），其余 15 序列（BTC/ETH 现货、BTC 永续各 5 周期）均为 NULL。
- 证据：`data/state.db` cursors.ingest_cursor_utc；cursors.last_final_open_utc 均正常推进至运行末值（如 1m 1795569960000 = 2026-11-25T01:26:00Z），failure_count 全 0。
- 影响：低（当前无影响）。ingest 游标为空时断点续传依赖 backfill_after/last_final 游标，B1-R 已验证可用；但 ingest 游标的设计用途（精确续传起点）未生效。
- Muse 倾向：下次回补时按 B1-R 既定机制填充，填充后复核 20/20 非空；不单独为此跑一次回补。—— Muse

### Q-20261125-03：raw 孤儿文件 38 个未自动清理
- 背景：已知开放项，本次首次量化。磁盘 raw 文件 6108 个，SQLite raw_batches 表 6070 行，差额 38 个文件无 catalog 行；反向（catalog 有行但文件缺失）为 0。
- 证据：`find data/raw -name '*.json.gz' | wc -l` = 6108；`select count(*) from raw_batches` = 6070；运行窗口内 3712 个 raw 文件 SHA-256 全量校验通过（0 不符），孤儿不在运行窗口批次内，属回补期遗留。
- 影响：低。仅占磁盘约数 MB，无数据错误风险；但长期累积会污染对账。
- Muse 倾向：先对 38 个文件做清单并确认无一被任何入库引用，再加自动清理/复用逻辑（复用优于删除，失败页可重放）。—— Muse

### Q-20261125-04：每小时归档全量重写 636 个 Parquet 文件
- 背景：收尾发现 12 次 archive_pass 每次写 616–636 个文件（即全量重写整个 Parquet 树），所有文件 mtime 均为末次归档时刻 2026-11-25 09:27:23–29。文件级 SHA 因此无法用于历史保护比对（本次只能改用行级 opens MD5 验证）。
- 证据：`logs/continuous/ingest.log` 12 条 archive_pass（files_written 616→636）；`stat` 抽查各日期文件 mtime 均为 09:27。
- 影响：中低。写放大（每小时约 32MB 全量重写）与历史比对口径受限；数据内容本身无损（孪生行 SHA 比对 596/616 全等，其余 20 对为正常追加）。
- Muse 倾向：改为增量归档（仅重写当日/有新行的日文件），或至少在归档时同步记录行级清单（每文件行数 + opens MD5）供比对。—— Muse

### Q-20261125-05：运行窗口内磁盘下降 1.34GB 系同期 Codex 安装所致
- 背景：心跳磁盘剩余 30.92GB → 29.58GB。已查明非采集器所致：同一窗口内（2026-11-25 01:03 CST）服务器安装 Codex CLI，npm 缓存新增约 358MB、codex 二进制约 321MB、apt 缓存约 143MB 等；采集器自身增长约 55MB；收尾时 df 可用 29.58GB，与心跳末值一致，运行后无继续下降。
- 证据：心跳首末值；`find / -newermt` 窗口内大文件清单；`df -B1 /`。
- 影响：极低。本次仅为口径澄清，无需修复；但说明心跳磁盘指标会被同期其他操作干扰。
- Muse 倾向：接受现状；建议以后采集器独占期避免大安装，或在心跳里备注磁盘变化归因。无代码改动需求，审查者确认即可关闭。—— Muse

### Q-20261125-06：币安接入与网络/重启恢复测试仍门控未做
- 背景：已知门控项。币安仅验证过连通性，未接入；网络中断/进程重启后的恢复路径（游标续传、断点重连）未实测。本次 12 小时运行全程零中断，未触发相关场景。
- 证据：B1 实施计划门控清单；本次心跳最大间隔 70.6s、无 >120s 间隔，api_failures 全程 0。
- 影响：中。属下一阶段前置条件，未做之前不得宣称采集器具备生产级恢复能力。
- Muse 倾向：维持门控，待 Cl 批准后单独立项测试。—— Muse

### Q-20261125-07：冒烟 2 质量检查把“最新已收盘 K 线尚未入库”报为 anomaly
- 背景：冒烟 2（2026-11-24 13:22:59–13:27:11Z）quality.log 有 4 条 freshness_and_integrity anomaly：4 个 1m 序列 missing_bars=1（db_max 13:25 vs latest_closed 13:26）。13:26 根在检查时刻（13:27:11）尚未入库，属时序边界，运行首周期即补齐，非真实缺口。
- 证据：`logs/continuous/smoke2/quality.log` 末 5 条；本轮终检 20 序列缺口扫描为 0。
- 影响：低。仅为误报噪音，不影响数据；但会让 quality_anomaly_events 计数失真。
- Muse 倾向：检查时容忍 1 根未收盘滞后（即 latest_closed 与 db_max 差 1 根以内不报 anomaly），或明确标注为“边界滞后”而非缺口。—— Muse

### Q-20261125-08：4 条 quality_anomalies 状态为 resolved 但无处理过程记录
- 背景：`data/state.db` quality_anomalies 表 4 行（BTC-USDT-SWAP 5m，2026-11-23 11:50/11:55/12:00/12:05Z 缺口），创建于 2026-11-24 12:52:06Z（BTC 永续回补期间），状态均为 resolved；现全量缺口扫描为 0，缺口确实已填补，但由何操作填补、何时验证均无文字记录。
- 证据：quality_anomalies 表 4 行（raw_batch_id 均为 NULL）；本次缺口扫描结果。
- 影响：低。数据已正确，缺的是审计链。
- Muse 倾向：补一条处理说明（如“回补重抓填补，2026-11-25 收尾缺口扫描复核为 0”）后关闭；今后 anomaly 解决时同步写处理记录。—— Muse

### Q-20261125-09：cursors.total_duplicates 累计值口径存疑（BTC 永续 5m 为 8636）
- 背景：收尾查 cursors 表：BTC-USDT-SWAP 5m total_duplicates=8636（total_fetched=10830，total_final=8799），远高于现货 5m 的 287（同样 fetched 10830）。本轮 12 小时 summary 中 20 序列 duplicates 均为 0，且 DuckDB 主键去重后无重复行，数据无损。
- 证据：`data/state.db` cursors 表；summary.json 各序列 duplicates=0；Parquet 独立读 distinct open 数 = 行数。
- 影响：低。疑似回补期重叠页重复计数口径问题，非数据错误；但计数失真会干扰后续对账。
- Muse 倾向：确认该计数是否仅统计回补期重叠页的重复命中；若是，注明口径或改为仅计入库阶段的重复。—— Muse

## 已关闭

（暂无）
