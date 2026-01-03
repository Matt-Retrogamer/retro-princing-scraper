"""CSV reading and writing with multi-language header support."""

import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd

from price_enricher.models import (
    CSVLanguage,
    GameItem,
    Region,
    denormalize_boolean,
    get_column_to_internal,
    get_internal_to_column,
    get_output_columns,
    normalize_boolean,
    normalize_platform,
    COLUMN_FR_TO_INTERNAL,
    COLUMN_EN_TO_INTERNAL,
)


def detect_csv_encoding(file_path: Path) -> str:
    """Detect CSV file encoding by trying common encodings."""
    encodings = ["utf-8", "utf-8-sig", "latin-1", "cp1252", "iso-8859-1"]

    for encoding in encodings:
        try:
            with open(file_path, "r", encoding=encoding) as f:
                f.read(1024)  # Try to read first 1KB
            return encoding
        except (UnicodeDecodeError, UnicodeError):
            continue

    return "utf-8"  # Default fallback


def detect_csv_delimiter(file_path: Path, encoding: str) -> str:
    """Detect CSV delimiter by analyzing first few lines."""
    with open(file_path, "r", encoding=encoding) as f:
        sample = f.read(4096)

    # Use csv.Sniffer to detect
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        return dialect.delimiter
    except csv.Error:
        # Count occurrences of common delimiters in first line
        first_line = sample.split("\n")[0]
        delimiters = {",": first_line.count(","), ";": first_line.count(";"), "\t": first_line.count("\t")}
        return max(delimiters, key=delimiters.get)


def detect_csv_language(columns: list[str]) -> CSVLanguage:
    """
    Auto-detect CSV language from column headers.

    Args:
        columns: List of column names from CSV header

    Returns:
        Detected language (defaults to EN if uncertain)
    """
    # Count matches for each language
    fr_matches = sum(1 for col in columns if col in COLUMN_FR_TO_INTERNAL)
    en_matches = sum(1 for col in columns if col in COLUMN_EN_TO_INTERNAL)

    # French-specific columns that don't exist in English
    fr_specific = {"Plateforme", "Titre", "Boîte", "Remarques", "Région", "État", "Rareté"}
    has_fr_specific = any(col in fr_specific for col in columns)

    if has_fr_specific or fr_matches > en_matches:
        return CSVLanguage.FR

    return CSVLanguage.EN


def parse_decimal(value: Any) -> Decimal | None:
    """Parse a value to Decimal, handling various formats."""
    if value is None or pd.isna(value):
        return None

    str_val = str(value).strip()
    if not str_val:
        return None

    # Remove currency symbols and whitespace
    str_val = str_val.replace("€", "").replace("$", "").replace("£", "").strip()

    # Handle French decimal format (comma as decimal separator)
    # But be careful: "1,234.56" uses comma as thousand separator
    if "," in str_val and "." not in str_val:
        # French format: "12,50" -> "12.50"
        str_val = str_val.replace(",", ".")
    elif "," in str_val and "." in str_val:
        # Mixed: "1,234.56" -> "1234.56"
        str_val = str_val.replace(",", "")

    # Remove thousand separators (spaces)
    str_val = str_val.replace(" ", "")

    try:
        return Decimal(str_val)
    except InvalidOperation:
        return None


def format_decimal(value: Decimal | None, language: CSVLanguage = CSVLanguage.EN) -> str:
    """
    Format Decimal for CSV output based on language.

    Args:
        value: Decimal value to format
        language: Target language (FR uses comma, EN uses period)

    Returns:
        Formatted string
    """
    if value is None:
        return ""
    formatted = f"{value:.2f}"
    if language == CSVLanguage.FR:
        return formatted.replace(".", ",")
    return formatted


# Backward compatibility alias
def format_decimal_fr(value: Decimal | None) -> str:
    """Format Decimal for French CSV output."""
    return format_decimal(value, CSVLanguage.FR)


