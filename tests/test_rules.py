"""Tests for rules engine."""

from datetime import date, time
from decimal import Decimal

import pytest

from bean_sieve.api import _set_target_accounts
from bean_sieve.config.schema import (
    AccountMapping,
    Config,
    Rule,
    RuleAction,
    RuleCondition,
)
from bean_sieve.core.metadata_keys import MATCHED_PRESET_RULE
from bean_sieve.core.preset_rules import (
    PresetRule,
    PresetRuleAction,
    PresetRuleCondition,
)
from bean_sieve.core.rules import RulesEngine, apply_rules
from bean_sieve.core.types import MatchSource, Transaction
from bean_sieve.providers.payment.alipay import AlipayProvider
from bean_sieve.providers.payment.wechat import WechatProvider


class TestRulesEngine:
    """Tests for RulesEngine."""

    def test_apply_account_mapping_by_method(self, sample_config):
        """Test account mapping by payment method."""
        engine = RulesEngine(sample_config)
        txn = Transaction(
            date=date(2025, 1, 4),
            amount=Decimal("99.00"),
            currency="CNY",
            description="Some transaction",
            provider="alipay",
            metadata={"method": "余额"},
        )
        result = engine.apply(txn)
        assert result.account == "Assets:Current:Alipay"

    def test_apply_account_mapping_contains_match(self, sample_config):
        """Test account mapping with contains match."""
        engine = RulesEngine(sample_config)
        txn = Transaction(
            date=date(2025, 1, 4),
            amount=Decimal("99.00"),
            currency="CNY",
            description="Some transaction",
            provider="alipay",
            metadata={"method": "华夏银行信用卡(1234)"},
        )
        result = engine.apply(txn)
        assert result.account == "Liabilities:CreditCard:HXB"

    def test_no_account_mapping_when_no_method(self, sample_config):
        """Test no account mapped when method is missing."""
        engine = RulesEngine(sample_config)
        txn = Transaction(
            date=date(2025, 1, 4),
            amount=Decimal("99.00"),
            currency="CNY",
            description="Some transaction",
            provider="unknown",
        )
        result = engine.apply(txn)
        assert result.account is None

    def test_apply_rule_regex_match(self, sample_config):
        """Test rule matching by description regex."""
        engine = RulesEngine(sample_config)
        txn = Transaction(
            date=date(2025, 1, 4),
            amount=Decimal("9.90"),
            currency="CNY",
            description="支付宝-瑞幸咖啡（中国）有限公司",
            provider="hxb_credit",
        )
        result = engine.apply(txn)
        assert result.contra_account == "Expenses:Food:Coffee"
        assert result.payee == "瑞幸咖啡"
        assert result.match_source == MatchSource.RULE
        assert result.confidence == 1.0

    def test_apply_rule_payee_regex_condition(self):
        """Test rule matching by payee regex condition."""
        config = Config(
            rules=[
                Rule(
                    condition=RuleCondition(payee=".*公司.*"),
                    action=RuleAction(contra_account="Income:Salary"),
                    priority=100,
                ),
            ]
        )
        engine = RulesEngine(config)
        txn = Transaction(
            date=date(2025, 1, 4),
            amount=Decimal("-10000.00"),
            currency="CNY",
            description="工资",
            payee="北京某某科技有限公司",
            provider="bank",
        )
        result = engine.apply(txn)
        assert result.contra_account == "Income:Salary"
        assert result.match_source == MatchSource.RULE

    def test_empty_target_payee_clears_payee(self):
        """`target_payee: ""` removes the payee and records the original."""
        config = Config(
            rules=[
                Rule(
                    condition=RuleCondition(description="desc-a"),
                    action=RuleAction(contra_account="Expenses:Test", payee=""),
                ),
            ]
        )
        txn = Transaction(
            date=date(2030, 1, 2),
            amount=Decimal("10.00"),
            currency="CNY",
            description="desc-a",
            payee="payee-a",
            provider="prov-a",
        )
        result = RulesEngine(config).apply(txn)
        assert result.payee is None
        assert result.metadata["original_payee"] == "payee-a"

    def test_unset_target_payee_keeps_payee(self):
        """A rule without target_payee leaves the payee untouched."""
        config = Config(
            rules=[
                Rule(
                    condition=RuleCondition(description="desc-a"),
                    action=RuleAction(contra_account="Expenses:Test"),
                ),
            ]
        )
        txn = Transaction(
            date=date(2030, 1, 2),
            amount=Decimal("10.00"),
            currency="CNY",
            description="desc-a",
            payee="payee-a",
            provider="prov-a",
        )
        result = RulesEngine(config).apply(txn)
        assert result.payee == "payee-a"
        assert "original_payee" not in result.metadata

    def test_rule_priority(self):
        """Test that higher priority rules are applied first."""
        config = Config(
            rules=[
                Rule(
                    condition=RuleCondition(description=".*咖啡.*"),
                    action=RuleAction(contra_account="Expenses:Drinks"),
                    priority=50,
                ),
                Rule(
                    condition=RuleCondition(description=".*瑞幸.*"),
                    action=RuleAction(contra_account="Expenses:Food:Coffee"),
                    priority=100,  # Higher priority
                ),
            ]
        )
        engine = RulesEngine(config)
        txn = Transaction(
            date=date(2025, 1, 4),
            amount=Decimal("9.90"),
            currency="CNY",
            description="瑞幸咖啡",
            provider="test",
        )
        result = engine.apply(txn)
        # Higher priority rule should win
        assert result.contra_account == "Expenses:Food:Coffee"

    def test_ignore_rule(self):
        """Test that ignore rule removes transaction."""
        config = Config(
            rules=[
                Rule(
                    condition=RuleCondition(description=".*还款.*"),
                    action=RuleAction(ignore=True),
                    priority=100,
                ),
            ]
        )
        txn = Transaction(
            date=date(2025, 1, 4),
            amount=Decimal("-1000.00"),
            currency="CNY",
            description="信用卡还款",
            provider="test",
        )
        result = apply_rules([txn], config)
        assert len(result) == 0  # Transaction should be filtered out

    def test_time_range_condition(self):
        """Test time range matching."""
        config = Config(
            rules=[
                Rule(
                    condition=RuleCondition(
                        description=".*美团.*",
                        time_range="11:00-14:00",
                    ),
                    action=RuleAction(contra_account="Expenses:Food:Lunch"),
                    priority=100,
                ),
            ]
        )
        engine = RulesEngine(config)

        # Transaction within time range
        txn_lunch = Transaction(
            date=date(2025, 1, 4),
            time=time(12, 30, 0),
            amount=Decimal("25.00"),
            currency="CNY",
            description="美团外卖",
            provider="test",
        )
        result = engine.apply(txn_lunch)
        assert result.contra_account == "Expenses:Food:Lunch"

        # Transaction outside time range
        txn_dinner = Transaction(
            date=date(2025, 1, 4),
            time=time(18, 30, 0),
            amount=Decimal("35.00"),
            currency="CNY",
            description="美团外卖",
            provider="test",
        )
        result = engine.apply(txn_dinner)
        assert result.contra_account is None  # Rule should not match

    def test_amount_range_condition(self):
        """Test amount range matching."""
        config = Config(
            rules=[
                Rule(
                    condition=RuleCondition(
                        description=".*转账.*",
                        min_amount=10000,
                    ),
                    action=RuleAction(
                        contra_account="Assets:Transfer",
                        tags=["large-transfer"],
                    ),
                    priority=100,
                ),
            ]
        )
        engine = RulesEngine(config)

        # Large transfer
        txn_large = Transaction(
            date=date(2025, 1, 4),
            amount=Decimal("50000.00"),
            currency="CNY",
            description="银行转账",
            provider="test",
        )
        result = engine.apply(txn_large)
        assert result.contra_account == "Assets:Transfer"
        assert "large-transfer" in result.tags

        # Small transfer
        txn_small = Transaction(
            date=date(2025, 1, 4),
            amount=Decimal("100.00"),
            currency="CNY",
            description="银行转账",
            provider="test",
        )
        result = engine.apply(txn_small)
        assert result.contra_account is None

    def test_direction_expense_condition(self):
        """Test direction condition matches only expense transactions."""
        config = Config(
            rules=[
                Rule(
                    condition=RuleCondition(
                        description=".*红包.*",
                        direction="expense",
                    ),
                    action=RuleAction(contra_account="Expenses:Social:RedEnvelope"),
                    priority=100,
                ),
                Rule(
                    condition=RuleCondition(
                        description=".*红包.*",
                        direction="income",
                    ),
                    action=RuleAction(contra_account="Income:Social:RedEnvelope"),
                    priority=90,
                ),
            ]
        )
        engine = RulesEngine(config)

        # Expense (positive amount) — should match expense rule
        txn_send = Transaction(
            date=date(2025, 1, 4),
            amount=Decimal("8.88"),
            currency="CNY",
            description="微信红包（群红包）",
            provider="wechat",
        )
        result = engine.apply(txn_send)
        assert result.contra_account == "Expenses:Social:RedEnvelope"

        # Income (negative amount) — should match income rule
        txn_recv = Transaction(
            date=date(2025, 1, 4),
            amount=Decimal("-1.91"),
            currency="CNY",
            description="微信红包",
            provider="wechat",
        )
        result = engine.apply(txn_recv)
        assert result.contra_account == "Income:Social:RedEnvelope"

    def test_direction_without_other_conditions(self):
        """Test direction condition works as sole condition."""
        config = Config(
            rules=[
                Rule(
                    condition=RuleCondition(direction="expense"),
                    action=RuleAction(contra_account="Expenses:FIXME"),
                    priority=50,
                ),
                Rule(
                    condition=RuleCondition(direction="income"),
                    action=RuleAction(contra_account="Income:FIXME"),
                    priority=40,
                ),
            ]
        )
        engine = RulesEngine(config)

        txn_expense = Transaction(
            date=date(2025, 1, 4),
            amount=Decimal("100.00"),
            currency="CNY",
            description="some expense",
            provider="test",
        )
        assert engine.apply(txn_expense).contra_account == "Expenses:FIXME"

        txn_income = Transaction(
            date=date(2025, 1, 4),
            amount=Decimal("-50.00"),
            currency="CNY",
            description="some income",
            provider="test",
        )
        assert engine.apply(txn_income).contra_account == "Income:FIXME"

    def test_flag_override(self):
        """Test flag override in rule action."""
        config = Config(
            rules=[
                Rule(
                    condition=RuleCondition(description=".*云闪付.*"),
                    action=RuleAction(
                        contra_account="Expenses:FIXME",
                        flag="!",
                    ),
                    priority=100,
                ),
            ]
        )
        engine = RulesEngine(config)
        txn = Transaction(
            date=date(2025, 1, 4),
            amount=Decimal("88.00"),
            currency="CNY",
            description="云闪付消费",
            provider="test",
        )
        result = engine.apply(txn)
        assert result.flag == "!"


