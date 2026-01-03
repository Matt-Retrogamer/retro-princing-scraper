"""Tests for price_enricher models."""

import pytest
from decimal import Decimal

from price_enricher.models import (
    CSVLanguage,
    GameItem,
    Region,
    PackagingState,
    Language,
    normalize_boolean,
    denormalize_boolean,
    normalize_platform,
    COLUMN_FR_TO_INTERNAL,
    COLUMN_EN_TO_INTERNAL,
    get_column_to_internal,
    get_internal_to_column,
)


class TestNormalizeBoolean:
    """Tests for boolean normalization."""

    def test_yes_values_french(self):
        """Test French yes values."""
        assert normalize_boolean("Oui", CSVLanguage.FR) == "Y"
        assert normalize_boolean("oui", CSVLanguage.FR) == "Y"
        assert normalize_boolean("OUI", CSVLanguage.FR) == "Y"
        assert normalize_boolean("1", CSVLanguage.FR) == "Y"

    def test_yes_values_english(self):
        """Test English yes values."""
        assert normalize_boolean("Yes", CSVLanguage.EN) == "Y"
        assert normalize_boolean("yes", CSVLanguage.EN) == "Y"
        assert normalize_boolean("Y", CSVLanguage.EN) == "Y"
        assert normalize_boolean("y", CSVLanguage.EN) == "Y"
        assert normalize_boolean("1", CSVLanguage.EN) == "Y"
        assert normalize_boolean("true", CSVLanguage.EN) == "Y"

    def test_no_values_french(self):
        """Test French no values."""
        assert normalize_boolean("Non", CSVLanguage.FR) == "N"
        assert normalize_boolean("non", CSVLanguage.FR) == "N"
        assert normalize_boolean("NON", CSVLanguage.FR) == "N"
        assert normalize_boolean("0", CSVLanguage.FR) == "N"

    def test_no_values_english(self):
        """Test English no values."""
        assert normalize_boolean("No", CSVLanguage.EN) == "N"
        assert normalize_boolean("no", CSVLanguage.EN) == "N"
        assert normalize_boolean("N", CSVLanguage.EN) == "N"
        assert normalize_boolean("n", CSVLanguage.EN) == "N"
        assert normalize_boolean("0", CSVLanguage.EN) == "N"
        assert normalize_boolean("false", CSVLanguage.EN) == "N"

    def test_empty_values(self):
        """Test empty/None values."""
        assert normalize_boolean(None, CSVLanguage.EN) is None
        assert normalize_boolean("", CSVLanguage.FR) is None
        assert normalize_boolean("  ", CSVLanguage.EN) is None

    def test_unknown_values(self):
        """Test unknown values return None."""
        assert normalize_boolean("maybe", CSVLanguage.EN) is None
        assert normalize_boolean("unknown", CSVLanguage.FR) is None


class TestDenormalizeBoolean:
    """Tests for boolean denormalization (back to target language)."""

    def test_yes_french(self):
        """Test Y -> Oui for French."""
        assert denormalize_boolean("Y", CSVLanguage.FR) == "Oui"

    def test_yes_english(self):
        """Test Y -> Yes for English."""
        assert denormalize_boolean("Y", CSVLanguage.EN) == "Yes"

    def test_no_french(self):
        """Test N -> Non for French."""
        assert denormalize_boolean("N", CSVLanguage.FR) == "Non"

    def test_no_english(self):
        """Test N -> No for English."""
        assert denormalize_boolean("N", CSVLanguage.EN) == "No"

    def test_none(self):
        """Test None -> empty string."""
        assert denormalize_boolean(None, CSVLanguage.FR) == ""
        assert denormalize_boolean(None, CSVLanguage.EN) == ""


class TestRegion:
    """Tests for Region enum."""

    def test_from_string_pal(self):
        """Test PAL region parsing."""
        assert Region.from_string("PAL") == Region.PAL
        assert Region.from_string("pal") == Region.PAL
        assert Region.from_string("EUR") == Region.PAL
        assert Region.from_string("Europe") == Region.PAL
        assert Region.from_string("UK") == Region.PAL

    def test_from_string_ntsc_u(self):
        """Test NTSC-U region parsing."""
        assert Region.from_string("NTSC-U") == Region.NTSC_U
        assert Region.from_string("NTSC-U/C") == Region.NTSC_U
        assert Region.from_string("USA") == Region.NTSC_U
        assert Region.from_string("US") == Region.NTSC_U
        assert Region.from_string("NA") == Region.NTSC_U

    def test_from_string_ntsc_j(self):
        """Test NTSC-J region parsing."""
        assert Region.from_string("NTSC-J") == Region.NTSC_J
        assert Region.from_string("Japan") == Region.NTSC_J
        assert Region.from_string("JAP") == Region.NTSC_J
        assert Region.from_string("JP") == Region.NTSC_J

    def test_from_string_default(self):
        """Test default region is PAL."""
        assert Region.from_string(None) == Region.PAL
        assert Region.from_string("") == Region.PAL
        assert Region.from_string("unknown") == Region.PAL


