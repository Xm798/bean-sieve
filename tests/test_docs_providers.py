"""Every registered provider has a docs page; see website/scripts/export_providers.py."""

import importlib.util
import sys
from pathlib import Path

import pytest

from bean_sieve.providers import PROVIDERS

SCRIPT = Path(__file__).parents[1] / "website" / "scripts" / "export_providers.py"


@pytest.fixture(scope="module")
def exporter():
    spec = importlib.util.spec_from_file_location("export_providers", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # dataclass resolves annotations through sys.modules[__module__].
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    del sys.modules[spec.name]


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
