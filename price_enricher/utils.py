"""Utility functions for query building, filtering, and helpers."""

import re
from typing import Any

from price_enricher.models import (
    GameItem,
    Language,
    PackagingState,
    Region,
    PLATFORM_EBAY_KEYWORDS,
    CARTRIDGE_PLATFORMS,
    DISC_PLATFORMS,
)


# =============================================================================
# Region Query Helpers
# =============================================================================


def get_region_include_keywords(region: Region) -> list[str]:
    """Get keywords to INCLUDE in search for a region."""
    if region == Region.PAL:
        return ["PAL"]
    elif region == Region.NTSC_U:
        return ["NTSC", "USA", "US"]
    elif region == Region.NTSC_J:
        return ["NTSC-J", "Japan", "Japanese", "JAP"]
    return []


def get_region_exclude_keywords(region: Region) -> list[str]:
    """Get keywords to EXCLUDE from search for a region."""
    if region == Region.PAL:
        return ["NTSC-U", "NTSC-J", "NTSCU", "NTSCJ", "JAP", "Japan", "Japanese", "USA"]
    elif region == Region.NTSC_U:
        return ["PAL", "JAP", "Japan", "Japanese", "NTSC-J", "NTSCJ"]
    elif region == Region.NTSC_J:
        return ["PAL", "USA", "US", "NTSC-U", "NTSCU"]
    return []


def get_language_keywords(language: Language) -> list[str]:
    """Get keywords for language preference."""
    lang_map = {
        Language.EN: ["English", "EN", "UK"],
        Language.FR: ["French", "FR", "Français"],
        Language.DE: ["German", "DE", "Deutsch"],
        Language.IT: ["Italian", "IT", "Italiano"],
        Language.ES: ["Spanish", "ES", "Español"],
    }
    return lang_map.get(language, [])


def get_language_exclude_keywords(language: Language) -> list[str]:
    """Get language keywords to exclude (for strict mode)."""
    all_langs = {Language.EN, Language.FR, Language.DE, Language.IT, Language.ES}

    if language == Language.ANY:
        return []

    exclude = []
    for lang in all_langs:
        if lang != language:
            exclude.extend(get_language_keywords(lang))

    return exclude


# =============================================================================
# Packaging Query Helpers
# =============================================================================


def get_packaging_keywords(packaging: PackagingState, platform: str) -> list[str]:
    """Get search keywords for packaging state."""
    is_cartridge = platform in CARTRIDGE_PLATFORMS

    if packaging == PackagingState.CIB:
        keywords = ["CIB", "complete", "boxed", "complete in box"]
        if is_cartridge:
            keywords.append("with box")
        return keywords
    elif packaging == PackagingState.LOOSE:
        if is_cartridge:
            return ["cartridge", "cart", "loose", "game only"]
        else:
            return ["disc", "loose", "game only", "disc only"]

    return []


def get_packaging_exclude_keywords(packaging: PackagingState) -> list[str]:
    """Get keywords to exclude based on packaging."""
    # Always exclude these misleading listings
    excludes = ["box only", "case only", "manual only", "empty box", "replacement case"]

    if packaging == PackagingState.LOOSE:
        excludes.extend(["CIB", "complete in box"])
    elif packaging == PackagingState.CIB:
        excludes.extend(["loose", "cartridge only", "disc only", "game only"])

    return excludes


# =============================================================================
# eBay Query Building
# =============================================================================


def build_ebay_query(
    item: GameItem,
    language: Language = Language.ANY,
    include_packaging: bool = True,
) -> str:
    """
    Build eBay search query for a game item.

    Returns the main search query string (without negative keywords,
    which should be added separately as eBay API parameters).
    """
    parts = []

    # Title
    clean_title = clean_title_for_search(item.title)
    parts.append(clean_title)

    # Platform
    platform_kw = PLATFORM_EBAY_KEYWORDS.get(item.platform, [item.platform])
    if platform_kw:
        parts.append(f"({' OR '.join(platform_kw)})")

    # Region (MANDATORY)
    region_kw = get_region_include_keywords(item.region)
    if region_kw:
        parts.append(f"({' OR '.join(region_kw)})")

    # Packaging
    if include_packaging:
        pkg_kw = get_packaging_keywords(item.packaging_state, item.platform)
        if pkg_kw:
            parts.append(f"({' OR '.join(pkg_kw)})")

    # Language preference
    if language != Language.ANY:
        lang_kw = get_language_keywords(language)
        if lang_kw:
            parts.append(f"({' OR '.join(lang_kw)})")

    return " ".join(parts)


def get_ebay_negative_keywords(
    item: GameItem,
    language: Language = Language.ANY,
    strict_language: bool = False,
    allow_lots: bool = False,
    allow_box_only: bool = False,
) -> list[str]:
    """Get list of negative keywords for eBay search."""
    negatives = []

    # Region exclusions (MANDATORY)
    negatives.extend(get_region_exclude_keywords(item.region))

    # Lot/bundle exclusions
    if not allow_lots:
        negatives.extend(["lot", "bundle", "job lot", "collection", "bulk"])

    # Box-only exclusions
    if not allow_box_only:
        negatives.extend(["box only", "case only", "manual only", "empty box"])

    # Language exclusions (strict mode)
    if strict_language and language != Language.ANY:
        negatives.extend(get_language_exclude_keywords(language))

    return negatives


