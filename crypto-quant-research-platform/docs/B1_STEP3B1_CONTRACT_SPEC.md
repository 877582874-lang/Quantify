# B1_STEP3B1_CONTRACT_SPEC — OKX BTC-USDT-SWAP 合约规格与成交量语义

版本：v1.0（B1 第三步 B1，2026-11-24）
依据：OKX 公开接口实测响应（非文档转述、非字段名猜测）。

## 1. 规格来源（原始响应留存）

- 接口：GET https://www.okx.com/api/v5/public/instruments?instType=SWAP&instId=BTC-USDT-SWAP（公开接口，无 Key）
- 请求时刻：1795524378792（2026-11-24 12:46:18.792Z）；接收时刻：1795524378943；HTTP 200，code=0
- 原始响应保存：data/raw/exchange=okx/market_type=swap/data_type=instrument_metadata/instrument_id=BTC-USDT-SWAP/instruments-1795524378943.json.gz
- SHA-256：00d42e116a61c6dd0f6bc073118a9b8d02c21aed061245c28c2a7c5c18f8d21e（同目录 .manifest.json 有登记）

## 2. 规格字段（以实际返回为准）

| 字段 | 实测值 | 说明 |
|---|---|---|
| instId | BTC-USDT-SWAP | 合约唯一标识 |
| instType | SWAP | 永续合约 |
| ctVal | 0.01 | 合约面值（每张合约的基础币数量） |
| ctValCcy | BTC | 面值计价单位 = 基础币 |
| ctMult | 1 | 合约乘数 |
| ctType | linear | 线性合约（USDT 本位） |
| lotSz | 0.01 | 下单数量步长（张） |
| minSz | 0.01 | 最小下单量（张） |
| tickSz | 0.1 | 最小价格跳动（USDT） |
| settleCcy | USDT | 结算货币 |
| state | live | 状态正常 |
| lever | 100 | 最高杠杆（仅记录，与本数据任务无关） |
| uly | BTC-USDT | 标的 |
| expTime | （空字符串） | 永续无到期日，交易所返回空值 — 标记为未提供/不适用，不填充 |

## 3. 问题答复（A–H）

**A. 一张 BTC-USDT-SWAP 合约代表多少 BTC？**
0.01 BTC。依据 ctVal=0.01 且 ctValCcy=BTC，并经 K 线样本交叉验证（见第 4 节）。

**B. ctVal 的数值和计价单位是什么？**
ctVal = 0.01，单位为 BTC（ctValCcy=BTC），即每张合约面值 0.01 BTC；ctMult=1。

**C. K 线原始 vol 字段的单位是什么？**
合约张数。样本：vol=22475.61（张）。小数张数源于 lotSz=0.01 的成交聚合。

**D. K 线原始 volCcy 字段的单位是什么？**
基础币数量（BTC）。样本：volCcy=224.7561 BTC。

**E. K 线原始 volCcyQuote 字段的单位是什么？**
计价币成交额（USDT）。样本：volCcyQuote=13602761.48967 USDT。

**F. 是否可以通过 ctVal 将合约张数换算为基础币数量？**
可以。且本项目 volume_base 直接采用交易所给出的 volCcy 原始字段（不依赖换算），ctVal 换算仅用作交叉验证，避免换算误差进入正式数据。

**G. 换算公式与适用条件**
volume_base(BTC) = volume_contract(张) × ctVal(0.01 BTC/张) × ctMult(1)。
适用条件：仅适用于 ctType=linear 且 ctValCcy 为基础币的线性永续；规格变更（ctVal 调整）后必须重新核实；反向合约（币本位，ctValCcy 为 USD 类）不适用本公式，本项目未接入此类合约。

**H. 换算结果如何与交易所原始字段交叉验证？**
对每根 K 线比较 vol × ctVal 与交易所 volCcy：两者应相等（至多末位舍入差）。实测样本 5/5 精确相等（第 4 节）；正式回补后将在验收阶段对全部入库行做全量校验并报告最大偏差。另以 volCcyQuote / volCcy 应落在该根 K 线 [low, high] 区间内（成交均价界）做第二重 sanity 检查（抽样）。

## 4. 样本交叉验证（2026-11-24 12:46Z 实测 1m K 线原文）

| ts | vol（张）[5] | volCcy（BTC）[6] | vol×0.01 | 是否相等 | volCcyQuote（USDT）[7] | Quote/Base 均价 | [low, high] |
|---|---|---|---|---|---|---|---|
| 1795524300000 | 22475.61 | 224.7561 | 224.7561 | ✔ 精确 | 13602761.48967 | 60524.9 | [60427.6, 60585] ✔ |
| 1795524240000 | 57393.31 | 573.9331 | 573.9331 | ✔ 精确 | 34750014.91693 | 60547.5 | [60392.7, 60632] ✔ |
| 1795524180000 | 17232.13 | 172.3213 | 172.3213 | ✔ 精确 | 10408805.48565 | 60403.4 | [60380.4, 60443.4] ✔ |
| 1795524120000 | 19568.89 | 195.6889 | 195.6889 | ✔ 精确 | 11815406.79939 | 60381.9 | [60305.9, 60447.8] ✔ |
| 1795524360000 | 3817.72 | 38.1772 | 38.1772 | ✔ 精确 | 2306405.17178 | 60412.9 | [60403.1, 60443] ✔（此根 confirm=0，仅作样本） |

## 5. 标准化映射（永续，正式生效口径）

| 标准化字段 | 来源 | 单位 |
|---|---|---|
| volume_native | 原始 [5] vol 原值 | 张（原始单位保留） |
| volume_contract | 原始 [5] vol | 张 |
| volume_base | 原始 [6] volCcy | BTC |
| volume_quote | 原始 [7] volCcyQuote | USDT |

与现货的根本区别：现货 [5] 即基础币数量；永续 [5] 是张数、基础币数量在 [6]。两套映射在代码中是两个独立函数（normalize_okx_spot / normalize_okx_swap），禁止共用列位假设。原始行完整保存在 raw_row 与 Raw 批次中，任何字段可追溯复核。
