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
    """Bank of Shanghai email statements: a quoted-printable HTML body."""

    provider_id = "bosc_credit"
    provider_name = "上海银行信用卡"
    supported_formats = [".eml"]
    filename_keywords = ["上海银行信用卡"]
    content_keywords = ["上海银行信用卡电子对账单", "bosc.cn"]

    def parse(self, file_path: Path) -> list[Transaction]:
        html = self.extract_html_from_eml(file_path)
        soup = self.parse_html(html)

        statement_period = self._extract_statement_period(soup)
        transactions = self._parse_transactions(soup, file_path)
        self.assign_statement_periods(transactions, statement_period)
        return transactions

    def _extract_statement_period(self, soup) -> tuple[date, date] | None:
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

    def _parse_transactions(self, soup, file_path: Path) -> list[Transaction]:
        transactions = []

        # 交易明细行带 loop2 属性。
        rows = soup.find_all("tr", attrs={"loop2": True})

        for row in rows:
            txn = self._parse_transaction_row(row, file_path)
            if txn:
                transactions.append(txn)

        return transactions

    def _parse_transaction_row(self, row, file_path: Path) -> Transaction | None:
        cells = row.find_all("td")
        if len(cells) < 5:
            return None

        trans_date_str = self.clean_text(cells[0].get_text())
        post_date_str = self.clean_text(cells[1].get_text())
        description = self.clean_text(cells[2].get_text())
        amount_str = self.clean_text(cells[3].get_text())
        card_last4 = self.clean_text(cells[4].get_text())

        # "人民币账户"、"上期余额" 等非交易行的首列不是日期。
        if not trans_date_str or not re.match(
            r"\d{4}年\d{1,2}月\d{1,2}日", trans_date_str
        ):
            return None

        trans_date = self._parse_date(trans_date_str)
        post_date = self._parse_date(post_date_str)
        if not trans_date:
            return None

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
        )

    def _parse_date(self, date_str: str) -> date | None:
        match = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", date_str)
        if match:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        return None

    def _parse_amount(self, amount_str: str) -> Decimal | None:
        """方向写在金额后缀：`10.00+` 为支出，`10.00-` 为退款或收入。"""
        amount_str = amount_str.replace(" ", "").replace(",", "")

        match = re.match(r"([\d.]+)([+-])", amount_str)
        if not match:
            return None

        try:
            value = Decimal(match.group(1))
            sign = match.group(2)
            return value if sign == "+" else -value
        except Exception:
            return None
