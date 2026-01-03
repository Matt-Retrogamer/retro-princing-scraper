"""PriceCharting scraper with rate limiting and caching.

Uses PriceCharting.com as the primary data source for retro game prices.
The original RetroGamePrices.com doesn't provide a working search API.
"""

import asyncio
import logging
import re
from decimal import Decimal
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from price_enricher.cache import PriceCache, CACHE_NS_RGP, TTL_RGP, build_cache_key
from price_enricher.models import (
    GameItem,
    PackagingState,
    PriceResult,
    PriceSource,
    Region,
)

# Setup logger
logger = logging.getLogger(__name__)

# PriceCharting configuration (more reliable than RetroGamePrices)
PRICECHARTING_BASE_URL = "https://www.pricecharting.com"
PRICECHARTING_SEARCH_URL = f"{PRICECHARTING_BASE_URL}/search-products"

# User agent for polite scraping
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class RGPClient:
    """
    Price scraper using PriceCharting.com as the data source.

    Provides loose, CIB, and new prices for retro games.
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
            wait_time = self.sleep_seconds - elapsed
            logger.debug(f"Rate limiting: waiting {wait_time:.2f}s")
            await asyncio.sleep(wait_time)
        self._last_request_time = asyncio.get_event_loop().time()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
        reraise=True,
    )
    async def _make_request(self, url: str) -> str:
        """Make HTTP request with retry and rate limiting."""
        await self._rate_limit()

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
        }

        logger.debug(f"Making request to: {url}")

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            logger.debug(f"Response status: {response.status_code}")

            if response.status_code == 404:
                raise ValueError(f"Page not found: {url}")
            if response.status_code == 403:
                raise ValueError(f"Access forbidden (possible rate limiting): {url}")

            response.raise_for_status()
            return response.text

    def _build_search_url(self, title: str, platform: str) -> str:
        """Build search URL for PriceCharting."""
        # Clean up title for search
        search_term = f"{title} {platform}"
        search_term = re.sub(r"[^\w\s-]", " ", search_term)
        search_term = " ".join(search_term.split())  # Normalize whitespace

        return f"{PRICECHARTING_SEARCH_URL}?q={quote_plus(search_term)}&type=videogames"

    def _map_platform_to_pricecharting(self, platform: str) -> str:
        """Map platform name to PriceCharting format."""
        platform_map = {
            # Nintendo
            "NES": "NES",
            "SNES": "Super Nintendo",
            "Super Nintendo": "Super Nintendo",
            "Nintendo 64": "Nintendo 64",
            "N64": "Nintendo 64",
            "GameCube": "Gamecube",
            "GC": "Gamecube",
            "Wii": "Wii",
            "Wii U": "Wii U",
            "Nintendo Switch": "Nintendo Switch",
            # Nintendo Handhelds
            "Game Boy": "Gameboy",
            "GB": "Gameboy",
            "Game Boy Color": "Gameboy Color",
            "GBC": "Gameboy Color",
            "Game Boy Advance": "Gameboy Advance",
            "GBA": "Gameboy Advance",
            "Nintendo DS": "Nintendo DS",
            "NDS": "Nintendo DS",
            "Nintendo 3DS": "Nintendo 3DS",
            "3DS": "Nintendo 3DS",
            # Sega
            "Master System": "Sega Master System",
            "Mega Drive": "Sega Genesis",
            "Genesis": "Sega Genesis",
            "Sega Saturn": "Sega Saturn",
            "Saturn": "Sega Saturn",
            "Dreamcast": "Sega Dreamcast",
            "Sega Dreamcast": "Sega Dreamcast",
            "Game Gear": "Sega Game Gear",
            # Sony
            "PlayStation": "Playstation",
            "PS1": "Playstation",
            "PSX": "Playstation",
            "PlayStation 2": "Playstation 2",
            "PS2": "Playstation 2",
            "PlayStation 3": "Playstation 3",
            "PS3": "Playstation 3",
            "PlayStation 4": "Playstation 4",
            "PS4": "Playstation 4",
            "PSP": "PSP",
            "PS Vita": "Playstation Vita",
            # Microsoft
            "Xbox": "Xbox",
            "Xbox 360": "Xbox 360",
            "Xbox One": "Xbox One",
            # Others
            "Atari 2600": "Atari 2600",
            "Neo Geo": "Neo Geo AES",
            "TurboGrafx-16": "TurboGrafx-16",
            "PC Engine": "TurboGrafx-16",
        }
        return platform_map.get(platform, platform)

    def _parse_price(self, text: str) -> Decimal | None:
        """Parse price from text string."""
        if not text:
            return None

        text = text.strip()

        # Handle "N/A", "None Available", etc.
        if text.lower() in ("n/a", "none", "none available", "-", ""):
            return None

        # Remove currency symbols and clean up
        text = text.replace("$", "").replace("€", "").replace("£", "").replace(",", "").strip()

        # Handle ranges like "10.00 - 20.00" by taking average
        if " - " in text:
            parts = text.split(" - ")
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
            if cleaned and cleaned != ".":
                return Decimal(cleaned)
        except Exception:
            pass

        return None

    def _parse_search_results(self, html: str, title: str, platform: str) -> dict | None:
        """
        Parse PriceCharting search results page to find matching game.

        Returns dict with game URL and title if found.
        """
        soup = BeautifulSoup(html, "lxml")
        title_lower = title.lower()
        platform_mapped = self._map_platform_to_pricecharting(platform).lower()

        logger.debug(f"Searching for title='{title_lower}', platform='{platform_mapped}'")

        # PriceCharting shows results in a table or list
        # Look for product links
        product_links = soup.select("a[href*='/game/']")

        best_match = None
        best_score = 0

        for link in product_links:
            href = link.get("href", "")
            link_text = link.get_text().lower().strip()

            # Check platform in URL or text
            parent = link.find_parent("tr") or link.find_parent("div")
            parent_text = parent.get_text().lower() if parent else ""

            # Score this match
            score = 0

            # Title matching (fuzzy)
            title_words = set(title_lower.split())
            link_words = set(link_text.split())
            common_words = title_words & link_words
            if common_words:
                score += len(common_words) * 10

            # Exact title bonus
            if title_lower in link_text:
                score += 50

            # Platform matching
            if platform_mapped in parent_text or platform_mapped in href.lower():
                score += 30

            logger.debug(f"  Match candidate: '{link_text}' (score={score}, href={href})")

            if score > best_score and score >= 20:
                best_score = score
                best_match = {
                    "url": href if href.startswith("http") else f"{PRICECHARTING_BASE_URL}{href}",
                    "title": link.get_text().strip(),
                }

        if best_match:
            logger.info(f"Found match: {best_match['title']} -> {best_match['url']}")

        return best_match

    def _parse_game_page(self, html: str) -> dict:
        """
        Parse PriceCharting game detail page for prices.

        Returns dict with loose_price, cib_price, new_price (all optional).
        """
        soup = BeautifulSoup(html, "lxml")
        result = {}

        # PriceCharting has a price table with specific IDs
        # Try to find prices by common patterns

        # Method 1: Look for specific price elements (PriceCharting format)
        price_mapping = {
            "loose_price": ["loose", "cart", "cartridge", "disc only"],
            "cib_price": ["cib", "complete", "complete in box"],
            "new_price": ["new", "sealed", "graded"],
        }

        # Look for price tables
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all(["td", "th"])
                if len(cells) >= 2:
                    label = cells[0].get_text().lower().strip()
                    price_text = cells[-1].get_text().strip()

                    for key, keywords in price_mapping.items():
                        if any(kw in label for kw in keywords):
                            price = self._parse_price(price_text)
                            if price:
                                result[key] = price
                                logger.debug(f"Found {key}: ${price}")

        # Method 2: Look for price spans/divs with specific classes
        if not result:
            for key, keywords in price_mapping.items():
                for kw in keywords:
                    elem = soup.find(["span", "td", "div"], string=re.compile(kw, re.I))
                    if elem:
                        parent = elem.find_parent("tr") or elem.find_parent("div")
                        if parent:
                            price_elem = parent.find(string=re.compile(r"\$[\d,]+\.?\d*"))
                            if price_elem:
                                price = self._parse_price(str(price_elem))
                                if price:
                                    result[key] = price

        # Method 3: Regex search on entire page
        if not result:
            all_text = soup.get_text()
            patterns = [
                (r"loose[:\s]*\$?([\d,]+\.?\d*)", "loose_price"),
                (r"cart(?:ridge)?[:\s]*\$?([\d,]+\.?\d*)", "loose_price"),
                (r"cib[:\s]*\$?([\d,]+\.?\d*)", "cib_price"),
                (r"complete[:\s]*\$?([\d,]+\.?\d*)", "cib_price"),
                (r"new[:\s]*\$?([\d,]+\.?\d*)", "new_price"),
                (r"sealed[:\s]*\$?([\d,]+\.?\d*)", "new_price"),
            ]

            for pattern, key in patterns:
                if key not in result:
                    match = re.search(pattern, all_text, re.IGNORECASE)
                    if match:
                        try:
                            price_str = match.group(1).replace(",", "")
                            result[key] = Decimal(price_str)
                            logger.debug(f"Regex found {key}: ${result[key]}")
                        except Exception:
                            pass

        logger.debug(f"Parsed prices: {result}")
        return result

    async def get_price(self, item: GameItem) -> PriceResult:
        """
        Get price estimate from PriceCharting.

        Args:
            item: Game item to price

        Returns:
            PriceResult with pricing data (prices in USD)
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
                logger.debug(f"Cache hit for {item.title}")
                result.success = True
                result.loose_price = Decimal(str(cached["loose_price"])) if cached.get("loose_price") else None
                result.cib_price = Decimal(str(cached["cib_price"])) if cached.get("cib_price") else None
                result.price_eur = Decimal(str(cached["price_eur"])) if cached.get("price_eur") else None
                result.details = cached.get("details", "")
                return result

        try:
            # Map platform name
            pc_platform = self._map_platform_to_pricecharting(item.platform)
            logger.info(f"Searching PriceCharting for: {item.title} ({pc_platform})")

            # Search for the game
            search_url = self._build_search_url(item.title, pc_platform)
            logger.debug(f"Search URL: {search_url}")

            search_html = await self._make_request(search_url)

            # Find game in results
            game_info = self._parse_search_results(search_html, item.title, pc_platform)

            if not game_info:
                result.success = False
                result.error = "Game not found in search results"
                result.details = f"PriceCharting: No match for '{item.title}' ({item.platform})\nSearched: {search_url}"
                logger.warning(f"No match found for {item.title} ({item.platform})")
                return result

            # Fetch game detail page
            logger.debug(f"Fetching game page: {game_info['url']}")
            game_html = await self._make_request(game_info["url"])
            prices = self._parse_game_page(game_html)

            if not prices:
                result.success = False
                result.error = "Could not parse prices from game page"
                result.details = f"PriceCharting: Found '{game_info['title']}' but couldn't extract prices\nURL: {game_info['url']}"
                logger.warning(f"Could not parse prices for {game_info['title']}")
                return result

            # Set prices (PriceCharting uses USD)
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
                f"PriceCharting: {game_info['title']}",
                f"  Loose: ${result.loose_price:.2f} USD" if result.loose_price else "  Loose: N/A",
                f"  CIB: ${result.cib_price:.2f} USD" if result.cib_price else "  CIB: N/A",
                f"  Selected ({item.packaging_state.value}): ${result.price_eur:.2f} USD" if result.price_eur else "",
                f"  Note: Prices are in USD, will be converted to EUR",
                f"  URL: {game_info['url']}",
            ]
            result.details = "\n".join(filter(None, details_parts))

            logger.info(f"Found prices for {item.title}: loose=${result.loose_price}, cib=${result.cib_price}")

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

        except httpx.HTTPStatusError as e:
            result.success = False
            result.error = f"HTTP {e.response.status_code}: {e.response.reason_phrase}"
            result.details = f"PriceCharting: HTTP error {e.response.status_code} - {str(e)}"
            logger.error(f"HTTP error for {item.title}: {e}")

        except httpx.TimeoutException as e:
            result.success = False
            result.error = f"Request timeout: {str(e)}"
            result.details = f"PriceCharting: Request timed out - {str(e)}"
            logger.error(f"Timeout for {item.title}: {e}")

        except httpx.ConnectError as e:
            result.success = False
            result.error = f"Connection error: {str(e)}"
            result.details = f"PriceCharting: Could not connect - {str(e)}"
            logger.error(f"Connection error for {item.title}: {e}")

        except ValueError as e:
            result.success = False
            result.error = str(e)
            result.details = f"PriceCharting: {str(e)}"
            logger.error(f"Value error for {item.title}: {e}")

        except Exception as e:
            result.success = False
            result.error = f"{type(e).__name__}: {str(e)}"
            result.details = f"PriceCharting: Unexpected error - {type(e).__name__}: {str(e)}"
            logger.exception(f"Unexpected error for {item.title}")

        return result


async def get_rgp_price(
    item: GameItem,
    cache: PriceCache | None = None,
    sleep_seconds: float = 2.0,
) -> PriceResult:
    """
    Convenience function to get price estimate for a game item.

    Args:
        item: Game item to price
        cache: Optional cache instance
        sleep_seconds: Rate limit delay

    Returns:
        PriceResult with pricing data (prices in USD, not EUR!)
    """
    try:
        client = RGPClient(cache=cache, sleep_seconds=sleep_seconds)
        return await client.get_price(item)
    except Exception as e:
        logger.exception(f"Error getting price for {item.title}")
        return PriceResult(
            source=PriceSource.RETROGAMEPRICES,
            success=False,
            error=f"{type(e).__name__}: {str(e)}",
            details=f"PriceCharting error: {type(e).__name__}: {str(e)}",
        )
