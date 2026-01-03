"""Price Enricher CLI - Enrich video game collection CSV with online price estimates."""

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table

from price_enricher.cache import PriceCache
from price_enricher.io_csv import read_csv, write_csv, preview_csv
from price_enricher.models import CSVLanguage, Language, Region
from price_enricher.pricing import PricingConfig, PricingEngine, apply_enrichment_to_items


# Initialize Typer app
app = typer.Typer(
    name="price-enricher",
    help="Enrich video game collection CSV with online price estimates from eBay and PriceCharting.",
    add_completion=False,
)

console = Console()


def setup_logging(debug: bool = False, verbose: bool = False) -> None:
    """Configure logging with Rich handler."""
    level = logging.DEBUG if debug else (logging.INFO if verbose else logging.WARNING)

    # Configure root logger
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=debug)],
    )

    # Set levels for our modules
    for module in ["price_enricher", "price_enricher.sources.rgp", "price_enricher.sources.ebay"]:
        logging.getLogger(module).setLevel(level)

    # Reduce noise from third-party libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def version_callback(value: bool) -> None:
    """Show version and exit."""
    if value:
        console.print("[bold]Price Enricher[/bold] v0.1.0")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    # Input/Output
    input_file: Optional[Path] = typer.Option(
        None,
        "--input", "-i",
        help="Input CSV file path (video game collection)",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
    output_file: Optional[Path] = typer.Option(
        None,
        "--output", "-o",
        help="Output CSV file path for enriched data",
        dir_okay=False,
    ),

    # Processing limits
    limit: Optional[int] = typer.Option(
        None,
        "--limit", "-n",
        help="Limit number of items to process (for testing)",
        min=1,
    ),
    sleep: float = typer.Option(
        1.5,
        "--sleep", "-s",
        help="Sleep time between API requests (seconds)",
        min=0.5,
        max=10.0,
    ),

    # Cache
    cache_path: Path = typer.Option(
        Path("cache.sqlite"),
        "--cache",
        help="SQLite cache file path",
    ),
    clear_cache: bool = typer.Option(
        False,
        "--clear-cache",
        help="Clear cache before running",
    ),

    # Source selection
    only_source: str = typer.Option(
        "both",
        "--only-source",
        help="Use only specific source: ebay, rgp, or both",
    ),

    # Weights
    weight_ebay: float = typer.Option(
        0.7,
        "--weight-ebay",
        help="Weight for eBay prices in combined estimate (0-1)",
        min=0.0,
        max=1.0,
    ),
    weight_rgp: float = typer.Option(
        0.3,
        "--weight-rgp",
        help="Weight for RetroGamePrices in combined estimate (0-1)",
        min=0.0,
        max=1.0,
    ),

    # eBay options
    include_shipping: bool = typer.Option(
        False,
        "--include-shipping",
        help="Include shipping costs in eBay prices",
    ),
    allow_lots: bool = typer.Option(
        False,
        "--allow-lots",
        help="Allow lot/bundle listings in results",
    ),
    allow_box_only: bool = typer.Option(
        False,
        "--allow-box-only",
        help="Allow box/manual only listings in results",
    ),

    # Region
    default_region: str = typer.Option(
        "PAL",
        "--default-region", "-r",
        help="Default region: PAL, NTSC-U, NTSC-J",
    ),
    region_relaxed: bool = typer.Option(
        False,
        "--region-relaxed",
        help="Allow items without explicit region match",
    ),
    add_region_column: bool = typer.Option(
        False,
        "--add-region-column",
        help="Add Région column to output if not present",
    ),

    # Language
    preferred_language: str = typer.Option(
        "ANY",
        "--preferred-language", "-l",
        help="Preferred language: ANY, EN, FR, DE, IT, ES",
    ),
    strict_language: bool = typer.Option(
        False,
        "--strict-language",
        help="Strictly filter by preferred language",
    ),

    # CSV Language
    csv_language: str = typer.Option(
        "auto",
        "--csv-language",
        help="CSV file language: EN, FR, or auto (auto-detect)",
    ),

    # Processing options
    include_non_game: bool = typer.Option(
        False,
        "--include-non-game",
        help="Process items without game (has_game != Y)",
    ),

    # Debug and verbosity
    debug: bool = typer.Option(
        False,
        "--debug", "-d",
        help="Enable debug output (very verbose)",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose", "-v",
        help="Enable verbose output (show progress details)",
    ),
    preview: bool = typer.Option(
        False,
        "--preview",
        help="Preview input CSV and exit",
    ),

    # Version
    version: bool = typer.Option(
        False,
        "--version", "-V",
        callback=version_callback,
        is_eager=True,
        help="Show version and exit",
    ),
) -> None:
    """
    Enrich a video game collection CSV with online price estimates.

    Queries eBay (sold listings) and PriceCharting.com to estimate
    market values for each game in the collection.

    Example:
        price-enricher --input collection.csv --output enriched.csv --default-region PAL
    """
    # If a subcommand is invoked, skip the main enrichment logic
    if ctx.invoked_subcommand is not None:
        return

    # Setup logging based on verbosity
    setup_logging(debug=debug, verbose=verbose)
    logger = logging.getLogger(__name__)

    # Validate required options for the main enrichment command
    if input_file is None:
        console.print("[red]Error:[/red] Missing required option '--input' / '-i'")
        raise typer.Exit(1)
    if output_file is None:
        console.print("[red]Error:[/red] Missing required option '--output' / '-o'")
        raise typer.Exit(1)

    # Validate source option
    if only_source not in ("ebay", "rgp", "both"):
        console.print("[red]Error:[/red] --only-source must be 'ebay', 'rgp', or 'both'")
        raise typer.Exit(1)

    # Parse region
    try:
        region = Region.from_string(default_region)
    except ValueError:
        console.print(f"[red]Error:[/red] Invalid region: {default_region}")
        raise typer.Exit(1)

    # Parse language
    try:
        language = Language(preferred_language.upper())
    except ValueError:
        console.print(f"[red]Error:[/red] Invalid language: {preferred_language}")
        console.print("Valid options: ANY, EN, FR, DE, IT, ES")
        raise typer.Exit(1)

    # Parse CSV language
    csv_lang: CSVLanguage | None = None
    if csv_language.upper() != "AUTO":
        try:
            csv_lang = CSVLanguage(csv_language.upper())
        except ValueError:
            console.print(f"[red]Error:[/red] Invalid CSV language: {csv_language}")
            console.print("Valid options: EN, FR, or auto")
            raise typer.Exit(1)

    # Check eBay credentials if needed
    ebay_available = bool(os.environ.get("EBAY_APP_ID"))
    if only_source in ("ebay", "both"):
        if not ebay_available:
            if only_source == "ebay":
                console.print("[red]Error:[/red] eBay source requires EBAY_APP_ID environment variable")
                console.print("Get your API key at: https://developer.ebay.com/")
                raise typer.Exit(1)
            else:
                console.print("[yellow]Note:[/yellow] EBAY_APP_ID not set - using PriceCharting only")
                console.print("  Set EBAY_APP_ID environment variable to enable eBay pricing")
                only_source = "rgp"
                logger.info("eBay disabled: EBAY_APP_ID not set")

    # Preview mode
    if preview:
        console.print(Panel(f"[bold]Preview:[/bold] {input_file}"))
        console.print(preview_csv(input_file, n_rows=10))
        raise typer.Exit()

    # Show configuration
    csv_lang_display = csv_lang.value if csv_lang else "auto"
    console.print(Panel.fit(
        f"[bold]Price Enricher[/bold]\n\n"
        f"Input:  {input_file}\n"
        f"Output: {output_file}\n"
        f"Source: {only_source}\n"
        f"Region: {region.value}\n"
        f"Language: {language.value}\n"
        f"CSV Language: {csv_lang_display}",
        title="Configuration",
    ))

    # Initialize cache
    cache = PriceCache(cache_path)
    if clear_cache:
        cleared = cache.clear_all()
        console.print(f"[yellow]Cleared {cleared} cache entries[/yellow]")

    # Read input CSV
    with console.status("[bold green]Reading CSV..."):
        try:
            items, original_columns, encoding, delimiter, detected_csv_lang = read_csv(
                input_file,
                default_region=region,
                include_non_game=include_non_game,
                language=csv_lang,
            )
        except Exception as e:
            console.print(f"[red]Error reading CSV:[/red] {e}")
            if debug:
                console.print_exception()
            raise typer.Exit(1)

    console.print(f"Loaded [bold]{len(items)}[/bold] items from CSV (language: {detected_csv_lang.value})")

    # Filter to processable items
    processable = [item for item in items if item.is_processable or include_non_game]
    console.print(f"Processable items: [bold]{len(processable)}[/bold]")

    # Apply limit if specified
    if limit:
        processable = processable[:limit]
        console.print(f"Limited to first [bold]{limit}[/bold] items")

    if not processable:
        console.print("[yellow]No items to process[/yellow]")
        raise typer.Exit()

    # Show item summary
    if debug:
        table = Table(title="Items to Process")
        table.add_column("Platform")
        table.add_column("Title")
        table.add_column("Region")
        table.add_column("State")

        for item in processable[:10]:
            table.add_row(
                item.platform,
                item.title[:40] + "..." if len(item.title) > 40 else item.title,
                item.region.value,
                item.packaging_state.value,
            )

        if len(processable) > 10:
            table.add_row("...", f"({len(processable) - 10} more)", "...", "...")

        console.print(table)

    # Create pricing config
    config = PricingConfig(
        only_source=only_source,  # type: ignore
        weight_ebay=weight_ebay,
        weight_rgp=weight_rgp,
        ebay_app_id=os.environ.get("EBAY_APP_ID"),
        strict_region=not region_relaxed,
        allow_lots=allow_lots,
        allow_box_only=allow_box_only,
        include_shipping=include_shipping,
        preferred_language=language,
        strict_language=strict_language,
        sleep_seconds=sleep,
        include_non_game=include_non_game,
    )
    config.validate()

    # Create pricing engine
    engine = PricingEngine(config=config, cache=cache, console=console)

    # Process items
    console.print("\n[bold]Processing items...[/bold]")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Enriching", total=len(processable))

        # Run async processing
        results = asyncio.run(
            engine.enrich_batch(processable, progress=progress, task_id=task)
        )

    # Apply results back to items
    items = apply_enrichment_to_items(items, results)

    # Summary statistics
    successful = sum(1 for r in results if r.success)
    failed = len(results) - successful

    console.print(f"\n[bold]Results:[/bold]")
    console.print(f"  Successful: [green]{successful}[/green]")
    console.print(f"  Failed: [red]{failed}[/red]")

    # Calculate average price for successful items
    prices = [r.final_estimate_eur for r in results if r.final_estimate_eur]
    if prices:
        avg_price = sum(prices) / len(prices)
        min_price = min(prices)
        max_price = max(prices)
        console.print(f"  Average: [bold]{avg_price:.2f} EUR[/bold]")
        console.print(f"  Range: {min_price:.2f} - {max_price:.2f} EUR")

    # Write output CSV
    with console.status("[bold green]Writing output CSV..."):
        try:
            # Create output directory if needed
            output_file.parent.mkdir(parents=True, exist_ok=True)

            write_csv(
                output_file,
                items,
                original_columns,
                encoding=encoding,
                delimiter=delimiter,
                add_region_column=add_region_column,
                language=detected_csv_lang,
            )
        except Exception as e:
            console.print(f"[red]Error writing CSV:[/red] {e}")
            if debug:
                console.print_exception()
            raise typer.Exit(1)

    console.print(f"\n[green]✓[/green] Output written to: [bold]{output_file}[/bold]")

    # Show cache stats
    if debug:
        stats = cache.get_stats()
        console.print(f"\n[dim]Cache stats: {stats}[/dim]")


