"""Tests for ICBC (工商银行) debit card statement provider."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from bean_sieve.providers import get_provider
from bean_sieve.providers.banks.debit.icbc import ICBCDebitProvider

# BOM prefix for UTF-8 with BOM files
BOM = "\ufeff"


def create_icbc_csv(
    tmp_path: Path,
    rows: list[dict],
    card_suffix: str = "5625",
    filename: str = "hisdetail_test.csv",
) -> Path:
    """Create a sample ICBC debit card CSV file.

    Args:
        tmp_path: Temporary directory path
        rows: List of row dicts with keys: date, summary, detail, location,
              income, expense, counterparty, counter_account
        card_suffix: Last 4 digits of card number
        filename: Output filename
    """
    header = "交易日期,摘要,交易详情,交易场所,交易国家或地区简称,钞/汇,交易金额(收入),交易金额(支出),交易币种,记账金额(收入),记账金额(支出),记账币种,余额,对方户名,对方账户"

    lines = [
        f"{BOM}明细查询文件下载",
        "",
        f'卡号: 6222****{card_suffix},"卡别名: "',
        "",
        '子账户序号: 00000,子账户类别: 活期,"子账户别名: "',
        "",
        header,
    ]

    for row in rows:
        income = row.get("income", "")
        expense = row.get("expense", "")
        counterparty = row.get("counterparty", "")
        counter_account = row.get("counter_account", "")
        fields = [
            row["date"],
            f'"{row.get("summary", "")}"',
            f'"{row.get("detail", "")}"',
            f'"{row.get("location", "")}"',
            '"CHN"',
            "钞",
            '"-"',
            '"-"',
            "-",
            f'"{income}"',
            f'"{expense}"',
            "人民币",
            '"10,000.00"',
            f'"{counterparty}"',
            f'"{counter_account}"',
        ]
        lines.append(",".join(fields))

    lines.append("")
    lines.append("人民币合计,,,,,,,,")

    file_path = tmp_path / filename
    file_path.write_text("\n".join(lines), encoding="utf-8")
    return file_path


@pytest.fixture
def icbc_csv_file(tmp_path: Path) -> Path:
    """Create a sample ICBC debit card CSV file."""
    rows = [
        {
            "date": "2026-03-02",
            "summary": "还款",
            "location": "支付宝-用户A",
            "expense": "14,955.00",
            "counterparty": "支付宝（中国）网络技术有限公司",
            "counter_account": "2155****0690",
        },
        {
            "date": "2026-03-02",
            "summary": "他行汇入",
            "detail": "转账",
            "income": "60,000.00",
            "counterparty": "用户B",
            "counter_account": "6229****3114",
        },
        {
            "date": "2026-02-25",
            "summary": "消费",
            "location": "抖音支付-丰巢网络技术有限公司",
            "expense": "0.01",
            "counterparty": "丰巢网络技术有限公司",
            "counter_account": "6175****0001",
        },
        {
            "date": "2025-12-21",
            "summary": "利息",
            "location": "批量业务",
            "income": "0.05",
        },
    ]
    return create_icbc_csv(tmp_path, rows)


class TestICBCDebitProvider:
    """Tests for ICBCDebitProvider."""

    def test_provider_registration(self) -> None:
        """Test that ICBC provider is properly registered."""
        provider = get_provider("icbc_debit")
        assert isinstance(provider, ICBCDebitProvider)
        assert provider.provider_id == "icbc_debit"
        assert provider.provider_name == "工商银行借记卡"
        assert ".csv" in provider.supported_formats

    def test_can_handle(self, tmp_path: Path) -> None:
        """Test file format detection by filename keyword."""
        # Create file with content keyword for content detection
        csv_file = tmp_path / "hisdetail12345.csv"
        csv_file.write_text("明细查询文件下载\n", encoding="utf-8")

        assert ICBCDebitProvider.can_handle(csv_file)
        assert not ICBCDebitProvider.can_handle(Path("random_file.csv"))
        assert not ICBCDebitProvider.can_handle(Path("hisdetail.xlsx"))

    def test_parse_transactions(self, icbc_csv_file: Path) -> None:
        """Test parsing all transactions."""
        provider = ICBCDebitProvider()
        transactions = provider.parse(icbc_csv_file)

        assert len(transactions) == 4

    def test_expense_transaction(self, icbc_csv_file: Path) -> None:
        """Test expense transactions are positive."""
        provider = ICBCDebitProvider()
        transactions = provider.parse(icbc_csv_file)
        txn = transactions[0]

        assert txn.date == date(2026, 3, 2)
        assert txn.amount == Decimal("14955.00")
        assert txn.is_expense
        assert txn.currency == "CNY"
        assert txn.payee == "支付宝（中国）网络技术有限公司"
        assert "还款" in txn.description
        assert txn.provider == "icbc_debit"

    def test_income_transaction(self, icbc_csv_file: Path) -> None:
        """Test income transactions are negative."""
        provider = ICBCDebitProvider()
        transactions = provider.parse(icbc_csv_file)
        txn = transactions[1]

        assert txn.date == date(2026, 3, 2)
        assert txn.amount == Decimal("-60000.00")
        assert txn.is_income
        assert "他行汇入" in txn.description

    def test_small_amount(self, icbc_csv_file: Path) -> None:
        """Test small amount transactions."""
        provider = ICBCDebitProvider()
        transactions = provider.parse(icbc_csv_file)
        txn = transactions[2]

        assert txn.amount == Decimal("0.01")
        assert "消费" in txn.description

    def test_interest_income(self, icbc_csv_file: Path) -> None:
        """Test interest income without counterparty."""
        provider = ICBCDebitProvider()
        transactions = provider.parse(icbc_csv_file)
        txn = transactions[3]

        assert txn.date == date(2025, 12, 21)
        assert txn.amount == Decimal("-0.05")
        assert txn.is_income
        assert "利息" in txn.description
        assert txn.payee is None

    def test_card_last4_extraction(self, icbc_csv_file: Path) -> None:
        """Test card_last4 is extracted from metadata header."""
        provider = ICBCDebitProvider()
        transactions = provider.parse(icbc_csv_file)

        for txn in transactions:
            assert txn.card_last4 == "5625"

    def test_source_info(self, icbc_csv_file: Path) -> None:
        """Test source file and line tracking."""
        provider = ICBCDebitProvider()
        transactions = provider.parse(icbc_csv_file)

        assert transactions[0].source_file == icbc_csv_file
        assert transactions[0].source_line is not None
        assert transactions[0].source_line > 0


class TestICBCEdgeCases:
    """Tests for ICBC edge cases."""

    def test_empty_statement(self, tmp_path: Path) -> None:
        """Test handling of statement with no transactions."""
        file_path = create_icbc_csv(tmp_path, [])
        provider = ICBCDebitProvider()
        transactions = provider.parse(file_path)
        assert transactions == []

    def test_thousand_separator_amounts(self, tmp_path: Path) -> None:
        """Test parsing amounts with thousand separators."""
        rows = [
            {
                "date": "2026-01-01",
                "summary": "跨行汇款",
                "location": "手机银行",
                "expense": "123,456.78",
                "counterparty": "用户A",
            },
        ]
        file_path = create_icbc_csv(tmp_path, rows)
        provider = ICBCDebitProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        assert transactions[0].amount == Decimal("123456.78")

    def test_summary_row_skipped(self, tmp_path: Path) -> None:
        """Test that the summary/footer row is not parsed as a transaction."""
        rows = [
            {
                "date": "2026-01-01",
                "summary": "消费",
                "expense": "100.00",
            },
        ]
        file_path = create_icbc_csv(tmp_path, rows)
        provider = ICBCDebitProvider()
        transactions = provider.parse(file_path)

        # Should only get the data row, not the summary
        assert len(transactions) == 1

    def test_description_with_detail(self, tmp_path: Path) -> None:
        """Test description includes detail when present."""
        rows = [
            {
                "date": "2026-01-01",
                "summary": "银联入账",
                "detail": "LAEP 提现",
                "location": "网上银行",
                "income": "10,000.00",
            },
        ]
        file_path = create_icbc_csv(tmp_path, rows)
        provider = ICBCDebitProvider()
        transactions = provider.parse(file_path)

        assert len(transactions) == 1
        desc = transactions[0].description
        assert "银联入账" in desc
        assert "LAEP 提现" in desc
        assert "网上银行" in desc

    def test_metadata_content(self, tmp_path: Path) -> None:
        """Test metadata includes summary, detail, and location."""
        rows = [
            {
                "date": "2026-01-01",
                "summary": "消费",
                "detail": "some detail",
                "location": "手机银行",
                "expense": "50.00",
            },
        ]
        file_path = create_icbc_csv(tmp_path, rows)
        provider = ICBCDebitProvider()
        transactions = provider.parse(file_path)

        assert transactions[0].metadata["summary"] == "消费"
        assert transactions[0].metadata["detail"] == "some detail"
        assert transactions[0].metadata["location"] == "手机银行"

    def test_gbk_encoding_fallback(self, tmp_path: Path) -> None:
        """Test GBK encoding fallback."""
        header = "交易日期,摘要,交易详情,交易场所,交易国家或地区简称,钞/汇,交易金额(收入),交易金额(支出),交易币种,记账金额(收入),记账金额(支出),记账币种,余额,对方户名,对方账户"
        content = "\n".join(
            [
                "明细查询文件下载",
                "",
                '卡号: 6222****1234,"卡别名: "',
                "",
                '子账户序号: 00000,子账户类别: 活期,"子账户别名: "',
                "",
                header,
                '2026-01-01,"消费","","手机银行","CHN",钞,"-","-",-,"","100.00",人民币,"10,000.00","商户A","1234****5678"',
            ]
        )
        file_path = tmp_path / "hisdetail_gbk.csv"
        file_path.write_bytes(content.encode("gbk"))

        provider = ICBCDebitProvider()
        transactions = provider.parse(file_path)
        assert len(transactions) == 1
        assert transactions[0].amount == Decimal("100.00")
