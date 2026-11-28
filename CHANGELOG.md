# CHANGELOG

## 2026-11-28 — QUANT-V2-CLEAN-AGG-20261128-002（Muse）
数据管道加固：清洗层（逐笔时间戳护栏/值域隔离/对侧比价 suspect、
快照倒挂跳过+连续强制重建/中间价脱节标记、K 线周期对齐+未来戳
隔离；上线即抓到 Binance 永续 2 笔 0 价成交并隔离）；回补端点
实测修正（OKX trades-history 出口 404 → /market/trades 500 条页
+GAP_UNFILLED；Binance 现货 fromId → historicalTrades 免 key，
上限提至 10 万笔）；双时钟（逐笔 recv_ms、K 线 available_ms）；
聚合层 quant-aggregator（1s/1m/5m 时间条、10 BTC Volume Bar、
100 万 USDT Dollar Bar、1m 盘口特征，derived.db）；对齐层
aligned_1m/5m（资金费率+盘口 as-of ≤ 收盘时刻，防未来信息）；
磁盘管家 quant-janitor（每日监控+投影报警，只监控不删除，
保留天数待 Cl 定）。服务器巡检：负载 0.06、内存可用 2.7G、
磁盘可用 23.93G、五服务全 active。
报告：quant-v2/reports/2026-11-28/quant-v2-cleaning-aggregation-20261128-002-report.txt

## 2026-11-28 — QUANT-V2-STREAMS-20261128-001（Muse）
新系统数据层补齐：新增 quant-trades（4 路 WS 逐笔成交 + REST 断线回补 +
归档尾锚定重启恢复）与 quant-books（4 路本地订单簿：Binance 序号/pu 链
校验、OKX seqId 链校验——其实测 checksum 恒为 0；5 秒前 100 档快照）。
归档为按天/小时 JSONL，当前小时明文、整点轮转 gzip 并校验；两服务内置
8 GiB 磁盘地板停采守卫。实测 1 小时：逐笔 340,612 笔（≈800 万/日）、
快照 ≈69,000 张/日、合计 ≈178 MB/日、地板余量可撑 ≈90 天、订单簿
0 重建、K 线采集无回归。上线调试期归档写入缺陷（gzip 追加坏缝）已
重构修复，调试期逐笔归档损失如实记录于报告第五节。
报告：quant-v2/reports/2026-11-28/quant-v2-streams-20261128-001-report.txt

## 2026-11-28 — 仓库清空重建（Muse）
按 Cl 指示清空旧量化系统全部内容（服务器 + 本仓库），为新系统保留
仓库与各 AI 访问权限。旧条目随旧历史一并移除。
