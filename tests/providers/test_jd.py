"""Tests for JD.com payment provider."""

from decimal import Decimal
from pathlib import Path

from bean_sieve.providers.payment.jd import JDProvider


class TestJDProvider:
    """Tests for JDProvider."""

    def test_parse_basic(self, tmp_path: Path) -> None:
        """Test basic parsing functionality."""
        # Create sample JD statement file
        sample_file = tmp_path / "jd_statement.csv"
        sample_content = """导出信息：
京东账号名：jd_test
申请时间：2026-02-08 16:47:11
日期区间：2026-01-08 至 2026-02-08
导出交易类型：全部
导出交易场景：全部
共：3笔记录
收入：0笔，0.00元
支出：2笔，200.00元
不计收支：1笔，100.00元

特别提示
1.本明细为每笔订单支付的明细，不包括已删除的记录；如需计算白条相关费用明细，请访问"白条-查账还款"进行查看；
2.个人资金互转、全额退款等记为【不计收支】类，部分退款的支出金额为剔除退款后的支付金额；
3.因系统原因或通讯故障等偶发因素导致本明细与实际交易结果不符时，以实际交易情况为准；
4.因统计逻辑不同，明细金额直接累加后，可能会和上方统计金额不一致，请以实际交易金额为准；
5.京东快捷支付等非余额支付方式可能既产生京东交易也同步产生银行交易，因此请勿使用本回单进行重复记账；
6.明细如经任何涂改、编造，均立即失去效力；
7.禁止将本回单用于非法用途；
8.本明细仅供个人对账使用。

交易时间,商户名称,交易说明,金额,收/付款方式,交易状态,收/支,交易分类,交易订单号,商家订单号,备注
2026-02-08 16:08:45\t,京东平台商户,办公用品购买,128.82,数字人民币-银行A钱包(1234),交易成功,支出,办公用品,100000001\t,200000001\t,
2026-01-23 23:19:28\t,京东平台商户,图书购买,71.18,数字人民币-银行B钱包(5678),交易成功,支出,图书音像,100000002\t,200000002\t,
2026-01-23 23:18:11\t,京东平台商户,退款-商品退货,100.00,某银行信用卡(9999),退款成功,不计收支,退款,100000003\t,200000003\t,
"""
        sample_file.write_text(sample_content, encoding="utf-8")

        provider = JDProvider()
        transactions = provider.parse(sample_file)

        # Should parse 2 transactions (2 支出), skip 1 不计收支
        assert len(transactions) == 2

        # Check first transaction
        tx1 = transactions[0]
        assert tx1.amount == Decimal("128.82")
        assert tx1.currency == "CNY"
        assert tx1.description == "办公用品购买"
        assert tx1.payee == "京东平台商户"
        assert tx1.card_last4 == "1234"
        assert tx1.order_id == "100000001"
        assert tx1.date.year == 2026
        assert tx1.date.month == 2
        assert tx1.date.day == 8
        assert tx1.time is not None
        assert tx1.time.hour == 16
        assert tx1.time.minute == 8

        # Check metadata
        assert tx1.metadata["payment_method"] == "数字人民币-银行A钱包(1234)"
        assert tx1.metadata["transaction_type"] == "支出"
        assert tx1.metadata["transaction_status"] == "交易成功"

        # Check second transaction
        tx2 = transactions[1]
        assert tx2.amount == Decimal("71.18")
        assert tx2.card_last4 == "5678"

    def test_skip_non_payment_transactions(self, tmp_path: Path) -> None:
        """Test that 不计收支 transactions are skipped."""
        sample_file = tmp_path / "jd_refund.csv"
        sample_content = """导出信息：
京东账号名：jd_test
申请时间：2026-02-08 16:47:11
日期区间：2026-01-08 至 2026-02-08
导出交易类型：全部
导出交易场景：全部
共：1笔记录
收入：0笔，0.00元
支出：0笔，0.00元
不计收支：1笔，100.00元

特别提示
1.本明细为每笔订单支付的明细，不包括已删除的记录；如需计算白条相关费用明细，请访问"白条-查账还款"进行查看；
2.个人资金互转、全额退款等记为【不计收支】类，部分退款的支出金额为剔除退款后的支付金额；
3.因系统原因或通讯故障等偶发因素导致本明细与实际交易结果不符时，以实际交易情况为准；
4.因统计逻辑不同，明细金额直接累加后，可能会和上方统计金额不一致，请以实际交易金额为准；
5.京东快捷支付等非余额支付方式可能既产生京东交易也同步产生银行交易，因此请勿使用本回单进行重复记账；
6.明细如经任何涂改、编造，均立即失去效力；
7.禁止将本回单用于非法用途；
8.本明细仅供个人对账使用。

交易时间,商户名称,交易说明,金额,收/付款方式,交易状态,收/支,交易分类,交易订单号,商家订单号,备注
2026-01-23 23:18:11\t,京东平台商户,退款-商品退货,100.00,某银行信用卡(9999),退款成功,不计收支,退款,100000003\t,200000003\t,
"""
        sample_file.write_text(sample_content, encoding="utf-8")

        provider = JDProvider()
        transactions = provider.parse(sample_file)

        # Should skip all 不计收支 transactions
        assert len(transactions) == 0

    def test_extract_card_last4(self) -> None:
        """Test card last 4 digits extraction."""
        provider = JDProvider()

        # Test with e-CNY wallet
        assert provider._extract_card_last4("数字人民币-某银行钱包(1234)") == "1234"

        # Test with credit card
        assert provider._extract_card_last4("某银行信用卡(5678)") == "5678"

        # Test with JD Baitiao (no card number)
        assert provider._extract_card_last4("京东白条") is None

        # Test with empty string
        assert provider._extract_card_last4("") is None

    def test_income_transaction(self, tmp_path: Path) -> None:
        """Test parsing income transactions (negative amount)."""
        sample_file = tmp_path / "jd_income.csv"
        sample_content = """导出信息：
京东账号名：jd_test
申请时间：2026-02-08 16:47:11
日期区间：2026-01-08 至 2026-02-08
导出交易类型：全部
导出交易场景：全部
共：1笔记录
收入：1笔，50.00元
支出：0笔，0.00元
不计收支：0笔，0.00元

特别提示
1.本明细为每笔订单支付的明细，不包括已删除的记录；如需计算白条相关费用明细，请访问"白条-查账还款"进行查看；
2.个人资金互转、全额退款等记为【不计收支】类，部分退款的支出金额为剔除退款后的支付金额；
3.因系统原因或通讯故障等偶发因素导致本明细与实际交易结果不符时，以实际交易情况为准；
4.因统计逻辑不同，明细金额直接累加后，可能会和上方统计金额不一致，请以实际交易金额为准；
5.京东快捷支付等非余额支付方式可能既产生京东交易也同步产生银行交易，因此请勿使用本回单进行重复记账；
6.明细如经任何涂改、编造，均立即失去效力；
7.禁止将本回单用于非法用途；
8.本明细仅供个人对账使用。

交易时间,商户名称,交易说明,金额,收/付款方式,交易状态,收/支,交易分类,交易订单号,商家订单号,备注
2026-02-08 10:00:00\t,京东平台商户,退款到账,50.00,京东余额,交易成功,收入,退款,100000004\t,200000004\t,
"""
        sample_file.write_text(sample_content, encoding="utf-8")

        provider = JDProvider()
        transactions = provider.parse(sample_file)

        assert len(transactions) == 1
        # Income should be negative in bean-sieve convention
        assert transactions[0].amount == Decimal("-50.00")

    def test_can_handle(self, tmp_path: Path) -> None:
        """Test file detection."""
        # Test with matching filename
        jd_file = tmp_path / "京东交易流水(申请时间2026年01月01日00时00分00秒).csv"
        jd_file.write_text("", encoding="utf-8")
        assert JDProvider.can_handle(jd_file)

        # Test with non-matching filename
        other_file = tmp_path / "other.csv"
        other_file.write_text("", encoding="utf-8")
        assert not JDProvider.can_handle(other_file)

        # Test with wrong extension
        wrong_ext = tmp_path / "京东交易流水.txt"
        wrong_ext.write_text("", encoding="utf-8")
        assert not JDProvider.can_handle(wrong_ext)
