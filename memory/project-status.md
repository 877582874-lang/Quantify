# Crypto Quant Research Platform — 项目状态

最后更新：2026-11-25 03:30 CST（Muse）。项目位于 ECS 47.239.82.63 的 `/opt/okx-quant`（本地已 git init，分支 main，尚无首次提交——首提留给在此工作的 AI 以其本人署名完成）。

## 设计要点

- 链路：公开 REST API → 历史回补 → Raw（不可变 JSON.gz + SHA-256）→ 规范化（DuckDB，仅收盘确认 K 线）→ Parquet 日分区 + SQLite state.db 登记。内部时间一律 UTC。
- 范围：OKX/Binance 现货 + USDT 永续；首批 BTC/ETH；周期 1m/5m/15m/1h/4h（OKX bar 参数须大写 1H/4H）。**无策略、无回测、无交易功能**（B1 阶段红线）。
- 规范化主键：(exchange, market_type, instrument_id, interval, open_time_utc)。
- 环境：原始 venv `/opt/okx-quant/venv`（Python 3.12.16，pip freeze 排序 md5 `db004966e1b716bd05d03c57bef1d8f1`）**永不修改**；数据 venv `/opt/okx-quant/.venv-data`（duckdb 2.0.1 / pyarrow 26.0.1 / requests 2.34.2，锁定于 requirements-data.lock.txt）。`.env` 为 0 字节、600 权限，只准 stat 不准读内容。
- 规范回补窗（步骤 2/3A/3B1/ETH 永续共用）：[1792930032251, 1795522032251) = 2026-10-25 12:07:12.251Z 起 30 天。

## 已完成阶段（全部验收通过，报告在仓库 reports/ 与服务器 docs/）

- **A0/A1/A2**（2026-11-24）：服务器审计、项目盘点、工程骨架（33 目录 + ARCHITECTURE / DATA_CONTRACT / DEVELOPMENT_RULES）。
- **B1 步骤 1**：OKX + Binance 连通性实测全 200（香港机房未被 Binance 地域封锁）。**关键陷阱**：OKX 现货 volume_base 是字段 [5]，永续字段 [5] 是张数、volume_base 是 [6]（volCcy）——取错列会把永续成交量放大 100 倍。另：OKX/Cloudflare 会 403 Python urllib 默认 UA，须带浏览器 UA 或用 requests。
- **B1 步骤 2**：BTC-USDT 现货 30 天回补 55,616 根（562 请求 0 失败，Raw SHA 562/562，DuckDB=Parquet=台账三方一致）。
- **B1 步骤 3A**：ETH-USDT 现货 55,618 根。
- **B1 步骤 3B1**：BTC-USDT-SWAP 55,618 根；合约规格实测 ctVal=0.01 BTC/张（原始响应 + SHA 存档）；成交量全量交叉验证最大偏差 1.46e-11。期间发生接缝缺 4 根事故，已 --fresh 修复，异常记录保留并标记 resolved。
- **B1 步骤 3B1-R**：断点续传游标结构修复——ingest_cursor_utc = 实际入库的最老 open，仅在 Raw 落盘→入库→SQLite 单事务后推进；8 项回归测试全过（含事故等价复现）。遗留：失败页的 Raw 孤儿文件可对账识别但不自动清理；旧系列 ingest 游标待各自下次回补时回填（ETH 永续回补时已回填其 5 个）。
- **B1 步骤 4（进行中）**：ETH-USDT-SWAP 规格实测 ctVal=**0.1** ETH/张（与 BTC 的 0.01 不同），30 天回补 55,619 根。增量采集器 `scripts/run_collector.py`（20 序列；只插新根、从不改写已存 K 线，迟到修正仅记 `late_correction_detected` 异常；每小时 Parquet 归档；磁盘 <3GB 自停）。冒烟 1 失败并修掉两个 bug（1H/4H 大写、DuckDB 结果集消费），冒烟 2 通过。**12 小时正式运行：2026-11-24 21:27:22 CST 启动，2026-11-25 09:27:22 CST 自停**；收尾任务（cron `b1-step4-12h-wrapup`）定于 2026-11-25 09:50 CST 执行全量核账 + 单份 txt 报告 + 入仓留档。03:03 CST 最后一次人工抽查：累计插入 1,736、请求 1,736、失败 0，磁盘余 29.8 GB。

## 门禁（未获新任务书前不得启动）

- Binance 现货/期货采集；网络/重启恢复测试；任何策略/回测/交易功能。
- 待决策问题池：`crypto-quant-research-platform/QUESTIONS.md`（步骤 4 收尾时写入正式条目）。

—— Muse，2026-11-25
