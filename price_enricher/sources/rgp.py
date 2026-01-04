"""PriceCharting scraper with rate limiting and caching.

Uses PriceCharting.com as the primary data source for retro game prices.
The original RetroGamePrices.com doesn't provide a working search API.

Search Strategy:
----------------
1. Builds a search URL matching the website's format (type=prices)
2. Parses the #games_table table in search results
3. Extracts structured data from each product row:
   - Game URL, title, platform, and region from the table cells
4. Scores and ranks matches based on:
   - Title similarity (50% weight)
   - Region match (30% weight) - PAL/JP/NTSC-U
   - Platform match (20% weight)
5. If redirected directly to a game page (exact match), uses that
6. Fetches the game detail page and extracts prices

Supported Price Types:
---------------------
- Loose: Game/cartridge/disc only
- CIB (Complete In Box): Game + box + manual
- Item & Box: Game + box, no manual
- Item & Manual: Game + manual, no box
- Box Only: Just the box
- Manual Only: Just the manual

The appropriate price is selected based on item components (has_game, has_box, has_manual).
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
# Lock is initialized lazily to avoid issues with event loop not being ready at import time
_global_rate_limit_lock: asyncio.Lock | None = None
_global_last_request_time = 0.0


def _get_rate_limit_lock() -> asyncio.Lock:
    """Get or create the global rate limit lock (lazy initialization)."""
    global _global_rate_limit_lock
    if _global_rate_limit_lock is None:
        _global_rate_limit_lock = asyncio.Lock()
    return _global_rate_limit_lock


class RGPClient:
    """
    Price scraper using PriceCharting.com as the data source.

    Provides loose, CIB, and new prices for retro games.
    Implements rate limiting and caching to be respectful.
    """

    def __init__(
        self,
        cache: PriceCache | None = None,
        sleep_seconds: float = 15.0,
    ):
        """
        Initialize RGP client.

        Args:
            cache: Optional cache instance
            sleep_seconds: Delay between requests (be respectful! PriceCharting rate limits aggressively)
        """
        self.cache = cache
        self.sleep_seconds = sleep_seconds

    async def _rate_limit(self) -> None:
        """Apply rate limiting between requests using global state."""
        global _global_last_request_time
        import time
        
        # Use lazily-initialized lock to ensure only one request proceeds at a time
        async with _get_rate_limit_lock():
            now = time.monotonic()
            
            # If this is the first request ever, just record the time
            if _global_last_request_time == 0.0:
                _global_last_request_time = now
                logger.info(f"Rate limit: First request, no delay needed")
                return
            
            # Calculate time since last request
            elapsed = now - _global_last_request_time
            
            # If not enough time has passed, wait
            if elapsed < self.sleep_seconds:
                wait_time = self.sleep_seconds - elapsed
                logger.info(f"Rate limiting: waiting {wait_time:.1f}s before next request...")
                await asyncio.sleep(wait_time)
            else:
                logger.debug(f"Rate limit: OK, {elapsed:.1f}s elapsed (>{self.sleep_seconds}s required)")
            
            # Update timestamp AFTER any sleep, just before releasing lock
            _global_last_request_time = time.monotonic()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
        reraise=True,
    )
    async def _make_request(self, url: str) -> tuple[str, str]:
        """Make HTTP request with retry and rate limiting.
        
        Returns:
            Tuple of (response_text, final_url) - final_url may differ from input if redirected
        """
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
            final_url = str(response.url)
            logger.debug(f"Response status: {response.status_code}, final URL: {final_url}")

            if response.status_code == 404:
                raise ValueError(f"Page not found: {url}")
            if response.status_code == 403:
                raise ValueError(f"Access forbidden (possible rate limiting): {url}")

            response.raise_for_status()
            return response.text, final_url

    def _clean_title_for_search(self, title: str) -> str:
        """Clean up title for better search matching.
        
        Removes:
        - Parenthetical notes like (loose), (Special Edition), (Platinum), (Essentials)
        - Special characters
        - Extra whitespace
        """
        # Remove common parenthetical notes that don't help with search
        cleaned = re.sub(r'\s*\([^)]*(?:loose|edition|platinum|essentials|classics|best seller|demo|rpg)\s*\)', '', title, flags=re.IGNORECASE)
        # Remove remaining parenthetical content that might be region/version specific
        cleaned = re.sub(r'\s*\([^)]*\)\s*$', '', cleaned)
        # Remove special characters except basic punctuation
        cleaned = re.sub(r'[^\w\s\'-]', ' ', cleaned)
        # Normalize whitespace
        cleaned = ' '.join(cleaned.split())
        return cleaned.strip()

    def _build_search_url(self, title: str, platform: str, region: str = "") -> str:
        """
        Build search URL for PriceCharting.
        
        Uses the same URL format as the website's search form:
        https://www.pricecharting.com/search-products?type=prices&q={query}
        
        Args:
            title: Game title to search for
            platform: Platform name (e.g., "Nintendo 3DS", "Gameboy")
            region: Region string (e.g., "PAL", "JP", or "" for NTSC-U)
            
        Returns:
            Full search URL
        """
        # Clean up title for search - remove parenthetical notes that hurt search
        clean_title = self._clean_title_for_search(title)
        
        # Build search term with platform and optional region
        if region:
            search_term = f"{clean_title} {platform} {region}"
        else:
            search_term = f"{clean_title} {platform}"
        
        # Keep apostrophes and basic punctuation that help search accuracy
        # Only remove truly problematic characters
        search_term = re.sub(r"[^\w\s\'-]", " ", search_term)
        search_term = " ".join(search_term.split())  # Normalize whitespace

        # Use type=prices (same as website) instead of type=videogames
        return f"{PRICECHARTING_SEARCH_URL}?type=prices&q={quote_plus(search_term)}"

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

    def _is_game_detail_page(self, url: str) -> bool:
        """
        Check if the URL is a game detail page (not search results).
        
        PriceCharting redirects directly to game pages for exact/good matches.
        - Game detail pages: /game/platform/game-name
        - Search results: /search-products?...
        """
        # Simple and reliable: check if URL contains /game/ path segment
        is_game_page = "/game/" in url and "/search" not in url
        logger.debug(f"URL check - is game page: {is_game_page} ({url})")
        return is_game_page

    def _extract_game_title_from_page(self, html: str) -> str | None:
        """Extract the game title from a game detail page."""
        soup = BeautifulSoup(html, "lxml")
        
        # Try various selectors for the title
        # 1. h1 tag (most common)
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text().strip()
            # Remove any "Prices" suffix that PriceCharting adds
            title = re.sub(r"\s+Prices?$", "", title, flags=re.IGNORECASE)
            if title:
                return title
        
        # 2. Title tag
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text().strip()
            # Remove site name and common suffixes
            title = re.sub(r"\s*[-|].*$", "", title)
            title = re.sub(r"\s+Prices?$", "", title, flags=re.IGNORECASE)
            if title:
                return title
        
        return None

    def _extract_platform_from_url(self, url: str) -> tuple[str, str]:
        """
        Extract platform slug and region from PriceCharting game URL.
        
        URLs have format: /game/{platform-slug}/{game-slug}
        Platform slugs can include region prefix: pal-gameboy, jp-nintendo-3ds, etc.
        
        Returns:
            Tuple of (platform_slug, region) where region is 'pal', 'jp', or '' (NTSC-U)
        """
        # Parse the URL path to extract platform
        # URL examples:
        #   https://www.pricecharting.com/game/pal-gameboy/super-mario-land-2
        #   https://www.pricecharting.com/game/pal-nintendo-3ds/luigi's-mansion-2
        #   /game/gameboy/super-mario-land-2  (relative)
        
        # Extract the path after /game/
        match = re.search(r'/game/([^/]+)/', url)
        if not match:
            return ("", "")
        
        platform_slug = match.group(1).lower()
        
        # Check for region prefix
        if platform_slug.startswith("pal-"):
            return (platform_slug[4:], "pal")
        elif platform_slug.startswith("jp-"):
            return (platform_slug[3:], "jp")
        else:
            return (platform_slug, "")  # NTSC-U (default, no prefix)

    def _normalize_platform_for_comparison(self, platform: str) -> str:
        """
        Normalize platform name for comparison with URL slugs.
        
        Converts input platform names to lowercase slug format matching PriceCharting URLs.
        """
        # Map common variations to URL slug format
        platform_lower = platform.lower().replace(" ", "-")
        
        # Special mappings for platforms that differ between input and URL slug
        slug_map = {
            "game-boy": "gameboy",
            "game-boy-color": "gameboy-color", 
            "game-boy-advance": "gameboy-advance",
            "nintendo-3ds": "nintendo-3ds",
            "3ds": "nintendo-3ds",
            "nintendo-ds": "nintendo-ds",
            "nds": "nintendo-ds",
            "super-nintendo": "super-nintendo",
            "snes": "super-nintendo",
            "nintendo-64": "nintendo-64",
            "n64": "nintendo-64",
            "gamecube": "gamecube",
            "gc": "gamecube",
            "mega-drive": "sega-genesis",
            "genesis": "sega-genesis",
            "sega-genesis": "sega-genesis",
            "playstation": "playstation",
            "ps1": "playstation",
            "psx": "playstation",
            "playstation-2": "playstation-2",
            "ps2": "playstation-2",
            "playstation-3": "playstation-3",
            "ps3": "playstation-3",
            "playstation-4": "playstation-4",
            "ps4": "playstation-4",
            "ps-vita": "playstation-vita",
            "dreamcast": "sega-dreamcast",
            "sega-dreamcast": "sega-dreamcast",
            "saturn": "sega-saturn",
            "sega-saturn": "sega-saturn",
            "master-system": "sega-master-system",
            "game-gear": "sega-game-gear",
            "nes": "nes",
            "wii": "wii",
            "wii-u": "wii-u",
            "nintendo-switch": "nintendo-switch",
        }
        
        return slug_map.get(platform_lower, platform_lower)

    def _calculate_title_similarity(self, search_title: str, result_title: str) -> float:
        """
        Calculate similarity score between search title and result title.
        
        Returns a score from 0.0 to 1.0 where 1.0 is exact match.
        """
        # Normalize both titles
        search_clean = self._clean_title_for_search(search_title).lower()
        result_clean = self._clean_title_for_search(result_title).lower()
        
        # Exact match
        if search_clean == result_clean:
            return 1.0
        
        # One contains the other
        if search_clean in result_clean or result_clean in search_clean:
            # Score based on length ratio
            shorter = min(len(search_clean), len(result_clean))
            longer = max(len(search_clean), len(result_clean))
            return 0.8 + (0.2 * shorter / longer) if longer > 0 else 0.8
        
        # Word-based matching
        search_words = set(search_clean.split())
        result_words = set(result_clean.split())
        
        # Remove very short words for matching
        search_words_significant = {w for w in search_words if len(w) > 2}
        result_words_significant = {w for w in result_words if len(w) > 2}
        
        if not search_words_significant:
            search_words_significant = search_words
        
        # Calculate overlap
        common_words = search_words_significant & result_words_significant
        
        if not common_words:
            return 0.0
        
        # Score based on coverage of search words in result
        coverage = len(common_words) / len(search_words_significant) if search_words_significant else 0
        
        return min(0.7, coverage * 0.7)  # Cap at 0.7 for word-based matching

    def _parse_search_results(self, html: str, title: str, platform: str, region: str = "") -> dict | None:
        """
        Parse PriceCharting search results page to find matching game.

        PriceCharting search results are displayed in a table with id="games_table".
        Each game row has:
        - Row with id="product-{id}" and data-product="{id}"
        - Title cell (.title) with link to game page
        - Console/platform cell (.console) with platform name
        - Price cells for loose, CIB, new

        Args:
            html: The HTML content of the search results page
            title: The game title we're searching for
            platform: The platform (e.g., "Nintendo 3DS", "Game Boy")
            region: The region ("PAL", "JP", or "" for NTSC-U)

        Returns:
            Dict with 'url' and 'title' of best matching game, or None if not found
        """
        soup = BeautifulSoup(html, "lxml")
        
        # Normalize inputs for matching
        search_title_clean = self._clean_title_for_search(title).lower()
        platform_slug = self._normalize_platform_for_comparison(platform)
        region_lower = region.lower() if region else ""
        
        logger.debug(f"Parsing search results for: title='{search_title_clean}', platform='{platform_slug}', region='{region_lower}'")
        
        # Find the games table - this is the main search results container
        games_table = soup.find("table", id="games_table")
        
        if not games_table:
            logger.warning("Could not find #games_table in search results")
            # Fallback: look for any table with product rows
            games_table = soup.find("table", class_="hoverable-rows")
        
        if not games_table:
            logger.warning("No games table found in search results HTML")
            return None
        
        # Find all product rows (they have id like "product-12345")
        product_rows = games_table.find_all("tr", id=lambda x: x and x.startswith("product-"))
        
        if not product_rows:
            # Try alternate selector
            product_rows = games_table.find_all("tr", attrs={"data-product": True})
        
        logger.debug(f"Found {len(product_rows)} product rows in search results")
        
        if not product_rows:
            return None
        
        candidates = []
        
        for row in product_rows:
            # Extract game URL from title cell link
            title_cell = row.find("td", class_="title")
            if not title_cell:
                continue
            
            title_link = title_cell.find("a", href=True)
            if not title_link:
                continue
            
            game_url = title_link.get("href", "")
            game_title = title_link.get_text().strip()
            
            # Skip if no valid URL
            if "/game/" not in game_url:
                continue
            
            # Extract platform and region from URL
            url_platform, url_region = self._extract_platform_from_url(game_url)
            
            # Also try to get platform from console cell as backup
            console_cell = row.find("td", class_="console")
            console_text = console_cell.get_text().strip().lower() if console_cell else ""
            
            # Calculate match scores
            title_score = self._calculate_title_similarity(title, game_title)
            
            # Platform matching
            platform_score = 0.0
            if url_platform:
                # Check if platforms match (normalize both for comparison)
                if platform_slug in url_platform or url_platform in platform_slug:
                    platform_score = 1.0
                elif platform_slug.replace("-", "") in url_platform.replace("-", ""):
                    platform_score = 0.9
                # Also check console text
                elif platform_slug.replace("-", " ") in console_text:
                    platform_score = 0.8
            
            # Region matching
            region_score = 0.0
            if region_lower:
                if region_lower == url_region:
                    region_score = 1.0  # Exact region match
                elif region_lower == "pal" and url_region == "":
                    region_score = 0.3  # PAL searching but found NTSC-U (acceptable fallback)
                elif region_lower == "" and url_region == "":
                    region_score = 1.0  # Both NTSC-U
            else:
                # No region specified - any region is fine
                region_score = 0.5
            
            # Combined score (title is most important, then region, then platform)
            total_score = (title_score * 0.5) + (region_score * 0.3) + (platform_score * 0.2)
            
            # Build full URL if relative
            if not game_url.startswith("http"):
                game_url = f"{PRICECHARTING_BASE_URL}{game_url}"
            
            logger.debug(
                f"  Candidate: '{game_title}' | platform={url_platform} region={url_region} | "
                f"scores: title={title_score:.2f} region={region_score:.2f} platform={platform_score:.2f} total={total_score:.2f}"
            )
            
            candidates.append({
                "url": game_url,
                "title": game_title,
                "score": total_score,
                "title_score": title_score,
                "region_score": region_score,
                "platform_score": platform_score,
            })
        
        if not candidates:
            logger.debug("No valid candidates found in search results")
            return None
        
        # Sort by total score, then by title score as tiebreaker
        candidates.sort(key=lambda x: (x["score"], x["title_score"]), reverse=True)
        
        best = candidates[0]
        
        # Only accept if we have a reasonable match
        # Minimum thresholds: title must be at least somewhat similar
        if best["title_score"] < 0.3:
            logger.debug(f"Best candidate '{best['title']}' has too low title score: {best['title_score']:.2f}")
            return None
        
        logger.info(f"Found match: '{best['title']}' (score={best['score']:.2f}) -> {best['url']}")
        
        return {
            "url": best["url"],
            "title": best["title"],
        }

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

            search_html, final_search_url = await self._make_request(search_url)

            # Check if we were redirected directly to a game page (exact match)
            # This happens when PriceCharting finds an exact match
            # Check by URL pattern: /game/ means game page, /search means search results
            if self._is_game_detail_page(final_search_url):
                logger.info(f"Search redirected directly to game page (exact match): {final_search_url}")
                game_info = {
                    "url": final_search_url,
                    "title": self._extract_game_title_from_page(search_html) or item.title,
                }
                # Use the already-fetched HTML for parsing prices (avoid extra request)
                game_html = search_html
            else:
                # We got search results page - parse to find matching game
                logger.debug(f"Got search results page, parsing for matches...")
                game_info = self._parse_search_results(search_html, item.title, pc_platform, region_str)

                if not game_info:
                    result.success = False
                    result.error = "Game not found in search results"
                    result.details = f"PriceCharting: No match for '{item.title}' ({item.platform}) [{region_str or 'NTSC-U'}]\nSearched: {search_url}"
                    logger.warning(f"No match found for {item.title} ({item.platform}) [{region_str or 'NTSC-U'}]")
                    return result

                # Fetch game detail page
                logger.info(f"Found in search results: {game_info['title']} - fetching detail page...")
                game_html, _ = await self._make_request(game_info["url"])

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
    sleep_seconds: float = 15.0,
) -> PriceResult:
    """
    Convenience function to get price estimate for a game item.

    Args:
        item: Game item to price
        cache: Optional cache instance
        sleep_seconds: Rate limit delay (default 15s to avoid 429 errors)

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
