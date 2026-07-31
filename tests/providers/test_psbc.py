"""Tests for the PSBC credit-card statement provider."""

import base64
import logging
from datetime import date
from decimal import Decimal
from email.header import Header
from pathlib import Path

import pytest

from bean_sieve.api import _set_target_accounts
from bean_sieve.config import Config
from bean_sieve.providers import auto_detect_provider, get_provider

HEADERS = (
    "交易日",
    "记账日",
    "交易摘要",
    "人民币金额",
    "卡号末四位",
    "交易国别",
    "境内外交易标识",
)


def create_psbc_eml(
    path: Path,
    *,
    rows: list[dict[str, str]] | None = None,
    headers: tuple[str, ...] = HEADERS,
    period: str | None = "2030/07/01-2030/07/31",
    subject: str = "邮储银行信用卡电子账单",
    title: str = "中国邮政储蓄银行信用卡对账单",
    before_table: str = "",
    raw_before_header_rows: str = "",
    raw_detail_rows: str = "",
    nested_wrapper: bool = False,
) -> Path:
    """Create a top-down synthetic PSBC-like EML fixture."""
    row_html = "".join(
        "<tr>"
        + "".join(f"<td>{row.get(header, '')}</td>" for header in headers)
        + "</tr>"
        for row in (rows or [])
    )
    period_html = f"<p>合成账单周期为【{period}】</p>" if period is not None else ""
    detail_table = (
        "<table><tbody>"
        + raw_before_header_rows
        + "<tr>"
        + "".join(f"<th>{header}</th>" for header in headers)
        + "</tr>"
        + raw_detail_rows
        + row_html
        + "</tbody></table>"
    )
    if nested_wrapper:
        detail_table = (
            "<table><tbody><tr><td>" + detail_table + "</td></tr></tbody></table>"
        )
    html = (
        "<html><head>"
        f"<title>{title}</title>"
        "</head><body>"
        f"{period_html}{before_table}"
        f"<section>{detail_table}</section>"
        "</body></html>"
    )
    encoded_html = base64.b64encode(html.encode("utf-8")).decode("ascii")
    encoded_subject = Header(subject, "utf-8").encode()
    eml = (
        "From: sender@fixture.invalid\n"
        "To: recipient@fixture.invalid\n"
        f"Subject: {encoded_subject}\n"
        'Content-Type: text/html; charset="utf-8"\n'
        "Content-Transfer-Encoding: base64\n"
        "MIME-Version: 1.0\n\n"
        f"{encoded_html}\n"
    )
    path.write_text(eml, encoding="utf-8")
    return path


def test_provider_is_registered() -> None:
    provider = get_provider("psbc_credit")

    assert provider.provider_name == "邮储银行信用卡"
    assert provider.supported_formats == [".eml"]
    assert provider.per_card_statement is True


def test_detects_bank_qualified_filename() -> None:
    provider = get_provider("psbc_credit")

    assert provider.can_handle(Path("邮储银行信用卡电子账单.eml"))
    assert auto_detect_provider(Path("邮储银行信用卡电子账单.eml")) is not None
    assert not provider.can_handle(Path("信用卡电子账单.eml"))
    assert not provider.can_handle(Path("邮储银行信用卡电子账单.pdf"))


def test_detects_encoded_content_with_neutral_filename(tmp_path: Path) -> None:
    path = create_psbc_eml(tmp_path / "neutral-fixture.eml")

    provider = auto_detect_provider(path)

    assert provider is not None
    assert provider.provider_id == "psbc_credit"


@pytest.mark.parametrize(
    ("subject", "title"),
    [
        ("邮储银行信用卡电子账单", "合成通用账单"),
        ("合成通用账单", "中国邮政储蓄银行信用卡对账单"),
    ],
)
def test_detects_either_qualified_subject_or_title(
    tmp_path: Path,
    subject: str,
    title: str,
) -> None:
    path = create_psbc_eml(
        tmp_path / "neutral-marker-branch.eml",
        subject=subject,
        title=title,
    )

    provider = auto_detect_provider(path)

    assert provider is not None
    assert provider.provider_id == "psbc_credit"


