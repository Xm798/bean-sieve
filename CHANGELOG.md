# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### 新增

- 民生银行借记卡 Provider（`cmbc_debit`）：解析个人账户对账单 PDF
- 中信银行借记卡 Provider（`cncb_debit`）：解析交易明细 XLS，按表头内容识别
- 浦发银行借记卡 Provider（`spdb_debit`）：解析交易流水 PDF 回单
- 浦发银行信用卡：解析 PDF 账单；同月传入交易明细和 PDF 时只采用交易明细
- 工商银行信用卡 Provider（`icbc_credit`）：解析电子邮件账单、补发对账单 PDF 和历史明细 PDF，同一账期多份文件只采用一份
- 交通银行信用卡：同一账期传入正本与补发时只采用一份，不再重复入账
- 招商银行信用卡：解析 PDF 补发账单；同月邮件与补发同时传入时只采用邮件
- 招商银行信用卡：邮件账单的美元账户段也会解析，记为 USD
- 建设银行信用卡：解析 PDF 补发账单；同一账单周期邮件与 PDF 同时传入时只采用邮件
- 广发银行信用卡：解析 PDF 补发账单；同一账单周期邮件与 PDF 同时传入时只采用邮件
- 民生银行信用卡：同一账单周期的正本与补发邮件同时传入时只采用靠前的一份
- 农业银行信用卡：解析 PDF 补发账单；同一账单周期邮件与 PDF 同时传入时只采用邮件
- 配置项 `fee_account`（`defaults` 和 `providers.<id>` 下均可设置）：账单里单列的手续费另记一条 posting，默认记到 `Expenses:FIXME`

### 修复

- 换汇行带手续费时输出手续费 posting
- 民生银行借记卡：被分页截断的交易行接上下一页的续行；同一张卡起止日期重叠的对账单只采用一份，缺页的排在完整的之后，没有交易的对账单也参与
- 账本里的换汇另带手续费或返现 posting 时，账单只出现一侧，另一侧的外币 posting 不再报为 Extra
- 账本里用 `{}` 记成本的买卖只有资金一侧出现在账单里时，持仓一侧不再报为 Extra
- 工商银行信用卡：本期只有 0.00 行或没有交易的卡也计入账单覆盖范围，折行的英文商户名在折行处补空格
- 多家银行的中文币种名改用同一张对照表，补全澳门元、韩元、新西兰元等
- 信用卡账单含分期或延迟入账的行时，账单周期之前的账本条目不再报为 Extra
- 信用卡账单与无账单周期的账单一起对账时，后者在信用卡周期外的交易不再被丢弃
- 招商银行信用卡：PDF 里不印卡号的利息、返现等行记到各卡共用的账户，不再是 `Assets:FIXME`
- 招商银行信用卡：同一文件的警告只打印一次；找不到账单月份的邮件改为跳过，不再按当前月份处理
- 账本里的 `@@` / `@` 换汇只有一侧出现在账单里时，另一侧的外币 posting 不再报为 Extra
- 招商银行信用卡：解析表头为图片的旧版账单邮件，邮件账单月份按正文、账单周期和标题识别
- 农业银行信用卡：早期版式的邮件账单（GBK 编码）能解析出交易和核对报告，不再是 0 笔；核对报告新增「未读出」一行列出缺项，读不出账单周期时不再生成按当天日期的刷卡金分录
- 建设银行信用卡：境外交易按结算币种和金额入账，原币金额记在 `original_amount`，不再按原币记账
- 中信银行（国际）、汇丰香港储蓄账户：交易记到配置里唯一的账户，不再输出 `Assets:FIXME`，`balance: true` 会生成余额断言
- 汇丰香港信用卡：文件名不带卡号后缀时，交易记到配置里唯一的账户
- 中国银行借记卡：`accounts` 唯一的键是 4 位数字时才作为交易的 `card_last4`，其他名称不再出现在卡号元数据里
- 余额断言：按入账日期取最后一笔的余额，币种取账单的余额币种
- 民生银行信用卡：退货和还款区段有多行时全部解析，不再只取第一行
- 民生银行信用卡：解析单表版式的旧账单邮件
- 民生银行信用卡：退货、还款行写入记账日 `posting_date`，并解析奖励返还区段
- 交通银行信用卡：解析 2017–2021 年旧版账单邮件，人民币金额统一记为 CNY
- 中国银行借记卡：解析 UTF-16 编码的 CSV 导出文件
- 中国银行信用卡：不再把浦发信用卡 PDF 识别为中行账单
- 中信银行信用卡：不再把中信借记卡 XLS 识别为信用卡账单
- 广发银行信用卡：识别主题只写「广发信用卡」的账单邮件
- 广发银行信用卡：解析 2024 年及更早的邮件账单，交易、币种和账单周期不再读错或缺失
- 广发银行信用卡：摘要里含「数字 币种 数字」的交易按行尾的金额和币种入账，读不出金额的交易行给出警告
- 建设银行借记卡：支持只有一列带符号「交易金额」的 9 列导出格式
- 农业、建设、兴业、平安银行借记卡：按文件内容识别账单，不再依赖银行默认文件名
- 微信支付：零钱提现的服务费单独记账，零钱按提现总额扣减；账本里按扣费后金额记的提现仍能匹配
- `bean-sieve.example.yaml`：App Store 改用资产账户，众安银行各币种共用一个账户，汇立银行去掉不起作用的 `balance: true`

