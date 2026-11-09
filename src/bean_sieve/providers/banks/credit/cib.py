"""Industrial Bank (兴业银行) credit card statement provider."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ....core.types import Transaction
from ... import register_provider
from ...base import BaseProvider


@register_provider
class CIBCreditProvider(BaseProvider):
    """
    Industrial Bank email statements: GBK HTML, one `detail_table_N` per card.

    Amounts are printed in the internal sign convention (positive = expense,
    negative = payment or refund).
    """

    provider_id = "cib_credit"
    provider_name = "兴业银行信用卡"
    supported_formats = [".eml"]
    filename_keywords = ["兴业银行", "兴业信用卡"]
    content_keywords = ["兴业银行信用卡"]
    per_card_statement = True  # CIB sends separate statements per card

    def parse(self, file_path: Path) -> list[Transaction]:
        html = self.extract_html_from_eml(file_path)
        soup = self.parse_html(html)

        statement_period = self._extract_statement_period(soup)

        transactions: list[Transaction] = []

        detail_tables = soup.find_all("table", id=re.compile(r"detail_table_\d+"))

        for table in detail_tables:
            card_last4 = self._extract_card_from_table(table)

            rows = table.find_all("tr", id=re.compile(r"detail_tr_\d+"))
            for idx, row in enumerate(rows):
                txn = self._parse_row(row, card_last4, file_path, idx + 1)
                if txn:
                    transactions.append(txn)

        self.assign_statement_periods(transactions, statement_period)
        return transactions

    def _extract_statement_period(self, soup) -> tuple[date, date] | None:
        text = soup.get_text()
        match = re.search(r"(\d{4})/(\d{2})/(\d{2})-(\d{4})/(\d{2})/(\d{2})", text)
        if match:
            start = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            end = date(int(match.group(4)), int(match.group(5)), int(match.group(6)))
            return (start, end)
        match = re.search(r"(\d{4})-(\d{2})-(\d{2})至(\d{4})-(\d{2})-(\d{2})", text)
        if match:
            start = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            end = date(int(match.group(4)), int(match.group(5)), int(match.group(6)))
            return (start, end)
        return None

    def _extract_card_from_table(self, table) -> str | None:
        for td in table.find_all("td"):
            text = td.get_text()
            match = re.search(r"卡号末四位\s*(\d{4})", text)
            if match:
                return match.group(1)
        return None

    def _parse_row(
        self,
        row,
        card_last4: str | None,
        file_path: Path,
        row_idx: int,
    ) -> Transaction | None:
        try:
            cells = row.find_all("td")
            if len(cells) < 5:
                return None

            tdate_span = row.find("span", id=re.compile(r"detail_tdate_\d+"))
            adate_span = row.find("span", id=re.compile(r"detail_adate_\d+"))
            desc_span = row.find("span", id=re.compile(r"detail_desc1_\d+"))
            amt_span = row.find("span", id=re.compile(r"detail_tamt_\d+"))

            if not all([tdate_span, desc_span, amt_span]):
                return None

            trans_date_str = tdate_span.get_text(strip=True)
            post_date_str = adate_span.get_text(strip=True) if adate_span else None
            description = desc_span.get_text(strip=True)
            amount_str = amt_span.get_text(strip=True)

            # A trans date may carry an HH:MM suffix, which
            # date.fromisoformat() rejects.
            trans_date = date.fromisoformat(trans_date_str.split()[0])
            post_date = (
                date.fromisoformat(post_date_str.split()[0]) if post_date_str else None
            )

            amount = self._parse_amount(amount_str)
            if amount is None:
                return None

            return Transaction(
                date=trans_date,
                post_date=post_date,
                amount=amount,
                currency="CNY",
                description=description,
                card_last4=card_last4,
                provider=self.provider_id,
                source_file=file_path,
                source_line=row_idx,
                metadata={
                    "original_date": trans_date_str,
                },
            )
        except (ValueError, AttributeError):
            return None

    def _parse_amount(self, amount_str: str) -> Decimal | None:
        try:
            cleaned = amount_str.replace(",", "").strip()
            return Decimal(cleaned)
        except InvalidOperation:
            return None