def test_rejects_unqualified_or_malformed_content(tmp_path: Path) -> None:
    generic = create_psbc_eml(
        tmp_path / "generic.eml",
        subject="通用信用卡电子账单",
        title="通用信用卡对账单",
    )
    other_bank = create_psbc_eml(
        tmp_path / "other-bank.eml",
        subject="测试银行信用卡电子账单",
        title="测试银行信用卡对账单",
    )
    malformed = tmp_path / "malformed.eml"
    malformed.write_text("synthetic malformed MIME", encoding="utf-8")
    body_reference = create_psbc_eml(
        tmp_path / "body-reference.eml",
        subject="合成通用账单",
        title="合成通用账单",
        before_table="<p>历史资料：中国邮政储蓄银行信用卡对账单</p>",
    )

    assert auto_detect_provider(generic) is None
    assert auto_detect_provider(other_bank) is None
    assert auto_detect_provider(malformed) is None
    assert auto_detect_provider(body_reference) is None


def test_parses_signed_rows_period_and_metadata(tmp_path: Path) -> None:
    path = create_psbc_eml(
        tmp_path / "synthetic-valid.eml",
        rows=[
            {
                "交易日": "20300703",
                "记账日": "20300704",
                "交易摘要": "  测试商户甲  ",
                "人民币金额": "￥12,345.67",
                "卡号末四位": "0007",
                "交易国别": "测试国别甲",
                "境内外交易标识": "测试境内标识",
            },
            {
                "交易日": "20300710",
                "记账日": "20300711",
                "交易摘要": "测试账单贷项乙",
                "人民币金额": "￥-271.82",
                "卡号末四位": "0007",
                "交易国别": "",
                "境内外交易标识": "",
            },
        ],
    )

    transactions = get_provider("psbc_credit").parse(path)

    assert len(transactions) == 2
    expense, credit = transactions
    assert expense.date == date(2030, 7, 3)
    assert expense.post_date == date(2030, 7, 4)
    assert expense.description == "测试商户甲"
    assert expense.amount == Decimal("12345.67")
    assert expense.currency == "CNY"
    assert expense.card_last4 == "0007"
    assert expense.statement_period == (date(2030, 7, 1), date(2030, 7, 31))
    assert expense.metadata == {
        "country": "测试国别甲",
        "domestic_or_overseas": "测试境内标识",
    }
    assert expense.provider == "psbc_credit"
    assert expense.source_file == path
    assert expense.source_line == 2
    assert credit.amount == Decimal("-271.82")
    assert credit.metadata == {}
    assert credit.source_line == 3


def test_uses_header_map_in_nested_tbody_without_duplicate_rows(
    tmp_path: Path,
) -> None:
    shuffled = (
        "卡号末四位",
        "交易摘要",
        "境内外交易标识",
        "交易日",
        "人民币金额",
        "交易国别",
        "记账日",
    )
    decoy = (
        "<table><tbody><tr><th>交易日</th><th>交易摘要</th></tr>"
        "<tr><td>20300701</td><td>测试诱饵</td></tr></tbody></table>"
    )
    path = create_psbc_eml(
        tmp_path / "synthetic-shuffled.eml",
        headers=shuffled,
        before_table=decoy,
        raw_before_header_rows="<tr><td>测试表头前布局行</td></tr>",
        nested_wrapper=True,
        rows=[
            {
                "交易日": "20300712",
                "记账日": "20300713",
                "交易摘要": "测试重排列",
                "人民币金额": "￥314.15",
                "卡号末四位": "8888",
                "交易国别": "测试国别乙",
                "境内外交易标识": "测试境外标识",
            }
        ],
    )

    transactions = get_provider("psbc_credit").parse(path)

    assert len(transactions) == 1
    transaction = transactions[0]
    assert transaction.description == "测试重排列"
    assert transaction.date == date(2030, 7, 12)
    assert transaction.post_date == date(2030, 7, 13)
    assert transaction.amount == Decimal("314.15")
    assert transaction.card_last4 == "8888"
    assert transaction.source_line == 3


