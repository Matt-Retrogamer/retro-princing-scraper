"""eBay Finding API integration with region filtering and fallback strategies."""

import asyncio
import os
from datetime import datetime
from decimal import Decimal
from typing import Any
from xml.etree import ElementTree as ET

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from price_enricher.cache import PriceCache, CACHE_NS_EBAY, TTL_EBAY, build_cache_key
from price_enricher.fx import FXConverter, normalize_currency_code
from price_enricher.models import (
    GameItem,
    Language,
    PackagingState,
    PriceResult,
    PriceSource,
    Region,
    SoldListing,
)
from price_enricher.utils import (
    build_ebay_query,
    filter_listing,
    format_listing_for_details,
    get_ebay_negative_keywords,
)


# eBay Finding API configuration
EBAY_FINDING_API_URL = "https://svcs.ebay.com/services/search/FindingService/v1"
EBAY_API_VERSION = "1.0.0"

# Namespaces for XML parsing
NS = {
    "ns": "http://www.ebay.com/marketplace/search/v1/services",
}


class EbayClient:
    """
    eBay Finding API client for fetching sold listings.

    Uses findCompletedItems with SoldItemsOnly=true to get
    actual sold prices (not just listings).
    """

    def __init__(
        self,
        app_id: str | None = None,
        cache: PriceCache | None = None,
        fx_converter: FXConverter | None = None,
        sleep_seconds: float = 1.5,
    ):
        """
        Initialize eBay client.

        Args:
            app_id: eBay Application ID (from env EBAY_APP_ID if not provided)
            cache: Optional cache instance
            fx_converter: Optional FX converter for currency conversion
            sleep_seconds: Delay between API calls
        """
        self.app_id = app_id or os.environ.get("EBAY_APP_ID")
        if not self.app_id:
            raise ValueError("EBAY_APP_ID environment variable or app_id parameter required")

        self.cache = cache
        self.fx_converter = fx_converter or FXConverter(cache)
        self.sleep_seconds = sleep_seconds
        self._last_request_time = 0.0

    async def _rate_limit(self) -> None:
        """Apply rate limiting between requests."""
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_request_time
        if elapsed < self.sleep_seconds:
            await asyncio.sleep(self.sleep_seconds - elapsed)
        self._last_request_time = asyncio.get_event_loop().time()

    def _build_request_params(
        self,
        query: str,
        negative_keywords: list[str],
        site_id: str = "EBAY-GB",  # Default to UK for PAL
        entries_per_page: int = 25,
    ) -> dict[str, str]:
        """Build API request parameters."""
        # Build negative keywords string
        negative_str = " ".join(f"-{kw}" for kw in negative_keywords) if negative_keywords else ""
        full_query = f"{query} {negative_str}".strip()

        params = {
            "OPERATION-NAME": "findCompletedItems",
            "SERVICE-VERSION": EBAY_API_VERSION,
            "SECURITY-APPNAME": self.app_id,
            "RESPONSE-DATA-FORMAT": "XML",
            "REST-PAYLOAD": "",
            "GLOBAL-ID": site_id,
            "keywords": full_query,
            "itemFilter(0).name": "SoldItemsOnly",
            "itemFilter(0).value": "true",
            "sortOrder": "EndTimeSoonest",  # Most recent first
            "paginationInput.entriesPerPage": str(entries_per_page),
        }

        return params

    def _get_site_id_for_region(self, region: Region) -> str:
        """Get eBay site ID for region."""
        if region == Region.PAL:
            return "EBAY-GB"  # UK site for PAL
        elif region == Region.NTSC_U:
            return "EBAY-US"  # US site
        elif region == Region.NTSC_J:
            return "EBAY-JP"  # Japan site (may have limited results)
        return "EBAY-GB"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def _make_request(self, params: dict[str, str]) -> str:
        """Make HTTP request to eBay API with retry."""
        await self._rate_limit()

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(EBAY_FINDING_API_URL, params=params)
            response.raise_for_status()
            return response.text

    def _parse_response(self, xml_text: str) -> list[dict[str, Any]]:
        """Parse eBay XML response into list of item dicts."""
        root = ET.fromstring(xml_text)

        # Check for errors
        ack = root.find(".//ns:ack", NS)
        if ack is not None and ack.text not in ("Success", "Warning"):
            error_msg = root.find(".//ns:errorMessage/ns:error/ns:message", NS)
            raise Exception(f"eBay API error: {error_msg.text if error_msg is not None else 'Unknown error'}")

        items = []
        for item in root.findall(".//ns:searchResult/ns:item", NS):
            try:
                item_data = self._parse_item(item)
                if item_data:
                    items.append(item_data)
            except Exception:
                continue

        return items

    def _parse_item(self, item: ET.Element) -> dict[str, Any] | None:
        """Parse a single item element."""
        # Get required fields
        title_el = item.find("ns:title", NS)
        if title_el is None or not title_el.text:
            return None

        # Selling status
        selling_status = item.find("ns:sellingStatus", NS)
        if selling_status is None:
            return None

        # Price
        current_price = selling_status.find("ns:currentPrice", NS)
        if current_price is None:
            return None

        price = Decimal(current_price.text) if current_price.text else Decimal("0")
        currency = current_price.get("currencyId", "USD")

        # End time
        end_time_el = item.find("ns:listingInfo/ns:endTime", NS)
        end_time = None
        if end_time_el is not None and end_time_el.text:
            try:
                # Parse ISO format
                end_time = datetime.fromisoformat(end_time_el.text.replace("Z", "+00:00"))
            except ValueError:
                pass

        # URL
        url_el = item.find("ns:viewItemURL", NS)
        url = url_el.text if url_el is not None else ""

        # Condition
        condition_el = item.find("ns:condition/ns:conditionDisplayName", NS)
        condition = condition_el.text if condition_el is not None else ""

        # Shipping cost (if available)
        shipping_el = item.find("ns:shippingInfo/ns:shippingServiceCost", NS)
        shipping_cost = None
        shipping_currency = currency
        if shipping_el is not None and shipping_el.text:
            try:
                shipping_cost = Decimal(shipping_el.text)
                shipping_currency = shipping_el.get("currencyId", currency)
            except Exception:
                pass

        return {
            "title": title_el.text,
            "price": price,
            "currency": currency,
            "end_time": end_time,
            "url": url,
            "condition": condition,
            "shipping_cost": shipping_cost,
            "shipping_currency": shipping_currency,
        }

    async def _convert_item_prices(
        self,
        items: list[dict[str, Any]],
        include_shipping: bool = False,
    ) -> list[SoldListing]:
        """Convert item dicts to SoldListing objects with EUR prices."""
        listings = []

        for item in items:
            price_eur = await self.fx_converter.convert_to_eur(
                item["price"], normalize_currency_code(item["currency"])
            )

            shipping_eur = None
            if include_shipping and item.get("shipping_cost"):
                shipping_eur = await self.fx_converter.convert_to_eur(
                    item["shipping_cost"],
                    normalize_currency_code(item.get("shipping_currency", item["currency"])),
                )

            listing = SoldListing(
                title=item["title"],
                price=item["price"],
                currency=item["currency"],
                sold_date=item["end_time"] or datetime.now(),
                condition=item.get("condition", ""),
                url=item.get("url", ""),
                shipping_cost=item.get("shipping_cost"),
                price_eur=price_eur,
                shipping_eur=shipping_eur,
            )
            listings.append(listing)

        return listings

    async def search_sold_listings(
        self,
        item: GameItem,
        language: Language = Language.ANY,
        strict_language: bool = False,
        strict_region: bool = True,
        allow_lots: bool = False,
        allow_box_only: bool = False,
        include_shipping: bool = False,
        max_results: int = 5,
    ) -> PriceResult:
        """
        Search for sold listings matching a game item.

        Implements fallback strategies if not enough results found.

        Args:
            item: Game item to search for
            language: Preferred language
            strict_language: Whether to exclude other languages
            strict_region: Whether to strictly filter by region
            allow_lots: Whether to include lot/bundle listings
            allow_box_only: Whether to include box/manual only listings
            include_shipping: Whether to include shipping in price
            max_results: Target number of results

        Returns:
            PriceResult with listings and average price
        """
        result = PriceResult(source=PriceSource.EBAY)

        # Check cache first
        if self.cache:
            cache_key = build_cache_key(
                platform=item.platform,
                title=item.title,
                region=item.region.value,
                packaging=item.packaging_state.value,
                language=language.value,
            )
            cached = self.cache.get(CACHE_NS_EBAY, cache_key)
            if cached:
                result.success = True
                result.price_eur = Decimal(str(cached["price_eur"])) if cached.get("price_eur") else None
                result.num_results = cached.get("num_results", 0)
                result.details = cached.get("details", "")
                result.strategy_used = cached.get("strategy_used", "cached")
                return result

        # Define fallback strategies
        strategies = [
            {
                "name": "strict",
                "include_packaging": True,
                "strict_language": strict_language,
            },
            {
                "name": "relaxed_language",
                "include_packaging": True,
                "strict_language": False,
            },
            {
                "name": "relaxed_packaging",
                "include_packaging": False,
                "strict_language": False,
            },
        ]

        site_id = self._get_site_id_for_region(item.region)
        filtered_listings: list[SoldListing] = []
        strategy_used = "none"

        for strategy in strategies:
            # Build query with current strategy
            query = build_ebay_query(
                item,
                language=language,
                include_packaging=strategy["include_packaging"],
            )

            negative_keywords = get_ebay_negative_keywords(
                item,
                language=language,
                strict_language=strategy["strict_language"],
                allow_lots=allow_lots,
                allow_box_only=allow_box_only,
            )

            try:
                # Make API request
                params = self._build_request_params(
                    query=query,
                    negative_keywords=negative_keywords,
                    site_id=site_id,
                    entries_per_page=50,  # Get more to filter down
                )

                xml_response = await self._make_request(params)
                raw_items = self._parse_response(xml_response)

                # Filter results
                for raw_item in raw_items:
                    passed, reason = filter_listing(
                        title=raw_item["title"],
                        region=item.region,
                        strict_region=strict_region,
                        allow_lots=allow_lots,
                        allow_box_only=allow_box_only,
                    )

                    if passed:
                        # Convert and add to filtered list
                        listings = await self._convert_item_prices([raw_item], include_shipping)
                        if listings:
                            filtered_listings.append(listings[0])

                        if len(filtered_listings) >= max_results:
                            break

                if len(filtered_listings) >= max_results:
                    strategy_used = strategy["name"]
                    break

            except Exception as e:
                result.error = str(e)
                continue

            if len(filtered_listings) > 0:
                strategy_used = strategy["name"]

        # Calculate results
        if filtered_listings:
            result.success = True
            result.listings = filtered_listings[:max_results]
            result.num_results = len(result.listings)
            result.strategy_used = strategy_used

            # Calculate average price
            total = sum(
                lst.total_eur if include_shipping and lst.total_eur else lst.price_eur
                for lst in result.listings
                if lst.price_eur
            )
            result.price_eur = total / len(result.listings) if result.listings else None

            # Build details string
            shipping_note = "included" if include_shipping else "excluded"
            details_lines = [
                f"eBay (region={item.region.value}, avg={result.price_eur:.2f} EUR, "
                f"n={result.num_results}, shipping={shipping_note}, strategy={strategy_used}):"
            ]

            for lst in result.listings:
                date_str = lst.sold_date.strftime("%Y-%m-%d")
                price = lst.total_eur if include_shipping and lst.total_eur else lst.price_eur
                details_lines.append(
                    format_listing_for_details(
                        date=date_str,
                        price_eur=price,
                        title=lst.title,
                        condition=lst.condition,
                        url=lst.url,
                    )
                )

            result.details = "\n".join(details_lines)

            # Cache the result
            if self.cache and result.success:
                cache_key = build_cache_key(
                    platform=item.platform,
                    title=item.title,
                    region=item.region.value,
                    packaging=item.packaging_state.value,
                    language=language.value,
                )
                self.cache.set(
                    CACHE_NS_EBAY,
                    cache_key,
                    {
                        "price_eur": float(result.price_eur) if result.price_eur else None,
                        "num_results": result.num_results,
                        "details": result.details,
                        "strategy_used": strategy_used,
                    },
                    ttl_hours=TTL_EBAY,
                )

        else:
            result.success = False
            result.error = "No matching sold listings found after filtering"
            result.details = f"eBay: No results found for {item.title} ({item.platform}, {item.region.value})"

        return result


