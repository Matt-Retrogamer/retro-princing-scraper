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

# Global rate limiting state (shared across all client instances)
_global_rate_limit_lock = asyncio.Lock()
_global_last_request_time = 0.0


class RGPClient:
    """
    Price scraper using PriceCharting.com as the data source.

    Provides loose, CIB, and new prices for retro games.
    Implements rate limiting and caching to be respectful.
    """

    def __init__(
        self,
        cache: PriceCache | None = None,
        sleep_seconds: float = 10.0,
    ):
        """
        Initialize RGP client.

        Args:
            cache: Optional cache instance
            sleep_seconds: Delay between requests (be respectful!)
        """
        self.cache = cache
        self.sleep_seconds = sleep_seconds

    async def _rate_limit(self) -> None:
        """Apply rate limiting between requests using global state."""
        global _global_last_request_time
        
        # Use a lock to ensure only one request proceeds at a time
        async with _global_rate_limit_lock:
            now = asyncio.get_event_loop().time()
            
            # If this is the first request ever, just record the time
            if _global_last_request_time == 0.0:
                _global_last_request_time = now
                logger.debug(f"Rate limit: First request, starting timer")
                return
            
            # Calculate time since last request
            elapsed = now - _global_last_request_time
            
            # If not enough time has passed, wait
            if elapsed < self.sleep_seconds:
                wait_time = self.sleep_seconds - elapsed
                logger.debug(f"Rate limiting: waiting {wait_time:.2f}s (last request was {elapsed:.2f}s ago)")
                await asyncio.sleep(wait_time)
            else:
                logger.debug(f"Rate limit: OK, {elapsed:.2f}s elapsed (>{self.sleep_seconds}s required)")
            
            # Update global timestamp for next request
            _global_last_request_time = asyncio.get_event_loop().time()

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

    def _build_search_url(self, title: str, platform: str, region: str = "") -> str:
        """Build search URL for PriceCharting."""
        # Clean up title for search, include region for better matching
        if region:
            search_term = f"{title} {platform} {region}"
        else:
            search_term = f"{title} {platform}"
        search_term = re.sub(r"[^\w\s-]", " ", search_term)
        search_term = " ".join(search_term.split())  # Normalize whitespace

        return f"{PRICECHARTING_SEARCH_URL}?q={quote_plus(search_term)}&type=videogames"

    def _map_region_to_pricecharting(self, region: Region) -> str:
        """Map region enum to PriceCharting search term."""
        region_map = {
            Region.PAL: "PAL",
            Region.NTSC_U: "",  # NTSC-U is the default on PriceCharting (US site)
            Region.NTSC_J: "JP",  # Japanese versions
        }
        return region_map.get(region, "")

    def _map_platform_to_pricecharting(self, platform: str, region: Region = Region.PAL) -> str:
        """Map platform name to PriceCharting format, considering region."""
        # Base platform mapping
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

    def _parse_search_results(self, html: str, title: str, platform: str, region: str = "") -> dict | None:
        """
        Parse PriceCharting search results page to find matching game.

        Returns dict with game URL and title if found.
        """
        soup = BeautifulSoup(html, "lxml")
        title_lower = title.lower()
        platform_mapped = self._map_platform_to_pricecharting(platform).lower()
        region_lower = region.lower() if region else ""

        logger.debug(f"Searching for title='{title_lower}', platform='{platform_mapped}', region='{region_lower}'")

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

            # Region matching - boost PAL/JP matches if searching for those regions
            if region_lower:
                href_lower = href.lower()
                # PriceCharting uses "pal-" prefix for PAL versions in URLs
                if region_lower == "pal" and "pal-" in href_lower:
                    score += 40  # Boost PAL matches
                    logger.debug(f"  PAL match found in URL: {href}")
                elif region_lower == "jp" and ("jp-" in href_lower or "japan" in parent_text):
                    score += 40  # Boost Japanese matches
                    logger.debug(f"  JP match found: {href}")
                elif region_lower == "pal" and "pal" in parent_text:
                    score += 35  # PAL in text is good too
                elif "pal-" not in href_lower and "jp-" not in href_lower:
                    # No region prefix = NTSC-U (default), slight penalty if searching for other region
                    score -= 10

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

        PriceCharting format shows prices like:
        - "Loose$18.61" (cartridge/disc only)
        - "Complete$442.29" (game + box + manual - CIB)
        - "New$11,999.99" (sealed - DO NOT use for pricing normal items)
        - "Box Only$258.33" (just the box)
        - "Manual Only$7.00" (just the manual)
        - "Item & Box$276.94" (game + box, no manual)
        - "Item & Manual$26.00" (game + manual, no box)

        We select the right price based on item components (has_game, has_box, has_manual).
        We NEVER want: New or Graded prices (those are for sealed/graded collectors).

        Returns dict with all available prices.
        """
        soup = BeautifulSoup(html, "lxml")
        result = {}

        # Get full page text to search with regex
        all_text = soup.get_text()

        # PriceCharting format: "LabelName$XX.XX" with NO space between label and $
        # Example: "Loose$18.61Item & Box$276.94Complete$442.29..."

        price_patterns = [
            # Loose price - for cartridge/disc only items
            # Matches: "Loose$18.61" (no space between Loose and $)
            (r"\bLoose\$([\d,]+\.?\d*)", "loose_price"),

            # Complete/CIB price - for complete in box items
            # Matches: "Complete$442.29" but NOT "Graded Complete" or "Incomplete"
            # Using negative lookbehind to avoid matching "Graded " prefix
            (r"(?<!Graded\s)(?<!Graded)Complete\$([\d,]+\.?\d*)", "cib_price"),

            # Item & Box price (game + box, no manual)
            (r"Item & Box\$([\d,]+\.?\d*)", "item_box_price"),

            # Item & Manual price (game + manual, no box)
            (r"Item & Manual\$([\d,]+\.?\d*)", "item_manual_price"),

            # Box Only price (the word "Box Only" has a space)
            (r"Box Only\$([\d,]+\.?\d*)", "box_only_price"),

            # Manual Only price (the word "Manual Only" has a space)
            (r"Manual Only\$([\d,]+\.?\d*)", "manual_only_price"),
        ]

        for pattern, key in price_patterns:
            match = re.search(pattern, all_text, re.IGNORECASE)
            if match:
                try:
                    price_str = match.group(1).replace(",", "")
                    price = Decimal(price_str)
                    # Sanity check: ignore prices over $50,000 (likely parsing error)
                    if price < 50000:
                        result[key] = price
                        logger.debug(f"Found {key}: ${price}")
                    else:
                        logger.warning(f"Ignoring unreasonably high {key}: ${price}")
                except Exception as e:
                    logger.debug(f"Failed to parse price from '{match.group(1)}': {e}")

        # Fallback: Try to find prices in table format if regex didn't work
        if not result:
            logger.debug("Regex parsing failed, trying table parsing...")
            for table in soup.find_all("table"):
                rows = table.find_all("tr")
                for row in rows:
                    cells = row.find_all(["td", "th"])
                    if len(cells) >= 2:
                        label = cells[0].get_text().lower().strip()
                        price_text = cells[-1].get_text().strip()

                        # Map table labels to price keys
                        # IMPORTANT: We specifically do NOT map "new" or "sealed" here
                        if "loose" in label and "loose_price" not in result:
                            price = self._parse_price(price_text)
                            if price and price < 50000:
                                result["loose_price"] = price
                                logger.debug(f"Table found loose_price: ${price}")

                        elif label == "complete" and "cib_price" not in result:
                            # Exact match for "complete" to avoid "incomplete"
                            price = self._parse_price(price_text)
                            if price and price < 50000:
                                result["cib_price"] = price
                                logger.debug(f"Table found cib_price: ${price}")

                        elif "box only" in label and "box_only_price" not in result:
                            price = self._parse_price(price_text)
                            if price and price < 50000:
                                result["box_only_price"] = price
                                logger.debug(f"Table found box_only_price: ${price}")

                        elif "manual only" in label and "manual_only_price" not in result:
                            price = self._parse_price(price_text)
                            if price and price < 50000:
                                result["manual_only_price"] = price
                                logger.debug(f"Table found manual_only_price: ${price}")

        if result:
            logger.info(f"Parsed prices: loose=${result.get('loose_price')}, cib=${result.get('cib_price')}, "
                       f"item_box=${result.get('item_box_price')}, item_manual=${result.get('item_manual_price')}, "
                       f"box=${result.get('box_only_price')}, manual=${result.get('manual_only_price')}")
        else:
            logger.warning("Could not parse any prices from game page")

        return result

    def _select_price_for_item(self, item: GameItem, prices: dict) -> tuple[Decimal | None, str]:
        """
        Select the best price based on item components.

        Uses has_game, has_box, has_manual to determine which price to use.

        Args:
            item: Game item with component flags
            prices: Dict of parsed prices from PriceCharting

        Returns:
            Tuple of (selected_price, description of what was selected)
        """
        has_game = item.has_game == "Y"
        has_box = item.has_box == "Y"
        has_manual = item.has_manual == "Y"

        loose = prices.get("loose_price")
        cib = prices.get("cib_price")
        item_box = prices.get("item_box_price")
        item_manual = prices.get("item_manual_price")
        box_only = prices.get("box_only_price")
        manual_only = prices.get("manual_only_price")

        # Case 1: No game - just accessories
        if not has_game:
            if has_box and has_manual:
                # Box + Manual only (no game)
                if box_only and manual_only:
                    price = box_only + manual_only
                    return price, f"Box Only + Manual Only (${box_only} + ${manual_only})"
                return None, "Box + Manual only (no prices available)"
            elif has_box:
                if box_only:
                    return box_only, "Box Only"
                return None, "Box only (no price available)"
            elif has_manual:
                if manual_only:
                    return manual_only, "Manual Only"
                return None, "Manual only (no price available)"
            return None, "No components"

        # Case 2: Complete In Box (Game + Box + Manual)
        if has_game and has_box and has_manual:
            if cib:
                return cib, "Complete (CIB)"
            # Fallback: try to calculate from components
            if loose and box_only and manual_only:
                price = loose + box_only + manual_only
                return price, f"Calculated CIB (Loose + Box + Manual: ${loose} + ${box_only} + ${manual_only})"
            if item_box and manual_only:
                price = item_box + manual_only
                return price, f"Calculated CIB (Item&Box + Manual: ${item_box} + ${manual_only})"
            return None, "CIB (no price available)"

        # Case 3: Game + Box (no manual)
        if has_game and has_box and not has_manual:
            if item_box:
                return item_box, "Item & Box"
            # Fallback: calculate from Loose + Box Only
            if loose and box_only:
                price = loose + box_only
                return price, f"Calculated Item&Box (Loose + Box: ${loose} + ${box_only})"
            # Last resort: use CIB price as upper estimate
            if cib:
                return cib, "CIB (used as estimate for Item & Box)"
            return loose, "Loose (Box value unknown)" if loose else (None, "Item & Box (no price available)")

        # Case 4: Game + Manual (no box)
        if has_game and has_manual and not has_box:
            if item_manual:
                return item_manual, "Item & Manual"
            # Fallback: calculate from Loose + Manual Only
            if loose and manual_only:
                price = loose + manual_only
                return price, f"Calculated Item&Manual (Loose + Manual: ${loose} + ${manual_only})"
            # Last resort: use Loose price
            return loose, "Loose (Manual value unknown)" if loose else (None, "Item & Manual (no price available)")

        # Case 5: Game only (Loose)
        if has_game and not has_box and not has_manual:
            if loose:
                return loose, "Loose"
            return None, "Loose (no price available)"

        # Default fallback
        if loose:
            return loose, "Loose (fallback)"
        if cib:
            return cib, "CIB (fallback)"
        return None, "No price available"

    async def get_price(self, item: GameItem) -> PriceResult:
        """
        Get price estimate from PriceCharting.

        Args:
            item: Game item to price

        Returns:
            PriceResult with pricing data (prices in USD)
        """
        result = PriceResult(source=PriceSource.RETROGAMEPRICES)

        # Get region string for search
        region_str = self._map_region_to_pricecharting(item.region)

        # Check cache first (include region in cache key)
        if self.cache:
            cache_key = build_cache_key(
                platform=item.platform,
                title=item.title,
                packaging=item.packaging_state.value,
                region=item.region.value if item.region else "",
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
            # Map platform name (considering region)
            pc_platform = self._map_platform_to_pricecharting(item.platform, item.region)
            logger.info(f"Searching PriceCharting for: {item.title} ({pc_platform}) [{region_str or 'NTSC-U'}]")

            # Search for the game (include region in search)
            search_url = self._build_search_url(item.title, pc_platform, region_str)
            logger.debug(f"Search URL: {search_url}")

            search_html = await self._make_request(search_url)

            # Find game in results (include region for better matching)
            game_info = self._parse_search_results(search_html, item.title, pc_platform, region_str)

            if not game_info:
                result.success = False
                result.error = "Game not found in search results"
                result.details = f"PriceCharting: No match for '{item.title}' ({item.platform}) [{region_str or 'NTSC-U'}]\nSearched: {search_url}"
                logger.warning(f"No match found for {item.title} ({item.platform}) [{region_str or 'NTSC-U'}]")
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

            # Select appropriate price based on item components (has_game, has_box, has_manual)
            selected_price, price_description = self._select_price_for_item(item, prices)
            result.price_eur = selected_price  # Note: Still in USD at this point!

            result.success = result.price_eur is not None

            # Build component description
            components = []
            if item.has_game == "Y":
                components.append("Game")
            if item.has_box == "Y":
                components.append("Box")
            if item.has_manual == "Y":
                components.append("Manual")
            component_str = " + ".join(components) if components else "None"

            # Build details with all available prices
            details_parts = [
                f"PriceCharting: {game_info['title']}",
                f"  Components: {component_str}",
                f"  Loose: ${prices.get('loose_price', 'N/A')}" if prices.get('loose_price') else "  Loose: N/A",
                f"  CIB: ${prices.get('cib_price', 'N/A')}" if prices.get('cib_price') else "  CIB: N/A",
                f"  Item & Box: ${prices.get('item_box_price', 'N/A')}" if prices.get('item_box_price') else None,
                f"  Item & Manual: ${prices.get('item_manual_price', 'N/A')}" if prices.get('item_manual_price') else None,
                f"  Box Only: ${prices.get('box_only_price', 'N/A')}" if prices.get('box_only_price') else None,
                f"  Manual Only: ${prices.get('manual_only_price', 'N/A')}" if prices.get('manual_only_price') else None,
                f"  → Selected: {price_description} = ${result.price_eur:.2f} USD" if result.price_eur else f"  → Selected: {price_description}",
                f"  Note: Prices are in USD, will be converted to EUR",
                f"  URL: {game_info['url']}",
            ]
            result.details = "\n".join(filter(None, details_parts))

            logger.info(f"Found prices for {item.title}: {price_description} = ${result.price_eur}" if result.price_eur else f"No price for {item.title}")

            # Cache the result (include region in cache key)
            if self.cache and result.success:
                cache_key = build_cache_key(
                    platform=item.platform,
                    title=item.title,
                    packaging=item.packaging_state.value,
                    region=item.region.value if item.region else "",
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
    sleep_seconds: float = 10.0,
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
