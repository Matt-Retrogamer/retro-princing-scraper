"""Tests for the PriceCharting scraper (RGP client)."""

import pytest
from decimal import Decimal

from price_enricher.sources.rgp import RGPClient
from price_enricher.models import GameItem, Region, PackagingState


class TestRGPClient:
    """Tests for the RGPClient class."""

    @pytest.fixture
    def client(self):
        """Create a test client instance."""
        return RGPClient(cache=None, sleep_seconds=0)

    # =========================================================================
    # URL Building Tests
    # =========================================================================

    def test_build_search_url_basic(self, client):
        """Test basic search URL construction."""
        url = client._build_search_url("Super Mario Land", "Gameboy", "")
        assert "pricecharting.com/search-products" in url
        assert "type=prices" in url
        assert "Super+Mario+Land" in url or "Super%20Mario%20Land" in url
        assert "Gameboy" in url

    def test_build_search_url_with_region(self, client):
        """Test search URL construction with region."""
        url = client._build_search_url("Super Mario Land", "Gameboy", "PAL")
        assert "type=prices" in url
        assert "PAL" in url

    def test_build_search_url_preserves_apostrophes(self, client):
        """Test that apostrophes are preserved in search URLs."""
        url = client._build_search_url("Luigi's Mansion 2", "Nintendo 3DS", "PAL")
        # URL encoded apostrophe or raw apostrophe should be present
        assert "Luigi" in url and "Mansion" in url

    def test_build_search_url_cleans_parenthetical_notes(self, client):
        """Test that edition notes are cleaned from search terms."""
        url = client._build_search_url("Zelda: Majora's Mask 3D (Special Edition)", "Nintendo 3DS", "PAL")
        # Special Edition should be removed
        assert "Special" not in url or "Edition" not in url

    # =========================================================================
    # Title Cleaning Tests
    # =========================================================================

    def test_clean_title_removes_edition_notes(self, client):
        """Test cleaning of common edition suffixes."""
        assert "Special Edition" not in client._clean_title_for_search("Game (Special Edition)")
        assert "Platinum" not in client._clean_title_for_search("Game (Platinum)")
        assert "Essentials" not in client._clean_title_for_search("Game (Essentials)")
        assert "Loose" not in client._clean_title_for_search("Game (loose)")

    def test_clean_title_preserves_game_name(self, client):
        """Test that the core game name is preserved."""
        cleaned = client._clean_title_for_search("Super Mario World (Special Edition)")
        assert "Super Mario World" in cleaned

    def test_clean_title_handles_special_characters(self, client):
        """Test handling of special characters."""
        cleaned = client._clean_title_for_search("Luigi's Mansion 2")
        assert "Luigi" in cleaned and "Mansion" in cleaned

    # =========================================================================
    # Platform Extraction Tests
    # =========================================================================

    def test_extract_platform_from_url_pal_gameboy(self, client):
        """Test extracting platform from PAL GameBoy URL."""
        url = "https://www.pricecharting.com/game/pal-gameboy/super-mario-land-2"
        platform, region = client._extract_platform_from_url(url)
        assert platform == "gameboy"
        assert region == "pal"

    def test_extract_platform_from_url_pal_3ds(self, client):
        """Test extracting platform from PAL Nintendo 3DS URL."""
        url = "https://www.pricecharting.com/game/pal-nintendo-3ds/luigi's-mansion-2"
        platform, region = client._extract_platform_from_url(url)
        assert platform == "nintendo-3ds"
        assert region == "pal"

    def test_extract_platform_from_url_jp(self, client):
        """Test extracting platform from JP URL."""
        url = "https://www.pricecharting.com/game/jp-gameboy/pokemon-green"
        platform, region = client._extract_platform_from_url(url)
        assert platform == "gameboy"
        assert region == "jp"

    def test_extract_platform_from_url_ntsc_u(self, client):
        """Test extracting platform from NTSC-U URL (no prefix)."""
        url = "https://www.pricecharting.com/game/gameboy/super-mario-land-2"
        platform, region = client._extract_platform_from_url(url)
        assert platform == "gameboy"
        assert region == ""

    def test_extract_platform_from_url_relative(self, client):
        """Test extracting platform from relative URL."""
        url = "/game/pal-nintendo-3ds/mario-kart-7"
        platform, region = client._extract_platform_from_url(url)
        assert platform == "nintendo-3ds"
        assert region == "pal"

    def test_extract_platform_from_url_invalid(self, client):
        """Test extracting platform from invalid URL."""
        url = "https://www.pricecharting.com/category/video-games"
        platform, region = client._extract_platform_from_url(url)
        assert platform == ""
        assert region == ""

    # =========================================================================
    # Platform Normalization Tests
    # =========================================================================

    def test_normalize_platform_gameboy_variations(self, client):
        """Test normalizing Game Boy platform variations."""
        assert client._normalize_platform_for_comparison("Game Boy") == "gameboy"
        assert client._normalize_platform_for_comparison("Gameboy") == "gameboy"
        assert client._normalize_platform_for_comparison("GB") == "gb"

    def test_normalize_platform_3ds_variations(self, client):
        """Test normalizing 3DS platform variations."""
        assert client._normalize_platform_for_comparison("Nintendo 3DS") == "nintendo-3ds"
        assert client._normalize_platform_for_comparison("3DS") == "nintendo-3ds"

    def test_normalize_platform_playstation_variations(self, client):
        """Test normalizing PlayStation platform variations."""
        assert client._normalize_platform_for_comparison("PlayStation") == "playstation"
        assert client._normalize_platform_for_comparison("PS1") == "playstation"
        assert client._normalize_platform_for_comparison("PlayStation 2") == "playstation-2"
        assert client._normalize_platform_for_comparison("PS2") == "playstation-2"

    # =========================================================================
    # Title Similarity Tests
    # =========================================================================

    def test_title_similarity_exact_match(self, client):
        """Test exact title match returns high score."""
        score = client._calculate_title_similarity("Super Mario Land 2", "Super Mario Land 2")
        assert score == 1.0

    def test_title_similarity_substring_match(self, client):
        """Test substring match returns high score."""
        score = client._calculate_title_similarity("Super Mario Land 2", "Super Mario Land 2 [Nintendo Classics]")
        assert score >= 0.8

    def test_title_similarity_word_overlap(self, client):
        """Test word overlap returns reasonable score."""
        score = client._calculate_title_similarity("Mario Kart 7", "Mario Kart 7")
        assert score == 1.0

    def test_title_similarity_no_match(self, client):
        """Test completely different titles return low score."""
        score = client._calculate_title_similarity("Super Mario Land", "Sonic the Hedgehog")
        assert score < 0.3

    def test_title_similarity_partial_match(self, client):
        """Test partial word match returns moderate score."""
        score = client._calculate_title_similarity("Legend of Zelda", "The Legend of Zelda: Ocarina of Time")
        assert 0.3 <= score <= 0.9

    # =========================================================================
    # Search Results Parsing Tests
    # =========================================================================

    def test_parse_search_results_finds_game(self, client):
        """Test parsing search results with valid game table."""
        html = """
        <html>
        <body>
        <table id="games_table" class="hoverable-rows">
            <thead><tr><th>Title</th><th>Console</th></tr></thead>
            <tbody>
                <tr id="product-46871" data-product="46871">
                    <td class="title">
                        <a href="/game/pal-gameboy/super-mario-land-2">Super Mario Land 2</a>
                    </td>
                    <td class="console">
                        <a href="/console/pal-gameboy">PAL GameBoy</a>
                    </td>
                </tr>
            </tbody>
        </table>
        </body>
        </html>
        """
        result = client._parse_search_results(html, "Super Mario Land 2", "Gameboy", "PAL")
        assert result is not None
        assert "super-mario-land-2" in result["url"]
        assert "Super Mario Land 2" in result["title"]

    def test_parse_search_results_handles_multiple_results(self, client):
        """Test parsing picks best match from multiple results."""
        html = """
        <html>
        <body>
        <table id="games_table">
            <tr id="product-1" data-product="1">
                <td class="title">
                    <a href="/game/pal-gameboy/super-mario-land-2">Super Mario Land 2</a>
                </td>
                <td class="console">PAL GameBoy</td>
            </tr>
            <tr id="product-2" data-product="2">
                <td class="title">
                    <a href="/game/pal-gameboy/super-mario-land-2-classics">Super Mario Land 2 [Nintendo Classics]</a>
                </td>
                <td class="console">PAL GameBoy</td>
            </tr>
            <tr id="product-3" data-product="3">
                <td class="title">
                    <a href="/game/pal-gameboy/gameboy-bundle">Gameboy [Super Mario Bundle]</a>
                </td>
                <td class="console">PAL GameBoy</td>
            </tr>
        </table>
        </body>
        </html>
        """
        result = client._parse_search_results(html, "Super Mario Land 2", "Gameboy", "PAL")
        assert result is not None
        # Should pick the exact match, not the bundle
        assert "super-mario-land-2" in result["url"]
        assert "Bundle" not in result["title"]

    def test_parse_search_results_prefers_correct_region(self, client):
        """Test that PAL region is preferred when searching for PAL."""
        html = """
        <html>
        <body>
        <table id="games_table">
            <tr id="product-1" data-product="1">
                <td class="title">
                    <a href="/game/gameboy/super-mario-land-2">Super Mario Land 2</a>
                </td>
                <td class="console">GameBoy</td>
            </tr>
            <tr id="product-2" data-product="2">
                <td class="title">
                    <a href="/game/pal-gameboy/super-mario-land-2">Super Mario Land 2</a>
                </td>
                <td class="console">PAL GameBoy</td>
            </tr>
        </table>
        </body>
        </html>
        """
        result = client._parse_search_results(html, "Super Mario Land 2", "Gameboy", "PAL")
        assert result is not None
        # Should prefer PAL version
        assert "pal-gameboy" in result["url"]

    def test_parse_search_results_empty_table(self, client):
        """Test handling of empty search results."""
        html = """
        <html>
        <body>
        <table id="games_table">
            <thead><tr><th>Title</th></tr></thead>
            <tbody></tbody>
        </table>
        </body>
        </html>
        """
        result = client._parse_search_results(html, "Super Mario Land 2", "Gameboy", "PAL")
        assert result is None

    def test_parse_search_results_no_table(self, client):
        """Test handling of page without games table."""
        html = """
        <html>
        <body>
        <div>No results found</div>
        </body>
        </html>
        """
        result = client._parse_search_results(html, "Super Mario Land 2", "Gameboy", "PAL")
        assert result is None

    def test_parse_search_results_html_entities(self, client):
        """Test handling of HTML entities in titles."""
        html = """
        <html>
        <body>
        <table id="games_table">
            <tr id="product-65486" data-product="65486">
                <td class="title">
                    <a href="/game/pal-nintendo-3ds/luigi%27s-mansion-2">Luigi&#39;s Mansion 2</a>
                </td>
                <td class="console">PAL Nintendo 3DS</td>
            </tr>
        </table>
        </body>
        </html>
        """
        result = client._parse_search_results(html, "Luigi's Mansion 2", "Nintendo 3DS", "PAL")
        assert result is not None
        assert "Luigi" in result["title"]

    # =========================================================================
    # Game Page Detection Tests
    # =========================================================================

    def test_is_game_detail_page_true(self, client):
        """Test detection of game detail page URLs."""
        assert client._is_game_detail_page("https://www.pricecharting.com/game/pal-gameboy/super-mario-land-2")
        assert client._is_game_detail_page("/game/pal-nintendo-3ds/luigi's-mansion-2")

    def test_is_game_detail_page_false_search(self, client):
        """Test that search URLs are not detected as game pages."""
        assert not client._is_game_detail_page("https://www.pricecharting.com/search-products?q=mario")
        assert not client._is_game_detail_page("/search-products?q=mario&type=prices")

    def test_is_game_detail_page_false_other(self, client):
        """Test that other URLs are not detected as game pages."""
        assert not client._is_game_detail_page("https://www.pricecharting.com/category/video-games")
        assert not client._is_game_detail_page("https://www.pricecharting.com/console/pal-gameboy")

    # =========================================================================
    # Price Parsing Tests
    # =========================================================================

    def test_parse_price_basic(self, client):
        """Test basic price parsing."""
        assert client._parse_price("$13.16") == Decimal("13.16")
        assert client._parse_price("$1,234.56") == Decimal("1234.56")

    def test_parse_price_with_euro(self, client):
        """Test parsing Euro prices."""
        assert client._parse_price("€25.00") == Decimal("25.00")

    def test_parse_price_na(self, client):
        """Test handling of N/A values."""
        assert client._parse_price("N/A") is None
        assert client._parse_price("None") is None
        assert client._parse_price("-") is None

    def test_parse_price_range(self, client):
        """Test parsing price ranges (returns average)."""
        result = client._parse_price("10.00 - 20.00")
        assert result == Decimal("15.00")

    # =========================================================================
    # Price Selection Tests
    # =========================================================================

    def test_select_price_loose(self, client):
        """Test price selection for loose game (game only)."""
        item = GameItem(
            platform="Game Boy",
            title="Test Game",
            has_game="Y",
            has_box="N",
            has_manual="N",
        )
        prices = {"loose_price": Decimal("10.00"), "cib_price": Decimal("50.00")}
        price, description = client._select_price_for_item(item, prices)
        assert price == Decimal("10.00")
        assert "Loose" in description

    def test_select_price_cib(self, client):
        """Test price selection for CIB (complete in box)."""
        item = GameItem(
            platform="Game Boy",
            title="Test Game",
            has_game="Y",
            has_box="Y",
            has_manual="Y",
        )
        prices = {"loose_price": Decimal("10.00"), "cib_price": Decimal("50.00")}
        price, description = client._select_price_for_item(item, prices)
        assert price == Decimal("50.00")
        assert "CIB" in description or "Complete" in description

    def test_select_price_game_and_box(self, client):
        """Test price selection for game + box (no manual)."""
        item = GameItem(
            platform="Game Boy",
            title="Test Game",
            has_game="Y",
            has_box="Y",
            has_manual="N",
        )
        prices = {
            "loose_price": Decimal("10.00"),
            "cib_price": Decimal("50.00"),
            "item_box_price": Decimal("35.00"),
        }
        price, description = client._select_price_for_item(item, prices)
        assert price == Decimal("35.00")
        assert "Item & Box" in description

    def test_select_price_box_only(self, client):
        """Test price selection for box only (no game)."""
        item = GameItem(
            platform="Game Boy",
            title="Test Game",
            has_game="N",
            has_box="Y",
            has_manual="N",
        )
        prices = {
            "loose_price": Decimal("10.00"),
            "cib_price": Decimal("50.00"),
            "box_only_price": Decimal("20.00"),
        }
        price, description = client._select_price_for_item(item, prices)
        assert price == Decimal("20.00")
        assert "Box" in description

    # =========================================================================
    # Region Mapping Tests
    # =========================================================================

    def test_map_region_to_pricecharting(self, client):
        """Test region mapping."""
        assert client._map_region_to_pricecharting(Region.PAL) == "PAL"
        assert client._map_region_to_pricecharting(Region.NTSC_U) == ""
        assert client._map_region_to_pricecharting(Region.NTSC_J) == "JP"

    # =========================================================================
    # Platform Mapping Tests
    # =========================================================================

    def test_map_platform_to_pricecharting(self, client):
        """Test platform mapping for search."""
        assert client._map_platform_to_pricecharting("Game Boy") == "Gameboy"
        assert client._map_platform_to_pricecharting("Nintendo 3DS") == "Nintendo 3DS"
        assert client._map_platform_to_pricecharting("PlayStation") == "Playstation"
        assert client._map_platform_to_pricecharting("Mega Drive") == "Sega Genesis"
