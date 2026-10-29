"""Regenerate the synthetic statement files under ``examples/<provider_id>/``.

    uv run --group examples python scripts/examples/generate.py [provider_id ...]

With no arguments every provider that has a generator is rebuilt. Only the
statement file is written; ``bean-sieve.yaml``, ``ledger.bean`` and
``expected.bean`` are left alone, so refresh the goldens afterwards with
``uv run pytest tests/test_examples.py --update-examples`` when the statement
content changed.

The output is deterministic: rerunning on an unchanged generator reproduces
the committed bytes. Examples whose statement was written by hand (boc_debit,
cncbi_debit, hsbchk_credit, hsbchk_debit, wechat) have no generator; their
committed file is the source of truth.
"""

import argparse
import sys
from pathlib import Path

import gen_eml  # noqa: F401
import gen_pdf  # noqa: F401
import gen_sheets  # noqa: F401
import gen_text  # noqa: F401
from _common import GENERATORS

EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate the example statement files."
    )
    parser.add_argument(
        "providers",
        nargs="*",
        metavar="provider_id",
        help=f"default: all of {', '.join(sorted(GENERATORS))}",
    )
    args = parser.parse_args(argv)

    unknown = sorted(set(args.providers) - GENERATORS.keys())
    if unknown:
        parser.error(f"no generator for: {', '.join(unknown)}")

    for provider_id in args.providers or sorted(GENERATORS):
        out_dir = EXAMPLES_DIR / provider_id
        out_dir.mkdir(parents=True, exist_ok=True)
        GENERATORS[provider_id](out_dir)
        print(f"examples/{provider_id}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
