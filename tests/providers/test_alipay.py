"""Tests for Alipay statement provider."""

from datetime import date, time
from decimal import Decimal
from pathlib import Path

import pytest

from bean_sieve.providers import get_provider
from bean_sieve.providers.payment.alipay import AlipayProvider


@pytest.fixture
def alipay_csv_content():
    """Sample Alipay CSV content (GBK encoding simulation)."""
    # This is a minimal representation of Alipay CSV structure
    # In real tests, we'd use actual GBK-encoded files
    header_lines = "\n".join(
        [
            "------------------------------------------------------------------------------------",
            "导出信息：",
            "姓名：测试用户",
            "支付宝账户：test@example.com",
            "起始时间：[2025-01-01 00:00:00]    终止时间：[2025-01-31 23:59:59]",
            "导出交易类型：[全部]",
            "导出时间：[2025-02-01 10:00:00]",
            "共3笔记录",
            "收入：1笔 100.00元",
            "支出：2笔 50.00元",
            "不计收支：0笔 0.00元",
            "",
            "特别提示：",
            "1.测试提示1",
            "2.测试提示2",
            "3.测试提示3",
            "4.测试提示4",
            "5.测试提示5",
            "6.测试提示6",
            "7.测试提示7",
            "8.测试提示8",
            "9.测试提示9",
            "",
            "------------------------支付宝支付科技有限公司  电子客户回单------------------------",
        ]
    )

    data_header = "交易时间,交易分类,交易对方,对方账号,商品说明,收/支,金额,收/付款方式,交易状态,交易订单号,商家订单号,备注,"

    data_rows = [
        "2025-01-15 14:30:00,餐饮美食,瑞幸咖啡,luckincoffee@example.com,生椰拿铁,支出,9.90,招商银行信用卡(1234),交易成功,2025011514300001,M001,",
        "2025-01-15 12:00:00,餐饮美食,公司食堂,cafeteria@example.com,午餐,支出,15.00,支付宝余额,交易成功,2025011512000002,M002,",
        "2025-01-14 10:00:00,转账,张三,zhangsan@example.com,转账,收入,100.00,,交易成功,2025011410000003,,",
    ]

    return header_lines + "\n" + data_header + "\n" + "\n".join(data_rows)


@pytest.fixture
def alipay_csv_file(tmp_path, alipay_csv_content):
    """Create a temporary Alipay CSV file."""
    file_path = tmp_path / "alipay_test.csv"
    file_path.write_text(alipay_csv_content, encoding="gbk")
    return file_path


class TestAlipayProvider:
    """Tests for AlipayProvider."""

    def test_provider_registration(self):
        """Test that Alipay provider is properly registered."""
        provider = get_provider("alipay")
        assert isinstance(provider, AlipayProvider)
        assert provider.provider_id == "alipay"
        assert provider.provider_name == "支付宝"
        assert ".csv" in provider.supported_formats

    def test_can_handle(self):
        """Test file format detection."""
        assert AlipayProvider.can_handle(Path("alipay_statement.csv"))
        assert AlipayProvider.can_handle(Path("支付宝账单.csv"))
        assert not AlipayProvider.can_handle(Path("alipay_statement.xlsx"))
        assert not AlipayProvider.can_handle(Path("statement.csv"))  # no keyword

    def test_parse_transactions(self, alipay_csv_file):
        """Test parsing transactions from CSV file."""
        provider = AlipayProvider()
        transactions = provider.parse(alipay_csv_file)

        assert len(transactions) == 3

        # Check expense transaction
        expense = transactions[0]
        assert expense.date == date(2025, 1, 15)
        assert expense.time == time(14, 30, 0)
        assert expense.amount == Decimal("9.90")  # expense is positive
        assert expense.currency == "CNY"
        assert expense.payee == "瑞幸咖啡"
        assert expense.description == "生椰拿铁"
        assert expense.order_id == "2025011514300001"
        assert expense.provider == "alipay"
        assert expense.is_expense

        # Check income transaction
        income = transactions[2]
        assert income.date == date(2025, 1, 14)
        assert income.amount == Decimal("-100.00")  # income is negative
        assert income.payee == "张三"
        assert income.is_income

    def test_metadata_extraction(self, alipay_csv_file):
        """Test that metadata is properly extracted."""
        provider = AlipayProvider()
        transactions = provider.parse(alipay_csv_file)

        txn = transactions[0]
        assert txn.metadata["category"] == "餐饮美食"
        assert txn.metadata["method"] == "招商银行信用卡(1234)"
        assert txn.metadata["status"] == "交易成功"
        assert txn.metadata["tx_type"] == "支出"

    def test_empty_file(self, tmp_path):
        """Test handling of file with no transactions."""
        # Create file with only headers
        header = "\n".join(["---"] * 24)  # 24 header lines
        file_path = tmp_path / "empty.csv"
        file_path.write_text(header, encoding="gbk")

        provider = AlipayProvider()
        transactions = provider.parse(file_path)
        assert transactions == []


class TestAlipayRefundHandling:
    """Tests for Alipay refund post-processing."""

    @pytest.fixture
    def alipay_with_refund(self, tmp_path):
        """Create Alipay CSV with a purchase and matching refund."""
        header_lines = "\n".join(["---"] * 23)
        header_lines += "\n------------------------分隔线------------------------"

        data_header = "交易时间,交易分类,交易对方,对方账号,商品说明,收/支,金额,收/付款方式,交易状态,交易订单号,商家订单号,备注,"

        data_rows = [
            # Original purchase
            "2025-01-15 10:00:00,日用百货,商家A,merchant@example.com,商品,支出,50.00,余额,交易成功,ORDER001,M001,",
            # Refund for the same purchase
            "2025-01-16 10:00:00,退款,商家A,merchant@example.com,退款-商品,不计收支,50.00,,退款成功,ORDER001_REFUND,M001,",
        ]

        content = header_lines + "\n" + data_header + "\n" + "\n".join(data_rows)
        file_path = tmp_path / "alipay_refund.csv"
        file_path.write_text(content, encoding="gbk")
        return file_path

    def test_closed_transaction_filtered(self, tmp_path):
        """Test that closed transactions with '不计收支' are filtered."""
        header_lines = "\n".join(["---"] * 23)
        header_lines += "\n------------------------分隔线------------------------"

        data_header = "交易时间,交易分类,交易对方,对方账号,商品说明,收/支,金额,收/付款方式,交易状态,交易订单号,商家订单号,备注,"

        data_rows = [
            "2025-01-15 10:00:00,日用百货,商家,merchant@example.com,商品,支出,50.00,余额,交易成功,ORDER001,M001,",
            "2025-01-15 11:00:00,日用百货,商家,merchant@example.com,取消订单,不计收支,50.00,,交易关闭,ORDER002,M002,",
        ]

        content = header_lines + "\n" + data_header + "\n" + "\n".join(data_rows)
        file_path = tmp_path / "alipay_closed.csv"
        file_path.write_text(content, encoding="gbk")

        provider = AlipayProvider()
        transactions = provider.parse(file_path)

        # Closed transaction should be filtered
        assert len(transactions) == 1
        assert transactions[0].order_id == "ORDER001"
