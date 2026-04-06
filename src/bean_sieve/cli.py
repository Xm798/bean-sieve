"""Command-line interface for Bean-Sieve."""

import os
import platform
from datetime import date, datetime
from pathlib import Path

import click
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from . import api
from .config import load_config
from .config.schema import Config
from .config.wizard import (
    extract_payment_methods,
    load_accounts_from_ledger,
    smart_sort_accounts,
)
from .providers import list_providers

console = Console()


def parse_date_range(date_range: str | None) -> tuple[date, date] | None:
    """Parse date range string (START:END) into tuple."""
    if not date_range:
        return None

    try:
        start_str, end_str = date_range.split(":")
        start = datetime.strptime(start_str.strip(), "%Y-%m-%d").date()
        end = datetime.strptime(end_str.strip(), "%Y-%m-%d").date()
        return (start, end)
    except ValueError as e:
        raise click.BadParameter(
            f"Invalid date range format. Use YYYY-MM-DD:YYYY-MM-DD. Error: {e}"
        ) from None


DEFAULT_CONFIG_NAME = "bean-sieve.yaml"
XDG_CONFIG_NAME = "config.yaml"


def get_config_search_paths() -> list[Path]:
    """Get config file search paths in priority order.

    Search order:
    1. Current working directory (bean-sieve.yaml)
    2. XDG_CONFIG_HOME/bean-sieve/config.yaml (Linux/macOS)
       or APPDATA/bean-sieve/config.yaml (Windows)
    3. ~/.config/bean-sieve/config.yaml (Linux/macOS fallback)
    """
    paths = [Path.cwd() / DEFAULT_CONFIG_NAME]

    if platform.system() == "Windows":
        appdata = os.environ.get("APPDATA")
        if appdata:
            paths.append(Path(appdata) / "bean-sieve" / XDG_CONFIG_NAME)
    else:
        xdg_config = os.environ.get("XDG_CONFIG_HOME")
        if xdg_config:
            paths.append(Path(xdg_config) / "bean-sieve" / XDG_CONFIG_NAME)
        paths.append(Path.home() / ".config" / "bean-sieve" / XDG_CONFIG_NAME)

    return paths


def resolve_config_path(config_path: str | None) -> Path | None:
    """Resolve config file path, auto-detecting default if not specified."""
    if config_path:
        return Path(config_path)

    for path in get_config_search_paths():
        if path.exists():
            return path

    return None


def resolve_ledger_path(
    ledger: str | None, config: Config | None, config_path: Path | None
) -> Path:
    """Resolve ledger path from CLI option or config file."""
    if ledger:
        return Path(ledger).expanduser()

    if config and config.defaults.ledger:
        ledger_from_config = Path(config.defaults.ledger).expanduser()
        # Resolve relative paths relative to config file location
        if config_path and not ledger_from_config.is_absolute():
            ledger_from_config = config_path.parent / ledger_from_config
        if not ledger_from_config.exists():
            raise click.UsageError(
                f"Ledger file not found: {ledger_from_config} "
                f"(from config: {config.defaults.ledger})"
            )
        return ledger_from_config

    raise click.UsageError(
        "Ledger path required: use -l/--ledger option or set defaults.ledger in config"
    )


@click.group()
@click.version_option(package_name="bean-sieve")
def main():
    """Bean-Sieve: Rule-based statement importer and reconciler for Beancount."""
    pass


@main.command()
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]))
def completion(shell: str):
    """Generate shell completion script.

    \b
    Setup:
      Bash: eval "$(bean-sieve completion bash)"  # add to ~/.bashrc
      Zsh:  eval "$(bean-sieve completion zsh)"   # add to ~/.zshrc
      Fish: bean-sieve completion fish > ~/.config/fish/completions/bean-sieve.fish
    """
    import importlib.metadata

    from click.shell_completion import get_completion_class

    # Get the completion class for the specified shell
    comp_cls = get_completion_class(shell)
    if comp_cls is None:
        raise click.ClickException(f"Unsupported shell: {shell}")

    # Get package info for the prog_name
    try:
        entry_points = importlib.metadata.entry_points(group="console_scripts")
        prog_name = "bean-sieve"
        for ep in entry_points:
            if ep.value == "bean_sieve.cli:main":
                prog_name = ep.name
                break
    except Exception:
        prog_name = "bean-sieve"

    comp = comp_cls(main, {}, prog_name, "_BEAN_SIEVE_COMPLETE")
    click.echo(comp.source())


