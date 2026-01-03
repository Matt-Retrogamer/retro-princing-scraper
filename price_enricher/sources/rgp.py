"""RetroGamePrices.com scraper with rate limiting and caching."""

import asyncio
import re
from decimal import Decimal
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from price_enricher.cache import PriceCache, CACHE_NS_RGP, TTL_RGP, build_cache_key
from price_enricher.models import (
    GameItem,
    PackagingState,
    PriceResult,
    PriceSource,
    Region,
)


# RetroGamePrices.com configuration
RGP_BASE_URL = "https://www.retrogameprices.com"
RGP_SEARCH_URL = f"{RGP_BASE_URL}/search"

# User agent for polite scraping
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class RGPClient:
    """
    RetroGamePrices.com scraper for price estimates.

    Provides loose and CIB prices for retro games.
    Implements rate limiting and caching to be respectful.
    """

    def __init__(
        self,
        cache: PriceCache | None = None,
        sleep_seconds: float = 2.0,
    ):
        """
        Initialize RGP client.

        Args:
            cache: Optional cache instance
            sleep_seconds: Delay between requests (be respectful!)
        """
        self.cache = cache
        self.sleep_seconds = sleep_seconds
        self._last_request_time = 0.0

    async def _rate_limit(self) -> None:
        """Apply rate limiting between requests."""
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_request_time
        if elapsed < self.sleep_seconds:
            await asyncio.sleep(self.sleep_seconds - elapsed)
        self._last_request_time = asyncio.get_event_loop().time()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def _make_request(self, url: str) -> str:
        """Make HTTP request with retry and rate limiting."""
        await self._rate_limit()

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.text

    def _build_search_url(self, title: str, platform: str) -> str:
        """Build search URL for RetroGamePrices."""
        # Clean up title for search
        search_term = f"{title} {platform}"
        search_term = re.sub(r"[^\w\s-]", "", search_term)
        search_term = search_term.strip()

        return f"{RGP_SEARCH_URL}?q={quote_plus(search_term)}"

    def _map_platform_to_rgp(self, platform: str) -> str:
        """Map platform name to RetroGamePrices format."""
        # RGP uses specific platform names
        platform_map = {
            "NES": "NES",
            "SNES": "Super Nintendo",
            "Nintendo 64": "Nintendo 64",
            "GameCube": "GameCube",
            "Wii": "Wii",
            "Game Boy": "Game Boy",
            "Game Boy Color": "Game Boy Color",
            "Game Boy Advance": "Game Boy Advance",
            "Nintendo DS": "Nintendo DS",
            "Nintendo 3DS": "Nintendo 3DS",
            "Master System": "Sega Master System",
            "Mega Drive": "Sega Genesis",
            "Genesis": "Sega Genesis",
            "Sega Saturn": "Sega Saturn",
            "Dreamcast": "Sega Dreamcast",
            "PlayStation": "PlayStation",
            "PlayStation 2": "PlayStation 2",
            "PlayStation 3": "PlayStation 3",
            "PSP": "PSP",
            "Xbox": "Xbox",
            "Xbox 360": "Xbox 360",
            "Atari 2600": "Atari 2600",
            "Neo Geo": "Neo Geo",
            "TurboGrafx-16": "TurboGrafx-16",
        }
        return platform_map.get(platform, platform)

    def _parse_price(self, text: str) -> Decimal | None:
        """Parse price from text string."""
        if not text:
            return None

        # Remove currency symbols and clean up
        text = text.replace("$", "").replace("€", "").replace("£", "").strip()

        # Handle ranges like "10.00 - 20.00" by taking average
        if "-" in text and not text.startswith("-"):
            parts = text.split("-")
            if len(parts) == 2:
                try:
                    low = Decimal(parts[0].strip())
                    high = Decimal(parts[1].strip())
                    return (low + high) / 2
                except Exception:
                    pass

        # Try to parse single value
        try:
            # Remove any non-numeric characters except decimal point
            cleaned = re.sub(r"[^\d.]", "", text)
            if cleaned:
                return Decimal(cleaned)
        except Exception:
            pass

        return None

    def _parse_search_results(self, html: str, title: str, platform: str) -> dict | None:
        """
        Parse search results page to find matching game.

        Returns dict with game URL if found.
        """
        soup = BeautifulSoup(html, "lxml")

        # Look for search results - RGP typically shows game cards
        # Structure may vary, so we try multiple selectors
        game_links = soup.select("a.game-card, .search-result a, .game-item a")

        title_lower = title.lower()
        platform_lower = platform.lower()

        for link in game_links:
            link_text = link.get_text().lower()

            # Check if title matches
            if title_lower in link_text or any(word in link_text for word in title_lower.split()):
                href = link.get("href")
                if href:
                    if not href.startswith("http"):
                        href = f"{RGP_BASE_URL}{href}"
                    return {"url": href, "title": link.get_text().strip()}

        # Try alternate parsing for different page structures
        rows = soup.select("tr, .list-item, .result-item")
        for row in rows:
            text = row.get_text().lower()
            if title_lower in text:
                link = row.find("a")
                if link:
                    href = link.get("href")
                    if href:
                        if not href.startswith("http"):
                            href = f"{RGP_BASE_URL}{href}"
                        return {"url": href, "title": link.get_text().strip()}

        return None

    def _parse_game_page(self, html: str) -> dict:
        """
        Parse game detail page for prices.

        Returns dict with loose_price, cib_price, new_price (all optional).
        """
        soup = BeautifulSoup(html, "lxml")
        result = {}

        # Look for price tables - RGP typically shows prices in tables or cards
        # Try to find loose and CIB prices

        # Method 1: Look for labeled price sections
        price_sections = soup.select(".price-section, .price-card, .price-row")
        for section in price_sections:
            text = section.get_text().lower()
            price_elem = section.select_one(".price, .value, .amount")
            if price_elem:
                price = self._parse_price(price_elem.get_text())
                if "loose" in text or "cart" in text:
                    result["loose_price"] = price
                elif "cib" in text or "complete" in text or "boxed" in text:
                    result["cib_price"] = price
                elif "new" in text or "sealed" in text:
                    result["new_price"] = price

        # Method 2: Look for table with condition/price columns
        if not result:
            tables = soup.find_all("table")
            for table in tables:
                rows = table.find_all("tr")
                for row in rows:
                    cells = row.find_all(["td", "th"])
                    if len(cells) >= 2:
                        condition_text = cells[0].get_text().lower()
                        price_text = cells[1].get_text()
                        price = self._parse_price(price_text)

                        if price:
                            if "loose" in condition_text or "cart" in condition_text:
                                result["loose_price"] = price
                            elif "cib" in condition_text or "complete" in condition_text:
                                result["cib_price"] = price
                            elif "new" in condition_text or "sealed" in condition_text:
                                result["new_price"] = price

        # Method 3: Look for any price-like text with context
        if not result:
            all_text = soup.get_text()

            # Regex patterns for prices with context
            patterns = [
                (r"loose[:\s]*\$?([\d.]+)", "loose_price"),
                (r"cart[:\s]*\$?([\d.]+)", "loose_price"),
                (r"cib[:\s]*\$?([\d.]+)", "cib_price"),
                (r"complete[:\s]*\$?([\d.]+)", "cib_price"),
                (r"boxed[:\s]*\$?([\d.]+)", "cib_price"),
            ]

            for pattern, key in patterns:
                match = re.search(pattern, all_text, re.IGNORECASE)
                if match:
                    try:
                        result[key] = Decimal(match.group(1))
                    except Exception:
                        pass

        return result

    async def get_price(self, item: GameItem) -> PriceResult:
        """
        Get price estimate from RetroGamePrices.com.

        Args:
            item: Game item to price

        Returns:
            PriceResult with RGP pricing data
        """
        result = PriceResult(source=PriceSource.RETROGAMEPRICES)

        # Check cache first
        if self.cache:
            cache_key = build_cache_key(
                platform=item.platform,
                title=item.title,
                packaging=item.packaging_state.value,
            )
            cached = self.cache.get(CACHE_NS_RGP, cache_key)
            if cached:
                result.success = True
                result.loose_price = Decimal(str(cached["loose_price"])) if cached.get("loose_price") else None
                result.cib_price = Decimal(str(cached["cib_price"])) if cached.get("cib_price") else None
                result.price_eur = Decimal(str(cached["price_eur"])) if cached.get("price_eur") else None
                result.details = cached.get("details", "")
                return result

        try:
            # Map platform name
            rgp_platform = self._map_platform_to_rgp(item.platform)

            # Search for the game
            search_url = self._build_search_url(item.title, rgp_platform)
            search_html = await self._make_request(search_url)

            # Find game in results
            game_info = self._parse_search_results(search_html, item.title, rgp_platform)

            if not game_info:
                result.success = False
                result.error = "Game not found in search results"
                result.details = f"RetroGamePrices: No match for {item.title} ({item.platform})"
                return result

            # Fetch game detail page
            game_html = await self._make_request(game_info["url"])
            prices = self._parse_game_page(game_html)

            if not prices:
                result.success = False
                result.error = "Could not parse prices from game page"
                result.details = f"RetroGamePrices: Found {game_info['title']} but couldn't extract prices"
                return result

            # Set prices (RGP uses USD, need to note for conversion)
            result.loose_price = prices.get("loose_price")
            result.cib_price = prices.get("cib_price")

            # Select appropriate price based on packaging state
            if item.packaging_state == PackagingState.CIB and result.cib_price:
                result.price_eur = result.cib_price  # Note: Still in USD!
            elif result.loose_price:
                result.price_eur = result.loose_price  # Note: Still in USD!
            elif result.cib_price:
                result.price_eur = result.cib_price  # Fallback to CIB if no loose

            result.success = result.price_eur is not None

            # Build details
            details_parts = [
                f"RetroGamePrices: {game_info['title']}",
                f"  Loose: ${result.loose_price:.2f}" if result.loose_price else "  Loose: N/A",
                f"  CIB: ${result.cib_price:.2f}" if result.cib_price else "  CIB: N/A",
                f"  Note: Region filtering not supported on RGP",
                f"  URL: {game_info['url']}",
            ]
            result.details = "\n".join(details_parts)

            # Cache the result
            if self.cache and result.success:
                cache_key = build_cache_key(
                    platform=item.platform,
                    title=item.title,
                    packaging=item.packaging_state.value,
                )
                self.cache.set(
                    CACHE_NS_RGP,
                    cache_key,
                    {
                        "loose_price": float(result.loose_price) if result.loose_price else None,
                        "cib_price": float(result.cib_price) if result.cib_price else None,
                        "price_eur": float(result.price_eur) if result.price_eur else None,
                        "details": result.details,
                    },
                    ttl_hours=TTL_RGP,
                )

        except httpx.HTTPError as e:
            result.success = False
            result.error = f"HTTP error: {str(e)}"
            result.details = f"RetroGamePrices: Connection error - {str(e)}"

        except Exception as e:
            result.success = False
            result.error = str(e)
            result.details = f"RetroGamePrices: Error - {str(e)}"

        return result


async def get_rgp_price(
    item: GameItem,
    cache: PriceCache | None = None,
    sleep_seconds: float = 2.0,
) -> PriceResult:
    """
    Convenience function to get RetroGamePrices price for a game item.

    Args:
        item: Game item to price
        cache: Optional cache instance
        sleep_seconds: Rate limit delay

    Returns:
        PriceResult with RGP pricing data (prices in USD, not EUR!)
    """
    try:
        client = RGPClient(cache=cache, sleep_seconds=sleep_seconds)
        return await client.get_price(item)
    except Exception as e:
        return PriceResult(
            source=PriceSource.RETROGAMEPRICES,
            success=False,
            error=str(e),
            details=f"RetroGamePrices error: {str(e)}",
        )
