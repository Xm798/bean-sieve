"""Every registered provider has a docs page; see website/scripts/export_providers.py."""

from pathlib import Path

import pytest

from bean_sieve.providers import PROVIDERS
from tests.website_exporter import load_exporter


@pytest.fixture(scope="module")
def exporter():
    return load_exporter()


def test_every_registered_provider_has_a_page(exporter) -> None:
    result = exporter.check_pages(sorted(PROVIDERS), exporter.PAGES_DIR)
    assert result.missing == [], "add website/content/docs/providers/<id>.mdx"
    assert result.orphaned == []


def test_check_pages_reports_missing_and_orphaned(exporter, tmp_path: Path) -> None:
    (tmp_path / "index.mdx").touch()
    (tmp_path / "alipay.mdx").touch()
    (tmp_path / "retired.mdx").touch()

    result = exporter.check_pages(["alipay", "wechat"], tmp_path)

    assert result.missing == ["wechat"]
    assert result.orphaned == ["retired"]
    assert not result.ok


def test_main_fails_without_writing_when_a_page_is_missing(
    exporter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(exporter, "PAGES_DIR", tmp_path / "pages")
    monkeypatch.setattr(exporter, "GENERATED_DIR", tmp_path / "generated")
    (tmp_path / "pages").mkdir()

    assert exporter.main() == 1
    assert not (tmp_path / "generated").exists()
    assert not (tmp_path / "pages" / "meta.json").exists()


def test_nav_groups_follow_category_order(exporter) -> None:
    meta = exporter.nav_meta(exporter.sorted_providers())
    separators = [p for p in meta["pages"] if p.startswith("---")]
    assert [s.split("]")[1].strip("-") for s in separators] == [
        "支付平台",
        "信用卡",
        "借记卡",
    ]
    listed = [p for p in meta["pages"] if not p.startswith("---") and p != "index"]
    assert sorted(listed) == sorted(PROVIDERS)


def test_check_examples_reports_missing_and_orphaned(exporter, tmp_path: Path) -> None:
    (tmp_path / "alipay").mkdir()
    (tmp_path / "retired").mkdir()
    (tmp_path / "README.md").touch()

    result = exporter.check_examples(["alipay", "wechat"], tmp_path)

    assert result.missing == ["wechat"]
    assert result.orphaned == ["retired"]


def test_site_data_carries_category_titles_and_detection_facts(exporter) -> None:
    data = exporter.site_data(exporter.sorted_providers())

    assert [c["title"] for c in data["categories"]] == ["支付平台", "信用卡", "借记卡"]
    assert data["detection"] == {
        "contentHeaderChars": 500,
        "filenameKeywordsIgnoreCase": True,
    }
    scopes = {p["id"]: p["contentScope"] for p in data["providers"]}
    assert scopes["alipay"] == "header"
    assert scopes["spdb_debit"] == "pdf"
    for pid in ("icbc_credit", "ccb_credit", "cgb_credit"):
        assert scopes[pid] == "eml-pdf"
    assert scopes["zabank_debit"] == "custom"


def test_content_scope_is_custom_for_any_provider_specific_content_check(
    exporter,
) -> None:
    data = exporter.site_data(exporter.sorted_providers())
    by_id = {p["id"]: p for p in data["providers"]}

    # abc_debit overrides _match_content and declares no content_keywords;
    # spdb_credit overrides can_handle, which reads its keywords for PDFs only.
    assert by_id["abc_debit"]["contentKeywords"] == []
    assert by_id["spdb_credit"]["contentKeywords"] == ["浦发个人信用卡对账单"]
    for pid in ("abc_debit", "spdb_credit"):
        assert by_id[pid]["contentScope"] == "custom"
