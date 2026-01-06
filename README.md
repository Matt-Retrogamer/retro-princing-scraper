# Price Enricher

Enrich video game collection CSV with online price estimates from eBay (sold listings) and PriceCharting.com.

## Features

- **eBay Integration**: Queries eBay Finding API for completed/sold listings with strict region filtering (requires API key)
- **PriceCharting**: Scrapes loose and CIB prices from PriceCharting.com (works without API key)
- **Multi-Language CSV Support**: Works with both English and French CSV files (auto-detected or specified)
- **Region-Aware**: Mandatory region filtering for accurate pricing (PAL, NTSC-U, NTSC-J)
- **Language Preference**: Optional language filtering for regional variants
- **Smart Caching**: SQLite-based caching to minimize API calls
- **Currency Conversion**: Automatic conversion to EUR with fallback rates

## Quick Start

### 1. Install Dependencies

First, install [uv](https://docs.astral.sh/uv/) if you don't have it:

```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Or with Homebrew
brew install uv

# Or with pip
pip install uv
```

Then install project dependencies:

```bash
# With task runner (optional)
brew install go-task
task setup

# Or directly with uv
uv sync --all-extras
```

### 2. Configure eBay API (Optional)

The tool works without eBay by using PriceCharting.com as the price source.

To enable eBay pricing (recommended for more accurate PAL region prices), get your eBay App ID from [eBay Developer Program](https://developer.ebay.com/).

```bash
export EBAY_APP_ID="your-app-id-here"
```

**Note:** If `EBAY_APP_ID` is not set, the tool will automatically use PriceCharting.com only.

### 3. Run Enrichment

```bash
# Basic usage (uses PriceCharting if no eBay API key)
uv run python -m price_enricher \
  --input "input_files/sample_collection_en.csv" \
  --output "output_files/enriched_collection.csv" \
  --default-region PAL

# With verbose output to see what's happening
uv run python -m price_enricher \
  --input "input_files/sample_collection_en.csv" \
  --output "output_files/enriched_collection.csv" \
  --default-region PAL \
  --verbose

# With debug output for troubleshooting
uv run python -m price_enricher \
  --input "input_files/sample_collection_en.csv" \
  --output "output_files/enriched_collection.csv" \
  --default-region PAL \
  --debug

# Using the French sample file
uv run python -m price_enricher \
  --input "input_files/sample_collection_fr.csv" \
  --output "output_files/enriched_collection.csv" \
  --default-region PAL

# Or with explicit language specification
uv run python -m price_enricher \
  --input "input_files/sample_collection_fr.csv" \
  --output "output_files/enriched_collection.csv" \
  --default-region PAL \
  --csv-language FR

# With limit for testing
uv run python -m price_enricher \
  --input "input_files/sample_collection_en.csv" \
  --output "output_files/enriched_collection.csv" \
  --default-region PAL \
  --limit 3
```

## CSV Format

Two sample collection files are included:
- `input_files/sample_collection_en.csv` - English headers (Yes/No values)
- `input_files/sample_collection_fr.csv` - French headers (Oui/Non values)

### Sample English CSV

```csv
Platform,Type,Title,Condition,Rarity,Estimate (€),Box,Manual,Insert,Game,Notes,Online Estimate,Calculation Details
SNES,Game,Super Mario World,Very good,Common,25,Yes,Yes,No,Yes,PAL version,,
Mega Drive,Game,Sonic the Hedgehog,Very good,Very common,15,No,No,No,Yes,Loose cartridge,,
PlayStation,Game,Final Fantasy VII,Excellent,Rare,60,Yes,Yes,Yes,Yes,3 CDs complete,,
...
```

### Sample French CSV

```csv
Plateforme,Type,Titre,État,Rareté,Estimation (€),Boîte,Manuel,Cale,Jeu,Remarques,Estimation Online,Détail Calcul
SNES,Jeu,Super Mario World,Très bon,Commune,25,Oui,Oui,Non,Oui,PAL version,,
Mega Drive,Jeu,Sonic the Hedgehog,Très bon,Très commune,15,Non,Non,Non,Oui,Loose cartridge,,
PlayStation,Jeu,Final Fantasy VII,Excellent,Rare,60,Oui,Oui,Oui,Oui,3 CDs complets,,
...
```

### Column Mapping

| English | French | Internal Key | Description |
|---------|--------|--------------|-------------|
| Platform | Plateforme | platform | Gaming platform (e.g., "SNES", "PlayStation") |
| Type | Type | item_type | Item type |
| Title | Titre | title | Game title |
| Condition | État | condition_text | Condition description |
| Rarity | Rareté | rarity | Rarity rating |
| Estimate (€) | Estimation (€) | local_estimate_eur | Your local estimate in EUR |
| Box | Boîte | has_box | Has box? (Yes/No or Oui/Non) |
| Manual | Manuel | has_manual | Has manual? (Yes/No or Oui/Non) |
| Insert | Cale | has_insert | Has insert/tray? (Yes/No or Oui/Non) |
| Game | Jeu | has_game | Has game? (Yes/No or Oui/Non) |
| Notes | Remarques | notes | Notes |
| Online Estimate | Estimation Online | online_estimate_eur | **OUTPUT**: Online price estimate |
| Calculation Details | Détail Calcul | calculation_details | **OUTPUT**: Calculation breakdown |

### Optional Region Column

If your CSV has a `Region` (EN) or `Région` (FR) column, it will be used. Otherwise, use `--default-region`.

## CLI Options

```bash
uv run python -m price_enricher --help
```

### Core Options

| Option | Default | Description |
|--------|---------|-------------|
| `--input`, `-i` | Required | Input CSV file |
| `--output`, `-o` | Required | Output CSV file |
| `--limit`, `-n` | None | Limit items to process |
| `--sleep`, `-s` | 1.5 | Delay between API requests |
| `--verbose`, `-v` | false | Show detailed progress |
| `--debug`, `-d` | false | Enable debug logging |

### Source Options

| Option | Default | Description |
|--------|---------|-------------|
| `--only-source` | both | Use `ebay`, `rgp`, or `both` (falls back to `rgp` if eBay unavailable) |
| `--weight-ebay` | 0.7 | eBay weight in combined estimate |
| `--weight-rgp` | 0.3 | PriceCharting weight in combined estimate |

**Note:** If `EBAY_APP_ID` is not set and `--only-source` is `both`, the tool automatically falls back to PriceCharting only.

### Region Options

| Option | Default | Description |
|--------|---------|-------------|
| `--default-region`, `-r` | PAL | Default region: PAL, NTSC-U, NTSC-J |
| `--region-relaxed` | false | Allow items without explicit region |
| `--add-region-column` | false | Add Region/Région column to output |

### CSV Language Options

| Option | Default | Description |
|--------|---------|-------------|
| `--csv-language` | auto | CSV file language: `EN`, `FR`, or `auto` (auto-detect) |

The CSV language determines:
- Column header recognition (Platform vs Plateforme, etc.)
- Boolean values (Yes/No vs Oui/Non)
- Decimal format in output (period vs comma)

### Search Language Options

| Option | Default | Description |
|--------|---------|-------------|
| `--preferred-language`, `-l` | ANY | Search filter: ANY, EN, FR, DE, IT, ES |
| `--strict-language` | false | Strictly filter search results by language |

### eBay Options

| Option | Default | Description |
|--------|---------|-------------|
| `--include-shipping` | false | Include shipping in prices |
| `--allow-lots` | false | Allow lot/bundle listings |
| `--allow-box-only` | false | Allow box/manual only listings |

### Cache Options

| Option | Default | Description |
|--------|---------|-------------|
| `--cache` | cache.sqlite | Cache database path |
| `--clear-cache` | false | Clear cache before running |

## Cache Management

```bash
# View cache statistics
uv run python -m price_enricher cache-stats

# Clear all cache
uv run python -m price_enricher clear-cache

# Clear specific namespace
uv run python -m price_enricher clear-cache --namespace ebay
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `EBAY_APP_ID` | No* | eBay Application ID for sold listings data |

\* The tool works without `EBAY_APP_ID` by using PriceCharting.com as the price source. Set this variable to enable eBay pricing for more accurate regional prices.

## Packaging State Logic

The tool determines if a game is **CIB** (Complete In Box) or **Loose**:

- **CIB**: `has_game=Y` AND `has_box=Y` AND `has_manual=Y`
- **Loose**: `has_game=Y` but missing box or manual
- **Skipped**: `has_game≠Y` (unless `--include-non-game`)

## Region Filtering

Region filtering is **mandatory** for eBay queries to ensure accurate pricing.

### PAL Region
- Includes: PAL, EUR, European, UK
- Excludes: NTSC-U, NTSC-J, USA, Japan

### NTSC-U Region
- Includes: NTSC, USA, US, North America
- Excludes: PAL, Japan

### NTSC-J Region  
- Includes: NTSC-J, Japan, Japanese, JAP
- Excludes: PAL, USA

### Fallback Strategies

If strict filtering returns <5 results, the tool automatically:
1. Relaxes language filtering
2. Relaxes packaging keywords (removes CIB/loose constraints)
3. **Never** relaxes region constraints

## Project Structure

```
price_enricher/
├── __init__.py
├── __main__.py      # CLI entry point
├── cache.py         # SQLite caching
├── fx.py            # Currency conversion
├── io_csv.py        # CSV reading/writing
├── models.py        # Data models & mappings
├── pricing.py       # Pricing orchestrator
├── utils.py         # Utility functions
└── sources/
    ├── __init__.py
    ├── ebay.py      # eBay Finding API
    └── rgp.py       # RetroGamePrices scraper
```

## Development

```bash
# Install with dev dependencies
task install

# Run tests
task test

# Run with coverage
task test-coverage

# Format code
task format

# Run linters
task lint
```

## Rate Limiting & Caching

The tool is designed to be respectful of external services:

- **Rate Limiting**: Configurable delay between requests (default 1.5s for eBay, 2.0s for RGP)
- **Caching**: 
  - eBay results: 3 days TTL
  - RGP results: 7 days TTL
  - FX rates: 24 hours TTL
- **Retry Logic**: Automatic retry with exponential backoff for transient failures

## Example Output

### Calculation Details Field

```
### Super Mario World (SNES) ###
Packaging: CIB
Region: PAL

--- eBay ---
eBay (region=PAL, avg=45.00 EUR, n=5, shipping=excluded, strategy=strict):
[2026-01-01] 42.50 EUR "Super Mario World SNES PAL Complete" (Good) url=...
[2025-12-30] 48.00 EUR "Super Mario World PAL CIB" (Very Good) url=...
...

--- RetroGamePrices ---
RetroGamePrices: Super Mario World - SNES
  Loose: 18.50 EUR
  CIB: 45.00 EUR
  Note: Region filtering not supported on RGP
  URL: https://www.retrogameprices.com/...

--- Final Estimate ---
Weighted average (eBay 70% / RGP 30%)
eBay: 45.00 EUR | RGP: 45.00 EUR
Final: 45.00 EUR
```

## License

MIT