class TestPresetRules:
    """Tests for preset rules in RulesEngine."""

    def test_contra_account_metadata_key_resolves(self):
        """Test that contra_account_metadata_key resolves via account_mappings."""
        config = Config(
            account_mappings=[
                AccountMapping(pattern="零钱", account="Assets:Wallet:WeChat:Balance"),
                AccountMapping(pattern="测试银行", account="Assets:Bank:Test:Savings"),
            ],
        )
        preset = PresetRule(
            rule_id="test_withdraw",
            name="零钱提现",
            provider="wechat",
            condition=PresetRuleCondition(
                metadata={"tx_type": r"^零钱提现$"},
            ),
            action=PresetRuleAction(
                account_keyword="零钱",
                contra_account_metadata_key="_withdrawal_target",
            ),
        )
        engine = RulesEngine(config, preset_rules=[preset])
        txn = Transaction(
            date=date(2025, 6, 15),
            amount=Decimal("200.00"),
            currency="CNY",
            description="零钱提现",
            provider="wechat",
            metadata={
                "tx_type": "零钱提现",
                "method": "零钱",
                "_withdrawal_target": "测试银行(0001)",
            },
        )
        result = engine.apply(txn)
        assert result.account == "Assets:Wallet:WeChat:Balance"
        assert result.contra_account == "Assets:Bank:Test:Savings"
        assert result.match_source == MatchSource.RULE

    def test_contra_account_metadata_key_missing_value(self):
        """Test that missing metadata value doesn't set contra_account."""
        config = Config(
            account_mappings=[
                AccountMapping(pattern="零钱", account="Assets:Wallet:WeChat:Balance"),
            ],
        )
        preset = PresetRule(
            rule_id="test_withdraw",
            name="零钱提现",
            provider="wechat",
            condition=PresetRuleCondition(
                metadata={"tx_type": r"^零钱提现$"},
            ),
            action=PresetRuleAction(
                account_keyword="零钱",
                contra_account_metadata_key="_withdrawal_target",
            ),
        )
        engine = RulesEngine(config, preset_rules=[preset])
        txn = Transaction(
            date=date(2025, 6, 15),
            amount=Decimal("200.00"),
            currency="CNY",
            description="零钱提现",
            provider="wechat",
            metadata={
                "tx_type": "零钱提现",
                "method": "零钱",
            },
        )
        result = engine.apply(txn)
        assert result.account == "Assets:Wallet:WeChat:Balance"
        assert result.contra_account is None

    def test_contra_account_metadata_key_no_mapping_match(self):
        """Test that unresolvable keyword doesn't set contra_account."""
        config = Config(
            account_mappings=[
                AccountMapping(pattern="零钱", account="Assets:Wallet:WeChat:Balance"),
            ],
        )
        preset = PresetRule(
            rule_id="test_withdraw",
            name="零钱提现",
            provider="wechat",
            condition=PresetRuleCondition(
                metadata={"tx_type": r"^零钱提现$"},
            ),
            action=PresetRuleAction(
                account_keyword="零钱",
                contra_account_metadata_key="_withdrawal_target",
            ),
        )
        engine = RulesEngine(config, preset_rules=[preset])
        txn = Transaction(
            date=date(2025, 6, 15),
            amount=Decimal("200.00"),
            currency="CNY",
            description="零钱提现",
            provider="wechat",
            metadata={
                "tx_type": "零钱提现",
                "method": "零钱",
                "_withdrawal_target": "未知银行(9999)",
            },
        )
        result = engine.apply(txn)
        assert result.account == "Assets:Wallet:WeChat:Balance"
        assert result.contra_account is None


