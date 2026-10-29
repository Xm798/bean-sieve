

# Bean-Sieve

Bean-Sieve 是一个基于规则的 [Beancount](https://github.com/beancount/beancount) 账单导入与对账工具。

与传统导入器只做「解析 → 导入」不同，Bean-Sieve 支持将账单与已有账本**智能对账**——自动识别已手动记录的交易，仅生成缺失条目，避免重复。同时也可作为独立的账单解析器，将各类账单统一导出为通用 CSV/XLSX 格式。

## 功能特性

- **解析** 支付宝、微信支付、银行信用卡/借记卡等各类账单
- **对账** 与已有 Beancount 账本比对，自动识别已记录交易，补充未记录交易
- **生成** 仅输出缺失的待录入条目，支持规则映射账户
- **导出** 可作为纯账单解析器，将账单统一导出为 CSV/XLSX

## 安装与使用

```bash
uv tool install beancount-sieve   # 或 pipx install beancount-sieve
bean-sieve reconcile data/statement/*.csv -l books/ -o pending.bean
```

安装方式、命令行与 Python API 用法、配置文件写法见文档站：<https://xm798.github.io/bean-sieve/>

## 支持的数据源

支付平台支持支付宝、微信支付、京东支付、美团和 App Store，银行账单支持国内和香港多家银行的信用卡与借记卡。完整列表，以及每种账单的下载方式、配置写法、元数据字段和示例，见文档站的[数据源页面](https://xm798.github.io/bean-sieve/docs/providers/)。

## 元数据输出

每个 Provider 解析账单时，除了标准字段（日期、金额、描述、收款方等）外，还会将账单中的其他列存入 `metadata`，各 Provider 的字段见[数据源页面](https://xm798.github.io/bean-sieve/docs/providers/)。可通过 `output_metadata` 配置控制哪些字段输出到 Beancount 文件。

全局配置（所有 Provider 生效）：

```yaml config
defaults:
  output_metadata:
    - time
```

Provider 级别配置（仅该 Provider 生效，与全局合并）：

```yaml config
providers:
  cib_debit:
    output_metadata:
      - counterparty_bank
      - counterparty_account
```

`output_metadata` 写在交易头下，作用于整笔交易；若要把字段写在 posting 行下（如 `card_last4` 用于区分同一账户下的多张物理卡），使用 `posting_metadata`：

```yaml config
providers:
  hxb_credit:
    posting_metadata:
      - card_last4
```

对于"按户管理"的账户（同一账户持有多张物理卡，如 HXB/SPDB/CMB），通过 `diagnostics.meta_check_accounts` 纳入检查后 `card_last4` 会被自动注入 posting，并在 ledger 缺失/冲突时产生诊断。详见下文 [Posting 元数据与诊断](#posting-元数据与诊断)。

## 借记卡余额断言 (Balance Directive)

对账后可自动生成 Beancount `balance` 指令，用于校验账户余额。取账单中最后一笔交易的余额，日期为账单结束日次日。

按 Provider 级别控制，默认关闭：

```yaml config
providers:
  pab_debit:
    accounts:
      "6666": Assets:Bank:PAB:6666
    balance: true
```

输出示例：

```beancount
2026-04-01 balance Assets:Bank:PAB:6666  12345.67 CNY
```

## Posting 元数据与诊断

支付宝/微信等支付平台的账单中，`method` 字段（如 `华夏银行信用卡(1234)`）指向实际刷卡的物理银行卡。对于"按户管理"的银行（HXB、SPDB、CMB 等），同一个 Liability 账户名下会挂多张物理卡，账户名本身无法区分用的哪张卡——此时需要把 `card_last4` 写在 posting 下面来消歧。

通过 `diagnostics.meta_check_accounts`（子串匹配）列出需要检查的账户：

```yaml config
diagnostics:
  meta_check: true
  meta_check_accounts:
    - Liabilities:Credit:HXB
    - Liabilities:Credit:SPDB
    - Liabilities:Credit:CMB
```

列在这里的账户会同时启用两件事：

1. **Writer 自动注入** —— 输出新交易时在这些账户的 posting 下加 `card_last4`：

```beancount
2025-03-15 ! "瑞幸咖啡" "拿铁"
  time: "10:23:00"
  Liabilities:Credit:HXB  -28.00 CNY
    card_last4: "1234"
  Expenses:Food:Coffee  28.00 CNY
```

2. **Metadata 诊断** —— 对账时 `card_last4` 软校验，匹配照常成立，ledger 中缺失或冲突的以 lint 风格输出在 pending.bean 末尾：

```
; ============================================================
; Metadata diagnostics (2)
; ============================================================
; books/2025/q1.bean:1234  hint  missing posting meta `card_last4: "1234"` on Liabilities:Credit:HXB
; books/2025/q2.bean:88    warn  posting meta `card_last4` mismatch on Liabilities:Credit:SPDB: ledger "5678", statement "1234"
```

- **hint** — ledger 中该交易没有 `card_last4`，可照提示补上
- **warn** — ledger 中 `card_last4` 与账单冲突，提示核对（交易本身仍被认为匹配，不会重复生成）

账户名已带卡号的（如 `Assets:Bank:ICBC:9999`、`Liabilities:Credit:BOCOM:U-东航金0001`）没必要纳入——不列就不做检查，也不产生诊断。

如需恢复旧的硬过滤行为（冲突即视为不同交易、进 missing），显式关闭：

```yaml config
diagnostics:
  meta_check: false
```

## 数据格式约定

### Transaction 字段

| 字段          | 类型    | 说明                       |
|---------------|---------|----------------------------|
| `date`        | date    | 交易日期                   |
| `time`        | time    | 交易时间（如有）           |
| `amount`      | Decimal | 金额（支出为正，收入为负） |
| `currency`    | str     | 币种                       |
| `description` | str     | 原始描述                   |
| `payee`       | str     | 交易对方                   |
| `card_last4`  | str     | 银行卡末四位（银行卡 Provider 直接提取；Alipay/WeChat 从 `method` 尾部 `(XXXX)` 中提取） |
| `order_id`    | str     | 订单号/流水号              |
| `provider`    | str     | 数据源标识                 |
| `metadata`    | dict    | 扩展元数据                 |

## 开发

```bash
# 安装开发依赖
uv sync --extra dev

# 运行测试
uv run pytest

# 代码检查
uv run ruff check src/ tests/

# 格式化
uv run ruff format src/ tests/
```

## 项目结构

```
bean-sieve/
├── bean-sieve.example.yaml    # 配置示例
├── bean-sieve.schema.json     # 配置 JSON Schema
├── src/bean_sieve/
│   ├── api.py                 # API 层
│   ├── cli.py                 # CLI 入口
│   ├── core/
│   │   ├── types.py           # 数据类型定义
│   │   ├── sieve.py           # 去重/匹配引擎
│   │   ├── rules.py           # 规则匹配引擎
│   │   ├── preset_rules.py    # 内置预设规则
│   │   ├── output.py          # Beancount 输出生成
│   │   ├── export.py          # CSV/XLSX 导出
│   │   └── suggest.py         # 规则自动生成
│   ├── providers/
│   │   ├── base.py            # Provider 基类
│   │   ├── payment/           # 支付平台（支付宝、微信、京东）
│   │   └── banks/
│   │       ├── credit/        # 信用卡（农行、中行、交行等）
│   │       └── debit/         # 借记卡（建行、工行、平安）
│   └── config/
│       ├── schema.py          # 配置 Schema
│       └── wizard.py          # 账户映射向导
└── tests/
```

## Community

[LINUX DO](https://linux.do/) - 社区讨论与交流

## License

MIT
