"""Pricing orchestrator: combines sources, calculates weighted average."""

import asyncio
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from rich.console import Console
from rich.progress import Progress, TaskID

from price_enricher.cache import PriceCache
from price_enricher.fx import FXConverter
from price_enricher.models import (
    EnrichmentResult,
    GameItem,
    Language,
    PackagingState,
    PriceResult,
    PriceSource,
)
from price_enricher.sources.ebay import get_ebay_price
from price_enricher.sources.rgp import get_rgp_price


@dataclass
class PricingConfig:
    """Configuration for pricing operations."""

    # Source selection
    only_source: Literal["ebay", "rgp", "both"] = "both"

    # Weights for combining sources
    weight_ebay: float = 0.7
    weight_rgp: float = 0.3

    # eBay specific
    ebay_app_id: str | None = None
    strict_region: bool = True
    allow_lots: bool = False
    allow_box_only: bool = False
    include_shipping: bool = False

    # Language
    preferred_language: Language = Language.ANY
    strict_language: bool = False

    # Rate limiting
    sleep_seconds: float = 1.5  # For eBay
    rgp_sleep_seconds: float = 12.0  # For PriceCharting (rate limits aggressively)

    # Processing
    include_non_game: bool = False

    def validate(self) -> None:
        """Validate configuration."""
        if self.weight_ebay + self.weight_rgp != 1.0:
            # Normalize weights
            total = self.weight_ebay + self.weight_rgp
            self.weight_ebay = self.weight_ebay / total
            self.weight_rgp = self.weight_rgp / total