def read_csv(
    file_path: Path,
    default_region: Region = Region.PAL,
    include_non_game: bool = False,
    language: CSVLanguage | None = None,
) -> tuple[list[GameItem], list[str], str, str, CSVLanguage]:
    """
    Read CSV file and convert to list of GameItem objects.

    Args:
        file_path: Path to CSV file
        default_region: Default region for items without region column
        include_non_game: Whether to include items without games
        language: CSV language (auto-detected if None)

    Returns:
        Tuple of:
        - List of GameItem objects
        - List of original column names (preserves order)
        - Detected encoding
        - Detected delimiter
        - Detected/specified language
    """
    # Detect encoding and delimiter
    encoding = detect_csv_encoding(file_path)
    delimiter = detect_csv_delimiter(file_path, encoding)

    # Read CSV with pandas
    df = pd.read_csv(
        file_path,
        encoding=encoding,
        delimiter=delimiter,
        dtype=str,  # Read all as strings initially
        keep_default_na=False,
    )

    # Store original column names
    original_columns = list(df.columns)

    # Auto-detect or use specified language
    detected_language = language if language else detect_csv_language(original_columns)

    # Get column mapping for detected language
    column_mapping = get_column_to_internal(detected_language)

    # Convert each row to GameItem
    items: list[GameItem] = []

    for idx, row in df.iterrows():
        raw_data = row.to_dict()

        # Map columns to internal keys
        mapped = {}
        for col_name, internal_key in column_mapping.items():
            if col_name in raw_data:
                mapped[internal_key] = raw_data[col_name]

        # Determine region
        region_str = mapped.get("region", "")
        region = Region.from_string(region_str) if region_str else default_region

        # Parse fields with language-aware normalization
        has_game = normalize_boolean(mapped.get("has_game"), detected_language)

        # Skip non-game items unless requested
        if not include_non_game and has_game != "Y":
            # Still create item but mark as not processable
            pass

        item = GameItem(
            platform=normalize_platform(mapped.get("platform", "")),
            title=mapped.get("title", ""),
            item_type=mapped.get("item_type", ""),
            condition_text=mapped.get("condition_text", ""),
            rarity=mapped.get("rarity", ""),
            local_estimate_eur=parse_decimal(mapped.get("local_estimate_eur")),
            has_box=normalize_boolean(mapped.get("has_box"), detected_language),
            has_manual=normalize_boolean(mapped.get("has_manual"), detected_language),
            has_insert=normalize_boolean(mapped.get("has_insert"), detected_language),
            has_game=has_game,
            notes=mapped.get("notes", ""),
            region=region,
            online_estimate_eur=parse_decimal(mapped.get("online_estimate_eur")),
            calculation_details=mapped.get("calculation_details", ""),
            row_index=int(idx),  # type: ignore
            raw_data=raw_data,
        )

        items.append(item)

    return items, original_columns, encoding, delimiter, detected_language


def write_csv(
    file_path: Path,
    items: list[GameItem],
    original_columns: list[str],
    encoding: str = "utf-8",
    delimiter: str = ",",
    add_region_column: bool = False,
    language: CSVLanguage = CSVLanguage.EN,
) -> None:
    """
    Write enriched GameItems back to CSV.

    Only updates the output columns (Online Estimate, Calculation Details).
    Preserves original structure, order, and unchanged values.

    Args:
        file_path: Output file path
        items: List of GameItem objects to write
        original_columns: Original column names to preserve
        encoding: File encoding
        delimiter: CSV delimiter
        add_region_column: Whether to add region column if missing
        language: Output language for column names and values
    """
    # Get language-specific mappings
    internal_to_column = get_internal_to_column(language)
    output_cols = get_output_columns(language)

    # Get region column name for this language
    region_col = internal_to_column["region"]

    # Check if region column exists in original (check both languages)
    has_region_column = any(col in ("Région", "Region") for col in original_columns)

    # Prepare output columns
    output_columns = list(original_columns)
    if add_region_column and not has_region_column:
        # Find position to insert (after notes column if exists)
        notes_col = internal_to_column["notes"]
        if notes_col in output_columns:
            insert_idx = output_columns.index(notes_col) + 1
        else:
            insert_idx = len(output_columns)
        output_columns.insert(insert_idx, region_col)

    # Ensure output columns exist
    for out_col in output_cols:
        if out_col not in output_columns:
            output_columns.append(out_col)

    # Get output column names
    online_estimate_col = internal_to_column["online_estimate_eur"]
    calc_details_col = internal_to_column["calculation_details"]

    # Build output rows
    rows: list[dict[str, str]] = []

    for item in items:
        # Start with original raw data
        row = dict(item.raw_data)

        # Update output columns (try both original column name and language-specific name)
        if item.online_estimate_eur is not None:
            # Write to the column that exists in original, or use language-specific name
            if online_estimate_col in row or online_estimate_col in output_columns:
                row[online_estimate_col] = format_decimal(item.online_estimate_eur, language)
            # Also try alternative column names
            for alt_col in ["Estimation Online", "Online Estimate"]:
                if alt_col in row or alt_col in output_columns:
                    row[alt_col] = format_decimal(item.online_estimate_eur, language)

        if item.calculation_details:
            if calc_details_col in row or calc_details_col in output_columns:
                row[calc_details_col] = item.calculation_details
            for alt_col in ["Détail Calcul", "Calculation Details"]:
                if alt_col in row or alt_col in output_columns:
                    row[alt_col] = item.calculation_details

        # Add region column if requested
        if add_region_column and not has_region_column:
            row[region_col] = item.region.value

        rows.append(row)

    # Write CSV
    with open(file_path, "w", encoding=encoding, newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=output_columns,
            delimiter=delimiter,
            extrasaction="ignore",  # Ignore extra fields not in fieldnames
        )
        writer.writeheader()
        writer.writerows(rows)


def preview_csv(file_path: Path, n_rows: int = 5) -> str:
    """Preview first N rows of CSV for debugging."""
    encoding = detect_csv_encoding(file_path)
    delimiter = detect_csv_delimiter(file_path, encoding)

    df = pd.read_csv(
        file_path,
        encoding=encoding,
        delimiter=delimiter,
        dtype=str,
        nrows=n_rows,
        keep_default_na=False,
    )

    return df.to_string()
