"""Tests for the extract-accounts interactive selection helpers."""

from __future__ import annotations

from bean_sieve import cli
from bean_sieve.config.wizard import PaymentMethod


class _FakeProc:
    def __init__(self, returncode: int, stdout: str):
        self.returncode = returncode
        self.stdout = stdout


def _patch_run(monkeypatch, returncode: int, stdout: str):
    def fake_run(*args, **kwargs):
        return _FakeProc(returncode, stdout)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)


def test_fzf_pick_enter_returns_selection(monkeypatch):
    # With --expect, Enter yields an empty key line followed by the selection.
    _patch_run(monkeypatch, 0, "\nAssets:Bank:CMB\n")
    key, selection = cli._fzf_pick("fzf", ["Assets:Bank:CMB"], "h", "p")
    assert key == ""
    assert selection == "Assets:Bank:CMB"


def test_fzf_pick_esc_is_skip(monkeypatch):
    _patch_run(monkeypatch, 0, "esc\nAssets:Bank:CMB\n")
    key, selection = cli._fzf_pick("fzf", ["Assets:Bank:CMB"], "h", "p")
    assert key == "esc"


def test_fzf_pick_ctrl_q_is_quit(monkeypatch):
    _patch_run(monkeypatch, 0, "ctrl-q\nAssets:Bank:CMB\n")
    key, _ = cli._fzf_pick("fzf", ["Assets:Bank:CMB"], "h", "p")
    assert key == "ctrl-q"


def test_fzf_pick_abort_treated_as_quit(monkeypatch):
    # Ctrl-C aborts fzf with exit code 130.
    _patch_run(monkeypatch, 130, "")
    key, selection = cli._fzf_pick("fzf", ["Assets:Bank:CMB"], "h", "p")
    assert key == "ctrl-q"
    assert selection is None


def test_fzf_pick_missing_binary(monkeypatch):
    def boom(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(cli.subprocess, "run", boom)
    assert cli._fzf_pick("fzf", ["x"], "h", "p") == (None, None)


def _methods(*raws: str) -> list[PaymentMethod]:
    return [PaymentMethod(raw=r, count=1) for r in raws]


def test_interactive_select_maps_chosen_account(monkeypatch):
    monkeypatch.setattr(cli, "_fzf_executable", lambda: "fzf")
    monkeypatch.setattr(cli, "_fzf_pick", lambda *_: ("", "Assets:Bank:CMB"))
    mappings = cli._interactive_select(_methods("method-a"), ["Assets:Bank:CMB"], set())
    assert mappings == [("method-a", "Assets:Bank:CMB")]


def test_interactive_select_esc_skips_without_mapping(monkeypatch):
    monkeypatch.setattr(cli, "_fzf_executable", lambda: "fzf")
    monkeypatch.setattr(cli, "_fzf_pick", lambda *_: ("esc", None))
    mappings = cli._interactive_select(_methods("method-a"), ["Assets:Bank:CMB"], set())
    assert mappings == []


def test_interactive_select_quit_keeps_prior_mappings(monkeypatch):
    monkeypatch.setattr(cli, "_fzf_executable", lambda: "fzf")
    calls = iter([("", "Assets:Bank:CMB"), ("ctrl-q", None)])
    monkeypatch.setattr(cli, "_fzf_pick", lambda *_: next(calls))
    mappings = cli._interactive_select(
        _methods("method-a", "method-b"), ["Assets:Bank:CMB"], set()
    )
    assert mappings == [("method-a", "Assets:Bank:CMB")]


def test_interactive_select_strips_closed_marker(monkeypatch):
    closed = {"Assets:Bank:Old"}
    monkeypatch.setattr(cli, "_fzf_executable", lambda: "fzf")
    monkeypatch.setattr(
        cli, "_fzf_pick", lambda *_: ("", "Assets:Bank:Old" + cli.CLOSED_MARKER)
    )
    mappings = cli._interactive_select(
        _methods("method-a"), ["Assets:Bank:Old"], closed
    )
    assert mappings == [("method-a", "Assets:Bank:Old")]
