"""``BaseProvider.can_handle``: which rules a provider must satisfy to claim a file."""

from pathlib import Path

from bean_sieve.providers.base import BaseProvider


class _ExtensionOnly(BaseProvider):
    provider_id = "fake_extension_only"
    supported_formats = [".xls"]

    def parse(self, file_path: Path):  # noqa: ARG002
        return []


class _CustomContent(_ExtensionOnly):
    provider_id = "fake_custom_content"

    @classmethod
    def _match_content(cls, file_path: Path) -> bool:
        return file_path.read_text() == "marker"


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path


def test_extension_alone_suffices_without_any_rule(tmp_path):
    assert _ExtensionOnly.can_handle(_write(tmp_path, "a.xls", "other"))


def test_overridden_match_content_is_required(tmp_path):
    assert _CustomContent.can_handle(_write(tmp_path, "a.xls", "marker"))
    assert not _CustomContent.can_handle(_write(tmp_path, "b.xls", "other"))


def test_extension_is_checked_before_content(tmp_path):
    assert not _CustomContent.can_handle(_write(tmp_path, "a.csv", "marker"))
