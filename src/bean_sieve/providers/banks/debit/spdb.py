"""Shanghai Pudong Development Bank (浦发银行) debit card statement provider."""

from __future__ import annotations

import re
from pathlib import Path

from ....core.types import Transaction
from ... import register_provider
from ..._tabular import parse_stamp, to_decimal
from ...base import BaseProvider

_TITLE = "上海浦东发展银行个人客户交易流水专用回单"
_ACCOUNT_RE = re.compile(r"账号:\s*(\d+)")
_PERIOD_RE = re.compile(r"起止日期:\s*(\d{8})-(\d{8})")
_CURRENCY_RE = re.compile(r"Currency:\s*([A-Z]{3})")


def _unwrap(cell: str | None) -> str:
    # The PDF wraps cells at the column width, splitting words mid-character.
    return "".join((cell or "").split("\n")).strip()


def _split_name(cell: str | None) -> tuple[str, str]:
    """Split 交易名称 into the name and the trailing all-digit reference lines.

    Transfers append a serial number and card repayments the card suffix,
    each wrapped onto lines of their own below the name.
    """
    lines = [line.strip() for line in (cell or "").split("\n") if line.strip()]
    name = lines[:1]
    ref: list[str] = []
    for line in lines[1:]:
        if line.isdigit():
            ref.append(line)
        elif not ref:
            name.append(line)
    return "".join(name), "".join(ref)


@register_provider
class SPDBDebitProvider(BaseProvider):
    """
    Provider for SPDB debit card transaction statements (交易流水专用回单, PDF).

    File format:
    - Title: 上海浦东发展银行个人客户交易流水专用回单
    - Header block: 户名, 账号, 起止日期 (YYYYMMDD-YYYYMMDD), 币种 / Currency
    - Ruled table, header row on the first page only: 交易日期 (YYYYMMDD),
      交易时间 (HHMMSS), 交易账号, 交易名称, 交易金额 (signed, negative =
      outflow), 账户余额, 对手姓名, 对手账号, 交易摘要
    - Cells wrap at the column width onto extra lines
    """

    provider_id = "spdb_debit"
    provider_name = "浦发银行借记卡"
    supported_formats = [".pdf"]
    content_keywords = [_TITLE]
    per_card_statement = True  # SPDB issues one statement per account

    NUM_COLS = 9

    def parse(self, file_path: Path) -> list[Transaction]:
        """Parse an SPDB debit card transaction statement PDF."""
        import fitz

        transactions: list[Transaction] = []
        with fitz.open(file_path) as doc:
            header = str(doc[0].get_text()) if doc.page_count else ""
            account = _ACCOUNT_RE.search(header)
            card_last4 = account.group(1)[-4:] if account else None
            currency_match = _CURRENCY_RE.search(header)
            currency = currency_match.group(1) if currency_match else "CNY"

            row_num = 0
            for page in doc:
                finder = page.find_tables()
                if finder is None:
                    continue
                for table in finder.tables:
                    for row in table.extract():
                        row_num += 1
                        txn = self._parse_row(
                            row, row_num, card_last4, currency, file_path
                        )
                        if txn:
                            transactions.append(txn)

        period = _PERIOD_RE.search(header)
        cycle = None
        if period:
            start = parse_stamp(period.group(1), "%Y%m%d")
            end = parse_stamp(period.group(2), "%Y%m%d")
            if start and end:
                cycle = (start.date(), end.date())
        if transactions:
            self.assign_statement_periods(transactions, cycle)
        return transactions

    def _parse_row(
        self,
        row: list[str | None],
        row_num: int,
        card_last4: str | None,
        currency: str,
        file_path: Path,
    ) -> Transaction | None:
        if len(row) < self.NUM_COLS:
            return None
        (
            date_str,
            time_str,
            account,
            _,
            amount_str,
            balance,
            counterparty,
            counterparty_account,
            summary,
        ) = (_unwrap(cell) for cell in row[: self.NUM_COLS])
        tx_date = parse_stamp(date_str, "%Y%m%d")
        if tx_date is None:
            return None
        value = to_decimal(amount_str)
        if value is None:
            return None

        # 交易名称 keeps its line breaks: they separate the name from its reference.
        name, reference = _split_name(row[3])
        tx_time = parse_stamp(time_str, "%H%M%S")
        metadata = {
            "balance": balance,
            "counterparty_account": counterparty_account,
            "reference": reference,
            "summary": summary,
        }

        return Transaction(
            date=tx_date.date(),
            time=tx_time.time() if tx_time else None,
            # The statement signs outflows negative; expenses are positive here.
            amount=-value,
            currency=currency,
            description=name,
            payee=counterparty or None,
            card_last4=account[-4:] if account else card_last4,
            provider=self.provider_id,
            source_file=file_path,
            source_line=row_num,
            metadata={key: val for key, val in metadata.items() if val},
        )
