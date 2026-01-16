"""Bank of Shanghai (上海银行) credit card statement provider."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from ....core.types import Transaction
from ... import register_provider
from ...base import BaseProvider


@register_provider
class BOSCCreditProvider(BaseProvider):
    """
    Provider for Bank of Shanghai (上海银行) credit card email statements.

    Parses .eml files containing quoted-printable encoded HTML statements.
    """

    provider_id = "bosc_credit"
    provider_name = "上海银行信用卡"
    supported_formats = [".eml"]
    filename_keywords = ["上海银行信用卡"]
    content_keywords = ["上海银行信用卡电子对账单", "bosc.cn"]

    def parse(self, file_path: Path) -> list[Transaction]:
        """解析上海银行信用卡电子对账单邮件"""
        html = self.extract_html_from_eml(file_path)
        soup = self.parse_html(html)

        statement_period = self._extract_statement_period(soup)
        return self._parse_transactions(soup, statement_period, file_path)

    def _extract_statement_period(self, soup) -> tuple[date, date] | None:
        """从账单中提取对账日期范围"""
        # 查找格式为 "2025年11月19日-2025年12月18日" 的日期范围
        text = soup.get_text()
        pattern = r"(\d{4})年(\d{1,2})月(\d{1,2})日-(\d{4})年(\d{1,2})月(\d{1,2})日"
        match = re.search(pattern, text)
        if match:
            start_date = date(
                int(match.group(1)), int(match.group(2)), int(match.group(3))
            )
            end_date = date(
                int(match.group(4)), int(match.group(5)), int(match.group(6))
            )
            return (start_date, end_date)
        return None

    def _parse_transactions(
        self, soup, statement_period: tuple[date, date] | None, file_path: Path
    ) -> list[Transaction]:
        """从HTML中解析交易记录"""
        transactions = []

        # 查找所有带 loop2 属性的 tr 标签 (交易明细行)
        rows = soup.find_all("tr", attrs={"loop2": True})

        for row in rows:
            txn = self._parse_transaction_row(row, statement_period, file_path)
            if txn:
                transactions.append(txn)

        return transactions

    def _parse_transaction_row(
        self,
        row,
        statement_period: tuple[date, date] | None,
        file_path: Path,
    ) -> Transaction | None:
        """解析单行交易记录"""
        cells = row.find_all("td")
        if len(cells) < 5:
            return None

        # 提取各列文本
        trans_date_str = self.clean_text(cells[0].get_text())
        post_date_str = self.clean_text(cells[1].get_text())
        description = self.clean_text(cells[2].get_text())
        amount_str = self.clean_text(cells[3].get_text())
        card_last4 = self.clean_text(cells[4].get_text())

        # 跳过非交易行 (如 "人民币账户", "上期余额" 等)
        if not trans_date_str or not re.match(
            r"\d{4}年\d{1,2}月\d{1,2}日", trans_date_str
        ):
            return None

        # 解析日期
        trans_date = self._parse_date(trans_date_str)
        post_date = self._parse_date(post_date_str)
        if not trans_date:
            return None

        # 解析金额 (格式: "130.13+" 或 "0.04-")
        amount = self._parse_amount(amount_str)
        if amount is None:
            return None

        return Transaction(
            date=trans_date,
            post_date=post_date,
            amount=amount,
            currency="CNY",
            description=description,
            card_last4=card_last4 if card_last4 else None,
            provider=self.provider_id,
            source_file=file_path,
            statement_period=statement_period,
        )

    def _parse_date(self, date_str: str) -> date | None:
        """解析日期字符串 (格式: 2025年11月29日)"""
        match = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", date_str)
        if match:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        return None

    def _parse_amount(self, amount_str: str) -> Decimal | None:
        """
        解析金额字符串

        格式: "130.13+" 表示支出 (正数), "0.04-" 表示退款/收入 (负数)
        """
        # 移除空格和逗号
        amount_str = amount_str.replace(" ", "").replace(",", "")

        # 匹配金额和符号
        match = re.match(r"([\d.]+)([+-])", amount_str)
        if not match:
            return None

        try:
            value = Decimal(match.group(1))
            sign = match.group(2)
            # + 表示支出 (正), - 表示退款/收入 (负)
            return value if sign == "+" else -value
        except Exception:
            return None