def clean_title_for_search(title: str) -> str:
    """
    Clean title for search query.

    Removes special characters, edition markers that might
    cause issues with search.
    """
    # Remove parenthetical content that might be metadata
    # e.g., "Game Name (PAL)" -> "Game Name"
    title = re.sub(r"\s*\([^)]*\)\s*", " ", title)

    # Remove common edition suffixes that vary
    title = re.sub(r"\s*(Edition|Version|Release)\s*$", "", title, flags=re.IGNORECASE)

    # Remove trademark symbols
    title = title.replace("™", "").replace("®", "")

    # Normalize whitespace
    title = re.sub(r"\s+", " ", title).strip()

    # Escape special characters for eBay query
    # eBay uses specific syntax, so we need to escape quotes
    title = title.replace('"', "")

    return title


# =============================================================================
# Result Filtering
# =============================================================================


def title_contains_region(title: str, region: Region) -> bool:
    """Check if listing title indicates the correct region."""
    title_lower = title.lower()

    if region == Region.PAL:
        # PAL indicators
        pal_indicators = ["pal", "european", "europe", "uk version", "eur"]
        # Must contain at least one PAL indicator
        return any(ind in title_lower for ind in pal_indicators)

    elif region == Region.NTSC_U:
        ntsc_u_indicators = ["ntsc", "usa", "us version", "north america", "american"]
        return any(ind in title_lower for ind in ntsc_u_indicators)

    elif region == Region.NTSC_J:
        ntsc_j_indicators = ["ntsc-j", "japan", "japanese", "jap", "jp"]
        return any(ind in title_lower for ind in ntsc_j_indicators)

    return False


def title_contains_region_strict(title: str, region: Region) -> bool:
    """
    Strict region check - title must contain region AND not contain other regions.
    """
    title_lower = title.lower()

    # Check for conflicting regions
    all_pal = {"pal", "european", "europe"}
    all_ntsc_u = {"ntsc-u", "usa", "us version", "american"}
    all_ntsc_j = {"ntsc-j", "japan", "japanese", "jap"}

    has_pal = any(ind in title_lower for ind in all_pal)
    has_ntsc_u = any(ind in title_lower for ind in all_ntsc_u)
    has_ntsc_j = any(ind in title_lower for ind in all_ntsc_j)

    if region == Region.PAL:
        return has_pal and not has_ntsc_u and not has_ntsc_j
    elif region == Region.NTSC_U:
        return has_ntsc_u and not has_pal and not has_ntsc_j
    elif region == Region.NTSC_J:
        return has_ntsc_j and not has_pal and not has_ntsc_u

    return False


def is_lot_or_bundle(title: str) -> bool:
    """Check if listing is a lot/bundle."""
    title_lower = title.lower()
    lot_indicators = ["lot of", "bundle", "job lot", "bulk", " x ", "collection of", "set of"]
    return any(ind in title_lower for ind in lot_indicators)


def is_box_or_manual_only(title: str) -> bool:
    """Check if listing is box/manual only (no game)."""
    title_lower = title.lower()
    indicators = [
        "box only",
        "case only",
        "manual only",
        "empty box",
        "replacement case",
        "no game",
        "no cartridge",
        "no disc",
        "artwork only",
        "cover only",
    ]
    return any(ind in title_lower for ind in indicators)


def filter_listing(
    title: str,
    region: Region,
    strict_region: bool = True,
    allow_lots: bool = False,
    allow_box_only: bool = False,
) -> tuple[bool, str]:
    """
    Filter a listing based on criteria.

    Returns:
        Tuple of (pass, reason)
        - pass: True if listing passes filter
        - reason: Reason for rejection (empty if passed)
    """
    # Lot/bundle check
    if not allow_lots and is_lot_or_bundle(title):
        return False, "lot/bundle"

    # Box/manual only check
    if not allow_box_only and is_box_or_manual_only(title):
        return False, "box/manual only"

    # Region check
    if strict_region:
        if not title_contains_region_strict(title, region):
            if not title_contains_region(title, region):
                return False, f"region mismatch (want {region.value})"

    return True, ""


# =============================================================================
# Formatting Helpers
# =============================================================================


def format_price_eur(price: Any) -> str:
    """Format price as EUR string."""
    if price is None:
        return "N/A"
    return f"{float(price):.2f} EUR"


def format_listing_for_details(
    date: str,
    price_eur: Any,
    title: str,
    condition: str = "",
    url: str = "",
) -> str:
    """Format a single listing for the calculation details field."""
    parts = [f"[{date}]", format_price_eur(price_eur), f'"{title[:50]}..."' if len(title) > 50 else f'"{title}"']

    if condition:
        parts.append(f"({condition})")

    if url:
        parts.append(f"url={url}")

    return " ".join(parts)