class TestWechatWalletTransferPresets:
    """WeChat preset rules resolve both legs of wallet-to-wallet/bank transfers."""

    @staticmethod
    def _config() -> Config:
        # 零钱 is listed before 零钱通 so a substring match on "零钱" would
        # wrongly pick it for 零钱通 rows.
        return Config(
            account_mappings=[
                AccountMapping(pattern="零钱", account="Assets:WeChat:Balance"),
                AccountMapping(pattern="零钱通", account="Assets:WeChat:Fund"),
                AccountMapping(pattern="经营账户", account="Assets:WeChat:Merchant"),
                AccountMapping(pattern="某银行", account="Assets:Bank:Some"),
            ],
        )

    @staticmethod
    def _txn(tx_type: str, method: str, target: str) -> Transaction:
        return Transaction(
            date=date(2030, 1, 3),
            amount=Decimal("10.00"),
            currency="CNY",
            description=tx_type,
            provider="wechat",
            metadata={
                "tx_type": tx_type,
                "method": method,
                "status": "资金已到账",
                "_withdrawal_target": target,
            },
        )

    def _engine(self, config: Config | None = None) -> RulesEngine:
        return RulesEngine(
            config or self._config(), preset_rules=WechatProvider.get_preset_rules()
        )

    def test_merchant_top_up(self):
        txn = self._txn("经营账户充值", "零钱", "经营账户")
        result = self._engine().apply(txn)
        assert result.account == "Assets:WeChat:Balance"
        assert result.contra_account == "Assets:WeChat:Merchant"
        assert result.metadata[MATCHED_PRESET_RULE] == "wechat_merchant_top_up"

    def test_lingqiantong_out_to_bank(self):
        txn = self._txn("零钱通转出-到某银行(1111)", "零钱通", "某银行(1111)")
        result = self._engine().apply(txn)
        assert result.account == "Assets:WeChat:Fund"
        assert result.contra_account == "Assets:Bank:Some"
        assert result.metadata[MATCHED_PRESET_RULE] == "wechat_lingqiantong_out"

    @pytest.mark.parametrize("bank_pattern", ["某银行储蓄卡(1111)", "某银行(1111)"])
    def test_lingqiantong_out_to_bank_with_card_pattern(self, bank_pattern):
        engine = self._engine(
            Config(
                account_mappings=[
                    AccountMapping(pattern="零钱通", account="Assets:WeChat:Fund"),
                    AccountMapping(pattern=bank_pattern, account="Assets:Bank:X:1111"),
                ],
            )
        )
        txn = self._txn("零钱通转出-到某银行(1111)", "零钱通", "某银行(1111)")
        result = engine.apply(txn)
        assert result.contra_account == "Assets:Bank:X:1111"

    def test_lingqiantong_out_to_balance(self):
        txn = self._txn("零钱通转出-到零钱", "零钱通", "零钱")
        result = self._engine().apply(txn)
        assert result.account == "Assets:WeChat:Fund"
        assert result.contra_account == "Assets:WeChat:Balance"

    def test_target_account_resolution_in_api(self):
        txns = [
            self._txn("经营账户充值", "零钱", "经营账户"),
            self._txn("零钱通转出-到某银行(1111)", "零钱通", "某银行(1111)"),
        ]
        result = _set_target_accounts(
            txns, self._config(), WechatProvider.get_preset_rules()
        )
        assert [t.account for t in result] == [
            "Assets:WeChat:Balance",
            "Assets:WeChat:Fund",
        ]

    def test_merchant_top_up_with_fund_mapping_listed_first(self):
        # 零钱通 comes first, so a "零钱" keyword lookup would land on it.
        config = Config(
            account_mappings=[
                AccountMapping(pattern="零钱通", account="Assets:WeChat:Fund"),
                AccountMapping(pattern="零钱", account="Assets:WeChat:Balance"),
                AccountMapping(pattern="经营账户", account="Assets:WeChat:Merchant"),
            ],
        )
        presets = WechatProvider.get_preset_rules()

        result = RulesEngine(config, preset_rules=presets).apply(
            self._txn("经营账户充值", "零钱", "经营账户")
        )
        assert result.account == "Assets:WeChat:Balance"
        assert result.contra_account == "Assets:WeChat:Merchant"

        (targeted,) = _set_target_accounts(
            [self._txn("经营账户充值", "零钱", "经营账户")], config, presets
        )
        assert targeted.account == "Assets:WeChat:Balance"
        applied = RulesEngine(config, preset_rules=presets).apply(targeted)
        assert applied.contra_account == "Assets:WeChat:Merchant"


