# CHANGELOG — Crypto Quant Research Platform

记录规则：每次更动/版本更迭在此追加一条，含日期、阶段、变更内容、验证证据与报告链接。最新在上。
署名规则（2026-11-24 起）：每条记录末尾标注维护者署名；本文件 2026-11-24 的全部既有条目均为 Muse 所记。

## 2026-11-25

### 记忆库建立（memory/）
- 按 Cl 指令在本仓库新建跨会话记忆库 `memory/`：README（用途与入库规则）、rules-and-preferences（常设规则）、infrastructure（服务器/密钥指纹/代理）、project-status（平台状态）、codex-remote（远程控制全记录）、claude-code（已卸载留档）、timeline（两日时间线）。此前全部关键事实首次整理入库；今后 Muse 主动维护。—— Muse

### ChatGPT / Codex 远程控制贯通（服务器侧变更，非平台代码变更）
- ECS 安装 Codex 0.172.0（Node 26.13.1）；为绕 OpenAI 香港地区 403，部署 mihomo 代理（仅监听 127.0.0.1，钉新加坡节点）并注入 SSH 会话代理变量；Cl 本人完成设备码登录（02:16）。
- /opt/okx-quant 完成 git init 与 Codex 信任/沙箱配置（workspace-write、可写根含项目目录、沙箱出网开启，均经 Cl 批准）。
- 两轮埋暗号实测通过：项目文件读/改/增/删（03:04）；Git 克隆/改/提交/推送（提交 4b2a045，署名 ChatGPT，03:11 终验）。测试文件与分支已清理（09677b4）。
- 生图实测：Codex 无原生生图能力，fallback 需 OPENAI_API_KEY，未开通。
- 细节与教训（PKCS#8 钥格式、pkill 自匹配、沙箱策略冻结等）见 `memory/codex-remote.md`。—— Muse

### Claude Code 安装后卸载
- 03:23 按 Cl 指令安装 @anthropic-ai/claude-code v2.1.331（未登录）；03:31 Cl 以 Anthropic 封号风险为由决定不用，Muse 随即卸载并复核无残留（释放约 381 MB），量化系统基线无变化。见 `memory/claude-code.md`。—— Muse

## 2026-11-24

### 建档（archive init）
- 本仓库由 Cl 定为 AI 个人档案库；当日全部工作一次性建档：代码快照（app/scripts/tests + 依赖锁定）、6 份设计文档、9 份阶段报告、本文件与 ARCHIVE-2026-11-24.md。
- 约定：今后任何变更同步更新代码快照 + 本日志 + 阶段报告。

### B1 第四步 — 增量采集系统 + 12 小时试运行（进行中）
- 新增 scripts/run_collector.py：20 序列（BTC/ETH × 现货/永续 × 1m/5m/15m/1h/4h）增量采集；六类轮转 JSON 日志（logs/continuous/）+ summary.json；每分钟质量检查（时效/缺口/OHLC/成交量/未收盘/游标一致性）；每小时 Parquet 归档；磁盘 <3GB 自动停；不改写已入库 K 线（迟到修正只记异常）；到点自停。
- ETH-USDT-SWAP 接入前规格核验：ctVal=0.1 ETH/张（区别于 BTC 永续 0.01），原始响应+SHA-256 存档于服务器；30 天回补 55,619 行，成交量按 ctVal=0.1 全量交叉验证（最大偏差 4.7e-10）。
- 冒烟 1 失败并修复两个 bug：OKX bar 参数须大写 1H/4H（小写报 51000，参数类错误改为不可重试）；DuckDB fetchone 循环未消费结果集堵死连接（改为单次批量 IN 查询 + fetchall）。
- 冒烟 2 通过（36 请求 0 失败，20 序列补齐约 78 分钟断档，错误日志为空）。
- 12 小时运行：2026-11-24 21:27:22 CST 启动 → 2026-11-25 09:27:22 CST 自动停止。运行报告待结束后补档。

