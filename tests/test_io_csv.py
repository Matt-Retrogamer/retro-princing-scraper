"""Tests for CSV I/O operations."""

import csv
import pytest
from decimal import Decimal
from pathlib import Path
from tempfile import NamedTemporaryFile

from price_enricher.io_csv import (
    read_csv,
    write_csv,
    parse_decimal,
    format_decimal,
    format_decimal_fr,
    detect_csv_encoding,
    detect_csv_delimiter,
    detect_csv_language,
)
from price_enricher.models import CSVLanguage, Region


class TestParseDecimal:
    """Tests for decimal parsing."""

    def test_simple_decimal(self):
        """Test simple decimal parsing."""
        assert parse_decimal("10.50") == Decimal("10.50")
        assert parse_decimal("100") == Decimal("100")

    def test_french_format(self):
        """Test French decimal format (comma separator)."""
        assert parse_decimal("10,50") == Decimal("10.50")
        assert parse_decimal("1234,56") == Decimal("1234.56")

    def test_with_currency_symbols(self):
        """Test with currency symbols."""
        assert parse_decimal("€10.50") == Decimal("10.50")
        assert parse_decimal("$20.00") == Decimal("20.00")
        assert parse_decimal("£15.99") == Decimal("15.99")

    def test_with_spaces(self):
        """Test with thousand separators (spaces)."""
        assert parse_decimal("1 234.56") == Decimal("1234.56")

    def test_empty_values(self):
        """Test empty/None values."""
        assert parse_decimal(None) is None
        assert parse_decimal("") is None
        assert parse_decimal("  ") is None

    def test_invalid_values(self):
        """Test invalid values return None."""
        assert parse_decimal("not a number") is None


class TestFormatDecimalFr:
    """Tests for French decimal formatting."""

    def test_formats_with_comma(self):
        """Test comma as decimal separator."""
        assert format_decimal_fr(Decimal("10.50")) == "10,50"
        assert format_decimal_fr(Decimal("1234.56")) == "1234,56"

    def test_pads_decimals(self):
        """Test decimal padding."""
        assert format_decimal_fr(Decimal("10")) == "10,00"
        assert format_decimal_fr(Decimal("10.5")) == "10,50"

    def test_none_value(self):
        """Test None returns empty string."""
        assert format_decimal_fr(None) == ""


class TestFormatDecimal:
    """Tests for language-aware decimal formatting."""

    def test_formats_french_with_comma(self):
        """Test French format uses comma."""
        assert format_decimal(Decimal("10.50"), CSVLanguage.FR) == "10,50"
        assert format_decimal(Decimal("1234.56"), CSVLanguage.FR) == "1234,56"

    def test_formats_english_with_period(self):
        """Test English format uses period."""
        assert format_decimal(Decimal("10.50"), CSVLanguage.EN) == "10.50"
        assert format_decimal(Decimal("1234.56"), CSVLanguage.EN) == "1234.56"

    def test_default_is_english(self):
        """Test default format is English."""
        assert format_decimal(Decimal("10.50")) == "10.50"

    def test_none_value(self):
        """Test None returns empty string."""
        assert format_decimal(None, CSVLanguage.FR) == ""
        assert format_decimal(None, CSVLanguage.EN) == ""


class TestDetectCSVLanguage:
    """Tests for CSV language detection."""

    def test_detects_french(self):
        """Test French column detection."""
        french_columns = ["Plateforme", "Titre", "Boîte", "Manuel"]
        assert detect_csv_language(french_columns) == CSVLanguage.FR

    def test_detects_english(self):
        """Test English column detection."""
        english_columns = ["Platform", "Title", "Box", "Manual"]
        assert detect_csv_language(english_columns) == CSVLanguage.EN

    def test_defaults_to_english(self):
        """Test unknown columns default to English."""
        unknown_columns = ["Column1", "Column2"]
        assert detect_csv_language(unknown_columns) == CSVLanguage.EN


