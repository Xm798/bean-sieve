# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### 其他

- Release workflow 升级到 `softprops/action-gh-release@v3`（runtime 由 Node 20 换为 Node 24，输入参数不变）

## [0.7.0] - 2026-09-04

### 新增

- **规则**：`target_payee: ""` 现在会清除交易的 payee（原值记入 `original_payee`）。此前空串被视为未设置，无法通过规则去掉 Provider 解析出的 payee

### 修复

- **输出**：没有 payee 的交易头改写为 `日期 标志 "narration"`，不再输出占位的 `""`，与 Beancount 惯例一致。生成的 .bean 文本会因此变化，语义不变

- **输出**：以 `_` 开头的元数据键（`_rebate_account`、`_withdrawal_target` 等）在 transaction- 与 posting-level 均不再写入生成的 .bean。这些键仅供 bean-sieve 内部使用，而 Beancount 要求元数据键以小写字母开头，此前未设置 `defaults.output_metadata` 或 `providers.<id>.posting_metadata` 时它们会原样输出，使生成的账本无法通过 bean-check
- **混合账单对账**：`post_output` 钩子改为对本次运行涉及的每个 Provider 各调用一次（按账单文件首次出现的顺序）。此前只有第一个账单文件所属的 Provider 会执行该钩子，因此同时导入多家银行账单时，其余 Provider 的结算/汇总分录（如农行的账单核对与返现分录）会丢失，且换一个文件顺序结果就不同；单 Provider 运行的输出保持不变
- **账单周期**：账单覆盖周期改为按卡计算。表头印出的周期仍是各卡的基准，但某张卡有落在周期外的交易（延迟入账的境外消费等）时，只把该卡的周期撑到那一天，其余卡保持表头周期；表头没有周期时，每张卡以自己交易日期的首尾为周期。涉及农行、上海银行、建行、广发、兴业、招商、民生、华夏、交行信用卡。邮储信用卡此前只要有一笔交易落在周期外就整份账单不给周期（该账单覆盖的账本分录一律不再判 Extra），现在同样改为撑开。周期决定哪些账本分录参与 Extra 判定，升级后 Extra 集合可能变化
- **覆盖账户**：Extra 判定改为按账户归属周期。按卡号后四位能归属到账户的行，只把自己那张卡的周期记到该账户名下；整户账单中账单从未提及的账户，仍按整份账单各周期的并集覆盖，卡号无法归属的行也覆盖全部已配置账户；按卡账单两者都不认领。同一账户配置了多张卡时，两张卡的周期合并保留（此前后一张卡的周期会覆盖前一张）。Extra 集合可能因此变化
- **华夏/交行信用卡**：只有 MM/DD 的交易行改为按账单周期推断年份，取不晚于周期截止日的最近一个 MM/DD——1 月账单上的 12 月行属于上一年，同一张账单上更早的 11 月行是再上一年延迟入账的结算。华夏此前优先用文件名中的年份，交行此前只处理跨年账单，同年周期内晚于截止日的行（如 2025/03/14-2025/04/13 周期上的 04/20）会被记成周期当年，现在记为上一年。交易日期会因此变化，匹配与 pending 分录随之变化

### 其他

- **Provider 钩子**：`ReconcileContext` 新增 `parsed` 字段，按账单文件保存 Provider 的原始解析结果（早于日期过滤、钩子与规则）。农行信用卡的账单核对据此复用解析结果，不再为每个 .eml 重复解析一次；核对口径仍是整份账单，输出不变
- **Provider 钩子**：补充 `post_output` 与 balance 指令的字节级 golden 测试；balance 指令的单行格式移入 `BeancountWriter.format_balance`，排序与空行框架仍在 `api.py`
- **账户映射**：`account_mappings` 的解析逻辑集中到 `core/accounts.py`。此前 `api.py` 与 `rules.py` 中有六处内联循环，各自实现四种不同的匹配语义（pattern 子串匹配区分/不区分大小写、preset keyword 子串匹配、preset keyword 正则匹配），改动一处无法看见其余几处。现在四种语义各为一个具名函数，调用方按名选择；行为不变

## [0.6.0] - 2026-07-29

### 新增

- **浦发信用卡**：新增浦发银行信用卡（`spdb_credit`）Provider，解析网银导出的 XLS 交易明细，支持多卡后四位、交易日与记账日、原始交易金额与币种元数据，并根据交易日期推断账单周期

## [0.5.0] - 2026-07-27

### 新增

