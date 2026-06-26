# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### 修复

- **支付宝**：`交易关闭`/`已关闭` 的交易仅在存在配对退款（`退款成功`）时保留，否则一律过滤，不再只过滤 `不计收支`。修复闲鱼等场景下买家取消订单产生的 `收入+交易关闭` 记录被当作幻象收入生成 pending 分录的问题

## [0.5.0] - 2026-06-13

### 新增

- **美团**：新增美团（`meituan`）支付平台 Provider，解析美团 CSV 账单（UTF-8 BOM、20 行表头）。以实付金额作为交易金额，存在优惠时将订单原价记入 `order_amount` 元数据；从订单标题首个 `-` 前缀提取商户作为 payee、`-` 之后作为描述（不重复商户名）；退款交易打 `#refund` 标签；支持纯日期格式的账单周期
- **汇丰香港**：新增汇丰香港（`hsbchk`）信用卡与借记卡 Provider
- **中信银行国际**：新增中信银行国际（`cncbi`）借记卡 Provider
- **汇立银行**：新增汇立银行（`welab_debit`）借记卡 Provider，解析 WeLab App 下载的多币种综合电子月结单 PDF。按币种代码映射账户，依坐标重建交易表（币种段可跨页，续页无段头时按上一页币种结转），借记/贷记符号转换为 bean-sieve 约定；跨币种兑换的两腿（卖出币种借记 + 买入币种贷记，共享 `Ref: FX…`）各自保留为独立交易，以便与 ledger 中同账户两腿的 `@@` 兑换记法逐腿匹配；退款交易打 `#refund` 标签并以 `^<订单号>` 关联；外币消费的实际交易日与换汇 `FX Ref` 记入元数据；交易种类/换汇详情/收款方亦记入元数据

### 修复

- **账户映射**：`account_mappings` 改为单向子串匹配（配置 `pattern` 包含于交易 `method`）。此前的双向匹配会让泛化的支付渠道（如美团 `云闪付`）误命中更具体的配置 pattern（如 `云闪付-交通银行(5871)`）而错误归属到具体卡，并连带挡住与 ledger 的正常匹配；现在无绑卡信息的泛化渠道会保留为 `Assets:FIXME`
- **支付宝**：剥离新格式（2026 起）退款订单号中的 `*REFUND` 标记，使退款记录能正确关联原始订单
- **CLI**：`extract-accounts` 交互选择改用 fzf 真实快捷键——Enter 选定账户、Esc 跳过当前支付方式、Ctrl-Q（或 Ctrl-C）退出并保留已选映射；快捷键在 fzf header 常驻显示，跳过操作更易发现

### 其他

- **Schema**：从 `field_mapping` enum 中移除 `transaction_status`

## [0.4.1] - 2026-04-29

### 修复

- **支付宝**：`alipay_refund` 预设规则误把所有 `退款` 开头的交易翻转为收入，导致用户主动发起的退款（`tx_type=支出`）变成幻象收入而无法对账。规则限定为 `tx_type=收入|不计收支`
- **民生信用卡**：补充解析对账单中的 `loopBand7`（退货）与 `loopBand5`（还款）两个区段，修复退款与还款记录此前完全缺失的问题
- **工行借记卡**：保证返回的交易按时间顺序排列

### 其他

- CI/Release workflow 升级到 `astral-sh/setup-uv@v8.1.0`

## [0.4.0] - 2026-04-29

### 新增

- **元数据诊断（card_last4 软校验）**：从支付宝/微信 `method` 字段提取卡号末四位；对共享账户（多卡共用，由 `account_mappings` 自动推断或 `meta_check_accounts` 显式声明）做软校验，不一致时输出 `MetaDiagnostic`，结果渲染到 output 的 diagnostics section
- **配置开关**：新增 `diagnostics.meta_check`（默认开启）和 `meta_check_accounts` 显式账户列表，配套更新 `bean-sieve.example.yaml` 和 JSON Schema

### 修复

- **BOC 信用卡**：从 PDF 读取真实账单截止日期，账单周期更准确
- **Output**：extra ledger entries 保留原始源文本

### 其他

- 借记卡 provider import 语句整理

## [0.3.1] - 2026-04-20

### 修复

- **京东账单**：修正 metadata key 从 `payment_method` 为 `method`，与支付宝/微信保持一致。此前导致 JD 交易无法匹配 `account_mappings`、全部落入 `FIXME`，`extract-accounts` 也无法识别支付方式

## [0.2.0] - 2026-04-06

### 新增

- **规则自动生成**：新增 `suggest-rules` 命令，从账本历史记录中自动分析高频 payee→account 映射，生成规则建议
- **社区链接**：README 添加 LINUX DO 社区入口

### 变更

- **移除 smart-importer 依赖**：移除 SmartPredictor 及相关机器学习依赖，简化项目依赖

## [0.1.0] - 2026-04-06

首次正式发布。

### 新增

- **核心对账引擎**：基于日期/金额的模糊匹配，支持跨账单去重、按卡对账及 Extra 计算
- **规则引擎**：正则匹配规则，支持优先级排序、收支方向条件、`contra_account` 解析、`target_description` 动作
- **预设规则系统**：支付宝、微信常见交易的自动账户匹配
- **余额断言**：对账后自动生成 balance 指令

#### 数据源

- **支付平台**：支付宝、微信支付、京东、App Store
- **信用卡**：农业银行、中国银行、交通银行、上海银行、建设银行、广发银行、兴业银行、招商银行、民生银行、中信银行、华夏银行
- **借记卡**：农业银行、中国银行、交通银行、建设银行、兴业银行、招商银行、工商银行、平安银行
- 自动识别：基于文件扩展名、文件名关键词、文件内容关键词

#### 命令行

- `reconcile`：完整对账流程，匹配账本已有记录
- `parse`：解析账单，支持表格/JSON 输出
- `providers`：列出可用数据源
- `export`：导出为 CSV/XLSX
- 交互式账户提取向导
- Shell 补全（bash/zsh/fish）
- 自动检测当前目录下的配置文件

#### 配置

- YAML 配置文件，附 JSON Schema 校验
- 账户映射（支付方式 → 资产账户）
- 数据源级别的输出和 posting 元数据配置
- 可配置的默认交易标记、时间排序、元数据字段
- 账户映射中的返现账户支持

#### 输出

- 生成合法的 Beancount 语法，集成 beanfmt 格式化
- 完整的 Extra 条目，附源文件链接
- 可配置的元数据字段，4 空格缩进
- Provider 生命周期钩子（`pre_reconcile`、`post_output`）

[0.5.0]: https://github.com/Xm798/bean-sieve/releases/tag/v0.5.0
[0.4.1]: https://github.com/Xm798/bean-sieve/releases/tag/v0.4.1
[0.4.0]: https://github.com/Xm798/bean-sieve/releases/tag/v0.4.0
[0.3.1]: https://github.com/Xm798/bean-sieve/releases/tag/v0.3.1
[0.2.1]: https://github.com/Xm798/bean-sieve/releases/tag/v0.2.1
[0.2.0]: https://github.com/Xm798/bean-sieve/releases/tag/v0.2.0
[0.1.0]: https://github.com/Xm798/bean-sieve/releases/tag/v0.1.0
