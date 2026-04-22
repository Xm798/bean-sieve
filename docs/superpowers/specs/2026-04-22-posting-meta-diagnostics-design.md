# Posting Metadata Diagnostics 设计

**日期**: 2026-04-22
**状态**: Approved, pending implementation

## 背景

支付宝 / 微信的对账单中，`method` 字段形如 `"华夏银行信用卡(3855)"`，描述了实际付款的物理银行卡。对于 "按户管理" 的银行（HXB、SPDB、CMB 等），多张物理卡在 ledger 中共用一个 Liability 账户，账户名本身无法区分具体是哪张卡。

当前问题：

1. 配置了 `providers.alipay.posting_metadata: [card_last4]`，但 `alipay` / `wechat` parser 不填 `txn.card_last4`（仅填 `metadata["method"]`），导致 posting 上从未输出 `card_last4`。
2. 需要对"共享账户"自动输出 `card_last4`，而对"按卡管理"账户（账户名已带卡号）则无需重复。
3. sieve 当前对 `card_last4` 做硬过滤：
   - ledger 中缺 meta：静默匹配（用户无法发现缺失）
   - ledger 中 meta 冲突：拒绝匹配 → 当成 missing → pending.bean 产生重复记账
4. 用户需要一种非侵入式的方式发现历史 ledger 中 meta 缺失或错配的条目，逐步修复。

## 目标

- 支付宝 / 微信自动抽取 `card_last4`
- "按户管理"账户自动输出 posting-level `card_last4`，零配置
- 匹配阶段把 `card_last4` 从硬过滤降级为软校验，产出 lint 风格诊断
- 结果呈现在 pending.bean 的独立诊断段

## 非目标 (YAGNI)

- 不新增独立 `bean-sieve lint-meta` 命令，诊断仅在 reconcile 时产出
- 不对 `card_last4` 以外的 meta 字段做差异检测（保留可扩展结构，但本期仅实现 card_last4）
- 不修改 `order_id` 匹配逻辑（order_id 仍是硬匹配键）
- 不自动修复 ledger 中的历史条目

## 设计

### 1. card_last4 自动抽取（Alipay / WeChat）

在 `providers/payment/alipay.py` 和 `providers/payment/wechat.py` 的 parser 中，对已填好的 `method` 字段用正则 `\((\d{4})\)$` 提取末四位，写入 `txn.card_last4`。

- 仅当 `method` 末尾匹配 `(xxxx)` 且 `xxxx` 为 4 位数字时设置
- 已由其他 provider（银行类）自填 `card_last4` 的不受影响

### 2. 共享账户自动识别

在 `api.py` 新增 `_infer_shared_account_metadata(config) -> set[str]`：

- 输入：`config.account_mappings`
- 按 `mapping.account` 分组
- 返回被 ≥ 2 条 pattern 指向的账户集合（`shared_accounts`）

该集合语义为"需要在 posting 上输出 `card_last4` 以消歧的账户"。对用户现有的 HXB / SPDB / CMB 账户，将自动识别为共享账户。

### 3. posting meta 注入

在 `BeancountWriter._format_postings()` 中：

- 保留现有的显式 `_posting_metadata` 机制（用户可继续显式配置）
- 新增隐式注入：若 `txn.account` 在 `shared_accounts` 中且 `txn.card_last4` 非空，自动在该 posting 下输出 `card_last4: "xxxx"`
- 两种来源的 meta 以 key 去重，显式配置优先

`shared_accounts` 通过 `BeancountWriter` 构造参数注入（由 `api.py` 在创建 writer 时计算并传入）。

### 4. Sieve 软校验

修改 `core/sieve.py` 的 `_is_match()`：

- `card_last4` 不再作为拒绝条件
- 保持 date / amount / payee / order_id 原有匹配逻辑
- 匹配成功后，若 statement txn 有 `card_last4` 而 ledger posting 没有 → 记 `hint`
- 匹配成功后，若两侧 `card_last4` 不同 → 记 `warn`（仍匹配，不进 missing）

诊断产出通过 `MatchResult.meta_diagnostics` 字段传递。

### 5. 数据结构

新增 `core/types.py`：

