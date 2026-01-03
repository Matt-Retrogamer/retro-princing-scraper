"""Data models and column mappings for the price enricher."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any


# =============================================================================
# CSV Language Support
# =============================================================================


class CSVLanguage(str, Enum):
    """Supported CSV languages."""

    EN = "EN"
    FR = "FR"


# =============================================================================
# Column Mappings (Header -> Internal Key)
# =============================================================================

# Internal column keys (technical names used in code)
INTERNAL_COLUMNS = [
    "platform",
    "item_type",
    "title",
    "condition_text",
    "rarity",
    "local_estimate_eur",
    "has_box",
    "has_manual",
    "has_insert",
    "has_game",
    "notes",
    "online_estimate_eur",
    "calculation_details",
    "region",
]

# French column name -> Internal key
COLUMN_FR_TO_INTERNAL: dict[str, str] = {
    "Plateforme": "platform",
    "Type": "item_type",
    "Titre": "title",
    "État": "condition_text",
    "Rareté": "rarity",
    "Estimation (€)": "local_estimate_eur",
    "Boîte": "has_box",
    "Manuel": "has_manual",
    "Cale": "has_insert",
    "Jeu": "has_game",
    "Remarques": "notes",
    "Estimation Online": "online_estimate_eur",
    "Détail Calcul": "calculation_details",
    "Région": "region",
    "Region": "region",  # Alternative spelling
}

# English column name -> Internal key
COLUMN_EN_TO_INTERNAL: dict[str, str] = {
    "Platform": "platform",
    "Type": "item_type",
    "Title": "title",
    "Condition": "condition_text",
    "Rarity": "rarity",
    "Estimate (€)": "local_estimate_eur",
    "Box": "has_box",
    "Manual": "has_manual",
    "Insert": "has_insert",
    "Game": "has_game",
    "Notes": "notes",
    "Online Estimate": "online_estimate_eur",
    "Calculation Details": "calculation_details",
    "Region": "region",
}

# Internal key -> French column name (for writing)
INTERNAL_TO_COLUMN_FR: dict[str, str] = {
    "platform": "Plateforme",
    "item_type": "Type",
    "title": "Titre",
    "condition_text": "État",
    "rarity": "Rareté",
    "local_estimate_eur": "Estimation (€)",
    "has_box": "Boîte",
    "has_manual": "Manuel",
    "has_insert": "Cale",
    "has_game": "Jeu",
    "notes": "Remarques",
    "online_estimate_eur": "Estimation Online",
    "calculation_details": "Détail Calcul",
    "region": "Région",
}

# Internal key -> English column name (for writing)
INTERNAL_TO_COLUMN_EN: dict[str, str] = {
    "platform": "Platform",
    "item_type": "Type",
    "title": "Title",
    "condition_text": "Condition",
    "rarity": "Rarity",
    "local_estimate_eur": "Estimate (€)",
    "has_box": "Box",
    "has_manual": "Manual",
    "has_insert": "Insert",
    "has_game": "Game",
    "notes": "Notes",
    "online_estimate_eur": "Online Estimate",
    "calculation_details": "Calculation Details",
    "region": "Region",
}

# Backward compatibility aliases
COLUMN_FR_TO_EN = COLUMN_FR_TO_INTERNAL
COLUMN_EN_TO_FR = INTERNAL_TO_COLUMN_FR

# Output columns by language
OUTPUT_COLUMNS_FR = ["Estimation Online", "Détail Calcul"]
OUTPUT_COLUMNS_EN = ["Online Estimate", "Calculation Details"]


def get_column_to_internal(language: CSVLanguage) -> dict[str, str]:
    """Get column name to internal key mapping for a language."""
    if language == CSVLanguage.FR:
        return COLUMN_FR_TO_INTERNAL
    return COLUMN_EN_TO_INTERNAL


def get_internal_to_column(language: CSVLanguage) -> dict[str, str]:
    """Get internal key to column name mapping for a language."""
    if language == CSVLanguage.FR:
        return INTERNAL_TO_COLUMN_FR
    return INTERNAL_TO_COLUMN_EN


def get_output_columns(language: CSVLanguage) -> list[str]:
    """Get output column names for a language."""
    if language == CSVLanguage.FR:
        return OUTPUT_COLUMNS_FR
    return OUTPUT_COLUMNS_EN


# =============================================================================
# Value Normalization
# =============================================================================

# Yes values (FR/EN) -> normalized
YES_VALUES_FR = {"oui", "o", "vrai"}
YES_VALUES_EN = {"yes", "y", "true"}
YES_VALUES_ALL = YES_VALUES_FR | YES_VALUES_EN | {"1"}

NO_VALUES_FR = {"non", "n", "faux"}
NO_VALUES_EN = {"no", "n", "false"}
NO_VALUES_ALL = NO_VALUES_FR | NO_VALUES_EN | {"0"}


def normalize_boolean(value: Any, language: CSVLanguage = CSVLanguage.EN) -> str | None:
    """
    Normalize boolean-like values to Y/N/None.

    Args:
        value: Input value to normalize
        language: CSV language (affects which values are recognized)

    Returns:
        Y, N, or None
    """
    if value is None:
        return None
    str_val = str(value).strip().lower()
    if not str_val or str_val == "n/a":
        return None

    # Use language-specific values first, then fall back to all
    if language == CSVLanguage.FR:
        yes_vals = YES_VALUES_FR | {"1"}
        no_vals = NO_VALUES_FR | {"0"}
    else:
        yes_vals = YES_VALUES_EN | {"1"}
        no_vals = NO_VALUES_EN | {"0"}

    if str_val in yes_vals:
        return "Y"
    if str_val in no_vals:
        return "N"

    # Fallback to all values for compatibility
    if str_val in YES_VALUES_ALL:
        return "Y"
    if str_val in NO_VALUES_ALL:
        return "N"

    return None


def denormalize_boolean(value: str | None, language: CSVLanguage = CSVLanguage.EN) -> str:
    """
    Convert normalized Y/N back to language-specific format for CSV output.

    Args:
        value: Normalized value (Y/N/None)
        language: Target CSV language

    Returns:
        Language-appropriate string (Oui/Non or Yes/No)
    """
    if value == "Y":
        return "Oui" if language == CSVLanguage.FR else "Yes"
    if value == "N":
        return "Non" if language == CSVLanguage.FR else "No"
    return ""


# =============================================================================
# Enums
# =============================================================================


class Region(str, Enum):
    """Video game region codes."""

    PAL = "PAL"
    NTSC_U = "NTSC-U"  # Also NTSC-U/C
    NTSC_J = "NTSC-J"

    @classmethod
    def from_string(cls, value: str | None) -> "Region":
        """Parse region from string, defaulting to PAL."""
        if not value:
            return cls.PAL
        value = value.strip().upper()
        # Handle various formats
        if value in ("PAL", "EUR", "EUROPE", "EUROPEAN", "UK"):
            return cls.PAL
        if value in ("NTSC-U", "NTSC-U/C", "NTSCU", "NTSC_U", "USA", "US", "NA", "NORTH AMERICA"):
            return cls.NTSC_U
        if value in ("NTSC-J", "NTSCJ", "NTSC_J", "JAP", "JAPAN", "JP", "JAPANESE"):
            return cls.NTSC_J
        # Default fallback
        return cls.PAL


class PackagingState(str, Enum):
    """Game packaging completeness state."""

    CIB = "CIB"  # Complete In Box (game + box + manual)
    LOOSE = "Loose"  # Cartridge/disc only or missing components
    UNKNOWN = "Unknown"


class PriceSource(str, Enum):
    """Price data source."""

    EBAY = "eBay"
    RETROGAMEPRICES = "RetroGamePrices"


class Language(str, Enum):
    """Preferred language for game variants."""

    ANY = "ANY"
    EN = "EN"
    FR = "FR"
    DE = "DE"
    IT = "IT"
    ES = "ES"


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class GameItem:
    """Represents a single game item from the CSV."""

    # Required fields
    platform: str
    title: str

    # Optional fields with defaults
    item_type: str = ""
    condition_text: str = ""
    rarity: str = ""
    local_estimate_eur: Decimal | None = None
    has_box: str | None = None  # Y/N/None
    has_manual: str | None = None  # Y/N/None
    has_insert: str | None = None  # Y/N/None
    has_game: str | None = None  # Y/N/None
    notes: str = ""
    region: Region = Region.PAL

    # Output fields (to be populated)
    online_estimate_eur: Decimal | None = None
    calculation_details: str = ""

    # Original row index for preserving order
    row_index: int = 0

    # Original raw data for preserving unchanged columns
    raw_data: dict[str, Any] = field(default_factory=dict)

    @property
    def packaging_state(self) -> PackagingState:
        """Determine if game is CIB or Loose based on components."""
        if self.has_game != "Y":
            return PackagingState.UNKNOWN
        if self.has_game == "Y" and self.has_box == "Y" and self.has_manual == "Y":
            return PackagingState.CIB
        return PackagingState.LOOSE

    @property
    def is_processable(self) -> bool:
        """Check if this item should be processed (has game)."""
        return self.has_game == "Y"


@dataclass
class SoldListing:
    """Represents a single eBay sold listing."""

    title: str
    price: Decimal
    currency: str
    sold_date: datetime
    condition: str = ""
    url: str = ""
    shipping_cost: Decimal | None = None

    # Price in EUR after conversion
    price_eur: Decimal | None = None
    shipping_eur: Decimal | None = None

    @property
    def total_eur(self) -> Decimal | None:
        """Total price in EUR including shipping if available."""
        if self.price_eur is None:
            return None
        if self.shipping_eur:
            return self.price_eur + self.shipping_eur
        return self.price_eur


@dataclass
class PriceResult:
    """Price result from a single source."""

    source: PriceSource
    price_eur: Decimal | None = None
    details: str = ""
    success: bool = False
    error: str = ""

    # eBay specific
    listings: list[SoldListing] = field(default_factory=list)
    num_results: int = 0
    strategy_used: str = ""  # e.g., "strict", "relaxed_language", "relaxed_packaging"

    # RetroGamePrices specific
    loose_price: Decimal | None = None
    cib_price: Decimal | None = None


@dataclass
class EnrichmentResult:
    """Combined enrichment result for a game item."""

    game: GameItem
    ebay_result: PriceResult | None = None
    rgp_result: PriceResult | None = None
    final_estimate_eur: Decimal | None = None
    calculation_details: str = ""
    success: bool = False


# =============================================================================
# Platform Mappings
# =============================================================================

# Map platform names (possibly French) to standardized English names
PLATFORM_NORMALIZATION: dict[str, str] = {
    # Nintendo
    "nes": "NES",
    "nintendo": "NES",
    "famicom": "Famicom",
    "snes": "SNES",
    "super nintendo": "SNES",
    "super nes": "SNES",
    "super famicom": "Super Famicom",
    "n64": "Nintendo 64",
    "nintendo 64": "Nintendo 64",
    "gamecube": "GameCube",
    "gc": "GameCube",
    "wii": "Wii",
    "wii u": "Wii U",
    "switch": "Nintendo Switch",
    "nintendo switch": "Nintendo Switch",
    "game boy": "Game Boy",
    "gameboy": "Game Boy",
    "gb": "Game Boy",
    "game boy color": "Game Boy Color",
    "gameboy color": "Game Boy Color",
    "gbc": "Game Boy Color",
    "game boy advance": "Game Boy Advance",
    "gameboy advance": "Game Boy Advance",
    "gba": "Game Boy Advance",
    "ds": "Nintendo DS",
    "nintendo ds": "Nintendo DS",
    "3ds": "Nintendo 3DS",
    "nintendo 3ds": "Nintendo 3DS",
    # Sega
    "master system": "Master System",
    "sms": "Master System",
    "mega drive": "Mega Drive",
    "megadrive": "Mega Drive",
    "genesis": "Genesis",
    "sega genesis": "Genesis",
    "saturn": "Sega Saturn",
    "sega saturn": "Sega Saturn",
    "dreamcast": "Dreamcast",
    "sega dreamcast": "Dreamcast",
    "game gear": "Game Gear",
    "gamegear": "Game Gear",
    "gg": "Game Gear",
    # Sony
    "playstation": "PlayStation",
    "ps1": "PlayStation",
    "psx": "PlayStation",
    "ps one": "PlayStation",
    "playstation 2": "PlayStation 2",
    "ps2": "PlayStation 2",
    "playstation 3": "PlayStation 3",
    "ps3": "PlayStation 3",
    "playstation 4": "PlayStation 4",
    "ps4": "PlayStation 4",
    "playstation 5": "PlayStation 5",
    "ps5": "PlayStation 5",
    "psp": "PSP",
    "playstation portable": "PSP",
    "ps vita": "PS Vita",
    "vita": "PS Vita",
    # Microsoft
    "xbox": "Xbox",
    "xbox 360": "Xbox 360",
    "x360": "Xbox 360",
    "xbox one": "Xbox One",
    "xbone": "Xbox One",
    "xbox series x": "Xbox Series X",
    "xbox series s": "Xbox Series S",
    # Other
    "neo geo": "Neo Geo",
    "neogeo": "Neo Geo",
    "neo geo aes": "Neo Geo AES",
    "neo geo cd": "Neo Geo CD",
    "turbografx-16": "TurboGrafx-16",
    "turbografx": "TurboGrafx-16",
    "pc engine": "PC Engine",
    "atari 2600": "Atari 2600",
    "atari": "Atari 2600",
    "atari 7800": "Atari 7800",
    "atari jaguar": "Atari Jaguar",
    "jaguar": "Atari Jaguar",
    "atari lynx": "Atari Lynx",
    "lynx": "Atari Lynx",
    "3do": "3DO",
    "colecovision": "ColecoVision",
    "intellivision": "Intellivision",
}


def normalize_platform(platform: str) -> str:
    """Normalize platform name to standard English form."""
    if not platform:
        return ""
    key = platform.strip().lower()
    return PLATFORM_NORMALIZATION.get(key, platform.strip())


# Platform keywords for eBay search (helps narrow down results)
PLATFORM_EBAY_KEYWORDS: dict[str, list[str]] = {
    "NES": ["NES", "Nintendo Entertainment System"],
    "Famicom": ["Famicom", "FC"],
    "SNES": ["SNES", "Super Nintendo", "Super NES"],
    "Super Famicom": ["Super Famicom", "SFC"],
    "Nintendo 64": ["N64", "Nintendo 64"],
    "GameCube": ["GameCube", "GC", "NGC"],
    "Wii": ["Wii", "Nintendo Wii"],
    "Wii U": ["Wii U"],
    "Nintendo Switch": ["Switch", "Nintendo Switch"],
    "Game Boy": ["Game Boy", "GameBoy", "GB"],
    "Game Boy Color": ["Game Boy Color", "GameBoy Color", "GBC"],
    "Game Boy Advance": ["Game Boy Advance", "GameBoy Advance", "GBA"],
    "Nintendo DS": ["DS", "Nintendo DS", "NDS"],
    "Nintendo 3DS": ["3DS", "Nintendo 3DS"],
    "Master System": ["Master System", "SMS"],
    "Mega Drive": ["Mega Drive", "Megadrive"],
    "Genesis": ["Genesis", "Sega Genesis"],
    "Sega Saturn": ["Saturn", "Sega Saturn"],
    "Dreamcast": ["Dreamcast", "DC"],
    "Game Gear": ["Game Gear", "GameGear", "GG"],
    "PlayStation": ["PlayStation", "PS1", "PSX", "PS One"],
    "PlayStation 2": ["PlayStation 2", "PS2"],
    "PlayStation 3": ["PlayStation 3", "PS3"],
    "PlayStation 4": ["PlayStation 4", "PS4"],
    "PlayStation 5": ["PlayStation 5", "PS5"],
    "PSP": ["PSP", "PlayStation Portable"],
    "PS Vita": ["PS Vita", "Vita", "PlayStation Vita"],
    "Xbox": ["Xbox", "Original Xbox"],
    "Xbox 360": ["Xbox 360", "X360"],
    "Xbox One": ["Xbox One", "Xbone"],
}


# Cartridge vs disc platforms (affects search keywords)
CARTRIDGE_PLATFORMS = {
    "NES",
    "Famicom",
    "SNES",
    "Super Famicom",
    "Nintendo 64",
    "Game Boy",
    "Game Boy Color",
    "Game Boy Advance",
    "Nintendo DS",
    "Nintendo 3DS",
    "Master System",
    "Mega Drive",
    "Genesis",
    "Game Gear",
    "Atari 2600",
    "Atari 7800",
    "Atari Jaguar",
    "Neo Geo AES",
    "TurboGrafx-16",
    "PC Engine",
}

DISC_PLATFORMS = {
    "PlayStation",
    "PlayStation 2",
    "PlayStation 3",
    "PlayStation 4",
    "PlayStation 5",
    "PSP",
    "PS Vita",
    "GameCube",
    "Wii",
    "Wii U",
    "Sega Saturn",
    "Dreamcast",
    "Xbox",
    "Xbox 360",
    "Xbox One",
    "Neo Geo CD",
    "3DO",
}