@pytest.mark.parametrize(
    ("overrides", "sentinel"),
    [
        ({"交易日": "INVALID_DATE_SENTINEL"}, "INVALID_DATE_SENTINEL"),
        ({"记账日": "INVALID_POST_SENTINEL"}, "INVALID_POST_SENTINEL"),
        ({"人民币金额": "INVALID_AMOUNT_SENTINEL"}, "INVALID_AMOUNT_SENTINEL"),
        ({"卡号末四位": "007"}, "007"),
        ({"卡号末四位": "00077"}, "00077"),
        ({"卡号末四位": "ABCD"}, "ABCD"),
        ({"交易摘要": ""}, ""),
    ],
)
def test_skips_malformed_rows_without_sensitive_logs(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    overrides: dict[str, str],
    sentinel: str,
) -> None:
    caplog.set_level(
        logging.WARNING,
        logger="bean_sieve.providers.banks.credit.psbc",
    )
    invalid = {
        "交易日": "20300803",
        "记账日": "20300804",
        "交易摘要": "测试无效行",
        "人民币金额": "￥314.15",
        "卡号末四位": "0007",
        "交易国别": "测试隐私国别",
        "境内外交易标识": "测试隐私标识",
    }
    invalid.update(overrides)
    valid = {
        "交易日": "20300805",
        "记账日": "20300806",
        "交易摘要": "测试有效行",
        "人民币金额": "￥0.00",
        "卡号末四位": "0007",
        "交易国别": "",
        "境内外交易标识": "",
    }
    path = create_psbc_eml(
        tmp_path / "PRIVATE_FILENAME_SENTINEL.eml",
        rows=[invalid, valid],
        period="2030/08/01-2030/08/31",
    )

    transactions = get_provider("psbc_credit").parse(path)

    assert [transaction.description for transaction in transactions] == ["测试有效行"]
    assert transactions[0].amount == Decimal("0.00")
    assert transactions[0].source_line == 3
    assert "PRIVATE_FILENAME_SENTINEL" not in caplog.text
    assert "测试无效行" not in caplog.text
    assert "测试隐私国别" not in caplog.text
    assert "测试隐私标识" not in caplog.text
    if sentinel:
        assert sentinel not in caplog.text
    assert str(path) not in caplog.text
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.name == "bean_sieve.providers.banks.credit.psbc"
    assert record.levelno == logging.WARNING
    assert record.getMessage() == "Skipping malformed psbc_credit row 2"
    assert record.args == ("psbc_credit", 2)
    assert record.exc_info is None
    record_text = repr(record.__dict__)
    for value in invalid.values():
        if value:
            assert value not in record_text