class TestCSVReadWrite:
    """Tests for CSV read/write operations."""

    @pytest.fixture
    def sample_csv_content_fr(self) -> str:
        """Sample CSV content with French headers."""
        return """Plateforme,Type,Titre,État,Rareté,Estimation (€),Boîte,Manuel,Cale,Jeu,Région,Remarques,Estimation Online,Détail Calcul
SNES,Jeu,Super Mario World,Bon,Commun,25,Oui,Oui,Non,Oui,PAL,Test game,,
Mega Drive,Jeu,Sonic the Hedgehog,Très bon,Commun,15,Non,Non,Non,Oui,NTSC-U,,,
PlayStation,Jeu,Final Fantasy VII,Excellent,Rare,50,Oui,Oui,Oui,Oui,NTSC-J,3 CDs,,
SNES,Boîte,Super Mario World,Bon,Commun,15,Oui,Non,Non,Non,PAL,Box only,,"""

    @pytest.fixture
    def sample_csv_content_en(self) -> str:
        """Sample CSV content with English headers."""
        return """Platform,Type,Title,Condition,Rarity,Estimate (€),Box,Manual,Insert,Game,Region,Notes,Online Estimate,Calculation Details
SNES,Game,Super Mario World,Good,Common,25,Yes,Yes,No,Yes,PAL,Test game,,
Mega Drive,Game,Sonic the Hedgehog,Very good,Common,15,No,No,No,Yes,NTSC-U,,,
PlayStation,Game,Final Fantasy VII,Excellent,Rare,50,Yes,Yes,Yes,Yes,NTSC-J,3 CDs,,
SNES,Box,Super Mario World,Good,Common,15,Yes,No,No,No,PAL,Box only,,"""

    @pytest.fixture
    def sample_csv_content_fr_no_region(self) -> str:
        """Sample CSV content with French headers but no region column."""
        return """Plateforme,Type,Titre,État,Rareté,Estimation (€),Boîte,Manuel,Cale,Jeu,Remarques,Estimation Online,Détail Calcul
SNES,Jeu,Super Mario World,Bon,Commun,25,Oui,Oui,Non,Oui,Test game,,
Mega Drive,Jeu,Sonic the Hedgehog,Très bon,Commun,15,Non,Non,Non,Oui,,,"""

    @pytest.fixture
    def csv_file_fr(self, sample_csv_content_fr: str, tmp_path: Path) -> Path:
        """Create a temporary French CSV file."""
        csv_path = tmp_path / "test_fr.csv"
        csv_path.write_text(sample_csv_content_fr, encoding="utf-8")
        return csv_path

    @pytest.fixture
    def csv_file_en(self, sample_csv_content_en: str, tmp_path: Path) -> Path:
        """Create a temporary English CSV file."""
        csv_path = tmp_path / "test_en.csv"
        csv_path.write_text(sample_csv_content_en, encoding="utf-8")
        return csv_path

    @pytest.fixture
    def csv_file_fr_no_region(self, sample_csv_content_fr_no_region: str, tmp_path: Path) -> Path:
        """Create a temporary French CSV file without region column."""
        csv_path = tmp_path / "test_fr_no_region.csv"
        csv_path.write_text(sample_csv_content_fr_no_region, encoding="utf-8")
        return csv_path

    # Legacy fixture for backward compatibility
    @pytest.fixture
    def csv_file(self, sample_csv_content_fr: str, tmp_path: Path) -> Path:
        """Create a temporary CSV file (French by default)."""
        csv_path = tmp_path / "test.csv"
        csv_path.write_text(sample_csv_content_fr, encoding="utf-8")
        return csv_path

    def test_read_csv_french(self, csv_file_fr: Path):
        """Test reading French CSV file."""
        items, columns, encoding, delimiter, language = read_csv(csv_file_fr)

        assert len(items) == 4
        assert "Plateforme" in columns
        assert "Titre" in columns
        assert "Région" in columns
        assert language == CSVLanguage.FR

    def test_read_csv_english(self, csv_file_en: Path):
        """Test reading English CSV file."""
        items, columns, encoding, delimiter, language = read_csv(csv_file_en)

        assert len(items) == 4
        assert "Platform" in columns
        assert "Title" in columns
        assert "Region" in columns
        assert language == CSVLanguage.EN

    def test_read_csv_explicit_language(self, csv_file_fr: Path):
        """Test reading with explicit language override."""
        items, columns, encoding, delimiter, language = read_csv(csv_file_fr, language=CSVLanguage.FR)

        assert language == CSVLanguage.FR

    def test_read_csv_normalizes_platform(self, csv_file_fr: Path):
        """Test platform normalization on read."""
        items, _, _, _, _ = read_csv(csv_file_fr)

        assert items[0].platform == "SNES"
        assert items[1].platform == "Mega Drive"
        assert items[2].platform == "PlayStation"

    def test_read_csv_normalizes_boolean_french(self, csv_file_fr: Path):
        """Test boolean normalization on read for French CSV."""
        items, _, _, _, _ = read_csv(csv_file_fr)

        # First item has Oui for box and manual
        assert items[0].has_box == "Y"
        assert items[0].has_manual == "Y"
        assert items[0].has_game == "Y"

        # Second item has Non for box and manual
        assert items[1].has_box == "N"
        assert items[1].has_manual == "N"

    def test_read_csv_normalizes_boolean_english(self, csv_file_en: Path):
        """Test boolean normalization on read for English CSV."""
        items, _, _, _, _ = read_csv(csv_file_en)

        # First item has Yes for box and manual
        assert items[0].has_box == "Y"
        assert items[0].has_manual == "Y"
        assert items[0].has_game == "Y"

        # Second item has No for box and manual
        assert items[1].has_box == "N"
        assert items[1].has_manual == "N"

    def test_read_csv_parses_decimals(self, csv_file_fr: Path):
        """Test decimal parsing on read."""
        items, _, _, _, _ = read_csv(csv_file_fr)

        assert items[0].local_estimate_eur == Decimal("25")
        assert items[2].local_estimate_eur == Decimal("50")

    def test_read_csv_default_region(self, csv_file_fr_no_region: Path):
        """Test default region is applied when no region column exists."""
        items, _, _, _, _ = read_csv(csv_file_fr_no_region, default_region=Region.PAL)

        for item in items:
            assert item.region == Region.PAL

    def test_read_csv_parses_region_column(self, csv_file_fr: Path):
        """Test region column is parsed correctly."""
        items, _, _, _, _ = read_csv(csv_file_fr)

        assert items[0].region == Region.PAL
        assert items[1].region == Region.NTSC_U
        assert items[2].region == Region.NTSC_J
        assert items[3].region == Region.PAL  # Box only item

    def test_read_csv_parses_region_column_english(self, csv_file_en: Path):
        """Test region column is parsed correctly for English CSV."""
        items, _, _, _, _ = read_csv(csv_file_en)

        assert items[0].region == Region.PAL
        assert items[1].region == Region.NTSC_U
        assert items[2].region == Region.NTSC_J

    def test_read_csv_accessory_only_items(self, csv_file_fr: Path):
        """Test accessory-only items are read correctly."""
        items, _, _, _, _ = read_csv(csv_file_fr)

        # Last item is box only (no game)
        box_only_item = items[3]
        assert box_only_item.has_game == "N"
        assert box_only_item.has_box == "Y"
        assert box_only_item.has_manual == "N"
        assert box_only_item.is_processable  # Should be processable
        assert box_only_item.is_accessory_only  # Should be accessory only
        assert not box_only_item.is_game_item  # Should not be a game item

    def test_read_csv_preserves_row_index(self, csv_file_fr: Path):
        """Test row index is preserved."""
        items, _, _, _, _ = read_csv(csv_file_fr)

        assert items[0].row_index == 0
        assert items[1].row_index == 1
        assert items[2].row_index == 2
        assert items[3].row_index == 3

    def test_write_csv_preserves_structure_french(self, csv_file_fr: Path, tmp_path: Path):
        """Test writing preserves original structure for French CSV."""
        items, columns, encoding, delimiter, language = read_csv(csv_file_fr)

        # Modify some items
        items[0].online_estimate_eur = Decimal("30.50")
        items[0].calculation_details = "Test details"

        output_path = tmp_path / "output.csv"
        write_csv(output_path, items, columns, encoding, delimiter, language=language)

        # Read back and verify
        with open(output_path, "r", encoding=encoding) as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            rows = list(reader)

        assert len(rows) == 4
        assert rows[0]["Estimation Online"] == "30,50"
        assert rows[0]["Détail Calcul"] == "Test details"

    def test_write_csv_preserves_structure_english(self, csv_file_en: Path, tmp_path: Path):
        """Test writing preserves original structure for English CSV."""
        items, columns, encoding, delimiter, language = read_csv(csv_file_en)

        # Modify some items
        items[0].online_estimate_eur = Decimal("30.50")
        items[0].calculation_details = "Test details"

        output_path = tmp_path / "output.csv"
        write_csv(output_path, items, columns, encoding, delimiter, language=language)

        # Read back and verify
        with open(output_path, "r", encoding=encoding) as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            rows = list(reader)

        assert len(rows) == 4
        assert rows[0]["Online Estimate"] == "30.50"  # English uses period
        assert rows[0]["Calculation Details"] == "Test details"

    def test_write_csv_add_region_column_french(self, csv_file_fr_no_region: Path, tmp_path: Path):
        """Test adding region column for French CSV when it doesn't exist."""
        items, columns, encoding, delimiter, language = read_csv(csv_file_fr_no_region)

        output_path = tmp_path / "output.csv"
        write_csv(output_path, items, columns, encoding, delimiter, add_region_column=True, language=language)

        # Read back and verify region column exists
        with open(output_path, "r", encoding=encoding) as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            rows = list(reader)

        assert "Région" in rows[0]
        assert rows[0]["Région"] == "PAL"

    def test_write_csv_add_region_column_english(self, csv_file_en: Path, tmp_path: Path):
        """Test adding region column for English CSV."""
        items, columns, encoding, delimiter, language = read_csv(csv_file_en)

        output_path = tmp_path / "output.csv"
        write_csv(output_path, items, columns, encoding, delimiter, add_region_column=True, language=language)

        # Read back and verify region column exists
        with open(output_path, "r", encoding=encoding) as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            rows = list(reader)

        assert "Region" in rows[0]
        assert rows[0]["Region"] == "PAL"


class TestCSVDetection:
    """Tests for CSV format detection."""

    def test_detect_utf8_encoding(self, tmp_path: Path):
        """Test UTF-8 encoding detection."""
        csv_path = tmp_path / "utf8.csv"
        csv_path.write_text("Col1,Col2\nÉté,été", encoding="utf-8")

        encoding = detect_csv_encoding(csv_path)
        assert encoding in ("utf-8", "utf-8-sig")

    def test_detect_comma_delimiter(self, tmp_path: Path):
        """Test comma delimiter detection."""
        csv_path = tmp_path / "comma.csv"
        csv_path.write_text("A,B,C\n1,2,3", encoding="utf-8")

        delimiter = detect_csv_delimiter(csv_path, "utf-8")
        assert delimiter == ","

    def test_detect_semicolon_delimiter(self, tmp_path: Path):
        """Test semicolon delimiter detection."""
        csv_path = tmp_path / "semi.csv"
        csv_path.write_text("A;B;C\n1;2;3", encoding="utf-8")

        delimiter = detect_csv_delimiter(csv_path, "utf-8")
        assert delimiter == ";"