class PricingEngine:
    """
    Main pricing engine that orchestrates price lookups from multiple sources.

    Handles:
    - Source selection (eBay, RGP, or both)
    - Weighted average calculation
    - Currency conversion
    - Result formatting
    """

    def __init__(
        self,
        config: PricingConfig,
        cache: PriceCache | None = None,
        console: Console | None = None,
    ):
        """
        Initialize pricing engine.

        Args:
            config: Pricing configuration
            cache: Optional cache instance
            console: Rich console for output
        """
        self.config = config
        self.cache = cache
        self.console = console or Console()
        self.fx_converter = FXConverter(cache)

    async def enrich_item(self, item: GameItem) -> EnrichmentResult:
        """
        Enrich a single game item with price data.

        Args:
            item: Game item to enrich

        Returns:
            EnrichmentResult with price data and details
        """
        result = EnrichmentResult(game=item)

        # Skip if item is not processable (no game)
        if not item.is_processable and not self.config.include_non_game:
            result.calculation_details = "Skipped: No game present (has_game != Y)"
            return result

        # Get eBay price
        if self.config.only_source in ("ebay", "both"):
            try:
                result.ebay_result = await get_ebay_price(
                    item=item,
                    app_id=self.config.ebay_app_id,
                    cache=self.cache,
                    language=self.config.preferred_language,
                    strict_region=self.config.strict_region,
                    allow_lots=self.config.allow_lots,
                    allow_box_only=self.config.allow_box_only,
                    include_shipping=self.config.include_shipping,
                    sleep_seconds=self.config.sleep_seconds,
                )
            except Exception as e:
                result.ebay_result = PriceResult(
                    source=PriceSource.EBAY,
                    success=False,
                    error=str(e),
                    details=f"eBay error: {str(e)}",
                )

        # Get RGP price
        if self.config.only_source in ("rgp", "both"):
            try:
                rgp_result = await get_rgp_price(
                    item=item,
                    cache=self.cache,
                    sleep_seconds=self.config.rgp_sleep_seconds,
                )

                # Convert RGP price from USD to EUR
                if rgp_result.success and rgp_result.price_eur:
                    rgp_result.price_eur = await self.fx_converter.convert_to_eur(
                        rgp_result.price_eur, "USD"
                    )
                    # Also convert individual prices for display
                    if rgp_result.loose_price:
                        rgp_result.loose_price = await self.fx_converter.convert_to_eur(
                            rgp_result.loose_price, "USD"
                        )
                    if rgp_result.cib_price:
                        rgp_result.cib_price = await self.fx_converter.convert_to_eur(
                            rgp_result.cib_price, "USD"
                        )

                result.rgp_result = rgp_result

            except Exception as e:
                result.rgp_result = PriceResult(
                    source=PriceSource.RETROGAMEPRICES,
                    success=False,
                    error=str(e),
                    details=f"RetroGamePrices error: {str(e)}",
                )

        # Calculate final estimate
        result.final_estimate_eur = self._calculate_weighted_average(
            result.ebay_result, result.rgp_result
        )

        # Build calculation details
        result.calculation_details = self._build_details(result)

        # Update success flag
        result.success = result.final_estimate_eur is not None

        return result

    def _calculate_weighted_average(
        self,
        ebay_result: PriceResult | None,
        rgp_result: PriceResult | None,
    ) -> Decimal | None:
        """Calculate weighted average from available sources."""
        ebay_price = ebay_result.price_eur if ebay_result and ebay_result.success else None
        rgp_price = rgp_result.price_eur if rgp_result and rgp_result.success else None

        if ebay_price and rgp_price:
            # Both sources available - weighted average
            weighted = (
                ebay_price * Decimal(str(self.config.weight_ebay))
                + rgp_price * Decimal(str(self.config.weight_rgp))
            )
            return weighted.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        elif ebay_price:
            return ebay_price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        elif rgp_price:
            return rgp_price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        return None

    def _build_details(self, result: EnrichmentResult) -> str:
        """Build calculation details string."""
        parts = []

        # Header - use ### instead of === to avoid Excel formula interpretation
        parts.append(f"### {result.game.title} ({result.game.platform}) ###")
        parts.append(f"Packaging: {result.game.packaging_state.value}")
        parts.append(f"Region: {result.game.region.value}")
        parts.append("")

        # eBay results
        if result.ebay_result:
            parts.append("--- eBay ---")
            if result.ebay_result.success:
                parts.append(result.ebay_result.details)
            else:
                parts.append(f"Error: {result.ebay_result.error or 'No results'}")
            parts.append("")

        # RGP results
        if result.rgp_result:
            parts.append("--- RetroGamePrices ---")
            if result.rgp_result.success:
                parts.append(result.rgp_result.details)
            else:
                parts.append(f"Error: {result.rgp_result.error or 'No results'}")
            parts.append("")

        # Final calculation
        parts.append("--- Final Estimate ---")
        if result.final_estimate_eur:
            ebay_note = ""
            rgp_note = ""

            if result.ebay_result and result.ebay_result.success:
                ebay_note = f"eBay: {result.ebay_result.price_eur:.2f} EUR"
            else:
                ebay_note = "eBay: N/A"

            if result.rgp_result and result.rgp_result.success:
                rgp_note = f"RGP: {result.rgp_result.price_eur:.2f} EUR"
            else:
                rgp_note = "RGP: N/A"

            # Show weighting if both sources used
            if (result.ebay_result and result.ebay_result.success and
                result.rgp_result and result.rgp_result.success):
                parts.append(
                    f"Weighted average (eBay {self.config.weight_ebay:.0%} / RGP {self.config.weight_rgp:.0%})"
                )
            else:
                parts.append("Single source")

            parts.append(f"{ebay_note} | {rgp_note}")
            parts.append(f"Final: {result.final_estimate_eur:.2f} EUR")
        else:
            parts.append("No estimate available - both sources failed")

        return "\n".join(parts)

    async def enrich_batch(
        self,
        items: list[GameItem],
        progress: Progress | None = None,
        task_id: TaskID | None = None,
    ) -> list[EnrichmentResult]:
        """
        Enrich a batch of game items.

        Args:
            items: List of game items to enrich
            progress: Optional Rich progress bar
            task_id: Progress task ID

        Returns:
            List of EnrichmentResult for each item
        """
        results = []

        for i, item in enumerate(items):
            result = await self.enrich_item(item)
            results.append(result)

            if progress and task_id is not None:
                progress.update(task_id, advance=1)

            # Log progress
            status = "✓" if result.success else "✗"
            price_str = f"{result.final_estimate_eur:.2f} EUR" if result.final_estimate_eur else "N/A"
            self.console.print(
                f"  [{i+1}/{len(items)}] {status} {item.title[:40]} - {price_str}"
            )

        return results


def apply_enrichment_to_items(
    items: list[GameItem],
    results: list[EnrichmentResult],
) -> list[GameItem]:
    """
    Apply enrichment results back to game items.

    Args:
        items: Original game items
        results: Enrichment results

    Returns:
        Updated game items with prices
    """
    # Create mapping by row index
    result_map = {r.game.row_index: r for r in results}

    for item in items:
        if item.row_index in result_map:
            result = result_map[item.row_index]
            item.online_estimate_eur = result.final_estimate_eur
            item.calculation_details = result.calculation_details

    return items
