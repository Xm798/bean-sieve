"""Pytest fixtures for Bean-Sieve tests."""

from datetime import date
from decimal import Decimal

import pytest

from bean_sieve.config import Config
from bean_sieve.config.schema import (
    AccountMapping,
    DefaultsConfig,
    Rule,
    RuleAction,
    RuleCondition,
)
from bean_sieve.core.types import Transaction


@pytest.fixture
def sample_transaction():
    """Create a sample transaction for testing."""
    return Transaction(
        date=date(2025, 1, 4),
        amount=Decimal("99.00"),
        currency="CNY",
        description="支付宝-瑞幸咖啡",
        payee="瑞幸咖啡",
        card_suffix="1234",
        provider="hxb_credit",
    )


@pytest.fixture
def sample_transactions():
    """Create a list of sample transactions."""
    return [
        Transaction(
            date=date(2025, 1, 4),
            amount=Decimal("9.90"),
            currency="CNY",
            description="支付宝-瑞幸咖啡（中国）有限公司",
            payee="瑞幸咖啡",
            provider="alipay",
            metadata={"method": "余额"},
        ),
        Transaction(
            date=date(2025, 1, 5),
            amount=Decimal("15.00"),
            currency="CNY",
            description="支付宝-示例餐饮管理有限公司",
            provider="alipay",
            metadata={"method": "华夏银行信用卡(1234)"},
        ),
        Transaction(
            date=date(2025, 1, 6),
            amount=Decimal("-100.00"),
            currency="CNY",
            description="微信转账-收入",
            provider="wechat",
            metadata={"method": "零钱"},
        ),
    ]


@pytest.fixture
def sample_config():
    """Create a sample configuration."""
    return Config(
        defaults=DefaultsConfig(
            currency="CNY",
            expense_account="Expenses:FIXME",
            income_account="Income:FIXME",
            date_tolerance=2,
        ),
        account_mappings=[
            AccountMapping(pattern="余额", account="Assets:Current:Alipay"),
            AccountMapping(pattern="零钱", account="Assets:Current:Wechat"),
            AccountMapping(pattern="华夏银行", account="Liabilities:CreditCard:HXB"),
        ],
        rules=[
            Rule(
                condition=RuleCondition(description=".*瑞幸.*"),
                action=RuleAction(
                    contra_account="Expenses:Food:Coffee",
                    payee="瑞幸咖啡",
                ),
                priority=100,
            ),
            Rule(
                condition=RuleCondition(description=".*示例餐饮.*"),
                action=RuleAction(
                    contra_account="Expenses:Food:Lunch",
                    payee="公司食堂",
                ),
                priority=90,
            ),
        ],
    )


@pytest.fixture
def temp_dir(tmp_path):
    """Create a temporary directory for test files."""
    return tmp_path
