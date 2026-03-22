"""Tests for CMB (招商银行) debit card statement provider."""

from datetime import date, time
from decimal import Decimal
from pathlib import Path

import pytest

from bean_sieve.providers import get_provider
from bean_sieve.providers.banks.debit.cmb import CMBDebitProvider

BOM = "\ufeff"


def create_cmb_debit_csv(
    tmp_path: Path,
    rows: list[dict],
    card_suffix: str = "5555",
    filename: str = "CMB_test.csv",
) -> Path:
    """Create a sample CMB debit card CSV file.

    Args:
        tmp_path: Temporary directory path
        rows: List of row dicts with keys: date, time, income, expense,
              balance, type, remark
        card_suffix: Last 4 digits of card number
        filename: Output filename
    """
    lines = [
        f'{BOM}"# 招商银行交易记录"',
        '"# 导出时间: [            2026-03-22 23:52:22]"',
        f'"# 账    号: [一卡通:6214********{card_suffix}   招商银行]"',
        '"# 币    种: [                         人民币]"',
        '"# 起始日期: [20260201]   终止日期: [20260331]"',
        '"# 过滤设置:  无"',
        "",
        '"交易日期","交易时间","收入","支出","余额","交易类型","交易备注"',
    ]

    for row in rows:
        income = row.get("income", "")
        expense = row.get("expense", "")
        balance = row.get("balance", "10000.00")
        fields = [
            f'"\t{row["date"]}"',
            f'"\t{row.get("time", "12:00:00")}"',
            f'"{income}"',
            f'"{expense}"',
            f'"{balance}"',
            f'"{row.get("type", "")}"',
            f'"\t{row.get("remark", "")}"',
        ]
        lines.append(",".join(fields))

    lines.append("")
    lines.append('"# 收入合计: 1 笔，共 100.00 元"')
    lines.append('"# 支出合计: 1 笔，共 100.00 元"')

    file_path = tmp_path / filename
    file_path.write_text("\n".join(lines), encoding="utf-8")
    return file_path


@pytest.fixture
def cmb_debit_csv(tmp_path: Path) -> Path:
    """Create a sample CMB debit card CSV file."""
    rows = [
        {
            "date": "20260115",
            "time": "10:30:00",
            "income": "2.50",
            "type": "账户结息",
            "remark": "结息：2.50扣税：0 ",
        },
        {
            "date": "20260112",
            "time": "18:45:20",
            "income": "5000.00",
            "type": "汇入汇款",
            "remark": " 张*三",
        },
        {
            "date": "20260108",
            "time": "09:15:30",
            "expense": "200.00",
            "type": "银联在线支付",
            "remark": "银联在线支付，某银行信用卡中心还款业务 ",
        },
        {
            "date": "20260103",
            "time": "14:20:00",
            "expense": "3000.00",
            "type": "转账汇款",
            "remark": "转账 李*四",
        },
    ]
    return create_cmb_debit_csv(tmp_path, rows)


