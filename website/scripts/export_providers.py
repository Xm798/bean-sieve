"""Export provider registry facts for the docs site.

Writes ``website/generated/providers.json`` (rendered by ``<ProviderFacts>``),
UTF-8 copies of the text statements under ``examples/`` (so pages can embed a
GBK CSV with ``<include>``), and the providers sidebar
``website/content/docs/providers/meta.json``. Exits non-zero when a registered
provider has no page, or a page names no registered provider.

Run from the repository root or ``website/``: ``uv run python website/scripts/export_providers.py``.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from bean_sieve.providers import PROVIDERS
from bean_sieve.providers._tabular import read_text_lines
from bean_sieve.providers.base import BaseProvider

ROOT = Path(__file__).resolve().parents[2]
WEBSITE = ROOT / "website"
EXAMPLES_DIR = ROOT / "examples"
PAGES_DIR = WEBSITE / "content" / "docs" / "providers"
GENERATED_DIR = WEBSITE / "generated"

# Nav groups in sidebar order, keyed by the provider's module package.
CATEGORIES: tuple[tuple[str, str, str, str], ...] = (
    ("payment", "bean_sieve.providers.payment.", "支付平台", "Wallet"),
    ("credit", "bean_sieve.providers.banks.credit.", "信用卡", "CreditCard"),
    ("debit", "bean_sieve.providers.banks.debit.", "借记卡", "Landmark"),
)

# Example files that are not statements; mirrors tests/test_examples.py.
EXAMPLE_RESERVED = {"bean-sieve.yaml", "ledger.bean", "expected.bean"}
TEXT_STATEMENT_SUFFIXES = {".csv", ".har"}
NON_PROVIDER_PAGES = {"index"}


@dataclass(frozen=True)
class PageCheck:
    missing: list[str]
    orphaned: list[str]

    @property
    def ok(self) -> bool:
        return not self.missing and not self.orphaned


def category_of(cls: type[BaseProvider]) -> str:
    for key, prefix, _title, _icon in CATEGORIES:
        if cls.__module__.startswith(prefix):
            return key
    raise ValueError(
        f"{cls.provider_id}: module {cls.__module__} fits no docs nav group; "
        "add one to CATEGORIES in website/scripts/export_providers.py"
    )


def example_statements(provider_id: str) -> list[str]:
    example_dir = EXAMPLES_DIR / provider_id
    if not example_dir.is_dir():
        return []
    return sorted(
        p.name
        for p in example_dir.iterdir()
        if p.is_file() and p.name not in EXAMPLE_RESERVED and not p.name.startswith(".")
    )


def provider_facts(cls: type[BaseProvider]) -> dict[str, object]:
    pattern = cls.filename_pattern
    statements = example_statements(cls.provider_id)
    return {
        "id": cls.provider_id,
        "name": cls.provider_name,
        "category": category_of(cls),
        "formats": list(cls.supported_formats),
        "filenameKeywords": list(cls.filename_keywords),
        "filenamePattern": pattern.pattern if pattern else None,
        "contentKeywords": list(cls.content_keywords),
        "perCardStatement": cls.per_card_statement,
        "presetRules": [rule.name for rule in cls.get_preset_rules()],
        "example": {"statements": statements} if statements else None,
    }


def sorted_providers() -> list[type[BaseProvider]]:
    order = {key: i for i, (key, *_rest) in enumerate(CATEGORIES)}
    return sorted(
        PROVIDERS.values(), key=lambda c: (order[category_of(c)], c.provider_id)
    )


def check_pages(provider_ids: list[str], pages_dir: Path) -> PageCheck:
    pages = {p.stem for p in pages_dir.glob("*.mdx")} - NON_PROVIDER_PAGES
    return PageCheck(
        missing=sorted(set(provider_ids) - pages),
        orphaned=sorted(pages - set(provider_ids)),
    )


def nav_meta(providers: list[type[BaseProvider]]) -> dict[str, object]:
    pages: list[str] = ["index"]
    for key, _prefix, title, icon in CATEGORIES:
        ids = [c.provider_id for c in providers if category_of(c) == key]
        if ids:
            pages.append(f"---[{icon}]{title}---")
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
        for name in example_statements(cls.provider_id):
            source = EXAMPLES_DIR / cls.provider_id / name
            if source.suffix.lower() not in TEXT_STATEMENT_SUFFIXES:
                continue
            target = out_dir / cls.provider_id / name
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

    write_json(GENERATED_DIR / "providers.json", [provider_facts(c) for c in providers])
    copy_text_statements(providers, GENERATED_DIR / "examples")
    write_json(PAGES_DIR / "meta.json", nav_meta(providers))
    return 0


if __name__ == "__main__":
    sys.exit(main())