### B1 第三步 B1-R — 断点续传游标机制修复
- 根因修复：游标拆分为抓取进度（backfill_after_utc，仅诊断）与入库游标（新增 ingest_cursor_utc，仅在 Raw 落盘→入库→SQLite 单事务登记后推进）；续传起点 = ingest + 步长（边界重叠一根，主键去重吸收）；存量 NULL 游标自动从 DuckDB min(open) 推导。
- state.db 附加式迁移（新增可空列），原 15 行游标逐字段不变。
- 8 项回归测试全过（tests/unit/test_cursor_repair.py），含原 4 根缺口事故的等价复现（窄窗→扩窗续传零缺口）。
- 正式数据保护比对：三组数据 PRODUCTION_DATA_ALL_OK。
- 报告：reports/2026-11-24/B1-step3b1r-cursor-repair-report.txt

### B1 第三步 B1 — OKX BTC-USDT-SWAP 永续回补
- 合约规格实测：ctVal=0.01 BTC/张、ctValCcy=BTC、ctMult=1、线性、tickSz=0.1、settleCcy=USDT、state=live；原始响应存档（SHA-256 在案）。
- 成交量映射独立实现 normalize_okx_swap（张数=[5]、volume_base=[6] 交易所原值、volume_quote=[7]）；全量 55,618 行交叉验证：张数×ctVal 与 volume_base 最大偏差 1.46e-11，均价越界 0。
- 回补 43,200/8,640/2,880/719/179 = 55,618 行；Raw 646 批次 SHA 全过；四方一致。
- 事故：5m 试运行→正式续传接缝缺 4 根（2026-11-23 11:50–12:05Z），质检当场抓出，--fresh 重拉修复；根因催生 B1-R。
- 报告：reports/2026-11-24/B1-step3b1-btc-swap-report.md；规格：docs/B1_STEP3B1_CONTRACT_SPEC.md

### B1 第三步 A — OKX ETH-USDT 现货回补
- 与 BTC 同一固定窗口回补 43,200/8,640/2,880/719/179 = 55,618 行，559 请求 0 失败。
- 提前修掉两个潜伏隔离 bug（归档按 interval 过滤会串写 BTC 文件；去重计数缺 instrument 过滤）。
- BTC 保护比对全等。报告：reports/2026-11-24/B1-step3a-eth-spot-report.md

### B1 第二步 — OKX BTC-USDT 现货 30 天回补
- 建独立环境 .venv-data（duckdb 2.0.1 / pyarrow 26.0.1 / requests 2.34.2，版本锁定）。
- 回补 43,199/8,640/2,879/719/179 = 55,616 行（差额为各周期未收盘一根）；562 请求 0 失败；Raw SHA 562/562；DuckDB=Parquet=SQLite 三方一致；质量异常 0。
- 补充时间覆盖审计（只读）：逐周期按各自抓取时刻核算，头尾缺失 0，Raw 已收盘集合与 DuckDB 逐一相等。
- 报告：reports/2026-11-24/B1-step2-btc-spot-report.txt、…/B1-step2-time-coverage-audit.txt

### B1 第一步 — 连通性与字段映射
- ECS 实测 OKX 现货/永续 candles 与 history-candles 均 200；Binance 现货与 USDT-M 亦可达（未接入，仅验证）。
- 关键陷阱实证：OKX 永续 [5] 是张数、[6] 才是 BTC 量，错列会把成交量放大 100 倍。
- 产出 docs/B1_FIELD_MAPPING.md、docs/B1_IMPLEMENTATION_PLAN.md。报告：reports/2026-11-24/B1-step1-report.txt

### A2 — 工程骨架与标准
- 33 个目录、pyproject（crypto-quant-research-platform v1.0.0）、ARCHITECTURE / DATA_CONTRACT / DEVELOPMENT_RULES 三份标准文档；验收 9/9。报告：reports/2026-11-24/A2-skeleton-report.txt

### A1 / A0 — 资产盘点与服务器审计
- A1：项目当时为空骨架，venv 健康。A0：新 ECS 只读审计，无硬阻塞（磁盘/内存/网络/OKX 延迟 1.4ms）。
- 报告：reports/2026-11-24/A1-project-inventory.txt、…/A0-server-audit.txt

### 基础设施（同日早些时候）
- 阿里云香港 ECS 上线：Ubuntu 22.04.5、2C4G、40G；4GiB swap；Docker 29.1 + Compose v5.8；Python 3.12.16（deadsnakes，系统 3.10 不动）；Caddy v2.11.9（80 端口默认页）。
- 经验：OKX/Cloudflare 对 Python urllib 默认 UA 返回 403，API 客户端必须带浏览器 UA 或用 requests。
