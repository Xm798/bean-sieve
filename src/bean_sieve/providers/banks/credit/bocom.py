"""Bank of Communications (交通银行) credit card statement provider."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from ....core.metadata_keys import ORIGINAL_AMOUNT, ORIGINAL_CURRENCY
from ....core.types import Transaction
from ... import register_provider
from ..._tabular import masked_card_last4
from ...base import BaseProvider

_FULL_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CARD_ROW_RE = re.compile(r"卡号末四位\s*(\d{4})")
_SECTION_HEADING_RE = re.compile(
    r"还款、退货[、及]费用返还明细|消费、取现[、及]其他费用明细"
)


@dataclass(frozen=True)
class _Row:
    trans_date: str
    post_date: str
    card_last4: str | None
    description: str
    original_amount: str
    billed_amount: str


@register_provider
class BOCOMCreditProvider(BaseProvider):
    """
    Provider for Bank of Communications (交通银行) credit card email statements.

    Parses .eml files containing HTML statements with transaction tables.

    Transaction sections:
    - 还款、退货、费用返还明细: payments/refunds (negative amounts)
    - 消费、取现、其他费用明细: spending/cash advances (positive amounts)

    BOCOM sends two statement layouts:
    - Legacy row cells: '', 交易日期 (MM/DD), 记账日期, 卡末四位, 交易说明,
      交易金额 (original currency, e.g. "HKD41.00"), 入账金额 (billed currency,
      e.g. "CNY37.00").
    - Detail row cells: 交易日期 (YYYY-MM-DD), 记账日期, 交易说明, 交易币种/金额
      ("HKD 41.00"), 入账币种/金额. The card is given by a "卡号末四位 1234" row
      above its transactions, and only where the table groups them by card;
      otherwise the rows belong to the statement's own card. Section headings
      read "…及费用返还明细" / "…及其他费用明细".
    """

    provider_id = "bocom_credit"
    provider_name = "交通银行信用卡"
    supported_formats = [".eml"]
    filename_keywords = ["交通银行"]
    content_keywords = ["交通银行信用卡电子账单"]
    per_card_statement = True  # BOCOM sends separate statements per card

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse BOCOM credit card email statement."""
        html = self.extract_html_from_eml(file_path)
        soup = self.parse_html(html)

        # Extract statement period (e.g., 2025/10/14-2025/11/13)
        text = soup.get_text()
        statement_period = self._extract_statement_period(text)
        statement_card = masked_card_last4(text)

        transactions: list[Transaction] = []
        tables = soup.find_all("table")
        row_counter = 0

        for table in tables:
            # Only process leaf tables (no nested tables)
            if table.find_all("table"):
                continue

            # Find transaction rows in this table
            rows = table.find_all("tr")
            trans_rows: list[_Row] = []
            card_last4 = statement_card
            for row in rows:
                cells = row.find_all("td")
                cell_texts = [c.get_text(strip=True) for c in cells]
                if len(cells) >= 7 and self._is_date(cell_texts[1]):
                    trans_date, post_date, row_card, desc, orig, billed = cell_texts[
                        1:7
                    ]
                elif len(cells) == 5 and _FULL_DATE_RE.match(cell_texts[0]):
                    trans_date, post_date, desc, orig, billed = cell_texts
                    row_card = card_last4
                else:
                    if len(cells) == 1 and (
                        card_match := _CARD_ROW_RE.search(cell_texts[0])
                    ):
                        card_last4 = card_match.group(1)
                    continue
                trans_rows.append(
                    _Row(trans_date, post_date, row_card, desc, orig, billed)
                )

            if not trans_rows:
                continue

            section = self._detect_section(table)

            # Parse each transaction row
            for trans_row in trans_rows:
                row_counter += 1
                txn = self._parse_row(
                    trans_row, section, file_path, row_counter, statement_period
                )
                if txn:
                    transactions.append(txn)

        self.assign_statement_periods(transactions, statement_period)
        return transactions

    def _detect_section(self, table) -> str | None:
        """Detect section type from the nearest heading before the table."""
        heading = table.find_previous(string=_SECTION_HEADING_RE)
        if heading is None:
            return None
        return "payment" if "还款" in heading else "spending"

    def _extract_statement_period(self, text: str) -> tuple[date, date] | None:
        """Extract statement period (e.g., '2025/10/14-2025/11/13')."""
        match = re.search(r"(\d{4})/(\d{2})/(\d{2})-(\d{4})/(\d{2})/(\d{2})", text)
        if match:
            start = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            end = date(int(match.group(4)), int(match.group(5)), int(match.group(6)))
            return (start, end)
        return None

    def _is_date(self, text: str) -> bool:
        """Check if text is a date in MM/DD format."""
        return bool(re.match(r"^\d{2}/\d{2}$", text))

    def _parse_row(
        self,
        row: _Row,
        section: str | None,
        file_path: Path,
        row_idx: int,
        statement_period: tuple[date, date] | None,
    ) -> Transaction | None:
        """Parse a single transaction row."""
        try:
            trans_date = self._parse_row_date(row.trans_date, statement_period)
            post_date = self._parse_row_date(row.post_date, statement_period)
        except ValueError:
            return None

        amount, currency = self._parse_amount(row.billed_amount)
        if amount is None:
            return None

        # payment section = payments to card = negative (income for cardholder)
        amount = -abs(amount) if section == "payment" else abs(amount)

        metadata: dict = {
            "original_date": row.trans_date,
            "section": section or "unknown",
        }
        original_amount, original_currency = self._parse_amount(row.original_amount)
        if original_amount is not None and original_currency != currency:
            metadata[ORIGINAL_AMOUNT] = original_amount
            metadata[ORIGINAL_CURRENCY] = original_currency

        return Transaction(
            date=trans_date,
            post_date=post_date,
            amount=amount,
            currency=currency,
            description=row.description,
            card_last4=row.card_last4,
            provider=self.provider_id,
            source_file=file_path,
            source_line=row_idx + 1,
            metadata=metadata,
        )

    def _parse_row_date(
        self, date_str: str, statement_period: tuple[date, date] | None
    ) -> date:
        if _FULL_DATE_RE.match(date_str):
            return date.fromisoformat(date_str)
        return self._parse_date_with_period(date_str, statement_period)

    def _parse_date_with_period(
        self, date_str: str, statement_period: tuple[date, date] | None
    ) -> date:
        """Resolve a MM/DD row to a full date using the statement period.

        Rows carry no year, and the statement closes on its period end, so a row
        is the latest MM/DD falling on or before that day: a December row on a
        January statement is the previous December, and a November row on that
        same statement a settlement delayed from the November before it.
        """
        month, day = map(int, date_str.split("/"))

        if not statement_period:
            return date(date.today().year, month, day)

        end = statement_period[1]
        year = end.year if (month, day) <= (end.month, end.day) else end.year - 1
        return date(year, month, day)

    def _parse_amount(self, amount_str: str) -> tuple[Decimal | None, str]:
        """Parse amount string like 'CNY9974.12' or 'USD 100.00'."""
        match = re.match(r"([A-Z]{3})\s*([\d,]+\.?\d*)", amount_str)
        if not match:
            return None, "CNY"

        currency = match.group(1)
        amount_num = match.group(2).replace(",", "")

        try:
            return Decimal(amount_num), currency
        except Exception:
            return None, currency