class TestWalletKeywordWithLongerWalletListedFirst:
    """A generic wallet keyword resolves to its own mapping even when a longer
    wallet name containing it is listed earlier."""

    @staticmethod
    def _wechat_config() -> Config:
        return Config(
            account_mappings=[
                AccountMapping(pattern="零钱通", account="Assets:WeChat:Fund"),
                AccountMapping(pattern="零钱", account="Assets:WeChat:Balance"),
                AccountMapping(pattern="某银行", account="Assets:Bank:Some"),
            ],
        )

    @staticmethod
    def _wechat_txns() -> list[Transaction]:
        withdraw = Transaction(
            date=date(2030, 1, 3),
            amount=Decimal("10.00"),
            currency="CNY",
            description="零钱提现",
            provider="wechat",
            metadata={
                "tx_type": "零钱提现",
                "method": "零钱",
                "status": "提现已到账",
                "_withdrawal_target": "某银行(1111)",
            },
        )
        deposit = Transaction(
            date=date(2030, 1, 4),
            amount=Decimal("-20.00"),
            currency="CNY",
            description="payee-a",
            provider="wechat",
            metadata={"tx_type": "转账", "status": "已存入零钱"},
        )
        return [withdraw, deposit]

    def test_wechat_rules_engine(self):
        engine = RulesEngine(
            self._wechat_config(), preset_rules=WechatProvider.get_preset_rules()
        )
        withdraw, deposit = (engine.apply(t) for t in self._wechat_txns())
        assert withdraw.metadata[MATCHED_PRESET_RULE] == "wechat_cash_withdraw"
        assert withdraw.account == "Assets:WeChat:Balance"
        assert deposit.metadata[MATCHED_PRESET_RULE] == "wechat_to_balance"
        assert deposit.account == "Assets:WeChat:Balance"

    def test_wechat_set_target_accounts(self):
        result = _set_target_accounts(
            self._wechat_txns(),
            self._wechat_config(),
            WechatProvider.get_preset_rules(),
        )
        assert [t.account for t in result] == [
            "Assets:WeChat:Balance",
            "Assets:WeChat:Balance",
        ]

    def test_alipay_balance_set_target_accounts(self):
        config = Config(
            account_mappings=[
                AccountMapping(pattern="余额宝", account="Assets:Alipay:YuEBao"),
                AccountMapping(pattern="余额", account="Assets:Alipay:Balance"),
            ],
        )
        txn = Transaction(
            date=date(2030, 1, 5),
            amount=Decimal("30.00"),
            currency="CNY",
            description="payee-b",
            provider="alipay",
            metadata={"method": "余额"},
        )
        (result,) = _set_target_accounts(
            [txn], config, AlipayProvider.get_preset_rules()
        )
        assert result.account == "Assets:Alipay:Balance"
        assert result.metadata[MATCHED_PRESET_RULE] == "alipay_balance"