## [0.8.0] - 2026-10-29

### 新增

- 配置项 `defaults.rebate_account_prefixes`：指定红包抵扣还款按总额匹配时计入的返利账户，默认 `["Income:Rebate:"]`；`rebate_account` 与 `rebate_income_account` 自动并入
- 外币交易的对方科目输出为 `原币 @@ 入账额`，涉及交通银行、浦发银行、广发银行信用卡

### 修复

- 招商银行、交通银行信用卡：境外交易金额取人民币入账金额，原币记入 `original_amount`；按原币导入的旧条目仍可匹配
- 广发银行信用卡：解析人民币、美元以外币种的交易
- 华夏银行信用卡：解析美元账户交易；账单月份优先取正文标题 `对账单(YYYY/MM)`
- 交通银行信用卡：支持日期为 YYYY-MM-DD 的新版账单
- 中国银行信用卡：合并账单中下一张卡的汇总不再混入上一张卡的交易
- 农业银行信用卡：刷卡金分录改用「本期调整金额」；重命名或显式指定 Provider 的账单也纳入核对报告
- 微信：解析 `零钱通转出-到XX` 与 `经营账户充值`
- App Store：交易日期按北京时间计；识别 `US$` 等带前缀的货币符号
- 支付宝：含红包抵扣的 `信用借还` 行可匹配银行卡与返利两腿合计等于账单金额的分录
- 预设规则：账户关键字优先匹配同名 pattern，`零钱` 不再误配到 `零钱通`
- Extra 判定：整户账单内的两腿转账，一腿匹配后另一腿不再报 Extra
- 借记卡：流水只覆盖本卡，同一 Provider 下其他卡的条目不再报 Extra

### 其他

- Release workflow 升级到 `softprops/action-gh-release@v3`
- PyPI 发布改用 Trusted Publishing

## [0.7.0] - 2026-09-04

### 新增

- 规则：`target_payee: ""` 清除交易的 payee，原值记入 `original_payee`

### 修复

- 输出：无 payee 的交易省略空的 `""`
- 输出：`_` 开头的内部元数据键不再写入 .bean，生成的账本可通过 bean-check
- 多 Provider 对账时，每个 Provider 的 `post_output` 钩子都会执行，此前只执行第一个
- 账单周期按卡计算，延迟入账的交易只扩展所在卡的周期；涉及农业银行、上海银行、建设银行、广发银行、兴业银行、招商银行、民生银行、华夏银行、交通银行、邮储银行信用卡。Extra 结果可能变化
- Extra 判定按账户归属周期；同一账户配置多张卡时合并各卡周期
- 华夏银行、交通银行信用卡：仅含 MM/DD 的交易按账单周期推断年份，修正跨年与延迟入账行的年份

### 其他

- `ReconcileContext` 新增 `parsed` 字段，钩子可复用各账单的解析结果
- balance 指令格式化移入 `BeancountWriter.format_balance`
- `account_mappings` 解析逻辑集中到 `core/accounts.py`，行为不变

## [0.6.0] - 2026-07-29

### 新增

- 浦发银行信用卡 Provider（`spdb_credit`）：解析网银导出的 XLS 交易明细

## [0.5.0] - 2026-07-27

### 新增

- 歧义匹配诊断：一笔流水可匹配多个账本分录时给出提示，在输出中单列「Ambiguous matches」区块；由 `diagnostics.ambiguous_match` 控制，默认开启
- 美团 Provider（`meituan`）：按实付金额入账，订单原价记入 `order_amount`，退款打 `#refund` 标签
- 汇丰香港 Provider（`hsbchk`）：信用卡与借记卡
- 中信银行国际 Provider（`cncbi`）：借记卡
- 汇立银行 Provider（`welab_debit`）：解析多币种电子月结单 PDF，换汇两腿分别输出，可与 `@@` 记法逐腿匹配

### 修复