- **歧义匹配诊断**：当一笔流水交易匹配到账本分录，但其他账本交易中存在同样合法的分录（日期/金额/符号一致）时，输出 `MatchDiagnostic` 提示歧义。新增 `diagnostics.ambiguous_match` 配置开关（默认开启），歧义数量计入 CLI 摘要，并在生成的 .bean 输出中渲染独立的「Ambiguous matches」区块（不受 `check_scope` 限制）；关闭该检查时 `_find_match` 仍在首个匹配处短路，不影响匹配性能
- **美团**：新增美团（`meituan`）支付平台 Provider，解析美团 CSV 账单（UTF-8 BOM、20 行表头）。以实付金额作为交易金额，存在优惠时将订单原价记入 `order_amount` 元数据；从订单标题首个 `-` 前缀提取商户作为 payee、`-` 之后作为描述（不重复商户名）；退款交易打 `#refund` 标签；支持纯日期格式的账单周期
- **汇丰香港**：新增汇丰香港（`hsbchk`）信用卡与借记卡 Provider
- **中信银行国际**：新增中信银行国际（`cncbi`）借记卡 Provider
- **汇立银行**：新增汇立银行（`welab_debit`）借记卡 Provider，解析 WeLab App 下载的多币种综合电子月结单 PDF。按币种代码映射账户，依坐标重建交易表（币种段可跨页，续页无段头时按上一页币种结转），借记/贷记符号转换为 bean-sieve 约定；跨币种兑换的两腿（卖出币种借记 + 买入币种贷记，共享 `Ref: FX…`）各自保留为独立交易，以便与 ledger 中同账户两腿的 `@@` 兑换记法逐腿匹配；退款交易打 `#refund` 标签并以 `^<订单号>` 关联；外币消费的实际交易日与换汇 `FX Ref` 记入元数据；交易种类/换汇详情/收款方亦记入元数据

### 修复

- **账户映射**：`account_mappings` 改为单向子串匹配（配置 `pattern` 包含于交易 `method`）。此前的双向匹配会让泛化的支付渠道（如美团 `云闪付`）误命中更具体的配置 pattern（如 `云闪付-交通银行(1234)`）而错误归属到具体卡，并连带挡住与 ledger 的正常匹配；现在无绑卡信息的泛化渠道会保留为 `Assets:FIXME`
- **支付宝**：剥离新格式（2026 起）退款订单号中的 `*REFUND` 标记，使退款记录能正确关联原始订单
- **支付宝**：`交易关闭`/`已关闭` 的交易仅在存在配对退款（`退款成功`）时保留，否则一律过滤，不再只过滤 `不计收支`。修复闲鱼等场景下买家取消订单产生的 `收入+交易关闭` 记录被当作幻象收入生成 pending 分录的问题
- **支付宝**：新增 `花呗还款` 预设规则，将 `信用借还` 类的花呗还款映射到花呗账户并翻转符号，使其匹配账本还款分录的花呗腿（银行卡腿交由对应银行流水匹配）。修复花呗还款时花呗腿悬空被误报为 Extra 的问题
- **农行信用卡**：修复两类因「卡号后四位」单元格解析失败而整行被丢弃的问题——附属卡行的卡号带主/附标识后缀（如 `1234附`），以及 `约定还款` 等账户级行该单元格为空。前者截取卡号前 4 位，后者回退到账单自身的卡号（农行账单为单卡账单）；同时三条失败路径（卡号无法识别、单元格数异常、行解析失败）改为告警而非静默丢弃，避免附属卡消费未解析导致对账金额不平、还款记录缺失被误报为 Extra
- **中行信用卡**：交易行改为按「交易日期」列锚定分组。此前 `_group_by_row` 用固定 20pt y 容差聚类文本块，同一天多笔间距很近的交易（如相隔约 16pt 的两笔连续还款）会被并成一行、后一笔金额覆盖前一笔，导致丢失交易并在对账时产生假 Extra。现在每个首行为日期的文本块各自作为行锚点，其余片段（如折行的描述）按 y 就近归属；无日期锚点时回退到原有的邻近分组
- **兴业信用卡**：交易日期可能带 `HH:MM` 时间后缀（如充电桩类交易），`date.fromisoformat()` 解析失败导致该行被静默丢弃。交易日与记账日均只取日期部分
- **CLI**：`extract-accounts` 交互选择改用 fzf 真实快捷键——Enter 选定账户、Esc 跳过当前支付方式、Ctrl-Q（或 Ctrl-C）退出并保留已选映射；快捷键在 fzf header 常驻显示，跳过操作更易发现

### 其他

- **Schema**：从 `field_mapping` enum 中移除 `transaction_status`
- **README**：更新交通银行网银入口链接

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

[0.7.0]: https://github.com/Xm798/bean-sieve/releases/tag/v0.7.0
[0.6.0]: https://github.com/Xm798/bean-sieve/releases/tag/v0.6.0
[0.5.0]: https://github.com/Xm798/bean-sieve/releases/tag/v0.5.0
[0.4.1]: https://github.com/Xm798/bean-sieve/releases/tag/v0.4.1
[0.4.0]: https://github.com/Xm798/bean-sieve/releases/tag/v0.4.0
[0.3.1]: https://github.com/Xm798/bean-sieve/releases/tag/v0.3.1
[0.2.1]: https://github.com/Xm798/bean-sieve/releases/tag/v0.2.1
[0.2.0]: https://github.com/Xm798/bean-sieve/releases/tag/v0.2.0
[0.1.0]: https://github.com/Xm798/bean-sieve/releases/tag/v0.1.0
