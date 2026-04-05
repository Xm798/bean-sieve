# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

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

[0.1.0]: https://github.com/Xm798/bean-sieve/releases/tag/v0.1.0