class TestNormalizePlatform:
    """Tests for platform normalization."""

    def test_nintendo_platforms(self):
        """Test Nintendo platform normalization."""
        assert normalize_platform("nes") == "NES"
        assert normalize_platform("NES") == "NES"
        assert normalize_platform("snes") == "SNES"
        assert normalize_platform("Super Nintendo") == "SNES"
        assert normalize_platform("n64") == "Nintendo 64"
        assert normalize_platform("gamecube") == "GameCube"

    def test_sega_platforms(self):
        """Test Sega platform normalization."""
        assert normalize_platform("mega drive") == "Mega Drive"
        assert normalize_platform("genesis") == "Genesis"
        assert normalize_platform("dreamcast") == "Dreamcast"

    def test_playstation_platforms(self):
        """Test PlayStation platform normalization."""
        assert normalize_platform("playstation") == "PlayStation"
        assert normalize_platform("ps1") == "PlayStation"
        assert normalize_platform("ps2") == "PlayStation 2"
        assert normalize_platform("psp") == "PSP"

    def test_unknown_platform(self):
        """Test unknown platform is returned as-is."""
        assert normalize_platform("Unknown Console") == "Unknown Console"
        assert normalize_platform("  Trimmed  ") == "Trimmed"


class TestGameItem:
    """Tests for GameItem dataclass."""

    def test_packaging_state_cib(self):
        """Test CIB packaging detection."""
        item = GameItem(
            platform="SNES",
            title="Super Mario World",
            has_game="Y",
            has_box="Y",
            has_manual="Y",
        )
        assert item.packaging_state == PackagingState.CIB

    def test_packaging_state_loose(self):
        """Test Loose packaging detection."""
        item = GameItem(
            platform="SNES",
            title="Super Mario World",
            has_game="Y",
            has_box="N",
            has_manual="N",
        )
        assert item.packaging_state == PackagingState.LOOSE

    def test_packaging_state_partial(self):
        """Test partial packaging is Loose."""
        item = GameItem(
            platform="SNES",
            title="Super Mario World",
            has_game="Y",
            has_box="Y",
            has_manual="N",
        )
        assert item.packaging_state == PackagingState.LOOSE

    def test_packaging_state_no_game(self):
        """Test no game is Unknown."""
        item = GameItem(
            platform="SNES",
            title="Super Mario World",
            has_game="N",
            has_box="Y",
            has_manual="Y",
        )
        assert item.packaging_state == PackagingState.UNKNOWN

    def test_is_processable(self):
        """Test processable detection."""
        processable = GameItem(platform="SNES", title="Test", has_game="Y")
        not_processable = GameItem(platform="SNES", title="Test", has_game="N")

        assert processable.is_processable is True
        assert not_processable.is_processable is False


class TestColumnMappings:
    """Tests for column mappings."""

    def test_french_to_internal_mapping(self):
        """Test French column names map to internal keys."""
        assert COLUMN_FR_TO_INTERNAL["Plateforme"] == "platform"
        assert COLUMN_FR_TO_INTERNAL["Titre"] == "title"
        assert COLUMN_FR_TO_INTERNAL["Boîte"] == "has_box"
        assert COLUMN_FR_TO_INTERNAL["Manuel"] == "has_manual"
        assert COLUMN_FR_TO_INTERNAL["Estimation Online"] == "online_estimate_eur"
        assert COLUMN_FR_TO_INTERNAL["Détail Calcul"] == "calculation_details"

    def test_english_to_internal_mapping(self):
        """Test English column names map to internal keys."""
        assert COLUMN_EN_TO_INTERNAL["Platform"] == "platform"
        assert COLUMN_EN_TO_INTERNAL["Title"] == "title"
        assert COLUMN_EN_TO_INTERNAL["Box"] == "has_box"
        assert COLUMN_EN_TO_INTERNAL["Manual"] == "has_manual"
        assert COLUMN_EN_TO_INTERNAL["Online Estimate"] == "online_estimate_eur"
        assert COLUMN_EN_TO_INTERNAL["Calculation Details"] == "calculation_details"

    def test_region_column_variants(self):
        """Test region column in both languages."""
        assert COLUMN_FR_TO_INTERNAL["Région"] == "region"
        assert COLUMN_EN_TO_INTERNAL["Region"] == "region"

    def test_get_column_to_internal(self):
        """Test dynamic column mapping retrieval."""
        fr_mapping = get_column_to_internal(CSVLanguage.FR)
        en_mapping = get_column_to_internal(CSVLanguage.EN)

        assert fr_mapping["Plateforme"] == "platform"
        assert en_mapping["Platform"] == "platform"

    def test_get_internal_to_column(self):
        """Test dynamic internal to column mapping retrieval."""
        fr_mapping = get_internal_to_column(CSVLanguage.FR)
        en_mapping = get_internal_to_column(CSVLanguage.EN)

        assert fr_mapping["platform"] == "Plateforme"
        assert en_mapping["platform"] == "Platform"
        assert fr_mapping["online_estimate_eur"] == "Estimation Online"
        assert en_mapping["online_estimate_eur"] == "Online Estimate"
