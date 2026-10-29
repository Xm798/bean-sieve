"""YAML config snippets in the docs must validate against the JSON Schema.

Every ```yaml fence in README.md and website/content/**/*.mdx carries one
marker word in its info string (after the language, alongside attributes
such as ``title="..."``):

- ``config``: a config file, validated as-is.
- ``config-partial``: an excerpt; ``required`` keywords are skipped.
- ``not-config``: YAML that is not bean-sieve config, left unchecked.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft7Validator
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from bean_sieve.config.schema import Config

ROOT = Path(__file__).parents[1]
SCHEMA = json.loads((ROOT / "bean-sieve.schema.json").read_text(encoding="utf-8"))

MARKERS = ("config", "config-partial", "not-config")
FENCE_OPEN = re.compile(r"^(?P<indent>\s*)(?P<fence>`{3,}|~{3,})(?P<info>.*)$")


@dataclass
class Fence:
    path: Path
    line: int
    lang: str
    words: list[str]
    body: str

    @property
    def location(self) -> str:
        return f"{self.path.relative_to(ROOT)}:{self.line}"


def iter_fences(path: Path, text: str) -> list[Fence]:
    fences: list[Fence] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = FENCE_OPEN.match(lines[i])
        if not m:
            i += 1
            continue
        indent, fence, info = m["indent"], m["fence"], m["info"].strip()
        start = i
        body: list[str] = []
        i += 1
        while i < len(lines):
            if re.fullmatch(rf"{fence[0]}{{{len(fence)},}}", lines[i].strip()):
                break
            body.append(lines[i].removeprefix(indent))
            i += 1
        i += 1
        # Attribute values such as title="a b" may contain spaces, so they
        # are removed before splitting the remaining info string into words.
        words = re.sub(r"""\S+=(?:"[^"]*"|'[^']*'|\S+)""", "", info).split()
        lang = words[0] if words else ""
        fences.append(Fence(path, start + 1, lang, words[1:], "\n".join(body)))
    return fences


def doc_files() -> list[Path]:
    return [ROOT / "README.md", *sorted((ROOT / "website" / "content").rglob("*.mdx"))]


def yaml_fences() -> list[Fence]:
    return [
        fence
        for path in doc_files()
        for fence in iter_fences(path, path.read_text(encoding="utf-8"))
        if fence.lang in ("yaml", "yml")
    ]


def without_required(schema: Any) -> Any:
    if isinstance(schema, dict):
        return {k: without_required(v) for k, v in schema.items() if k != "required"}
    if isinstance(schema, list):
        return [without_required(v) for v in schema]
    return schema


def schema_errors(data: Any, *, partial: bool) -> list[str]:
    validator = Draft7Validator(without_required(SCHEMA) if partial else SCHEMA)
    return [
        f"at {'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
        for e in sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    ]


def check_fence(fence: Fence) -> list[str]:
    markers = [w for w in fence.words if w in MARKERS]
    if len(markers) != 1:
        return [f"needs exactly one of the markers {', '.join(MARKERS)}"]
    if markers[0] == "not-config":
        return []
    try:
        data = YAML(typ="safe").load(fence.body) or {}
    except YAMLError as exc:
        return [f"invalid YAML: {exc}"]
    errors = schema_errors(data, partial=markers[0] == "config-partial")
    if not errors and markers[0] == "config":
        try:
            Config.from_dict(data)
        except Exception as exc:
            errors.append(f"rejected by Config.from_dict: {exc}")
    return errors


FENCES = yaml_fences()


def test_docs_contain_config_snippets() -> None:
    assert any("config" in f.words for f in FENCES)


@pytest.mark.parametrize("fence", FENCES, ids=[f.location for f in FENCES])
def test_doc_yaml_snippet_matches_schema(fence: Fence) -> None:
    errors = check_fence(fence)
    assert not errors, f"YAML fence at {fence.location}:\n  " + "\n  ".join(errors)


def test_example_config_matches_schema() -> None:
    data = YAML(typ="safe").load(
        (ROOT / "bean-sieve.example.yaml").read_text(encoding="utf-8")
    )
    assert schema_errors(data, partial=False) == []


def _fence(info: str, body: str) -> Fence:
    text = f"intro\n\n```{info}\n{body}\n```\n"
    return iter_fences(ROOT / "README.md", text)[0]


def test_fence_location_and_attributes() -> None:
    fence = _fence('yaml title="my config.yaml" config', "defaults: {}")
    assert fence.location == "README.md:3"
    assert fence.lang == "yaml"
    assert fence.words == ["config"]


def test_unmarked_yaml_fence_fails() -> None:
    assert check_fence(_fence("yaml", "defaults: {}"))


def test_not_config_fence_is_skipped() -> None:
    assert check_fence(_fence("yaml not-config", "on: push")) == []


def test_partial_fence_skips_required_only() -> None:
    body = "account_mappings:\n  - pattern: x"
    assert check_fence(_fence("yaml config", body))
    assert check_fence(_fence("yaml config-partial", body)) == []
    assert check_fence(_fence("yaml config-partial", "defaultz: {}"))


@pytest.mark.parametrize(
    "data",
    [
        {"acounts": {}},
        {"defaults": {"curency": "CNY"}},
        {"format": {"indnet": 4}},
        {"account_mappings": [{"pattern": "x", "account": "A", "acount": "B"}]},
        {"rules": [{"description": "x", "contra_acount": "Expenses:A"}]},
        {"providers": {"alipay": {"acounts": {}}}},
        {"providers": {"custom": {"acounts": {}}}},
        {"diagnostics": {"meta_chek": True}},
    ],
)
def test_schema_rejects_misspelled_keys(data: dict[str, Any]) -> None:
    assert schema_errors(data, partial=True)
