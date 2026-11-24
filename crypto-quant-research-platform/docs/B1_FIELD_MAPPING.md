# B1_FIELD_MAPPING — 交易所 K 线字段映射表

版本：v1.0（B1 第一步，2026-11-24）
依据：两所公开 API 官方文档 + 2026-11-24 19:59 CST 在目标服务器（47.239.82.63）对四个接口的实测响应样本（样本原文摘要见第 6 节）。
本表是 B1 的开发契约：实现必须逐字段按本表适配，禁止假设两所字段相同。

## 1. 接口清单（全部为公开行情接口，无需任何 API Key）

| 交易所/市场 | 接口 | 用途 | 分页与单次上限 |
|---|---|---|---|
| OKX 现货/永续 | GET https://www.okx.com/api/v5/market/candles | 最新 K 线（仅近期窗口） | limit 最大 300（默认 100），按 after/before（毫秒时间戳）向历史方向翻页，返回**倒序**（新→旧） |
| OKX 现货/永续 | GET https://www.okx.com/api/v5/market/history-candles | 历史回补主力接口 | limit 最大 100，after/before 翻页，返回倒序；30 天回补必须用此接口，不能只用 candles |
| Binance 现货 | GET https://api.binance.com/api/v3/klines | 最新与历史 K 线 | limit 最大 1000，startTime/endTime（毫秒）翻页，返回**正序**（旧→新） |
| Binance USDT-M 永续 | GET https://fapi.binance.com/fapi/v1/klines | 最新与历史 K 线 | limit 最大 1500（保守按 1000 用），startTime/endTime 翻页，返回正序 |

公共要求：请求必须带浏览器型 User-Agent（实测：Python 默认 UA 会被 OKX 前置 Cloudflare 403，见 A0 审计）；两所均只用公开 GET，不签名、不带账户凭证。

## 2. 标的映射

| 统一 instrument_id | OKX native_symbol (instId) | Binance native_symbol | market_type | 备注 |
|---|---|---|---|---|
| BTC-USDT (spot) | BTC-USDT | BTCUSDT | spot | — |
| ETH-USDT (spot) | ETH-USDT | ETHUSDT | spot | — |
| BTC-USDT-SWAP | BTC-USDT-SWAP | BTCUSDT | swap | Binance 永续与现货同名，必须靠接口域名（fapi）+ market_type 区分，禁止只看 symbol |
| ETH-USDT-SWAP | ETH-USDT-SWAP | ETHUSDT | swap | 同上 |

周期映射：统一 `1m/5m/15m/1h/4h`；OKX bar 参数为 `1m/5m/15m/1H/4H`（小时为大写 H），Binance interval 为 `1m/5m/15m/1h/4h`（全小写）。此差异必须在适配层内消化。

## 3. OKX 原始字段 → 标准化字段

原始为数组（无字段名），顺序固定：`[ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]`

| 标准化字段 | OKX 现货 | OKX 永续 | 说明 |
|---|---|---|---|
| exchange | 常量 `okx` | 常量 `okx` | — |
| market_type | `spot` | `swap` | 由 instId 是否含 -SWAP 判定 |
| open_time_utc | [0] ts（开盘时间，ms） | 同 | UTC epoch ms |
| close_time_utc | open + 周期 − 1ms（推导，原始无此字段） | 同 | 必须在标准化层推导并注明 derived |
| received_at_utc | 本地接收时刻（UTC ms） | 同 | 采集端打点，非交易所字段 |
| is_final | [8] confirm：`1`=已收盘，`0`=未收盘 | 同 | 实测：返回第一条恒为当前未收盘 K 线（confirm=0） |
| open/high/low/close | [1]/[2]/[3]/[4] | 同 | 字符串转 Decimal/高精度浮点 |
| volume_base | [5] vol（基础币数量） | **[6] volCcy**（基础币数量） | ⚠️ 两市场取值列不同，见第 5 节 |
| volume_quote | [7] volCcyQuote | [7] volCcyQuote | 计价币（USDT）成交额 |
| volume_contract | 空（现货无合约概念，原因：现货无张数） | [5] vol（合约张数） | 永续 1 张 = 0.01 BTC/ETH（ctVal，以 instruments 接口为准，实现时须核对并记录） |
| trades_count | 空（candles 接口不提供） | 空 | 原因：OKX K 线接口无成交笔数字段，不造数 |
| source | `rest:market/history-candles` 或 `rest:market/candles` | 同 | 记录实际使用的接口 |

