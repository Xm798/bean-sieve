"""Export provider registry facts for the docs site.

Writes ``website/generated/providers.json`` (rendered by ``<ProviderFacts>``),
UTF-8 copies of the text statements under ``examples/`` (so pages can embed a
GBK CSV with ``<include>``), and the providers sidebar
``website/content/docs/providers/meta.json``. Exits non-zero when a registered
provider has no page, or a page names no registered provider.

Also owns the ``examples/<provider_id>/`` layout that ``tests/test_examples.py``
runs, so both read statements through ``statement_paths``.

Run from the repository root or ``website/``: ``uv run python website/scripts/export_providers.py``.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from bean_sieve.providers import PROVIDERS
from bean_sieve.providers._tabular import read_text_lines
from bean_sieve.providers.base import BaseProvider

ROOT = Path(__file__).resolve().parents[2]
WEBSITE = ROOT / "website"
EXAMPLES_DIR = ROOT / "examples"
PAGES_DIR = WEBSITE / "content" / "docs" / "providers"
GENERATED_DIR = WEBSITE / "generated"


class Category(NamedTuple):
    key: str
    module_prefix: str
    title: str
    icon: str


# Nav groups in sidebar order, keyed by the provider's module package.
CATEGORIES: tuple[Category, ...] = (
    Category("payment", "bean_sieve.providers.payment.", "支付平台", "Wallet"),
    Category("credit", "bean_sieve.providers.banks.credit.", "信用卡", "CreditCard"),
    Category("debit", "bean_sieve.providers.banks.debit.", "借记卡", "Landmark"),
)

CONFIG_NAME = "bean-sieve.yaml"
LEDGER_NAME = "ledger.bean"
EXPECTED_NAME = "expected.bean"
RESERVED_NAMES = {CONFIG_NAME, LEDGER_NAME, EXPECTED_NAME}
TEXT_STATEMENT_SUFFIXES = {".csv", ".har"}
NON_PROVIDER_PAGES = {"index"}


@dataclass(frozen=True)
class Coverage:
    missing: list[str]
    orphaned: list[str]

    @property
    def ok(self) -> bool:
        return not self.missing and not self.orphaned


def coverage(provider_ids: list[str], names: set[str]) -> Coverage:
    return Coverage(
        missing=sorted(set(provider_ids) - names),
        orphaned=sorted(names - set(provider_ids)),
    )


def category_of(cls: type[BaseProvider]) -> str:
    for category in CATEGORIES:
        if cls.__module__.startswith(category.module_prefix):
            return category.key
    raise ValueError(
        f"{cls.provider_id}: module {cls.__module__} fits no docs nav group; "
        "add one to CATEGORIES in website/scripts/export_providers.py"
    )


def statement_paths(example_dir: Path) -> list[Path]:
    if not example_dir.is_dir():
        return []
    return sorted(
        p
        for p in example_dir.iterdir()
        if p.is_file() and p.name not in RESERVED_NAMES and not p.name.startswith(".")
    )


def _overrides(cls: type[BaseProvider], name: str) -> bool:
    return getattr(cls, name).__func__ is not getattr(BaseProvider, name).__func__


def content_scope(cls: type[BaseProvider]) -> str:
    """Where ``content_keywords`` are searched, per ``BaseProvider._match_content``.

    ``custom`` marks a provider that overrides ``_match_content`` or
    ``can_handle`` with its own reader (PDF, XLS, parsed HTML). Its content check
    may use no keywords at all, and its scope is left to the provider's page.
    """
    if _overrides(cls, "_match_content") or _overrides(cls, "can_handle"):
        return "custom"
    if ".eml" in cls.supported_formats:
        return "eml"
    return "pdf" if ".pdf" in cls.supported_formats else "header"


def filename_keywords_ignore_case() -> bool:
    probe = type("Probe", (BaseProvider,), {"filename_keywords": ["Ab"]})
    return probe._match_filename(Path("aB.csv"))


def provider_facts(cls: type[BaseProvider]) -> dict[str, object]:
    pattern = cls.filename_pattern
    statements = [p.name for p in statement_paths(EXAMPLES_DIR / cls.provider_id)]
    return {
        "id": cls.provider_id,
        "name": cls.provider_name,
        "category": category_of(cls),
        "formats": list(cls.supported_formats),
        "filenameKeywords": list(cls.filename_keywords),
        "filenamePattern": pattern.pattern if pattern else None,
        "contentKeywords": list(cls.content_keywords),
        "contentScope": content_scope(cls),
        "perCardStatement": cls.per_card_statement,
        "presetRules": [rule.name for rule in cls.get_preset_rules()],
        "example": {"statements": statements} if statements else None,
    }


def site_data(providers: list[type[BaseProvider]]) -> dict[str, object]:
    return {
        "categories": [{"key": c.key, "title": c.title} for c in CATEGORIES],
        "detection": {
            "contentHeaderChars": BaseProvider.CONTENT_HEADER_CHARS,
            "filenameKeywordsIgnoreCase": filename_keywords_ignore_case(),
        },
        "providers": [provider_facts(c) for c in providers],
    }


def sorted_providers() -> list[type[BaseProvider]]:
    order = {c.key: i for i, c in enumerate(CATEGORIES)}
    return sorted(
        PROVIDERS.values(), key=lambda c: (order[category_of(c)], c.provider_id)
    )


def check_pages(provider_ids: list[str], pages_dir: Path) -> Coverage:
    pages = {p.stem for p in pages_dir.glob("*.mdx")} - NON_PROVIDER_PAGES
    return coverage(provider_ids, pages)


def check_examples(provider_ids: list[str], examples_dir: Path) -> Coverage:
    dirs = {p.name for p in examples_dir.iterdir() if p.is_dir()}
    return coverage(provider_ids, dirs)


def nav_meta(providers: list[type[BaseProvider]]) -> dict[str, object]:
    pages: list[str] = ["index"]
    for category in CATEGORIES:
        ids = [c.provider_id for c in providers if category_of(c) == category.key]
        if ids:
            pages.append(f"---[{category.icon}]{category.title}---")
            pages.extend(ids)
    return {"title": "数据源", "pages": pages}


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def copy_text_statements(providers: list[type[BaseProvider]], out_dir: Path) -> None:
    shutil.rmtree(out_dir, ignore_errors=True)
    for cls in providers:
        for source in statement_paths(EXAMPLES_DIR / cls.provider_id):
            if source.suffix.lower() not in TEXT_STATEMENT_SUFFIXES:
                continue
            target = out_dir / cls.provider_id / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("".join(read_text_lines(source)), encoding="utf-8")


def main() -> int:
    providers = sorted_providers()
    result = check_pages([c.provider_id for c in providers], PAGES_DIR)
    if not result.ok:
        rel = os.path.relpath(PAGES_DIR)
        for pid in result.missing:
            print(
                f"error: provider {pid!r} has no page {rel}/{pid}.mdx", file=sys.stderr
            )
        for stem in result.orphaned:
            print(
                f"error: {rel}/{stem}.mdx names no registered provider",
                file=sys.stderr,
            )
        return 1

    write_json(GENERATED_DIR / "providers.json", site_data(providers))
    copy_text_statements(providers, GENERATED_DIR / "examples")
    write_json(PAGES_DIR / "meta.json", nav_meta(providers))
    return 0


if __name__ == "__main__":
    sys.exit(main())
