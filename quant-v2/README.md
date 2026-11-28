# Quant System v2 — 数据层（重建起点）

2026-11-28 按 Cl 指示：旧量化系统全部清除后重建。本目录只含新系统的
数据采集层：统一口径数据库 + 实时采集器。旧系统的一切（代码、数据、
报告）已删除；各 AI 的访问权限、OKX Demo API 密钥（/root/.okx-demo.env）
按指示保留未动。

## 统一数据口径（canonical caliber）

- exchange：`okx` | `binance`；market_type：`spot` | `swap`
- symbol：`BTC-USDT`（单一币种，2026-11-28 Cl 裁定 ETH 不做；两家交易所同名，永续为 USDT 本位）
- interval：`1m` | `5m` | `15m` | `1h` | `4h`
- 时间：一律 UTC 毫秒（open_time_utc = K 线开盘时刻）
- volume_base：一律基础资产数量（BTC/ETH 个数）。
  OKX 现货取字段[5]；OKX 永续取字段[6] volCcy（字段[5] 是张数，禁止入此列）；
  Binance 现货与合约的 volume 字段本身即基础数量。
- volume_quote：源头报告的计价货币成交额；源头不给则 NULL，绝不推算伪造。
- 只存已收盘（final）的 K 线；final 不可变，主键去重，重复采集幂等。
- 资金费率（仅永续）存 funding_rates，同口径键。

## 数据库

SQLite（WAL），文件 data/market.db：
- klines(exchange, market_type, symbol, interval, open_time_utc,
  open, high, low, close, volume_base, volume_quote)，
  PK = (exchange, market_type, symbol, interval, open_time_utc)
- funding_rates(exchange, symbol, funding_time_utc, funding_rate)
- collector_meta(key, value)

## 采集器

collector.py（纯标准库，无第三方依赖），systemd 单元 quant-collector
（Restart=always，开机自启）。2026-11-28 按 Cl 指示清空重来：数据库从 2026-11-28 00:00 CST 起点开始，
起点之前的任何数据永不入库（起点存于 collector_meta.collect_floor_ms）。
启动时补齐起点至今的 K 线，随后每 60 秒轮询全部 20 个序列的最新已收盘 K 线；
资金费率每小时补拉。日志 logs/collector.log。

—— Muse，2026-11-28

## 双源交叉校验（2026-11-28 Cl 指示）

两家交易所互为参照，任何 K 线不得凭单一来源入库：
抓取 → staging 暂存 → OKX 与 Binance 同市场/同周期/同时刻配对 →
收盘价偏差 ≤ 0.10% 两条一起入 klines；> 0.10% 两条一起隔离进
anomalies（reason=price_mismatch，含双方收盘价与偏差），永不入库，
并立即记入日志。单边数据等对方 15 分钟未到 → 隔离
（counterpart_missing）；OHLC 自洽性不过 → 隔离（ohlc_sanity）。
阈值依据：2026-11-28 实测 2,484 对真实数据，平均偏差 0.005%、
p99 0.021%、最大 0.024%——0.10% 约为最差健康值的 4 倍。
隔离区保留完整 OHLCV，可审计、可人工恢复。资金费率是各交易所
自身属性，不做跨所配对校验。

## 逐笔成交与订单簿（2026-11-28 Cl 指示补齐）

trades_service.py（quant-trades 服务）：4 路 WS 逐笔
（OKX/Binance × 现货/永续），统一口径 JSONL 按天/小时 gzip 归档于
data/trades/<交易所>/<市场>/<日期>/<小时>.jsonl.gz，字段
exchange/market_type/symbol/trade_id/trade_time_utc/price/qty/side，
qty 一律基础币（OKX 永续 sz 为张数，×0.01 换算），side 为主动方。
每路维护 last_trade_id，断线重连先 REST 回补缺口再接实时流，
回补上限外的缺口记 GAP_UNFILLED 日志留痕。

books_service.py（quant-books 服务）：4 路本地订单簿 + 每 5 秒
前 100 档快照，归档于 data/books/<交易所>/<市场>/<日期>/<小时>.jsonl.gz。
Binance：REST 快照 + 增量流序号校验（现货 U/u 衔接、合约 pu 链），
断链即全量重建。OKX：books 通道 seqId/prevSeqId 链校验（实测
checksum 字段恒为 0，不可用；非 0 时仍验 CRC32），断链即重订重建。
两服务独立于 K 线采集器，互不拖累；磁盘可用 < 8 GiB 时自动停写
并报警留痕（DISK_FLOOR），恢复到 8.5 GiB 自动续采。
统计：logs/trades-stats.json、logs/books-stats.json（每分钟）。

更正（2026-11-28 晚）：归档写入方式已改为 当前小时明文 .jsonl + 整点轮转时 gzip 压缩并校验（旧的 gzip 追加写在进程被杀时产生坏缝，已废弃）；OKX books 的 checksum 字段实测恒为 0，完整性校验以 seqId 链为准。详见 Quantify 仓库归档报告 QUANT-V2-STREAMS-20261128-001。

== 清洗/聚合/对齐层（2026-11-28 晚，Cl 指令）==
- 清洗：逐笔三道闸（时间戳量级/漂移>5min 隔离、价量<=0 隔离、对侧价偏离>2% 打 suspect 标记）；快照倒挂不落盘（连续 3 次强制重建订单簿）、中间价偏离最新成交价>1% 打 suspect；K 线加周期对齐 + 未来时间戳隔离。隔离区在 data/quarantine/。
- 双时钟：逐笔带 recv_ms（到达时刻），K 线带 available_ms（过闸可用时刻，旧行为 NULL 不伪造）。回测只能用 >= available_ms / close_ms 的数据。
- 聚合（quant-aggregator，derived.db）：逐笔 1s/1m/5m 时间条、Volume Bar 10 BTC、Dollar Bar 100 万 USDT、快照 1m 盘口特征；aligned_1m/5m 为资金费率+盘口 as-of 对齐（<= 收盘时刻，防未来信息）。执行口径：信号在 final bar 收盘后算、按下一根开盘成交。
- 磁盘管家 quant-janitor（每日，只监控不删除）：体积报告 + 跌破 8 GiB 地板的天数投影报警；保留天数待 Cl 定。
- 回补端点实测：OKX trades-history 在本机出口 404，改用 /market/trades 最新 500 条 + GAP_UNFILLED 留痕；Binance 现货 fromId 走 api.binance.com 的 historicalTrades（免 key）。