def test_skips_truncated_row_and_preserves_physical_row_number(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    valid = {
        "交易日": "20300805",
        "记账日": "20300806",
        "交易摘要": "测试有效行",
        "人民币金额": "￥0.00",
        "卡号末四位": "0007",
        "交易国别": "",
        "境内外交易标识": "",
    }
    path = create_psbc_eml(
        tmp_path / "TRUNCATED_FILENAME_SENTINEL.eml",
        raw_detail_rows=("<tr><td>TRUNCATED_CELL_SENTINEL</td><td>20300804</td></tr>"),
        rows=[valid],
        period="2030/08/01-2030/08/31",
    )

    transactions = get_provider("psbc_credit").parse(path)

    assert [transaction.description for transaction in transactions] == ["测试有效行"]
    assert transactions[0].source_line == 3
    assert "Skipping malformed psbc_credit row 2" in caplog.text
    assert "TRUNCATED_FILENAME_SENTINEL" not in caplog.text
    assert "TRUNCATED_CELL_SENTINEL" not in caplog.text


@pytest.mark.parametrize(
    "period",
    [
        None,
        "2030/02/30-2030/03/31",
        "2030/09/30-2030/09/01",
        "2030/10/01-2030/10/31",
    ],
)
def test_uses_transaction_range_when_period_is_unusable(
    tmp_path: Path,
    period: str | None,
) -> None:
    path = create_psbc_eml(
        tmp_path / "synthetic-period.eml",
        period=period,
        rows=[
            {
                "交易日": "20300912",
                "记账日": "20300913",
                "交易摘要": "测试周期回退",
                "人民币金额": "￥314.15",
                "卡号末四位": "0007",
                "交易国别": "",
                "境内外交易标识": "",
            }
        ],
    )

    transactions = get_provider("psbc_credit").parse(path)

    assert len(transactions) == 1
    assert transactions[0].statement_period == (
        date(2030, 9, 12),
        date(2030, 9, 12),
    )


def test_period_fallback_uses_transaction_dates_and_keeps_order(
    tmp_path: Path,
) -> None:
    path = create_psbc_eml(
        tmp_path / "synthetic-period-order.eml",
        period=None,
        rows=[
            {
                "交易日": "20300919",
                "记账日": "20300930",
                "交易摘要": "测试顺序甲",
                "人民币金额": "￥314.15",
                "卡号末四位": "0007",
                "交易国别": "",
                "境内外交易标识": "",
            },
            {
                "交易日": "20300911",
                "记账日": "20300912",
                "交易摘要": "测试顺序乙",
                "人民币金额": "￥-271.82",
                "卡号末四位": "0007",
                "交易国别": "",
                "境内外交易标识": "",
            },
        ],
    )

    transactions = get_provider("psbc_credit").parse(path)

    assert [transaction.description for transaction in transactions] == [
        "测试顺序甲",
        "测试顺序乙",
    ]
    assert all(
        transaction.statement_period == (date(2030, 9, 11), date(2030, 9, 19))
        for transaction in transactions
    )


def test_empty_statement_has_no_extra_coverage(tmp_path: Path) -> None:
    path = create_psbc_eml(tmp_path / "synthetic-empty.eml")
    provider = get_provider("psbc_credit")
    config = Config.from_dict(
        {
            "providers": {
                "psbc_credit": {
                    "accounts": {"0007": "Liabilities:CreditCard:PSBC:0007"}
                }
            }
        }
    )

    assert provider.parse(path) == []
    assert provider.get_covered_accounts([], config) == []
    assert provider.get_covered_ranges([], config) is None


def test_missing_or_incomplete_detail_table_returns_empty(tmp_path: Path) -> None:
    path = create_psbc_eml(
        tmp_path / "synthetic-incomplete.eml",
        headers=HEADERS[:-1],
    )

    assert get_provider("psbc_credit").parse(path) == []


def test_per_card_coverage_isolated_to_parsed_suffixes(tmp_path: Path) -> None:
    path = create_psbc_eml(
        tmp_path / "synthetic-coverage.eml",
        rows=[
            {
                "交易日": "20301007",
                "记账日": "20301008",
                "交易摘要": "测试覆盖范围",
                "人民币金额": "￥314.15",
                "卡号末四位": "0007",
                "交易国别": "",
                "境内外交易标识": "",
            },
            {
                "交易日": "20301009",
                "记账日": "20301010",
                "交易摘要": "测试第二卡覆盖",
                "人民币金额": "￥-271.82",
                "卡号末四位": "8888",
                "交易国别": "",
                "境内外交易标识": "",
            },
        ],
        period="2030/10/01-2030/10/31",
    )
    provider = get_provider("psbc_credit")
    transactions = provider.parse(path)
    config = Config.from_dict(
        {
            "providers": {
                "psbc_credit": {
                    "accounts": {
                        "0007": "Liabilities:CreditCard:PSBC:0007",
                        "8888": "Liabilities:CreditCard:PSBC:8888",
                    }
                }
            }
        }
    )

    assert len(transactions) == 2
    assert provider.get_covered_accounts(transactions, config) == [
        "Liabilities:CreditCard:PSBC:0007",
        "Liabilities:CreditCard:PSBC:8888",
    ]
    assert provider.get_covered_ranges(transactions, config) == {
        "Liabilities:CreditCard:PSBC:0007": [(date(2030, 10, 1), date(2030, 10, 31))],
        "Liabilities:CreditCard:PSBC:8888": [(date(2030, 10, 1), date(2030, 10, 31))],
    }
    assert [
        transaction.account
        for transaction in _set_target_accounts(transactions, config)
    ] == [
        "Liabilities:CreditCard:PSBC:0007",
        "Liabilities:CreditCard:PSBC:8888",
    ]
    unknown = [
        transaction.model_copy(update={"card_last4": "9999"})
        for transaction in transactions
    ]
    assert provider.get_covered_accounts(unknown, config) == []
    assert provider.get_covered_ranges(unknown, config) is None