@app.command("cache-stats")
def cache_stats(
    cache_path: Path = typer.Option(
        Path("cache.sqlite"),
        "--cache",
        help="SQLite cache file path",
    ),
) -> None:
    """Show cache statistics."""
    if not cache_path.exists():
        console.print("[yellow]Cache file does not exist[/yellow]")
        raise typer.Exit()

    cache = PriceCache(cache_path)
    stats = cache.get_stats()

    table = Table(title="Cache Statistics")
    table.add_column("Namespace")
    table.add_column("Entries")
    table.add_column("Hits")

    for ns, data in stats["namespaces"].items():
        table.add_row(ns, str(data["count"]), str(data["hits"]))

    console.print(table)
    console.print(f"\nExpired entries: {stats['expired_entries']}")
    console.print(f"Database size: {stats['db_size_bytes'] / 1024:.1f} KB")


@app.command("clear-cache")
def clear_cache_cmd(
    cache_path: Path = typer.Option(
        Path("cache.sqlite"),
        "--cache",
        help="SQLite cache file path",
    ),
    namespace: Optional[str] = typer.Option(
        None,
        "--namespace", "-n",
        help="Clear only specific namespace (ebay, rgp, fx)",
    ),
) -> None:
    """Clear the price cache."""
    if not cache_path.exists():
        console.print("[yellow]Cache file does not exist[/yellow]")
        raise typer.Exit()

    cache = PriceCache(cache_path)

    if namespace:
        cleared = cache.clear_namespace(namespace)
        console.print(f"Cleared {cleared} entries from namespace '{namespace}'")
    else:
        cleared = cache.clear_all()
        console.print(f"Cleared {cleared} total entries")


if __name__ == "__main__":
    app()
