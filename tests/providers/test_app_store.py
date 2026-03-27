"""Tests for App Store purchase history provider."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from bean_sieve.providers.payment.app_store import AppStoreProvider


def _make_har(
    purchases: list[dict],
    url: str = "https://reportaproblem.apple.com/api/purchase/search",
) -> dict:
    """Build a minimal HAR structure with given purchases."""
    return {
        "log": {
            "version": "1.2",
            "entries": [
                {
                    "request": {"url": url},
                    "response": {
                        "content": {
                            "text": json.dumps({"purchases": purchases}),
                        }
                    },
                }
            ],
        }
    }


def _make_purchase(
    *,
    weborder: str = "MXGTEST001",
    amount: str = "¥30.00",
    item_name: str = "测试内购项目",
    detail: str = "测试App",
    pli_date: str = "2026-03-07T17:11:50Z",
    is_free: bool = False,
    is_credit: bool = False,
    media_type: str = "App 内购买项目",
    item_id: str = "900000000000001",
    adam_id: str = "9000000001",
) -> dict:
    """Build a single purchase record."""
    return {
        "purchaseId": "900000000000099",
        "invoiceAmount": amount,
        "plis": [
            {
                "itemId": item_id,
                "purchaseId": "900000000000099",
                "adamId": adam_id,
                "amountPaid": amount,
                "pliDate": pli_date,
                "isFreePurchase": is_free,
                "isCredit": is_credit,
                "localizedContent": {
                    "nameForDisplay": item_name,
                    "detailForDisplay": detail,
                    "mediaType": media_type,
                },
                "lineItemType": "BaseLineItem",
                "estimatedTotal": amount,
            }
        ],
        "weborder": weborder,
        "invoiceDate": "2026-03-07T23:27:50Z",
        "purchaseDate": pli_date,
        "isPendingPurchase": False,
        "estimatedTotalAmount": amount,
    }


class TestAppStoreProvider:
    """Tests for AppStoreProvider."""

    def test_parse_basic(self, tmp_path: Path) -> None:
        """Test basic parsing of a paid purchase."""
        har = _make_har([_make_purchase()])
        har_file = tmp_path / "reportaproblem.apple.com.har"
        har_file.write_text(json.dumps(har), encoding="utf-8")

        provider = AppStoreProvider()
        txns = provider.parse(har_file)

        assert len(txns) == 1
        txn = txns[0]
        assert txn.amount == Decimal("30.00")
        assert txn.currency == "CNY"
        assert txn.description == "测试内购项目"
        assert txn.payee == "测试App"
        assert txn.order_id == "MXGTEST001-900000000000001"
        assert txn.provider == "app_store"
        assert txn.metadata["media_type"] == "App 内购买项目"

    def test_skip_free_purchases(self, tmp_path: Path) -> None:
        """Test that free purchases are skipped."""
        har = _make_har(
            [
                _make_purchase(is_free=True, amount="¥0.00", item_id="free1"),
                _make_purchase(item_id="paid1"),
            ]
        )
        har_file = tmp_path / "reportaproblem.apple.com.har"
        har_file.write_text(json.dumps(har), encoding="utf-8")

        provider = AppStoreProvider()
        txns = provider.parse(har_file)

        assert len(txns) == 1
        assert txns[0].amount == Decimal("30.00")

    def test_credit_is_negative(self, tmp_path: Path) -> None:
        """Test that credit/refund purchases have negative amount."""
        har = _make_har([_make_purchase(is_credit=True, amount="¥10.00")])
        har_file = tmp_path / "reportaproblem.apple.com.har"
        har_file.write_text(json.dumps(har), encoding="utf-8")

        provider = AppStoreProvider()
        txns = provider.parse(har_file)

        assert len(txns) == 1
        assert txns[0].amount == Decimal("-10.00")

    def test_multiple_batches(self, tmp_path: Path) -> None:
        """Test parsing HAR with multiple paginated responses."""
        har = {
            "log": {
                "version": "1.2",
                "entries": [
                    {
                        "request": {
                            "url": "https://reportaproblem.apple.com/api/purchase/search"
                        },
                        "response": {
                            "content": {
                                "text": json.dumps(
                                    {
                                        "purchases": [
                                            _make_purchase(
                                                weborder="ORDER1", item_id="id1"
                                            )
                                        ],
                                    }
                                ),
                            }
                        },
                    },
                    {
                        "request": {
                            "url": "https://reportaproblem.apple.com/api/purchase/search"
                        },
                        "response": {
                            "content": {
                                "text": json.dumps(
                                    {
                                        "purchases": [
                                            _make_purchase(
                                                weborder="ORDER2",
                                                item_id="id2",
                                                amount="¥50.00",
                                            )
                                        ],
                                    }
                                ),
                            }
                        },
                    },
                ],
            }
        }
        har_file = tmp_path / "apple.har"
        har_file.write_text(json.dumps(har), encoding="utf-8")

        provider = AppStoreProvider()
        txns = provider.parse(har_file)

        assert len(txns) == 2
        assert "ORDER1" in txns[0].order_id
        assert "ORDER2" in txns[1].order_id
        assert txns[1].amount == Decimal("50.00")

    def test_multi_pli_purchase(self, tmp_path: Path) -> None:
        """Test a purchase with multiple line items."""
        purchase = {
            "purchaseId": "900000000000050",
            "invoiceAmount": "¥60.00",
            "plis": [
                {
                    "itemId": "pli1",
                    "purchaseId": "900000000000050",
                    "adamId": "1001",
                    "amountPaid": "¥30.00",
                    "pliDate": "2026-03-07T10:00:00Z",
                    "isFreePurchase": False,
                    "isCredit": False,
                    "localizedContent": {
                        "nameForDisplay": "测试物品A",
                        "detailForDisplay": "测试游戏",
                        "mediaType": "App 内购买项目",
                    },
                    "lineItemType": "BaseLineItem",
                    "estimatedTotal": "¥30.00",
                },
                {
                    "itemId": "pli2",
                    "purchaseId": "900000000000050",
                    "adamId": "1002",
                    "amountPaid": "¥30.00",
                    "pliDate": "2026-03-07T10:01:00Z",
                    "isFreePurchase": False,
                    "isCredit": False,
                    "localizedContent": {
                        "nameForDisplay": "测试物品B",
                        "detailForDisplay": "测试游戏",
                        "mediaType": "App 内购买项目",
                    },
                    "lineItemType": "BaseLineItem",
                    "estimatedTotal": "¥30.00",
                },
            ],
            "weborder": "MXGTEST01",
            "invoiceDate": "2026-03-07T23:27:50Z",
            "purchaseDate": "2026-03-07T10:00:00Z",
            "isPendingPurchase": False,
            "estimatedTotalAmount": "¥60.00",
        }
        har = _make_har([purchase])
        har_file = tmp_path / "apple.har"
        har_file.write_text(json.dumps(har), encoding="utf-8")

        provider = AppStoreProvider()
        txns = provider.parse(har_file)

        assert len(txns) == 2
        assert txns[0].description == "测试物品A"
        assert txns[1].description == "测试物品B"
        # Composite order_id ensures unique match_key per PLI
        assert txns[0].order_id == "MXGTEST01-pli1"
        assert txns[1].order_id == "MXGTEST01-pli2"
        assert txns[0].match_key != txns[1].match_key

    def test_dedup_across_batches(self, tmp_path: Path) -> None:
        """Test that duplicate item_ids across batches are deduplicated."""
        purchase = _make_purchase(item_id="dup1")
        har = {
            "log": {
                "version": "1.2",
                "entries": [
                    {
                        "request": {
                            "url": "https://reportaproblem.apple.com/api/purchase/search"
                        },
                        "response": {
                            "content": {"text": json.dumps({"purchases": [purchase]})}
                        },
                    },
                    {
                        "request": {
                            "url": "https://reportaproblem.apple.com/api/purchase/search"
                        },
                        "response": {
                            "content": {"text": json.dumps({"purchases": [purchase]})}
                        },
                    },
                ],
            }
        }
        har_file = tmp_path / "apple.har"
        har_file.write_text(json.dumps(har), encoding="utf-8")

        provider = AppStoreProvider()
        txns = provider.parse(har_file)

        assert len(txns) == 1

    def test_empty_har(self, tmp_path: Path) -> None:
        """Test parsing an empty HAR file."""
        har = {"log": {"version": "1.2", "entries": []}}
        har_file = tmp_path / "apple.har"
        har_file.write_text(json.dumps(har), encoding="utf-8")

        provider = AppStoreProvider()
        txns = provider.parse(har_file)

        assert txns == []

    def test_skip_non_search_entries(self, tmp_path: Path) -> None:
        """Test that non-search API entries are ignored."""
        har = _make_har(
            [_make_purchase()],
            url="https://reportaproblem.apple.com/api/other/endpoint",
        )
        har_file = tmp_path / "apple.har"
        har_file.write_text(json.dumps(har), encoding="utf-8")

        provider = AppStoreProvider()
        txns = provider.parse(har_file)

        assert txns == []

    def test_date_with_milliseconds(self, tmp_path: Path) -> None:
        """Test parsing dates with millisecond precision."""
        har = _make_har([_make_purchase(pli_date="2026-03-21T15:48:21.264Z")])
        har_file = tmp_path / "apple.har"
        har_file.write_text(json.dumps(har), encoding="utf-8")

        provider = AppStoreProvider()
        txns = provider.parse(har_file)

        assert len(txns) == 1
        assert txns[0].date.isoformat() == "2026-03-21"

    def test_subscription_metadata(self, tmp_path: Path) -> None:
        """Test that media_type is captured for subscriptions."""
        har = _make_har([_make_purchase(media_type="订阅续期")])
        har_file = tmp_path / "apple.har"
        har_file.write_text(json.dumps(har), encoding="utf-8")

        provider = AppStoreProvider()
        txns = provider.parse(har_file)

        assert txns[0].metadata["media_type"] == "订阅续期"

    def test_amount_with_comma(self, tmp_path: Path) -> None:
        """Test parsing amount with thousand separators."""
        har = _make_har([_make_purchase(amount="¥1,280.00")])
        har_file = tmp_path / "apple.har"
        har_file.write_text(json.dumps(har), encoding="utf-8")

        provider = AppStoreProvider()
        txns = provider.parse(har_file)

        assert len(txns) == 1
        assert txns[0].amount == Decimal("1280.00")

    def test_can_handle_detection(self, tmp_path: Path) -> None:
        """Test file detection by filename keywords."""
        har_file = tmp_path / "reportaproblem.apple.com.har"
        har_file.write_text("{}", encoding="utf-8")

        assert AppStoreProvider.can_handle(har_file) is True

    def test_can_handle_rejects_csv(self, tmp_path: Path) -> None:
        """Test that non-HAR files are rejected."""
        csv_file = tmp_path / "apple.csv"
        csv_file.write_text("", encoding="utf-8")

        assert AppStoreProvider.can_handle(csv_file) is False

    @pytest.mark.parametrize(
        ("amount_str", "expected_amount", "expected_currency"),
        [
            ("¥19.90", Decimal("19.90"), "CNY"),
            ("$4.99", Decimal("4.99"), "USD"),
            ("€2.99", Decimal("2.99"), "EUR"),
            ("£1.99", Decimal("1.99"), "GBP"),
            ("30.00", Decimal("30.00"), "CNY"),  # no symbol defaults to CNY
        ],
    )
    def test_parse_amount_currencies(
        self,
        amount_str: str,
        expected_amount: Decimal,
        expected_currency: str,
    ) -> None:
        """Test amount parsing with different currency symbols."""
        amount, currency = AppStoreProvider._parse_amount(amount_str)
        assert amount == expected_amount
        assert currency == expected_currency

    def test_malformed_response_json(self, tmp_path: Path) -> None:
        """Test that malformed JSON in response is skipped gracefully."""
        har = {
            "log": {
                "version": "1.2",
                "entries": [
                    {
                        "request": {
                            "url": "https://reportaproblem.apple.com/api/purchase/search"
                        },
                        "response": {"content": {"text": "{truncated..."}},
                    }
                ],
            }
        }
        har_file = tmp_path / "apple.har"
        har_file.write_text(json.dumps(har), encoding="utf-8")

        provider = AppStoreProvider()
        txns = provider.parse(har_file)

        assert txns == []

    def test_empty_plis(self, tmp_path: Path) -> None:
        """Test that a purchase with empty plis produces no transactions."""
        purchase = _make_purchase()
        purchase["plis"] = []
        har = _make_har([purchase])
        har_file = tmp_path / "apple.har"
        har_file.write_text(json.dumps(har), encoding="utf-8")

        provider = AppStoreProvider()
        txns = provider.parse(har_file)

        assert txns == []

    def test_missing_amount_skipped(self, tmp_path: Path) -> None:
        """Test that PLI with missing amountPaid is skipped."""
        purchase = _make_purchase()
        purchase["plis"][0]["amountPaid"] = None
        har = _make_har([purchase])
        har_file = tmp_path / "apple.har"
        har_file.write_text(json.dumps(har), encoding="utf-8")

        provider = AppStoreProvider()
        txns = provider.parse(har_file)

        assert txns == []

    def test_missing_pli_date_skipped(self, tmp_path: Path) -> None:
        """Test that PLI with missing pliDate is skipped."""
        purchase = _make_purchase()
        purchase["plis"][0]["pliDate"] = ""
        har = _make_har([purchase])
        har_file = tmp_path / "apple.har"
        har_file.write_text(json.dumps(har), encoding="utf-8")

        provider = AppStoreProvider()
        txns = provider.parse(har_file)

        assert txns == []

    def test_null_localized_content(self, tmp_path: Path) -> None:
        """Test that PLI with null localizedContent doesn't crash."""
        purchase = _make_purchase()
        purchase["plis"][0]["localizedContent"] = None
        har = _make_har([purchase])
        har_file = tmp_path / "apple.har"
        har_file.write_text(json.dumps(har), encoding="utf-8")

        provider = AppStoreProvider()
        txns = provider.parse(har_file)

        assert len(txns) == 1
        assert txns[0].description == ""
