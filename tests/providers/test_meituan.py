"""Tests for Meituan payment provider."""

from datetime import date, time
from decimal import Decimal
from pathlib import Path

from bean_sieve.providers.payment.meituan import MeituanProvider

# 20 header lines (indices 0-18 metadata/tips/section, index 19 column header),
# data rows start at index 20. All values synthetic.
HEADER = """美团交易账单明细
美团用户名：[test_user]
起始时间：[2030-01-01] 终止时间：[2030-01-31]
导出交易类型：[全部]
导出时间：[2030-02-01 10:00:00]
""
共：N笔记录
支出：N笔 0.00元
收入：N笔 0.00元
不计收支：N笔 0.00元
""
特别提示：
1. 提示一
2. 提示二
3. 提示三
4. 提示四
5. 提示五
""
【美团交易账单明细列表】
交易创建时间,交易成功时间,交易类型,订单标题,收/支,支付方式,订单金额,实付金额,交易单号,商家单号,备注
"""


def _write(tmp_path: Path, rows: str, name: str = "美团账单.csv") -> Path:
    sample = tmp_path / name
    sample.write_text(HEADER + rows, encoding="utf-8-sig")
    return sample


class TestMeituanProvider:
    """Tests for MeituanProvider."""

    def test_parse_basic(self, tmp_path: Path) -> None:
        """Basic expense row maps to a positive amount."""
        rows = (
            "2030-01-15 12:00:01,2030-01-15 12:00:05,支付,测试餐厅-代金券,支出,"
            "测试银行信用卡(8888),¥100.00,¥100.00,"
            '"TX1000000001\t","MC2000000001\t",/\r\n'
        )
        provider = MeituanProvider()
        txns = provider.parse(_write(tmp_path, rows))

        assert len(txns) == 1
        tx = txns[0]
        assert tx.amount == Decimal("100.00")
        assert tx.currency == "CNY"
        assert tx.payee == "测试餐厅"  # prefix before first "-"
        assert tx.description == "代金券"  # remainder; merchant not repeated
        assert tx.card_last4 == "8888"
        assert tx.order_id == "TX1000000001"
        assert tx.date == date(2030, 1, 15)
        assert tx.time == time(12, 0, 5)  # uses success time
        assert tx.provider == "meituan"
        assert tx.metadata["tx_type"] == "支付"
        assert tx.metadata["merchant_id"] == "MC2000000001"
        # "/" remarks are dropped, order == paid so no order_amount
        assert "remarks" not in tx.metadata
        assert "order_amount" not in tx.metadata

    def test_refund_is_negative(self, tmp_path: Path) -> None:
        """退款/收入 rows become negative (income)."""
        rows = (
            "2030-01-16 09:00:00,2030-01-16 09:00:02,退款,测试餐厅-代金券,收入,"
            "测试银行信用卡(8888),¥30.00,¥30.00,"
            '"TX1000000002\t","MC2000000002\t",/\r\n'
        )
        txns = MeituanProvider().parse(_write(tmp_path, rows))
        assert len(txns) == 1
        assert txns[0].amount == Decimal("-30.00")
        assert txns[0].metadata["tx_type"] == "退款"

    def test_discount_records_order_amount(self, tmp_path: Path) -> None:
        """When paid differs from order amount, keep nominal order_amount."""
        rows = (
            "2030-01-17 08:00:00,2030-01-17 08:00:01,支付,测试奶茶店-果茶,支出,"
            "测试银行信用卡(8888),¥20.00,¥18.00,"
            '"TX1000000003\t","MC2000000003\t",/\r\n'
        )
        txns = MeituanProvider().parse(_write(tmp_path, rows))
        assert len(txns) == 1
        assert txns[0].amount == Decimal("18.00")
        assert txns[0].payee == "测试奶茶店"
        assert txns[0].description == "果茶"  # merchant stripped from description
        assert txns[0].metadata["order_amount"] == "20.00"

    def test_methods_without_card(self, tmp_path: Path) -> None:
        """ApplePay / 云闪付 / 微信支付 have no card suffix."""
        rows = (
            "2030-01-18 10:00:00,2030-01-18 10:00:01,支付,测试商品A,支出,"
            "ApplePay,¥50.00,¥50.00,"
            '"TX1000000004\t","MC2000000004\t",/\r\n'
            "2030-01-18 11:00:00,2030-01-18 11:00:01,支付,测试话费充值,支出,"
            "云闪付,¥200.00,¥200.00,"
            '"TX1000000005\t","MC2000000005\t",/\r\n'
            "2030-01-18 12:00:00,2030-01-18 12:00:01,支付,测试商品B,支出,"
            "微信支付,¥1.50,¥1.50,"
            '"TX1000000006\t","MC2000000006\t",/\r\n'
        )
        txns = MeituanProvider().parse(_write(tmp_path, rows))
        assert len(txns) == 3
        assert all(tx.card_last4 is None for tx in txns)
        assert txns[0].metadata["method"] == "ApplePay"

    def test_payee_empty_without_separator(self, tmp_path: Path) -> None:
        """Titles without '-' leave payee empty; description keeps full title."""
        rows = (
            "2030-01-21 10:00:00,2030-01-21 10:00:01,支付,测试话费充值200元,支出,"
            "云闪付,¥200.00,¥200.00,"
            '"TX1000000011\t","MC2000000011\t",/\r\n'
        )
        txns = MeituanProvider().parse(_write(tmp_path, rows))
        assert txns[0].payee is None
        assert txns[0].description == "测试话费充值200元"

    def test_success_time_fallback_to_create_time(self, tmp_path: Path) -> None:
        """Empty success time falls back to creation time."""
        rows = (
            "2030-01-22 08:30:00,,支付,测试餐厅-套餐,支出,"
            "测试银行信用卡(8888),¥60.00,¥60.00,"
            '"TX1000000012\t","MC2000000012\t",/\r\n'
        )
        txns = MeituanProvider().parse(_write(tmp_path, rows))
        assert txns[0].time == time(8, 30, 0)
        assert txns[0].date == date(2030, 1, 22)

    def test_skip_neutral_and_empty(self, tmp_path: Path) -> None:
        """不计收支 rows and blank/short rows are skipped."""
        rows = (
            "2030-01-19 10:00:00,2030-01-19 10:00:01,充值,测试充值,不计收支,"
            "测试银行信用卡(8888),¥100.00,¥100.00,"
            '"TX1000000007\t","MC2000000007\t",/\r\n'
            "\r\n"
            "短行,无效\r\n"
        )
        txns = MeituanProvider().parse(_write(tmp_path, rows))
        assert txns == []

    def test_statement_period_date_only(self, tmp_path: Path) -> None:
        """Meituan header uses date-only statement period."""
        rows = (
            "2030-01-15 12:00:01,2030-01-15 12:00:05,支付,测试餐厅,支出,"
            "测试银行信用卡(8888),¥100.00,¥100.00,"
            '"TX1000000008\t","MC2000000008\t",/\r\n'
        )
        txns = MeituanProvider().parse(_write(tmp_path, rows))
        assert txns[0].statement_period == (date(2030, 1, 1), date(2030, 1, 31))

    def test_empty_statement(self, tmp_path: Path) -> None:
        """Header-only file returns no transactions."""
        assert MeituanProvider().parse(_write(tmp_path, "")) == []

    def test_thousand_separator_amount(self, tmp_path: Path) -> None:
        """Amounts with thousand separators parse correctly."""
        rows = (
            "2030-01-20 10:00:00,2030-01-20 10:00:01,支付,测试大额消费,支出,"
            '测试银行信用卡(8888),"¥1,549.00","¥1,549.00",'
            '"TX1000000009\t","MC2000000009\t",/\r\n'
        )
        txns = MeituanProvider().parse(_write(tmp_path, rows))
        assert txns[0].amount == Decimal("1549.00")

    def test_detection(self, tmp_path: Path) -> None:
        """Filename and content keywords detect Meituan statements."""
        rows = (
            "2030-01-15 12:00:01,2030-01-15 12:00:05,支付,测试餐厅,支出,"
            "测试银行信用卡(8888),¥100.00,¥100.00,"
            '"TX1000000010\t","MC2000000010\t",/\r\n'
        )
        sample = _write(tmp_path, rows)
        assert MeituanProvider.can_handle(sample)