class TestCMBDebitProvider:
    """Tests for CMBDebitProvider."""

    def test_provider_registration(self) -> None:
        """Test that CMB debit provider is properly registered."""
        provider = get_provider("cmb_debit")
        assert isinstance(provider, CMBDebitProvider)
        assert provider.provider_id == "cmb_debit"
        assert provider.provider_name == "招商银行借记卡"
        assert ".csv" in provider.supported_formats

    def test_can_handle(self, tmp_path: Path) -> None:
        """Test file format detection by filename keyword."""
        csv_file = tmp_path / "CMB_6214_test.csv"
        csv_file.write_text(f'{BOM}"# 招商银行交易记录"\n', encoding="utf-8")

        assert CMBDebitProvider.can_handle(csv_file)
        assert not CMBDebitProvider.can_handle(Path("random_file.csv"))
        assert not CMBDebitProvider.can_handle(Path("CMB_test.xlsx"))

    def test_parse_transactions(self, cmb_debit_csv: Path) -> None:
        """Test parsing all transactions."""
        provider = CMBDebitProvider()
        transactions = provider.parse(cmb_debit_csv)
        assert len(transactions) == 4

    def test_income_transaction(self, cmb_debit_csv: Path) -> None:
        """Test income transactions are negative."""
        provider = CMBDebitProvider()
        transactions = provider.parse(cmb_debit_csv)
        txn = transactions[0]

        assert txn.date == date(2026, 1, 15)
        assert txn.time == time(10, 30, 0)
        assert txn.amount == Decimal("-2.50")
        assert txn.is_income
        assert "账户结息" in txn.description
        assert txn.provider == "cmb_debit"

    def test_expense_transaction(self, cmb_debit_csv: Path) -> None:
        """Test expense transactions are positive."""
        provider = CMBDebitProvider()
        transactions = provider.parse(cmb_debit_csv)
        txn = transactions[2]

        assert txn.date == date(2026, 1, 8)
        assert txn.amount == Decimal("200.00")
        assert txn.is_expense
        assert "银联在线支付" in txn.description

    def test_transfer_expense(self, cmb_debit_csv: Path) -> None:
        """Test transfer expense transaction."""
        provider = CMBDebitProvider()
        transactions = provider.parse(cmb_debit_csv)
        txn = transactions[3]

        assert txn.amount == Decimal("3000.00")
        assert "转账汇款" in txn.description

    def test_card_last4_extraction(self, cmb_debit_csv: Path) -> None:
        """Test card_last4 is extracted from metadata header."""
        provider = CMBDebitProvider()
        transactions = provider.parse(cmb_debit_csv)

        for txn in transactions:
            assert txn.card_last4 == "5555"

    def test_source_info(self, cmb_debit_csv: Path) -> None:
        """Test source file and line tracking."""
        provider = CMBDebitProvider()
        transactions = provider.parse(cmb_debit_csv)

        assert transactions[0].source_file == cmb_debit_csv
        assert transactions[0].source_line is not None
        assert transactions[0].source_line > 0

    def test_time_parsing(self, cmb_debit_csv: Path) -> None:
        """Test time is parsed correctly."""
        provider = CMBDebitProvider()
        transactions = provider.parse(cmb_debit_csv)

        assert transactions[1].time == time(18, 45, 20)

    def test_metadata_contains_type(self, cmb_debit_csv: Path) -> None:
        """Test metadata includes transaction type."""
        provider = CMBDebitProvider()
        transactions = provider.parse(cmb_debit_csv)

        assert transactions[0].metadata["type"] == "账户结息"
        assert transactions[2].metadata["type"] == "银联在线支付"


class TestCMBDebitEdgeCases:
    """Tests for CMB debit edge cases."""

    def test_empty_statement(self, tmp_path: Path) -> None:
        """Test handling of statement with no transactions."""
        file_path = create_cmb_debit_csv(tmp_path, [])
        provider = CMBDebitProvider()
        transactions = provider.parse(file_path)
        assert transactions == []

    def test_footer_lines_skipped(self, tmp_path: Path) -> None:
        """Test that footer summary lines are not parsed."""
        rows = [
            {
                "date": "20260101",
                "expense": "100.00",
                "type": "消费",
                "remark": "test",
            },
        ]
        file_path = create_cmb_debit_csv(tmp_path, rows)
        provider = CMBDebitProvider()
        transactions = provider.parse(file_path)
        assert len(transactions) == 1

    def test_description_building(self, tmp_path: Path) -> None:
        """Test description combines type and remark."""
        rows = [
            {
                "date": "20260101",
                "expense": "50.00",
                "type": "网联协议支付",
                "remark": "财付通-微信支付-企业微信红包 ",
            },
        ]
        file_path = create_cmb_debit_csv(tmp_path, rows)
        provider = CMBDebitProvider()
        transactions = provider.parse(file_path)

        desc = transactions[0].description
        assert "网联协议支付" in desc
        assert "财付通-微信支付-企业微信红包" in desc

    def test_gbk_encoding_fallback(self, tmp_path: Path) -> None:
        """Test GBK encoding fallback."""
        content = "\n".join(
            [
                '"# 招商银行交易记录"',
                '"# 导出时间: [            2026-03-22 23:52:22]"',
                '"# 账    号: [一卡通:6214********1234   招商银行]"',
                '"# 币    种: [                         人民币]"',
                '"# 起始日期: [20260201]   终止日期: [20260331]"',
                '"# 过滤设置:  无"',
                "",
                '"交易日期","交易时间","收入","支出","余额","交易类型","交易备注"',
                '"\t20260101","\t12:00:00","","100.00","10000.00","消费","\t测试"',
            ]
        )
        file_path = tmp_path / "CMB_gbk_test.csv"
        file_path.write_bytes(content.encode("gbk"))

        provider = CMBDebitProvider()
        transactions = provider.parse(file_path)
        assert len(transactions) == 1
        assert transactions[0].amount == Decimal("100.00")