- `account_mappings` 改为单向子串匹配（`pattern` 包含于 `method`），泛化支付渠道不再误配到具体卡
- 支付宝：去除新格式退款订单号中的 `*REFUND` 标记，退款可关联原订单
- 支付宝：`交易关闭` 的交易仅在有配对退款时保留
- 支付宝：新增 `花呗还款` 预设规则，花呗腿不再报 Extra
- 农业银行信用卡：解析附属卡行（如 `1234附`）与卡号为空的账户级行；行解析失败时输出警告
- 中国银行信用卡：按交易日期分行，同日间距较近的多笔交易不再合并
- 兴业银行信用卡：解析带 `HH:MM` 时间的交易日期
- CLI：`extract-accounts` 改用 fzf 快捷键，Enter 选定，Esc 跳过，Ctrl-Q 退出并保留已选映射

### 其他

- Schema：`field_mapping` 移除 `transaction_status`
- README：更新交通银行网银入口链接

## [0.4.1] - 2026-04-29

### 修复

- 支付宝：`alipay_refund` 预设规则只作用于 `tx_type` 为 `收入` 或 `不计收支` 的交易，用户发起的退款不再被记为收入
- 民生银行信用卡：解析退货与还款区段
- 工商银行借记卡：交易按时间排序

### 其他

- CI 与 Release workflow 升级到 `astral-sh/setup-uv@v8.1.0`

## [0.4.0] - 2026-04-29

### 新增

- 元数据诊断：从支付宝、微信的 `method` 提取卡号后四位，校验多卡共用的账户，不一致时输出到 diagnostics 区块
- 配置项 `diagnostics.meta_check`（默认开启）与 `meta_check_accounts`

### 修复

- 中国银行信用卡：从 PDF 读取账单截止日期
- 输出：Extra 条目保留原始文本

### 其他

- 整理借记卡 Provider 的 import

## [0.3.1] - 2026-04-20

### 修复

- 京东：元数据键 `payment_method` 改为 `method`，与支付宝、微信一致，`account_mappings` 与 `extract-accounts` 可识别其支付方式

## [0.2.0] - 2026-04-06

### 新增

- `suggest-rules` 命令：根据账本历史生成 payee → account 规则建议
- README 添加 LINUX DO 社区链接

### 变更

- 移除 smart-importer 依赖

## [0.1.0] - 2026-04-06

首次发布。

### 新增

- 对账引擎：按日期、金额模糊匹配，支持跨账单去重、按卡对账与 Extra 计算
- 规则引擎：正则规则，支持优先级、收支方向条件、`contra_account` 与 `target_description`
- 支付宝、微信常见交易的预设规则
- 对账后生成 balance 指令

#### 数据源

- 支付平台：支付宝、微信支付、京东、App Store
- 信用卡：农业银行、中国银行、交通银行、上海银行、建设银行、广发银行、兴业银行、招商银行、民生银行、中信银行、华夏银行
- 借记卡：农业银行、中国银行、交通银行、建设银行、兴业银行、招商银行、工商银行、平安银行
- 按扩展名、文件名关键词与文件内容关键词自动识别

#### 命令行

- `reconcile`：解析账单并与账本对账
- `parse`：解析账单，输出表格或 JSON
- `providers`：列出可用数据源
- `export`：导出为 CSV 或 XLSX
- 交互式账户提取
- Shell 补全（bash、zsh、fish）
- 自动查找当前目录下的配置文件

#### 配置

- YAML 配置文件与 JSON Schema
- 账户映射：支付方式 → 资产账户，可指定返现账户
- 按数据源配置输出与 posting 元数据
- 可配置默认交易标记、时间排序与元数据字段

#### 输出

- 生成 Beancount 语法，使用 beanfmt 格式化，4 空格缩进
- Extra 条目附源文件链接
- Provider 生命周期钩子（`pre_reconcile`、`post_output`）

[0.8.0]: https://github.com/Xm798/bean-sieve/releases/tag/v0.8.0
[0.7.0]: https://github.com/Xm798/bean-sieve/releases/tag/v0.7.0
[0.6.0]: https://github.com/Xm798/bean-sieve/releases/tag/v0.6.0
[0.5.0]: https://github.com/Xm798/bean-sieve/releases/tag/v0.5.0
[0.4.1]: https://github.com/Xm798/bean-sieve/releases/tag/v0.4.1
[0.4.0]: https://github.com/Xm798/bean-sieve/releases/tag/v0.4.0
[0.3.1]: https://github.com/Xm798/bean-sieve/releases/tag/v0.3.1
[0.2.1]: https://github.com/Xm798/bean-sieve/releases/tag/v0.2.1
[0.2.0]: https://github.com/Xm798/bean-sieve/releases/tag/v0.2.0
[0.1.0]: https://github.com/Xm798/bean-sieve/releases/tag/v0.1.0
