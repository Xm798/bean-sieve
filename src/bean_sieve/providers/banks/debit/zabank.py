"""ZA Bank (众安银行) debit account statement provider."""

from __future__ import annotations

import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ....core.types import ReconcileContext, Transaction
from ... import register_provider
from ...base import BaseProvider

logger = logging.getLogger(__name__)

MONTH_MAP = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}

# Currency section markers and their mapped currency codes
SECTION_MARKERS: dict[str, str] = {
    "HKD Savings 港元活期储蓄": "HKD",
    "CNY 人民币": "CNY",
    "USD 美元": "USD",
    "EUR 欧元": "EUR",
    "GBP 英镑": "GBP",
    "JPY 日元": "JPY",
}


@register_provider
class ZABankProvider(BaseProvider):
    """
    Provider for ZA Bank (众安银行) consolidated monthly PDF statements.

    File format:
    - PDF with multiple currency sections (HKD, CNY, USD, etc.)
    - Each section has: Date, Transaction Details, Deposit, Withdrawal, Balance
    - Statement period in header: "01 Mar 2026 - 31 Mar 2026"
    - Multi-line transaction details (currency exchange includes rate and txn number)
    - "Opening balance 上期结余" rows should be skipped
    """

    provider_id = "zabank_debit"
    provider_name = "众安银行"
    supported_formats = [".pdf"]
    filename_keywords = ["zabank", "众安", "ZA Bank"]
    content_keywords = ["ZA Bank", "众安银行"]

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse ZA Bank consolidated monthly PDF statement."""
        import fitz

        doc = fitz.open(file_path)
        statement_period = self._extract_statement_period(doc)
        transactions: list[Transaction] = []
        row_counter = 0
        last_currency: str | None = None

        for page_num in range(doc.page_count):
            page = doc[page_num]
            section_positions = self._find_section_positions(page)

            # Always prepend carried-forward currency at top of page
            # so tables before the first section marker get the right currency
            if last_currency:
                section_positions.insert(0, (0.0, last_currency))

            table_finder = page.find_tables()
            if table_finder is None:
                continue
            for table in table_finder.tables:
                table_y = table.bbox[1]

                # Skip the deposit summary table
                if page_num == 0 and self._is_summary_table(table):
                    continue

                currency = self._get_currency_for_position(table_y, section_positions)
                if not currency:
                    continue

                last_currency = currency

                for row in table.extract():
                    txn = self._parse_row(
                        row, row_counter, currency, file_path, statement_period
                    )
                    if txn:
                        transactions.append(txn)
                        row_counter += 1

        doc.close()
        return transactions

    def pre_reconcile(
        self,
        transactions: list[Transaction],
        context: ReconcileContext,  # noqa: ARG002
    ) -> list[Transaction]:
        """Merge paired currency exchange transactions into single entries with @@ price."""
        return self._merge_exchange_pairs(transactions)

    @staticmethod
    def _merge_exchange_pairs(
        transactions: list[Transaction],
    ) -> list[Transaction]:
        """Find exchange pairs by order_id and merge into single multi-currency entries.

        Each currency exchange appears as two transactions (one per currency section).
        We keep the "sell" side (positive amount = withdrawal) and annotate it with
        the buy amount as price, so the output uses @@ syntax.
        """
        # Group exchange transactions by order_id
        exchange_by_id: dict[str, list[Transaction]] = {}
        result: list[Transaction] = []

        for txn in transactions:
            if txn.order_id and "exchange" in txn.description.lower():
                exchange_by_id.setdefault(txn.order_id, []).append(txn)
            else:
                result.append(txn)

        for _order_id, group in exchange_by_id.items():
            if len(group) != 2:
                # Unpaired — keep as-is
                result.extend(group)
                continue

            # Identify sell (withdrawal=positive) and buy (deposit=negative) sides
            sell = next((t for t in group if t.amount > 0), None)
            buy = next((t for t in group if t.amount < 0), None)
            if not sell or not buy:
                result.extend(group)
                continue

            # Merge: keep sell side, annotate with buy amount as price
            result.append(
                sell.model_copy(
                    update={
                        "price_amount": abs(buy.amount),
                        "price_currency": buy.currency,
                        "description": f"Currency Exchange ({sell.currency}→{buy.currency})",
                    }
                )
            )

        # Sort by date to maintain chronological order
        result.sort(key=lambda t: t.date)
        return result

    @staticmethod
    def _find_section_positions(page) -> list[tuple[float, str]]:
        """Find currency section markers and their y-positions on a page."""
        positions: list[tuple[float, str]] = []
        blocks: list = page.get_text("blocks")  # type: ignore[assignment]

        for block in blocks:
            content = str(block[4]).strip()
            y0 = float(block[1])
            for marker, currency in SECTION_MARKERS.items():
                if marker in content:
                    positions.append((y0, currency))
                    break

        positions.sort(key=lambda x: x[0])
        return positions

    @staticmethod
    def _is_summary_table(table) -> bool:
        """Check if table is the deposit summary (not transaction data).

        Summary tables have no date in the first column and contain
        "Savings" with a percentage pattern like "(0.20%)".
        """
        data = table.extract()
        if not data:
            return False
        first_col = str(data[0][0] or "").strip()
        # Transaction tables have a date in column 0; summary tables don't
        if re.match(r"\d{1,2}\s+\w{3}\s+\d{4}", first_col):
            return False
        first_row_text = " ".join(str(cell) for cell in data[0] if cell)
        return bool(
            re.search(r"Savings.*\([\d.]+%\)|储蓄.*\([\d.]+%\)", first_row_text)
        )

    @staticmethod
    def _get_currency_for_position(
        y_pos: float, section_positions: list[tuple[float, str]]
    ) -> str | None:
        """Find the currency for a given y-position based on section markers."""
        currency = None
        for marker_y, cur in section_positions:
            if marker_y <= y_pos + 5:
                currency = cur
            else:
                break
        return currency

    def _parse_row(
        self,
        row: list,
        row_idx: int,
        currency: str,
        file_path: Path,
        statement_period: tuple[date, date] | None,
    ) -> Transaction | None:
        """Parse a single table row into a Transaction."""
        if not row or len(row) < 5:
            return None

        date_str = (row[0] or "").strip()
        details = (row[1] or "").strip()
        deposit_str = (row[2] or "").strip()
        withdrawal_str = (row[3] or "").strip()

        if not date_str or not details:
            return None
        if "Opening balance" in details or "上期结余" in details:
            return None

        tx_date = self._parse_date(date_str)
        if tx_date is None:
            return None

        amount = self._parse_amount(deposit_str, withdrawal_str)
        if amount is None:
            return None

        # Parse multi-line details
        lines = details.split("\n")
        description = lines[0].strip()
        detail_lines = [line.strip() for line in lines[1:] if line.strip()]

        order_id = self._extract_order_id(detail_lines)
        exchange_info = self._extract_exchange_info(detail_lines)
        counterparty = self._extract_counterparty(detail_lines)

        metadata: dict[str, str] = {}
        if exchange_info:
            metadata["exchange_info"] = exchange_info
        if counterparty:
            metadata["counterparty"] = counterparty
        balance = (row[4] or "").strip()
        if balance:
            metadata["balance"] = balance

        return Transaction(
            date=tx_date,
            amount=amount,
            currency=currency,
            description=description,
            payee=counterparty,
            order_id=order_id,
            card_last4=currency,  # used as account mapping key
            provider=self.provider_id,
            source_file=file_path,
            source_line=row_idx + 1,
            statement_period=statement_period,
            metadata=metadata,
        )

    @staticmethod
    def _parse_date(date_str: str) -> date | None:
        """Parse date from 'dd Mon yyyy' format (e.g., '28 Mar 2026')."""
        match = re.match(r"(\d{1,2})\s+(\w{3})\s+(\d{4})", date_str)
        if not match:
            return None
        day = int(match.group(1))
        month = MONTH_MAP.get(match.group(2))
        year = int(match.group(3))
        if month is None:
            return None
        try:
            return date(year, month, day)
        except ValueError:
            return None

    @staticmethod
    def _parse_amount(deposit_str: str, withdrawal_str: str) -> Decimal | None:
        """Parse amount. Withdrawal=positive (expense), Deposit=negative (income)."""
        try:
            if withdrawal_str:
                return Decimal(withdrawal_str.replace(",", ""))
            if deposit_str:
                return -Decimal(deposit_str.replace(",", ""))
        except (InvalidOperation, ValueError):
            return None
        return None

    @staticmethod
    def _extract_order_id(detail_lines: list[str]) -> str | None:
        """Extract transaction number from detail lines."""
        for i, line in enumerate(detail_lines):
            if "Transaction number" in line or "交易编号" in line:
                after_colon = line.split(":")[-1].strip() if ":" in line else ""
                if after_colon and after_colon[0].isdigit():
                    return after_colon
                if (
                    i + 1 < len(detail_lines)
                    and detail_lines[i + 1]
                    and detail_lines[i + 1][0].isdigit()
                ):
                    return detail_lines[i + 1]
        return None

    @staticmethod
    def _extract_exchange_info(detail_lines: list[str]) -> str | None:
        """Extract sell/buy info from currency exchange details."""
        for line in detail_lines:
            if line.startswith("sell") or line.startswith("sell卖出"):
                return line
        return None

    @staticmethod
    def _extract_counterparty(detail_lines: list[str]) -> str | None:
        """Extract counterparty name from transfer detail lines."""
        for line in detail_lines:
            if any(
                kw in line
                for kw in [
                    "货币兑换",
                    "sell",
                    "Exchange rate",
                    "Transaction number",
                    "消费冲正",
                ]
            ):
                continue
            if line and line[0].isdigit():
                continue
            if re.match(r"^[A-Z\u4e00-\u9fff]", line) and "*" in line:
                return line
        return None

    def _extract_statement_period(self, doc) -> tuple[date, date] | None:
        """Extract statement period from PDF header."""
        if doc.page_count < 1:
            return None
        text = doc[0].get_text()[:500]
        match = re.search(
            r"(\d{1,2}\s+\w{3}\s+\d{4})\s*-\s*(\d{1,2}\s+\w{3}\s+\d{4})", text
        )
        if match:
            start = self._parse_date(match.group(1))
            end = self._parse_date(match.group(2))
            if start and end:
                return (start, end)
        return None

    @classmethod
    def _match_content(cls, file_path: Path) -> bool:
        """Check if PDF content contains ZA Bank keywords."""
        try:
            import fitz

            doc = fitz.open(file_path)
            if doc.page_count > 0:
                text = doc[0].get_text()[:1000]
                doc.close()
                return "ZA Bank" in text or "众安银行" in text
            doc.close()
        except Exception:
            pass
        return False
