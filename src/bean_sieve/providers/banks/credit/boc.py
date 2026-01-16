"""Bank of China (中国银行) credit card statement provider."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from ....core.types import Transaction
from ... import register_provider
from ...base import BaseProvider


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

        # Extract statement period from filename or content
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
        return transactions

    def _group_by_row(
        self, blocks: list[tuple[float, float, float, str]], tolerance: float = 20
    ) -> list[list[tuple[float, float, str]]]:
        """Group blocks by y-coordinate into rows."""
        if not blocks:
            return []

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

        for x1, _x0, content in sorted_blocks:
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
        """Extract statement period from filename or content."""
        # Try filename first: 中国银行信用卡电子合并账单2025年12月账单.PDF
        filename_match = re.search(r"(\d{4})年(\d{2})月", file_path.name)
        if filename_match:
            year = int(filename_match.group(1))
            month = int(filename_match.group(2))
            # Statement month, estimate period as previous month's 5th to this month's 4th
            start = date(year - 1, 12, 5) if month == 1 else date(year, month - 1, 5)
            end = date(year, month, 4)
            return (start, end)

        # Try to find statement date in content
        if doc.page_count > 0:
            text = doc[0].get_text()
            # Look for Statement Closing Date
            date_match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
            if date_match:
                closing_date = self._parse_date(date_match.group(1))
                if closing_date:
                    # Estimate period as ~30 days before closing
                    if closing_date.month == 1:
                        start = date(closing_date.year - 1, 12, closing_date.day + 1)
                    else:
                        start = date(
                            closing_date.year,
                            closing_date.month - 1,
                            closing_date.day + 1,
                        )
                    return (start, closing_date)

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
