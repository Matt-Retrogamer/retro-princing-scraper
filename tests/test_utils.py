"""Tests for price_enricher utilities."""

import pytest

from price_enricher.models import GameItem, Region, Language, PackagingState
from price_enricher.utils import (
    get_region_include_keywords,
    get_region_exclude_keywords,
    get_language_keywords,
    get_packaging_keywords,
    build_ebay_query,
    get_ebay_negative_keywords,
    clean_title_for_search,
    title_contains_region,
    title_contains_region_strict,
    is_lot_or_bundle,
    is_box_or_manual_only,
    filter_listing,
)


class TestRegionKeywords:
    """Tests for region keyword helpers."""

    def test_pal_include_keywords(self):
        """Test PAL include keywords."""
        kw = get_region_include_keywords(Region.PAL)
        assert "PAL" in kw

    def test_pal_exclude_keywords(self):
        """Test PAL exclude keywords."""
        kw = get_region_exclude_keywords(Region.PAL)
        assert "NTSC-U" in kw
        assert "NTSC-J" in kw
        assert "Japan" in kw
        assert "USA" in kw

    def test_ntsc_u_include_keywords(self):
        """Test NTSC-U include keywords."""
        kw = get_region_include_keywords(Region.NTSC_U)
        assert "NTSC" in kw
        assert "USA" in kw

    def test_ntsc_j_include_keywords(self):
        """Test NTSC-J include keywords."""
        kw = get_region_include_keywords(Region.NTSC_J)
        assert any("Japan" in k for k in kw)


class TestLanguageKeywords:
    """Tests for language keyword helpers."""

    def test_french_keywords(self):
        """Test French language keywords."""
        kw = get_language_keywords(Language.FR)
        assert "French" in kw
        assert "FR" in kw

    def test_any_keywords(self):
        """Test ANY returns empty list."""
        kw = get_language_keywords(Language.ANY)
        assert kw == []


class TestPackagingKeywords:
    """Tests for packaging keyword helpers."""

    def test_cib_keywords(self):
        """Test CIB keywords."""
        kw = get_packaging_keywords(PackagingState.CIB, "SNES")
        assert "CIB" in kw
        assert "complete" in kw

    def test_loose_cartridge_keywords(self):
        """Test loose cartridge keywords."""
        kw = get_packaging_keywords(PackagingState.LOOSE, "SNES")
        assert "cartridge" in kw or "cart" in kw

    def test_loose_disc_keywords(self):
        """Test loose disc keywords."""
        kw = get_packaging_keywords(PackagingState.LOOSE, "PlayStation 2")
        assert "disc" in kw


class TestCleanTitle:
    """Tests for title cleaning."""

    def test_removes_parentheses(self):
        """Test parenthetical content removal."""
        assert "Game Name" in clean_title_for_search("Game Name (PAL)")
        assert "PAL" not in clean_title_for_search("Game Name (PAL)")

    def test_removes_trademark(self):
        """Test trademark symbol removal."""
        assert "™" not in clean_title_for_search("Game™ Name")
        assert "®" not in clean_title_for_search("Game® Name")

    def test_normalizes_whitespace(self):
        """Test whitespace normalization."""
        cleaned = clean_title_for_search("Game   Name  ")
        assert "  " not in cleaned


class TestRegionDetection:
    """Tests for region detection in titles."""

    def test_detects_pal(self):
        """Test PAL detection."""
        assert title_contains_region("Super Mario World PAL", Region.PAL)
        assert title_contains_region("Super Mario World European Version", Region.PAL)

    def test_detects_ntsc_u(self):
        """Test NTSC-U detection."""
        assert title_contains_region("Super Mario World NTSC USA", Region.NTSC_U)

    def test_detects_ntsc_j(self):
        """Test NTSC-J detection."""
        assert title_contains_region("Super Mario World Japan", Region.NTSC_J)

    def test_strict_region_conflict(self):
        """Test strict mode rejects conflicting regions."""
        # Title mentions both PAL and USA - should fail strict check
        assert not title_contains_region_strict("Game PAL USA", Region.PAL)