async def get_ebay_price(
    item: GameItem,
    app_id: str | None = None,
    cache: PriceCache | None = None,
    language: Language = Language.ANY,
    strict_region: bool = True,
    allow_lots: bool = False,
    allow_box_only: bool = False,
    include_shipping: bool = False,
    sleep_seconds: float = 1.5,
) -> PriceResult:
    """
    Convenience function to get eBay price for a game item.

    Args:
        item: Game item to price
        app_id: eBay App ID (or from EBAY_APP_ID env var)
        cache: Optional cache instance
        language: Preferred language
        strict_region: Whether to strictly filter by region
        allow_lots: Whether to allow lot/bundle listings
        allow_box_only: Whether to allow box/manual only listings
        include_shipping: Whether to include shipping in price
        sleep_seconds: Rate limit delay

    Returns:
        PriceResult with eBay pricing data
    """
    try:
        client = EbayClient(
            app_id=app_id,
            cache=cache,
            sleep_seconds=sleep_seconds,
        )

        return await client.search_sold_listings(
            item=item,
            language=language,
            strict_region=strict_region,
            allow_lots=allow_lots,
            allow_box_only=allow_box_only,
            include_shipping=include_shipping,
        )
    except Exception as e:
        return PriceResult(
            source=PriceSource.EBAY,
            success=False,
            error=str(e),
            details=f"eBay error: {str(e)}",
        )
