# Bean-Sieve

Bean-Sieve 是一个基于规则的 [Beancount](https://github.com/beancount/beancount) 账单导入与对账工具。

与传统导入器只做「解析 → 导入」不同，Bean-Sieve 支持将账单与已有账本**智能对账**——自动识别已手动记录的交易，仅生成缺失条目，避免重复。同时也可作为独立的账单解析器，将各类账单统一导出为通用 CSV/XLSX 格式。

## 功能特性

- **解析** 支付宝、微信支付、银行信用卡/借记卡等各类账单
- **对账** 与已有 Beancount 账本比对，自动识别已记录交易，补充未记录交易
- **生成** 仅输出缺失的待录入条目，支持规则映射账户
- **导出** 可作为纯账单解析器，将账单统一导出为 CSV/XLSX
- **预测** 基于 smart-importer 的智能账户分类（开发中）

## 安装

### 作为 CLI 工具安装（推荐）

```bash
# 使用 uv
uv tool install git+https://github.com/Xm798/bean-sieve.git

# 使用 pipx
pipx install git+https://github.com/Xm798/bean-sieve.git

# 从本地目录安装（适合开发）
uv tool install -e .
```

以上命令会将 `bean-sieve` 安装到 `~/.local/bin`（Linux/macOS）或用户 PATH（Windows）。请确保该目录在 PATH 中。

### 开发安装

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

### Shell 自动补全

```bash
# Bash - 添加到 ~/.bashrc
eval "$(bean-sieve completion bash)"

# Zsh - 添加到 ~/.zshrc
eval "$(bean-sieve completion zsh)"

# Fish
bean-sieve completion fish > ~/.config/fish/completions/bean-sieve.fish
```

## 支持的数据源

### 支付平台

| Provider | 名称     | 格式       | 说明                                              |
|----------|----------|------------|----------------------------------------------------|
| `alipay` | 支付宝   | CSV        | 支付宝导出的交易明细                              |
| `app_store` | App Store | HAR     | App Store 购买历史 (reportaproblem.apple.com HAR 导出) |
| `jd`     | 京东支付 | CSV        | 京东交易流水导出文件                              |
| `wechat` | 微信支付 | CSV / XLSX | 微信支付账单流水文件                              |

### 信用卡

| Provider       | 名称           | 格式 | 说明               |
|----------------|----------------|------|--------------------|
| `abc_credit`   | 农业银行信用卡 | EML  | 邮件账单           |
| `boc_credit`   | 中国银行信用卡 | PDF  | PDF 账单           |
| `bocom_credit` | 交通银行信用卡 | EML  | 邮件账单           |
| `bosc_credit`  | 上海银行信用卡 | EML  | 邮件账单           |
| `ccb_credit`   | 建设银行信用卡 | EML  | 邮件账单           |
| `cgb_credit`   | 广发银行信用卡 | EML  | 邮件账单           |
| `cib_credit`   | 兴业银行信用卡 | EML  | 邮件账单           |
| `cncb_credit`  | 中信银行信用卡 | XLS  | 网银导出账单       |
| `cmb_credit`   | 招商银行信用卡 | EML  | 邮件账单           |
| `cmbc_credit`  | 民生银行信用卡 | EML  | 邮件账单           |
| `hxb_credit`   | 华夏银行信用卡 | EML  | 邮件账单           |

### 借记卡

| Provider       | 名称           | 格式      | 说明               |
|----------------|----------------|-----------|--------------------|
| `ccb_debit`    | 建设银行借记卡 | XLS       | XLS 导出账单       |
| `cmb_debit`    | 招商银行借记卡 | CSV       | CSV 导出账单       |
| `icbc_debit`   | 工商银行借记卡 | CSV       | CSV 导出账单       |
| `pab_debit`    | 平安银行借记卡 | XLS/ XLSX | Excel 导出账单     |

更多 Provider 正在开发中。

## 使用方法

### 命令行

```bash
# 对账：解析账单 + 比对账本 + 生成缺失条目
bean-sieve reconcile data/statement/*.csv -l books/ -o pending.bean

# 仅解析：输出标准化交易
bean-sieve parse data/statement/*.csv

# 导出：将解析结果导出为 CSV 或 XLSX
bean-sieve export data/statement/*.csv -f csv -o output/

# 仅检查：显示对账结果，不生成文件
bean-sieve check data/statement/*.csv -l books/

# 提取账单中的支付方式并交互式映射到账本账户
bean-sieve extract-accounts data/statement/*.csv -l books/

# 列出支持的数据源
bean-sieve providers

# 生成 Shell 自动补全脚本
bean-sieve completion bash
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

配置文件搜索顺序：

1. 命令行 `-c/--config` 指定的路径
2. 当前目录 `./bean-sieve.yaml`
3. 系统配置目录：
   - Linux/macOS: `$XDG_CONFIG_HOME/bean-sieve/config.yaml` 或 `~/.config/bean-sieve/config.yaml`
   - Windows: `%APPDATA%\bean-sieve\config.yaml`

复制 `bean-sieve.example.yaml` 为 `bean-sieve.yaml`（当前目录）或 `config.yaml`（系统配置目录）并根据需要修改：

```yaml
# 默认设置
defaults:
  currency: CNY
  expense_account: Expenses:FIXME
  income_account: Income:FIXME
  date_tolerance: 0  # 日期模糊匹配容差（天）

# ML 账户预测
predict:
  enabled: false  # 设为 false 关闭 smart-importer

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

## 账单下载方式

### 借记卡

| 银行 | 下载方式 | 系统要求 | 备注 |
| :--- | :--- | :--- | :--- |
| 招商银行 | [专业版](https://cmbchina.com/pbankwebNew/downloadPage.aspx) PC 客户端 | Windows | 需安装客户端 |
| 建设银行 | [个人网上银行](https://ibsbjstar.ccb.com.cn/CCBIS/V6/STY1/CN/login.jsp) | Windows / macOS | Chrome 即可，无需安全控件 |
| 工商银行 | [个人网上银行](https://mybank.icbc.com.cn/icbc/newperbank/perbank3/frame/frame_index.jsp) | Windows / macOS | 需安装安全控件，macOS 需用 Safari |
| 平安银行 | [个人网上银行](https://bank.pingan.com.cn/m/main/index.html) | Windows / macOS | 扫码登录无需安全控件 |

### 信用卡

信用卡账单通常通过邮件获取，在发卡行官网或 App 设置账单邮箱即可。部分银行支持网银导出：

| 银行 | 下载方式 | 备注 |
| :--- | :--- | :--- |
| 中信银行 | [信用卡网银](https://e.creditcard.ecitic.com/citiccard/ebank-ocp/ebankpc/bill.html) | 登录后导出已出账单明细 XLS |

### 其他

| 平台 | 下载方式 | 备注 |
| :--- | :--- | :--- |
| Apple | [Report a Problem](https://reportaproblem.apple.com) | 登录后 F12 打开控制台，滚动加载完后导出 search 请求为 HAR |

## 信用卡账单管理方式

| 管理方式 | 银行 | 特点 |
| :--- | :--- | :--- |
| **按户管理** | 招商银行、民生银行、华夏银行、平安银行、浦发银行、北京银行、上海银行 | 信报、还款、积分按户**合并管理** |
| **按卡管理** | 广发银行、建设银行 | 独立信报，**账单日合并**，**独立还款**。 |
| **按卡管理** | 中信银行、光大银行、交通银行、农业银行、工商银行、兴业银行、中国银行、邮政储蓄 | 独立信报，独立账单，独立还款。 |

## Provider 特定功能

### 农业银行信用卡 (abc_credit)

农业银行账单会显示刷卡金抵扣金额，但不会在交易明细中体现。Bean-Sieve 支持：

1. **自动核对**：比较解析的消费金额与账单应还金额，判断是否平账
2. **自动生成刷卡金条目**：平账时自动生成刷卡金收入记录

#### 配置

```yaml
providers:
  abc_credit:
    accounts:
      "1234": Liabilities:Credit:ABC:U-示例卡1234
    # 刷卡金收入账户（生成条目时使用）
    rebate_income_account: Income:Rebate:ABC
    # 关键词：用于识别账本中已存在的刷卡金记录
    rebate_keywords:
      - 刷卡金
      - 返现
```

#### 去重检测逻辑

配置 `rebate_income_account` 或 `rebate_keywords` 后，对账时会在账本的 Extra 条目中查找：

- 日期在账单周期内
- 账户包含卡号后四位
- 金额精确匹配
- 描述包含关键词 **或** posting 使用了指定的收入账户

如果找到匹配记录，显示 `✅ 平账 (刷卡金 X.XX 已记录)`；否则自动生成刷卡金条目。

#### 输出示例

```beancount
; --- 刷卡金抵扣 ---
2025-11-27 * "农业银行" "刷卡金抵扣 (尾号1234)"
  Liabilities:Credit:ABC:U-示例卡1234  1.33 CNY
  Income:Rebate:ABC

; ============================================================
; 农业银行信用卡 账单核对
; ============================================================
;
; 卡号: 620000******1234 (尾号 1234)
; 账单周期: 2025/10/28-2025/11/27
;
;   解析消费:           32036.00 CNY
;   账单应还:           32034.67 CNY
;   刷卡金抵扣:             1.33 CNY
;
;   状态: ✅ 平账 (刷卡金 1.33)
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
│   │   └── predictor.py       # ML 账户预测
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

## License

MIT
