# DEVELOPMENT_RULES — 开发与工程规范

版本：v1.0（A2 阶段确立）

## 1. 阶段与审批

- 项目按阶段推进：每阶段先审计/出方案，经项目负责人批准后施工，完成后验收，再进入下一阶段。
- 未经批准不得提前开发下一阶段功能（尤其：行情采集、策略、回测、交易执行）。
- 每次施工前先做备份（backups/ 下带时间戳），发现文件冲突先停手报告，不强行覆盖。

## 2. 环境与依赖

- Python 版本锁定 3.12 系列（pyproject.toml：>=3.12,<3.13）。宿主机系统 Python（3.10）不得用于本项目。
- 依赖以 requirements-existing.lock.txt 为基线快照（A2 时点：venv pip freeze，10 个包）。
- **不得擅自升级 pandas、numpy**；新增或升级任何依赖必须：先在隔离环境验证与现有 pandas/numpy 及拟装量化库（CCXT、VectorBT、MLflow 等）的兼容性 → 更新 pyproject.toml 与锁定文件 → 记录变更原因。
- 虚拟环境 venv 不入版本管理；环境复现只认锁定文件。

## 3. 代码组织

- 代码按 ARCHITECTURE.md 的分层落位：exchanges / collectors / storage / data_quality / strategies / backtesting / risk / evaluation / api / services / core。
- 一个策略一个目录，策略必须声明唯一名称与版本；策略参数放 config/strategies/ 的配置模板，不硬编码在代码里。
- 公共逻辑进 core，禁止在各策略里复制粘贴公共代码。
- 时间处理一律用 UTC 的工具函数（core 提供），业务代码禁止直接用本地时间做存储与计算。

## 4. 配置与凭证

- 配置模板可以入库（config/），**真实凭证只能放 .env**（权限 600），禁止写入代码、模板、文档、镜像与提交记录。
- .env 不读取、不打印、不外传；日志中禁止输出任何凭证、Token、签名原文。

## 5. 数据纪律

- 遵守 DATA_CONTRACT.md：raw 只增不改；清洗只读 raw 写 normalized；缺口留痕，不造数据。
- 实验必须留全元数据（参数、数据范围、代码版本），无元数据的结果无效（见 ARCHITECTURE.md 第 5 节）。

## 6. 测试

- 新功能必须带测试：单元测试放 tests/unit，跨模块放 tests/integration，阶段验收放 tests/acceptance。
- 回测相关代码必须包含防未来数据测试（用构造的时序数据验证信号不引用未来值）。
- 提交前测试须全部通过；测试数据用构造数据或脱敏样本，不用真实凭证。

## 7. 日志与产物

- 运行日志进 logs/，按服务与日期切分，并受宿主机/容器日志轮转约束；禁止把行情全量数据打进日志。
- 实验产物进 experiments/，本地产物不入版本管理（见 .gitignore）；对外报告须注明数据范围与假设。

## 8. 安全红线

- 研究阶段不连接交易账户、不保存可交易凭证、不开发下单功能。
- 对外服务只经 Caddy（80/443）暴露；数据库与内部端口不映射公网。
- 服务器侧操作延续既有纪律：不改 Docker/Caddy/SSH/防火墙配置除非该阶段方案明确批准；不重启服务除非方案明确批准。