## 4. Binance 原始字段 → 标准化字段

原始为 12 元数组：`[openTime, o, h, l, c, volume, closeTime, quoteVolume, tradesCount, takerBuyBase, takerBuyQuote, ignore]`
现货（/api/v3/klines）与永续（/fapi/v1/klines）字段顺序相同，但仍分适配器实现，以便独立应对后续变更。

| 标准化字段 | Binance 现货 | Binance 永续 | 说明 |
|---|---|---|---|
| exchange | 常量 `binance` | 常量 `binance` | — |
| market_type | `spot` | `swap` | 由接口域名判定，不由 symbol 判定 |
| open_time_utc | [0] openTime（ms） | 同 | — |
| close_time_utc | [6] closeTime（ms，实测 = open + 周期 − 1） | 同 | 原生提供，无需推导 |
| received_at_utc | 本地接收时刻 | 同 | — |
| is_final | **推导**：closeTime < 交易所服务器时间 → true | 同 | 原始无 final 标志；当前正在形成的 K 线也会被返回（实测第二条即为未收盘），必须靠此规则过滤 |
| open/high/low/close | [1]/[2]/[3]/[4] | 同 | — |
| volume_base | [5] volume（基础币数量） | [5] volume（基础币数量） | 永续的 [5] 已是币数量（实测 157.204 BTC），不是张数 |
| volume_quote | [7] quoteVolume | [7] quoteVolume | — |
| volume_contract | 空（现货无） | 空（该接口不提供张数口径） | 原因：Binance 永续 K 线只给币数量与计价额，张数需另行换算，本阶段不换算、不造数，留空并在此注明 |
| trades_count | [8] | [8] | OKX 侧为空、Binance 侧有值，属正常字段适用性差异 |
| source | `rest:api/v3/klines` | `rest:fapi/v1/klines` | — |

## 5. 必须分别适配的差异（禁止统一假设）

1. **成交量列位不同（OKX）**：现货 volume_base 取 [5]，永续 volume_base 取 [6]、[5] 是张数。实测样本：OKX 永续一行 vol=2448.62（张）、volCcy=24.4862（BTC）、volCcyQuote=1479400.67（USDT）；OKX 现货一行 vol=0.46299862（BTC）、volCcy 与 volCcyQuote 均为 27989.70（USDT 口径）。若两市场套同一列位，永续成交量将放大 100 倍。
2. **is_final 来源不同**：OKX 有原生 confirm；Binance 靠 closeTime 与服务器时间比较推导。
3. **close_time 来源不同**：Binance 原生提供；OKX 需按周期推导。
4. **返回顺序不同**：OKX 倒序、Binance 正序，入库前统一按 open_time 升序。
5. **分页机制不同**：OKX 用 after/before 时间戳游标 + 小 limit（history 上限 100）；Binance 用 startTime/endTime + 大 limit（1000）。回补驱动必须各写各的。
6. **同名 symbol 不同市场（Binance）**：BTCUSDT 同时是现货与永续符号，唯一键必须含 market_type 与 exchange（见实现方案的去重键）。
7. **成交量不可直接跨所比较**：口径差异（张数/币量/计价额）已在本表分列保存；任何跨所比较必须先统一到 volume_base 或 volume_quote 并注明口径。

## 6. 实测样本摘要（2026-11-24 19:59 CST，服务器侧）

- OKX 现货 candles：HTTP 200，2 条，首条 confirm=0（未收盘）、次条 confirm=1，ts 为开盘 ms。
- OKX 永续 candles：HTTP 200，字段同上，vol/volCcy/volCcyQuote 三列口径如第 5 节。
- OKX history-candles：HTTP 200，结构与 candles 相同。
- Binance 现货 klines：HTTP 200，约 0.106s，12 元数组，closeTime=open+59999（1m）。
- Binance 永续 klines：HTTP 200，约 0.108s，结构同现货。
- 结论：四个接口从本服务器均可达，无需代理、无需凭证。
