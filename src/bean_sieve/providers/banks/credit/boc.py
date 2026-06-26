"""Bank of China (中国银行) credit card statement provider."""

from __future__ import annotations

import calendar
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from ....core.types import Transaction
from ... import register_provider
from ...base import BaseProvider

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@register_provider
class BOCCreditProvider(BaseProvider):
    """
    Provider for Bank of China (中国银行) credit card PDF statements.

    Parses PDF files containing consolidated credit card statements.

    File format:
    - PDF with text blocks
    - Transaction table columns: 交易日, 银行记账日, 卡号后四位, 交易描述, 存入, 支出
    - Multiple cards may appear in one statement (合并账单)
    - Each card requires separate repayment (按卡管理)
    """

    provider_id = "boc_credit"
    provider_name = "中国银行信用卡"
    supported_formats = [".pdf"]
    filename_keywords = ["中国银行"]
    content_keywords = ["中国银行信用卡账单", "信用卡账单"]
    per_card_statement = True  # BOC manages cards separately

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse BOC credit card PDF statement."""
        import fitz

        doc = fitz.open(file_path)
        transactions: list[Transaction] = []

        statement_period = self._extract_statement_period(doc, file_path)

        # Track current card
        current_card: str | None = None
        row_counter = 0

        for page_num in range(doc.page_count):
            page = doc[page_num]
            blocks = list(page.get_text("blocks"))

            # First pass: detect card number and transaction section boundaries
            in_transaction_section = False
            trans_start_y: float | None = None
            trans_end_y: float | None = None

            for block in blocks:
                y0 = float(block[1])
                content = str(block[4]).strip()
                block_type = int(block[6])

                if block_type != 0:
                    continue

                # Detect card number
                card_match = re.search(r"\(卡号[：:]\s*(\d+)\)", content)
                if card_match:
                    current_card = card_match.group(1)[-4:]

                # Detect transaction section start (after "Expenditure" header)
                if content == "Expenditure":
                    in_transaction_section = True
                    trans_start_y = y0 + 30  # Start after header row
                    continue

                # Detect transaction section end
                if in_transaction_section and (
                    "Loyalty Plan" in content or "积分奖励计划" in content
                ):
                    trans_end_y = y0
                    break

            if not current_card or trans_start_y is None:
                continue

            # Second pass: collect transaction blocks within the section
            trans_blocks: list[tuple[float, float, float, str]] = []
            for block in blocks:
                y0 = float(block[1])
                x1 = float(block[2])
                content = str(block[4]).strip()
                block_type = int(block[6])

                if block_type != 0 or not content:
                    continue

                # Check if within transaction section
                if y0 < trans_start_y:
                    continue
                if trans_end_y and y0 >= trans_end_y:
                    continue

                trans_blocks.append((y0, x1, float(block[0]), content))

            # Group blocks by y-coordinate (same row)
            rows = self._group_by_row(trans_blocks)

            # Parse each row
            for row_blocks in rows:
                txn = self._parse_transaction_row(
                    row_blocks,
                    current_card,
                    file_path,
                    row_counter,
                    statement_period,
                )
                if txn:
                    row_counter += 1
                    transactions.append(txn)

        doc.close()

        # Delayed-settlement entries can fall before the computed cycle start
        if transactions and statement_period:
            min_date = min(t.date for t in transactions)
            if min_date < statement_period[0]:
                for t in transactions:
                    t.statement_period = (min_date, statement_period[1])

        return transactions

    def _group_by_row(
        self, blocks: list[tuple[float, float, float, str]], tolerance: float = 20
    ) -> list[list[tuple[float, float, str]]]:
        """Group blocks into transaction rows, anchored on the date column.

        Each transaction row begins with a block whose first line is the
        transaction date (交易日 column). Such blocks seed the rows; every other
        fragment (e.g. a description wrapping onto a second visual line) is
        attached to the nearest anchor by y-coordinate.

        Anchoring is used instead of a flat y-tolerance because consecutive rows
        can be closer together than a description's own line spacing: a blanket
        tolerance wide enough to absorb a wrapped description would also merge
        two adjacent rows, dropping a transaction. When no date anchors are
        present (e.g. synthetic input), fall back to proximity grouping.
        """
        if not blocks:
            return []

        anchors: list[tuple[float, float, float, str]] = []
        others: list[tuple[float, float, float, str]] = []
        for block in blocks:
            first_line = block[3].split("\n", 1)[0].strip()
            (anchors if _DATE_RE.match(first_line) else others).append(block)

        if not anchors:
            return self._group_by_proximity(blocks, tolerance)

        anchors.sort(key=lambda b: b[0])
        anchor_ys = [b[0] for b in anchors]
        rows: list[list[tuple[float, float, str]]] = [
            [(b[1], b[2], b[3])] for b in anchors
        ]

        for y0, x1, x0, content in others:
            idx = min(enumerate(anchor_ys), key=lambda t: abs(t[1] - y0))[0]
            rows[idx].append((x1, x0, content))

        return rows

    def _group_by_proximity(
        self, blocks: list[tuple[float, float, float, str]], tolerance: float
    ) -> list[list[tuple[float, float, str]]]:
        """Group blocks by y-coordinate proximity (fallback)."""
        # Sort by y, then x
        sorted_blocks = sorted(blocks, key=lambda b: (b[0], b[2]))

        rows: list[list[tuple[float, float, str]]] = []
        current_row: list[tuple[float, float, str]] = []
        current_y: float | None = None

        for y0, x1, x0, content in sorted_blocks:
            if current_y is None or abs(y0 - current_y) > tolerance:
                if current_row:
                    rows.append(current_row)
                current_row = [(x1, x0, content)]
                current_y = y0
            else:
                current_row.append((x1, x0, content))

        if current_row:
            rows.append(current_row)

        return rows

    def _parse_transaction_row(
        self,
        row_blocks: list[tuple[float, float, str]],
        current_card: str,
        file_path: Path,
        row_idx: int,
        statement_period: tuple[date, date] | None,
    ) -> Transaction | None:
        """Parse a single transaction row from blocks."""
        # Sort blocks by x position
        sorted_blocks = sorted(row_blocks, key=lambda b: b[1])

        trans_date: str | None = None
        post_date: str | None = None
        card_last4: str | None = None
        description_parts: list[str] = []
        deposit: str = ""
        expense: str = ""

        for x1, _, content in sorted_blocks:
            lines = content.split("\n")
            for line in lines:
                line = line.strip()
                if not line:
                    continue

                # Try to match date pattern
                if re.match(r"^\d{4}-\d{2}-\d{2}$", line):
                    if not trans_date:
                        trans_date = line
                    elif not post_date:
                        post_date = line
                    continue

                # Try to match card number (4 digits)
                if re.match(r"^\d{4}$", line):
                    card_last4 = line
                    continue

                # Try to match amount
                amount_match = re.match(r"^[\d,]+\.\d{2}$", line)
                if amount_match:
                    amount_str = line.replace(",", "")
                    # x1 > 500 indicates expense column
                    # x1 between 400-500 indicates deposit column
                    if x1 > 500:
                        expense = amount_str
                    else:
                        deposit = amount_str
                    continue

                # Otherwise it's description
                description_parts.append(line)

        # Validate required fields
        if not trans_date:
            return None

        description = "".join(description_parts).strip()
        if not description:
            return None

        # Determine amount and sign
        if expense:
            amount = Decimal(expense)
        elif deposit:
            amount = -Decimal(deposit)
        else:
            return None

        parsed_date = self._parse_date(trans_date)
        if not parsed_date:
            return None

        return Transaction(
            date=parsed_date,
            post_date=self._parse_date(post_date) if post_date else None,
            amount=amount,
            currency="CNY",
            description=description,
            card_last4=card_last4 or current_card,
            provider=self.provider_id,
            source_file=file_path,
            source_line=row_idx + 1,
            statement_period=statement_period,
            metadata={
                "original_trans_date": trans_date,
                "original_post_date": post_date or "",
            },
        )

    def _extract_statement_period(
        self, doc, file_path: Path
    ) -> tuple[date, date] | None:
        """Extract statement period.

        BOC's closing day varies per cardholder, so the actual Statement
        Closing Date must be read from the PDF; the filename only encodes
        the statement month, not the cycle boundaries.
        """
        closing_date = self._find_closing_date(doc)
        if closing_date:
            return (self._months_back(closing_date, 1), closing_date)

        filename_match = re.search(r"(\d{4})年(\d{2})月", file_path.name)
        if filename_match:
            year = int(filename_match.group(1))
            month = int(filename_match.group(2))
            start = date(year - 1, 12, 1) if month == 1 else date(year, month - 1, 1)
            end = date(year, month, calendar.monthrange(year, month)[1])
            return (start, end)

        return None

    @staticmethod
    def _months_back(d: date, months: int) -> date:
        """Subtract whole months, clamping the day to the target month's length."""
        total = d.year * 12 + (d.month - 1) - months
        year, month = divmod(total, 12)
        month += 1
        last_day = calendar.monthrange(year, month)[1]
        return date(year, month, min(d.day, last_day))

    def _find_closing_date(self, doc) -> date | None:
        """Locate the Statement Closing Date on page 1."""
        if doc.page_count == 0:
            return None

        # Summary block layout: "payment_due_date\nclosing_date\n..."
        for block in doc[0].get_text("blocks"):
            content = str(block[4])
            lines = [ln.strip() for ln in content.split("\n") if ln.strip()]
            if (
                len(lines) >= 2
                and _DATE_RE.match(lines[0])
                and _DATE_RE.match(lines[1])
            ):
                due = self._parse_date(lines[0])
                closing = self._parse_date(lines[1])
                if due and closing and closing < due:
                    return closing
        return None

    def _parse_date(self, date_str: str | None) -> date | None:
        """Parse date string in YYYY-MM-DD format."""
        if not date_str:
            return None
        try:
            parts = date_str.split("-")
            return date(int(parts[0]), int(parts[1]), int(parts[2]))
        except (ValueError, IndexError):
            return None

    @classmethod
    def _match_content(cls, file_path: Path) -> bool:
        """Check if PDF content contains BOC credit card keywords."""
        try:
            import fitz

            doc = fitz.open(file_path)
            if doc.page_count > 0:
                text = doc[0].get_text()[:1000]
                doc.close()
                return any(kw in text for kw in cls.content_keywords)
            doc.close()
        except Exception:
            pass
        return False