@main.command()
@click.argument("files", nargs=-1, required=True, type=click.Path(exists=True))
@click.option(
    "-l",
    "--ledger",
    type=click.Path(exists=True),
    help="Beancount ledger path (or set defaults.ledger in config)",
)
@click.option(
    "-c",
    "--config",
    "config_path",
    type=click.Path(exists=True),
    help="Config file path",
)
@click.option(
    "-o", "--output", default="pending.bean", type=click.Path(), help="Output file path"
)
@click.option("-p", "--provider", help="Provider ID (auto-detect if not specified)")
@click.option("--date-range", help="Date range filter (YYYY-MM-DD:YYYY-MM-DD)")
@click.option("--account-filter", help="Filter ledger to accounts with this prefix")
@click.option("--dry-run", is_flag=True, help="Show results without writing file")
@click.option("-v", "--verbose", is_flag=True, help="Verbose output")
@click.option("-q", "--quiet", is_flag=True, help="Quiet mode")
def reconcile(
    files,
    ledger,
    config_path,
    output,
    provider,
    date_range,
    account_filter,
    dry_run,
    verbose,
    quiet,
):
    """
    Reconcile statement files against ledger.

    Parse statement files, match against existing ledger entries,
    and generate Beancount entries for missing transactions.
    """
    try:
        # Parse inputs
        file_paths = [Path(f) for f in files]
        config_file = resolve_config_path(config_path)
        config = load_config(config_file) if config_file else None
        ledger_path = resolve_ledger_path(ledger, config, config_file)
        output_path = None if dry_run else Path(output)
        dr = parse_date_range(date_range)

        if not quiet:
            console.print(f"[bold]Processing {len(file_paths)} file(s)...[/bold]")

        # Run reconciliation
        result = api.full_reconcile(
            statement_paths=file_paths,
            ledger_path=ledger_path,
            config_path=config_file,
            output_path=output_path,
            provider_id=provider,
            date_range=dr,
            account_filter=account_filter,
        )

        # Display results
        if not quiet:
            _display_result(result, verbose)

            if output_path and not dry_run:
                console.print(f"\n[green]Output written to:[/green] {output_path}")

    except Exception as e:
        console.print(f"[red]Error:[/red] {escape(str(e))}")
        raise click.Abort() from None


@main.command()
@click.argument("files", nargs=-1, required=True, type=click.Path(exists=True))
@click.option("-p", "--provider", help="Provider ID")
@click.option(
    "-f",
    "--format",
    "output_format",
    type=click.Choice(["table", "json", "csv"]),
    default="table",
    help="Output format",
)
@click.option("-o", "--output", type=click.Path(), help="Output file (default: stdout)")
def parse(files, provider, output_format, output):
    """
    Parse statement files without reconciliation.

    Useful for debugging and testing providers.
    """
    import json

    try:
        file_paths = [Path(f) for f in files]
        transactions = api.parse_statements(file_paths, provider)

        if output_format == "table":
            _display_transactions_table(transactions)
        elif output_format == "json":
            data = [t.to_dict() for t in transactions]
            content = json.dumps(data, indent=2, ensure_ascii=False)
            if output:
                Path(output).write_text(content)
            else:
                console.print(content)
        elif output_format == "csv":
            _output_csv(transactions, Path(output) if output else None)

        console.print(f"\n[bold]Total:[/bold] {len(transactions)} transactions")

    except Exception as e:
        console.print(f"[red]Error:[/red] {escape(str(e))}")
        raise click.Abort() from None