```python
class MetaDiagnostic(BaseModel):
    severity: Literal["hint", "warn"]  # hint=缺失, warn=冲突
    file: str
    line: int
    account: str
    key: str                   # 本期固定为 "card_last4"
    expected: str              # statement 值
    actual: str | None         # ledger 现值；None 表示缺失
    message: str               # 预渲染的完整消息
```

`MatchResult` 新增：

```python
meta_diagnostics: list[MetaDiagnostic] = Field(default_factory=list)
```

### 6. 输出渲染

`BeancountWriter.format_result()` 在 `Extra entries` 段之后、摘要之前插入诊断段：

```
; ============================================================
; Metadata diagnostics (N)
; ============================================================
; books/2025/q1.bean:1234  hint  missing posting meta `card_last4: "3855"` on Liabilities:Credit:HXB
; books/2025/q2.bean:88    warn  posting meta `card_last4` mismatch on Liabilities:Credit:SPDB: ledger "4192", statement "3855"
```

格式契约：

- 每行一条诊断，严格遵循 `<file>:<line>  <severity>  <message>`
- `severity` 左对齐到 4 字符（`hint` / `warn`），双空格分隔各字段
- `file:line` 使用 ledger entry 的原始路径（相对路径，与 beancount `meta.filename` 保持一致）
- 若 `meta_diagnostics` 为空，不输出该段
- 诊断按 `(file, line, severity)` 升序排序

### 7. 配置

`config/schema.py` 新增：

```python
class DiagnosticsConfig(BaseModel):
    meta_check: bool = True

class Config(BaseModel):
    ...
    diagnostics: DiagnosticsConfig = Field(default_factory=DiagnosticsConfig)
```

当 `diagnostics.meta_check = false`：

- sieve 恢复旧行为（`card_last4` 硬过滤）
- 不产出 `meta_diagnostics`
- 不在输出中渲染诊断段

默认开启，向前兼容（用户无需修改 yaml 即可获得新行为；旧行为可显式回退）。

同步更新 `bean-sieve.schema.json` 与 `bean-sieve.example.yaml`。

## 影响范围

| 文件 | 改动 |
|---|---|
| `providers/payment/alipay.py` | 从 method 抽 card_last4 |
| `providers/payment/wechat.py` | 从 method 抽 card_last4 |
| `core/types.py` | 新增 `MetaDiagnostic`；`MatchResult.meta_diagnostics` |
| `core/sieve.py` | card_last4 软校验；产出诊断 |
| `core/output.py` | 接收 `shared_accounts`；posting meta 隐式注入；诊断段渲染 |
| `api.py` | `_infer_shared_account_metadata()`；向 writer 传参 |
| `config/schema.py` | `DiagnosticsConfig` |
| `bean-sieve.schema.json` | 同步 |
| `bean-sieve.example.yaml` | 同步 |
| 测试 | 新增 card_last4 抽取、shared_accounts 推导、diagnostics 生成三组 |

## 测试要点

1. **card_last4 抽取**
   - Alipay method = `"华夏银行信用卡(3855)"` → `card_last4 == "3855"`
   - Alipay method = `"余额"` → `card_last4 is None`
   - WeChat method = `"零钱"` → `card_last4 is None`

2. **shared_accounts 推导**
   - 两条 pattern 指向同一 account → account 入集合
   - 一条 pattern 对应一 account → 不入集合
   - 空 `account_mappings` → 空集合

3. **posting meta 注入**
   - shared account + card_last4 → posting 下出现 `card_last4: "xxxx"`
   - 非 shared account + card_last4 → 不输出
   - 显式 `posting_metadata` 与隐式规则同时命中同一 key → 不重复

4. **软校验 + 诊断**
   - ledger 无 meta：匹配成功 + 产出 `hint`
   - ledger meta 冲突：匹配成功 + 产出 `warn`（不进 missing）
   - ledger meta 一致：匹配成功 + 无诊断
   - `diagnostics.meta_check = false`：恢复硬过滤行为

5. **输出渲染**
   - 诊断按 `(file, line, severity)` 排序
   - 空诊断不输出段
   - 格式严格匹配契约

## 向前兼容

- 新配置默认开启，用户旧 yaml 无需改动即可获得新行为
- `providers.*.posting_metadata` 显式配置仍然生效，未被移除
- 如用户依赖 `card_last4` 硬过滤行为，可显式 `diagnostics.meta_check: false` 回退
