

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

元数据输出、余额断言和卡号诊断的配置见文档站的[元数据与诊断](https://xm798.github.io/bean-sieve/docs/metadata/)和[余额断言](https://xm798.github.io/bean-sieve/docs/balance/)。

## 开发

```bash
# 安装开发依赖
uv sync --extra dev

# 运行测试
uv run pytest

# 代码检查
uv run ruff check src/ tests/ scripts/

# 格式化
uv run ruff format src/ tests/ scripts/
```

## Community

[LINUX DO](https://linux.do/) - 社区讨论与交流

## License

MIT