@main.command()
@click.argument("files", nargs=-1, required=True, type=click.Path(exists=True))
@click.option("-p", "--provider", help="Provider ID (auto-detect if not specified)")
@click.option("-o", "--output", type=click.Path(), help="Output file path")
@click.option(
    "-f",
    "--format",
    "output_format",
    type=click.Choice(["csv", "xlsx"]),
    default="csv",
    help="Output format (default: csv)",
)
def export(files, provider, output, output_format):
    """
    Export parsed transactions to CSV/XLSX format.

    Pure parsing and export, no reconciliation logic.
    Each input file is exported separately with auto-generated filename if -o not specified.
    """
    from .core.export import export_csv, export_xlsx, generate_export_filename

    try:
        file_paths = [Path(f) for f in files]

        for file_path in file_paths:
            transactions = api.parse_statements([file_path], provider)

            if not transactions:
                console.print(f"[yellow]No transactions in {file_path.name}[/yellow]")
                continue

            # Determine output path
            if output and len(file_paths) == 1:
                out_path = Path(output)
            else:
                out_path = generate_export_filename(file_path, output_format)

            # Export
            if output_format == "csv":
                export_csv(transactions, out_path)
            else:
                export_xlsx(transactions, out_path)

            console.print(
                f"[green]{file_path.name}[/green] → {out_path.name} "
                f"({len(transactions)} transactions)"
            )

    except Exception as e:
        console.print(f"[red]Error:[/red] {escape(str(e))}")
        raise click.Abort() from None


@main.command()
@click.argument("files", nargs=-1, required=True, type=click.Path(exists=True))
@click.option(
    "-l",
    "--ledger",
    type=click.Path(exists=True),
    help="Beancount ledger path (or set defaults.ledger in config)",
)
@click.option(
    "-c",
    "--config",
    "config_path",
    type=click.Path(exists=True),
    help="Config file path",
)
@click.option("-p", "--provider", help="Provider ID")
@click.option("--date-range", help="Date range filter (YYYY-MM-DD:YYYY-MM-DD)")
def check(files, ledger, config_path, provider, date_range):
    """
    Check reconciliation status without generating output.

    Shows matched, missing, and extra transactions.
    """
    try:
        file_paths = [Path(f) for f in files]
        config_file = resolve_config_path(config_path)
        config = load_config(config_file) if config_file else None
        ledger_path = resolve_ledger_path(ledger, config, config_file)
        dr = parse_date_range(date_range)
        transactions = api.parse_statements(file_paths, provider)

        if dr:
            transactions = [t for t in transactions if dr[0] <= t.date <= dr[1]]

        # Load ledger and match
        sieve = api.load_ledger(
            ledger_path,
            date_range=dr,
            date_tolerance=config.defaults.date_tolerance if config else 2,
        )
        match_result = sieve.match(transactions)

        # Display results
        _display_check_result(match_result)

    except Exception as e:
        console.print(f"[red]Error:[/red] {escape(str(e))}")
        raise click.Abort() from None


@main.command("providers")
def list_providers_cmd():
    """List available statement providers."""
    providers = list_providers()

    if not providers:
        console.print("[yellow]No providers registered.[/yellow]")
        console.print("Providers will be available after implementation.")
        return

    table = Table(title="Available Providers")
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="green")
    table.add_column("Formats", style="yellow")

    for p in providers:
        table.add_row(p["id"], p["name"], p["formats"])

    console.print(table)


