"""Golden tests over ``examples/<provider_id>/``.

Each example directory holds a synthetic statement, a ``bean-sieve.yaml``, a
``ledger.bean`` and the ``expected.bean`` that ``bean-sieve reconcile`` writes
for them. Regenerate the expected files with
``uv run pytest tests/test_examples.py --update-examples``.
"""

import difflib
import os
from datetime import datetime
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from bean_sieve.api import full_reconcile
from bean_sieve.core import output
from bean_sieve.providers import PROVIDERS, resolve_providers
from tests.test_doc_snippets import schema_errors
from tests.website_exporter import load_exporter

layout = load_exporter()
EXAMPLES_DIR = layout.EXAMPLES_DIR
CONFIG_NAME = layout.CONFIG_NAME
LEDGER_NAME = layout.LEDGER_NAME
EXPECTED_NAME = layout.EXPECTED_NAME


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: ARG003
        return cls(2000, 1, 1, 0, 0, 0)


def _example_dirs() -> list[Path]:
    return sorted(p for p in EXAMPLES_DIR.iterdir() if p.is_dir())


def test_every_registered_provider_has_an_example() -> None:
    result = layout.check_examples(sorted(PROVIDERS), EXAMPLES_DIR)
    assert result.missing == [], "add examples/<provider_id>/"
    assert result.orphaned == [], "rename or remove examples/<dir>/"


# Provider pages embed these configs as complete files, so they get the full
# schema, including `required`.
@pytest.mark.parametrize("example_dir", _example_dirs(), ids=lambda p: p.name)
def test_example_config_matches_schema(example_dir: Path) -> None:
    config_path = example_dir / CONFIG_NAME
    data = YAML(typ="safe").load(config_path.read_text(encoding="utf-8")) or {}
    assert schema_errors(data, partial=False) == []


@pytest.mark.parametrize("example_dir", _example_dirs(), ids=lambda p: p.name)
def test_example_output(
    example_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    statements = layout.statement_paths(example_dir)
    assert statements, f"{example_dir} has no statement file"

    # The directory name is the provider id, and auto-detection must pick it,
    # so the example also documents a plain `bean-sieve reconcile` invocation.
    detected = {
        provider.provider_id for _, provider in resolve_providers(statements).files
    }
    assert detected == {example_dir.name}

    monkeypatch.setattr(output, "datetime", _FrozenDatetime)
    out_path = tmp_path / EXPECTED_NAME
    full_reconcile(
        statements,
        example_dir / LEDGER_NAME,
        config_path=example_dir / CONFIG_NAME,
        output_path=out_path,
    )
    # Extra entries cite the ledger by absolute path, which differs per checkout.
    actual = out_path.read_text(encoding="utf-8").replace(
        os.path.abspath(example_dir) + os.sep, ""
    )

    expected_path = example_dir / EXPECTED_NAME
    if request.config.getoption("--update-examples"):
        expected_path.write_text(actual, encoding="utf-8")
        return

    assert expected_path.exists(), (
        f"{expected_path} is missing; run pytest with --update-examples"
    )
    expected = expected_path.read_text(encoding="utf-8")
    if actual != expected:
        diff = "".join(
            difflib.unified_diff(
                expected.splitlines(keepends=True),
                actual.splitlines(keepends=True),
                fromfile=str(expected_path.relative_to(EXAMPLES_DIR.parent)),
                tofile="actual",
            )
        )
        pytest.fail(
            "output differs from the expected file "
            "(run pytest with --update-examples to accept it):\n" + diff,
            pytrace=False,
        )
