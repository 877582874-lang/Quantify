# DATA_CONTRACT — 数据规范与契约

版本：v1.0（A2 阶段确立）
所有进入本系统的数据必须遵守本契约；不满足契约的数据不得进入 normalized 区。

## 1. 支持范围（第一版）

| 维度 | 取值 |
|---|---|
| 交易所 | OKX、Binance |
| 市场类型 | 现货（spot）、USDT 本位永续合约（swap） |
| 优先交易对 | BTC-USDT、ETH-USDT（现货与永续分别标识） |
| 行情周期 | 1m、5m、15m、1h、4h |
| 后续扩展 | 成交明细（trades）、订单簿（orderbook）、资金费率（funding_rate）、持仓量（open_interest）、标记价格（mark_price） |

## 2. 记录必备字段（每条行情记录缺一不可）

| 字段 | 说明 |
|---|---|
| exchange | 交易所标识，小写：`okx` / `binance` |
| symbol | 交易对，交易所原生格式并可映射统一格式，如 `BTC-USDT`、`BTC-USDT-SWAP` |
| market_type | `spot` / `swap` |
| data_type | `kline` / `trades` / `orderbook` / `funding_rate` / `open_interest` / `mark_price` |
| interval | K 线周期（仅 kline）：`1m` / `5m` / `15m` / `1h` / `4h` |
| ts_utc | 事件时间，UTC，epoch 毫秒（int）或带 Z 的 ISO 8601；K 线为开始时间 |
| ingested_at_utc | 入库时间，UTC，同上格式 |
| source | 数据来源：`rest` / `websocket`，及接口名称 |

## 3. K 线（OHLCV）字段

`open, high, low, close`（价格，字符串或高精度小数存储，禁止用二进制浮点直接做账务级计算）、`volume`（基础币数量）、`quote_volume`（计价币成交额，可得时必填）、`confirm`（该 K 线是否已收盘确认，未确认的 K 线必须可区分）。

## 4. 存储分区

- `data/raw/`：交易所返回的原始数据，按 `exchange/market_type/data_type/symbol/` 分目录，**只增不改不删**。
- `data/normalized/`：按本契约统一字段后的数据，只能由清洗管线从 raw 生成。
- `data/features/`：由 normalized 计算出的特征，必须记录生成代码版本与参数。
- `data/historical/`：外部导入的历史数据，必须附来源与导入时间说明，不得与 raw 混放。

## 5. 时间与时区

- 全部存储 UTC；北京时间仅用于展示层转换（UTC+8）。
- 跨交易所对齐以 ts_utc 为准；发现交易所时间戳异常（如未来时间、重复时间）须写入数据质量记录，不得静默丢弃。

## 6. 数据质量

- 清洗管线必须检查：缺失 K 线、重复记录、OHLC 逻辑（low ≤ open/close ≤ high）、时间连续性、未确认 K 线标记。
- 质量检查结果留痕于数据质量报告（由 `app/data_quality/` 产出）；原始数据的问题通过标记处理，**不回改 raw**。
- 断线/停机造成的数据缺口必须记录起止时间与原因（已知时），不得用插值冒充真实行情进入 raw/normalized；插值只能出现在 features 并明确标记。

## 7. 未来数据禁令

- 任何字段进入策略/回测可用集合的时间，不得早于其在真实市场可获得的时间。
- 回测数据视图必须按决策时点过滤，禁止把已收盘确认后的修正值回填到决策时点之前。