@main.command("extract-accounts")
@click.argument("files", nargs=-1, required=True, type=click.Path(exists=True))
@click.option(
    "-l",
    "--ledger",
    type=click.Path(exists=True),
    help="Beancount ledger path (or set defaults.ledger in config)",
)
@click.option("-p", "--provider", help="Provider ID")
@click.option(
    "-o",
    "--output",
    type=click.Path(),
    default="bean-sieve.yaml",
    help="Output YAML file",
)
@click.option("--non-interactive", is_flag=True, help="Skip interactive selection")
def extract_accounts(files, ledger, provider, output, non_interactive):
    """
    Extract payment methods and generate account mappings interactively.

    Parses statement files, extracts unique payment methods (e.g., "建设银行信用卡(0800)"),
    and lets you select corresponding accounts using fzf fuzzy search.
    """
    try:
        file_paths = [Path(f) for f in files]
        output_path = Path(output)

        # Load existing config (also used to resolve ledger path)
        existing_config = load_config(output_path) if output_path.exists() else None
        ledger_path = resolve_ledger_path(ledger, existing_config, output_path)

        # Skip already-configured patterns
        existing_patterns: set[str] = set()
        if existing_config:
            existing_patterns = {m.pattern for m in existing_config.account_mappings}
            if existing_patterns:
                console.print(
                    f"[dim]已有 {len(existing_patterns)} 个配置，将跳过[/dim]"
                )

        # Parse statements
        console.print(f"[bold]Parsing {len(file_paths)} file(s)...[/bold]")
        transactions = api.parse_statements(file_paths, provider)

        if not transactions:
            console.print("[yellow]No transactions found.[/yellow]")
            return

        # Extract payment methods (skip already configured, case-insensitive dedup)
        methods = extract_payment_methods(transactions, existing_patterns)

        if not methods:
            console.print("[yellow]No new payment methods to configure.[/yellow]")
            return

        console.print(
            Panel(
                f"发现 [bold cyan]{len(methods)}[/bold cyan] 个新的支付方式",
                title="Account Extraction",
            )
        )

        # Load accounts from ledger
        accounts, closed = load_accounts_from_ledger(ledger_path)
        if not accounts:
            console.print("[red]No accounts found in ledger.[/red]")
            return

        console.print(f"Loaded [cyan]{len(accounts)}[/cyan] accounts from ledger\n")

        if non_interactive:
            # Non-interactive mode: just output template
            _output_template(methods, output)
            return

        # Interactive mode with fzf
        mappings = _interactive_select(methods, accounts, closed)

        if not mappings:
            console.print("[yellow]No mappings created.[/yellow]")
            return

        # Merge with existing config and save
        import shutil

        from .config.schema import get_yaml

        yaml = get_yaml()

        config_data: dict = {}
        if output_path.exists():
            # Create backup before modifying
            backup_path = output_path.with_suffix(".yaml.bak")
            shutil.copy2(output_path, backup_path)
            console.print(f"[dim]Backup saved to {backup_path}[/dim]")

            with open(output_path, encoding="utf-8") as f:
                config_data = yaml.load(f) or {}

        # Append new mappings
        if "account_mappings" not in config_data:
            config_data["account_mappings"] = []

        for pattern, account in mappings:
            config_data["account_mappings"].append(
                {"pattern": pattern, "account": account}
            )

        with open(output_path, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f)
        console.print(f"\n[green]Config written to:[/green] {output}")

    except Exception as e:
        console.print(f"[red]Error:[/red] {escape(str(e))}")
        raise click.Abort() from None


CLOSED_MARKER = "[CLOSED]"


def _interactive_select(methods, accounts, closed: set[str]) -> list[tuple[str, str]]:
    """Interactive account selection using fzf."""
    try:
        from iterfzf import iterfzf
    except ImportError:
        console.print("[red]iterfzf not installed. Run: pip install iterfzf[/red]")
        console.print("[yellow]Also ensure fzf is installed on your system.[/yellow]")
        return []

    mappings = []
    total = len(methods)

    for i, method in enumerate(methods, 1):
        # Build header for fzf
        header_lines = [f"[{i}/{total}] {method.raw}"]
        # Only show hint if we detected card type (credit/debit)
        if method.is_credit is not None:
            card_type = "信用卡" if method.is_credit else "储蓄卡"
            header_lines.append(f"  → {card_type}")
        header = "\n".join(header_lines)

        # Smart sort accounts (closed accounts go to the end)
        sorted_accounts = smart_sort_accounts(accounts, method, closed)

        # Mark closed accounts
        display_accounts = [
            acc + CLOSED_MARKER if acc in closed else acc for acc in sorted_accounts
        ]

        # Add special options
        options = display_accounts + ["[s] 跳过", "[q] 退出"]

        # fzf selection with header (half screen)
        selected = iterfzf(
            options,
            prompt="选择账户 → ",
            __extra__=["--header", header, "--height=50%"],
        )

        if selected is None or selected == "[q] 退出":
            console.print("[yellow]已退出[/yellow]")
            break
        elif selected == "[s] 跳过":
            console.print(f"[dim]{escape(method.raw)} → 已跳过[/dim]", emoji=False)
            continue
        else:
            # Strip closed marker before saving
            assert isinstance(selected, str)
            account = selected.removesuffix(CLOSED_MARKER)
            mappings.append((method.raw, account))
            console.print(
                f"[green]{escape(method.raw)} → {escape(account)}[/green]", emoji=False
            )

    return mappings