class TestLotBundleDetection:
    """Tests for lot/bundle detection."""

    def test_detects_lot(self):
        """Test lot detection."""
        assert is_lot_or_bundle("Lot of 5 SNES Games")
        assert is_lot_or_bundle("Job lot Nintendo")
        assert is_lot_or_bundle("Game Bundle")

    def test_not_a_lot(self):
        """Test non-lot titles."""
        assert not is_lot_or_bundle("Super Mario World PAL Complete")


class TestBoxOnlyDetection:
    """Tests for box/manual only detection."""

    def test_detects_box_only(self):
        """Test box only detection."""
        assert is_box_or_manual_only("Super Mario World Box Only")
        assert is_box_or_manual_only("Game Case Only No Disc")
        assert is_box_or_manual_only("Manual Only")

    def test_not_box_only(self):
        """Test complete listings."""
        assert not is_box_or_manual_only("Super Mario World Complete")


class TestFilterListing:
    """Tests for listing filtering."""

    def test_filters_lots(self):
        """Test lot filtering."""
        passed, reason = filter_listing(
            "Lot of 5 Games",
            Region.PAL,
            allow_lots=False,
        )
        assert not passed
        assert "lot" in reason

    def test_filters_box_only(self):
        """Test box only filtering."""
        passed, reason = filter_listing(
            "Game Box Only",
            Region.PAL,
            allow_box_only=False,
        )
        assert not passed
        assert "box" in reason

    def test_filters_wrong_region(self):
        """Test region filtering."""
        passed, reason = filter_listing(
            "Game USA Version",
            Region.PAL,
            strict_region=True,
        )
        assert not passed
        assert "region" in reason

    def test_passes_valid_listing(self):
        """Test valid listing passes."""
        passed, reason = filter_listing(
            "Super Mario World PAL Complete",
            Region.PAL,
        )
        assert passed
        assert reason == ""


class TestBuildEbayQuery:
    """Tests for eBay query building."""

    def test_includes_title(self):
        """Test query includes title."""
        item = GameItem(
            platform="SNES",
            title="Super Mario World",
            region=Region.PAL,
            has_game="Y",
            has_box="Y",
            has_manual="Y",
        )
        query = build_ebay_query(item)
        assert "Super Mario World" in query

    def test_includes_platform(self):
        """Test query includes platform."""
        item = GameItem(
            platform="SNES",
            title="Test",
            region=Region.PAL,
            has_game="Y",
        )
        query = build_ebay_query(item)
        assert "SNES" in query or "Super Nintendo" in query

    def test_includes_region(self):
        """Test query includes region."""
        item = GameItem(
            platform="SNES",
            title="Test",
            region=Region.PAL,
            has_game="Y",
        )
        query = build_ebay_query(item)
        assert "PAL" in query


class TestEbayNegativeKeywords:
    """Tests for eBay negative keywords."""

    def test_excludes_other_regions(self):
        """Test region exclusions."""
        item = GameItem(
            platform="SNES",
            title="Test",
            region=Region.PAL,
            has_game="Y",
        )
        negatives = get_ebay_negative_keywords(item)
        assert "NTSC-U" in negatives or "NTSCU" in negatives
        assert "Japan" in negatives

    def test_excludes_lots_by_default(self):
        """Test lot exclusions."""
        item = GameItem(platform="SNES", title="Test", region=Region.PAL)
        negatives = get_ebay_negative_keywords(item, allow_lots=False)
        assert "lot" in negatives
        assert "bundle" in negatives

    def test_allows_lots_when_specified(self):
        """Test lots allowed when flag set."""
        item = GameItem(platform="SNES", title="Test", region=Region.PAL)
        negatives = get_ebay_negative_keywords(item, allow_lots=True)
        assert "lot" not in negatives
