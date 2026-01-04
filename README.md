# Bean-Sieve

> 基于规则的复式记账账单导入与对账工具

Bean-Sieve 是一个 Python 工具，用于将各类账单文件解析并导入到 [Beancount](https://github.com/beancount/beancount) 账本中。

## 功能特性

- **解析** 各类账单文件（支付宝、微信支付、银行账单等）
- **筛选** 去重，与已有 Beancount 账本比对
- **生成** 待录入的 Beancount 条目，支持规则映射
- **预测** 智能分类，基于 smart-importer 实现

## 安装

```bash
# 本地开发
uv sync

# 包含开发依赖
uv sync --extra dev
```

### 在其他项目中引入

```bash
# 从 Git 安装
uv add git+https://github.com/Xm798/bean-sieve.git

# 从本地路径安装
uv add --editable ../bean-sieve
```

## 支持的数据源

| Provider | 名称     | 格式       | 说明                   |
|----------|----------|------------|------------------------|
| `alipay` | 支付宝   | CSV (GBK)  | 支付宝导出的交易明细   |
| `wechat` | 微信支付 | XLSX / CSV | 微信支付账单流水文件   |

更多 Provider 正在开发中（银行信用卡账单、加密货币等）。

## 使用方法

### 命令行

```bash
# 对账：解析账单 + 比对账本 + 生成缺失条目
bean-sieve reconcile data/statement/*.csv -l books/ -o pending.bean

# 仅解析：输出标准化交易
bean-sieve parse data/statement/*.csv

# 仅检查：显示对账结果，不生成文件
bean-sieve check data/statement/*.csv -l books/

# 列出支持的数据源
bean-sieve providers
```

### Python API

```python
from pathlib import Path
from bean_sieve.providers import get_provider, list_providers

# 查看所有可用的 Provider
for p in list_providers():
    print(f"{p['id']}: {p['name']} ({p['formats']})")

# 解析支付宝账单
alipay = get_provider("alipay")
transactions = alipay.parse(Path("data/statement/支付宝交易明细.csv"))

for txn in transactions[:5]:
    print(f"{txn.date} | {txn.payee:20s} | {txn.amount:>10} CNY | {txn.description}")

# 解析微信账单
wechat = get_provider("wechat")
transactions = wechat.parse(Path("data/statement/微信支付账单.xlsx"))
```

## 配置

复制 `bean-sieve.example.yaml` 为 `bean-sieve.yaml` 并根据需要修改：

```yaml
# 默认设置
defaults:
  currency: CNY
  expense_account: Expenses:FIXME
  income_account: Income:FIXME
  date_tolerance: 2  # 日期模糊匹配容差（天）

# ML 账户预测（默认启用）
predict:
  enabled: true  # 设为 false 关闭 smart-importer

# 账户映射（按 provider）
accounts:
  alipay:
    default: Assets:Current:Alipay

  wechat:
    default: Assets:Current:Wechat

# 规则匹配（优先级最高，按顺序匹配）
rules:
  # 正则匹配 description
  - description: ".*瑞幸.*"
    payee: 瑞幸咖啡
    contra_account: Expenses:Food:Coffee

  - description: ".*美团.*外卖.*"
    payee: 美团外卖
    contra_account: Expenses:Food:Delivery

  # 多关键词匹配（OR 逻辑）
  - description: "(滴滴|高德|T3出行)"
    payee: 打车
    contra_account: Expenses:Transport:Taxi

  # 时间范围
  - description: ".*食堂.*"
    time: "11:00-14:00"
    contra_account: Expenses:Food:Lunch

  - description: ".*食堂.*"
    time: "17:00-20:00"
    contra_account: Expenses:Food:Dinner

  # 忽略某些交易
  - description: ".*还款.*"
    ignore: true
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
| `order_id`    | str     | 订单号/流水号              |
| `provider`    | str     | 数据源标识                 |
| `metadata`    | dict    | 扩展元数据                 |

### Metadata 字段

不同 Provider 会提取不同的 metadata：

**Alipay:**

- `category`: 交易分类
- `peer_account`: 对方账号
- `method`: 收/付款方式
- `status`: 交易状态
- `merchant_id`: 商家订单号

**Wechat:**

- `tx_type`: 交易类型（商户消费、转账、红包等）
- `method`: 支付方式
- `status`: 当前状态
- `commission`: 服务费（如有）

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
├── bean-sieve.example.yaml   # 配置示例
├── src/bean_sieve/
│   ├── api.py                 # API 层
│   ├── cli.py                 # CLI 入口
│   ├── core/
│   │   ├── types.py           # 数据类型定义
│   │   ├── sieve.py           # 去重/匹配引擎
│   │   ├── rules.py           # 规则匹配引擎
│   │   └── output.py          # Beancount 输出生成
│   ├── providers/
│   │   ├── base.py            # Provider 基类
│   │   └── payment/
│   │       ├── alipay.py      # 支付宝
│   │       └── wechat.py      # 微信支付
│   └── config/
│       └── schema.py          # 配置 Schema
└── tests/
    └── providers/
        ├── test_alipay.py
        └── test_wechat.py
```

## License

MIT