def _output_template(methods, output_path):
    """Output non-interactive template."""
    lines = ["# 发现以下支付方式，请补充对应账户：", "account_mappings:"]

    for method in methods:
        lines.append(f'  - pattern: "{method.raw}"')
        lines.append(f'    account: ""  # 出现 {method.count} 次')

    content = "\n".join(lines)

    if output_path:
        Path(output_path).write_text(content, encoding="utf-8")
        console.print(f"[green]Template written to:[/green] {output_path}")
    else:
        console.print(content)


def _display_result(result, verbose: bool = False):
    """Display reconciliation result."""
    mr = result.match_result

    # Summary
    console.print("\n[bold]Summary:[/bold]")
    console.print(f"  [green]✅ Matched:[/green] {len(mr.matched)}")
    console.print(f"  [yellow]⚠️  Missing:[/yellow] {len(mr.missing)}")
    console.print(f"  [red]❓ Extra:[/red] {len(mr.extra)}")

    # Categorization stats
    if result.processed:
        by_source = {}
        for t in result.processed:
            source = t.match_source.value if t.match_source else "unknown"
            by_source[source] = by_source.get(source, 0) + 1

        console.print("\n[bold]Categorization:[/bold]")
        for source, count in sorted(by_source.items()):
            icon = "✅" if source == "rule" else "❓"
            console.print(f"  {icon} {source}: {count}")

    # Show missing transactions if verbose
    if verbose and mr.missing:
        console.print(f"\n[bold]Missing transactions ({len(mr.missing)}):[/bold]")
        for txn in sorted(mr.missing, key=lambda t: t.date)[:20]:
            console.print(
                f"  {txn.date}  {txn.amount:>10} {txn.currency}  {txn.description[:40]}"
            )
        if len(mr.missing) > 20:
            console.print(f"  ... and {len(mr.missing) - 20} more")


def _display_check_result(match_result):
    """Display check command result."""
    console.print("\n[bold]Reconciliation Check:[/bold]")
    console.print(f"  [green]✅ Matched:[/green] {len(match_result.matched)}")
    console.print(f"  [yellow]⚠️  Missing:[/yellow] {len(match_result.missing)}")
    console.print(f"  [red]❓ Extra:[/red] {len(match_result.extra)}")

    if match_result.missing:
        console.print("\n[bold]Missing transactions:[/bold]")
        for txn in sorted(match_result.missing, key=lambda t: t.date)[:10]:
            console.print(
                f"  {txn.date}  {txn.amount:>10} {txn.currency}  {txn.description[:40]}"
            )
        if len(match_result.missing) > 10:
            console.print(f"  ... and {len(match_result.missing) - 10} more")

    if match_result.extra:
        console.print("\n[bold]Extra entries in ledger:[/bold]")
        for entry in match_result.extra[:10]:
            txn = entry.txn
            posting = entry.posting
            amount = posting.units.number if posting.units else "?"
            console.print(f"  {txn.date}  {amount:>10}  {posting.account}")
        if len(match_result.extra) > 10:
            console.print(f"  ... and {len(match_result.extra) - 10} more")


def _display_transactions_table(transactions):
    """Display transactions as a table."""
    table = Table(title="Parsed Transactions")
    table.add_column("Date", style="cyan")
    table.add_column("Amount", justify="right", style="green")
    table.add_column("Description", style="white")
    table.add_column("Payee", style="yellow")

    for txn in transactions[:50]:  # Limit display
        table.add_row(
            str(txn.date),
            f"{txn.amount} {txn.currency}",
            txn.description[:40],
            txn.payee or "",
        )

    console.print(table)
    if len(transactions) > 50:
        console.print(f"[dim]... and {len(transactions) - 50} more[/dim]")


def _output_csv(transactions, output_path):
    """Output transactions as CSV."""
    import contextlib
    import csv
    import sys

    fieldnames = ["date", "amount", "currency", "description", "payee", "card_last4"]

    @contextlib.contextmanager
    def open_output():
        if output_path:
            with open(output_path, "w", newline="", encoding="utf-8") as f:
                yield f
        else:
            yield sys.stdout

    with open_output() as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for txn in transactions:
            writer.writerow(
                {
                    "date": txn.date.isoformat(),
                    "amount": str(txn.amount),
                    "currency": txn.currency,
                    "description": txn.description,
                    "payee": txn.payee or "",
                    "card_last4": txn.card_last4 or "",
                }
            )


if __name__ == "__main__":
    main()
