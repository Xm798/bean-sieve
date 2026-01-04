"""Command-line interface for Bean-Sieve."""

from datetime import date, datetime
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from . import api
from .config import load_config
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


@click.group()
@click.version_option(package_name="bean-sieve")
def main():
    """Bean-Sieve: Rule-based statement importer and reconciler for Beancount."""
    pass


@main.command()
@click.argument("files", nargs=-1, required=True, type=click.Path(exists=True))
@click.option(
    "-l",
    "--ledger",
    required=True,
    type=click.Path(exists=True),
    help="Beancount ledger path",
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
@click.option("--predict", is_flag=True, help="Use ML prediction for accounts")
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
    predict,
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
        ledger_path = Path(ledger)
        config_file = Path(config_path) if config_path else None
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
            use_predictor=predict,
        )

        # Display results
        if not quiet:
            _display_result(result, verbose)

            if output_path and not dry_run:
                console.print(f"\n[green]Output written to:[/green] {output_path}")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
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
        console.print(f"[red]Error:[/red] {e}")
        raise click.Abort() from None


@main.command()
@click.argument("files", nargs=-1, required=True, type=click.Path(exists=True))
@click.option(
    "-l",
    "--ledger",
    required=True,
    type=click.Path(exists=True),
    help="Beancount ledger path",
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
        ledger_path = Path(ledger)
        config_file = Path(config_path) if config_path else None
        dr = parse_date_range(date_range)

        # Load config and parse
        config = load_config(config_file) if config_file else None
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
        console.print(f"[red]Error:[/red] {e}")
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
            icon = "✅" if source == "rule" else "🤖" if source == "predict" else "❓"
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

    fieldnames = ["date", "amount", "currency", "description", "payee", "card_suffix"]

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
                    "card_suffix": txn.card_suffix or "",
                }
            )


if __name__ == "__main__":
    main()
