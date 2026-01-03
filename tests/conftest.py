"""Pytest configuration and fixtures."""

import pytest
from pathlib import Path


@pytest.fixture
def fixtures_dir() -> Path:
    """Path to test fixtures directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_csv_path(fixtures_dir: Path, tmp_path: Path) -> Path:
    """Create a sample CSV for testing."""
    content = """Plateforme,Type,Titre,État,Rareté,Estimation (€),Boîte,Manuel,Cale,Jeu,Remarques,Estimation Online,Détail Calcul
SNES,Jeu,Super Mario World,Bon,Commun,25,Oui,Oui,Non,Oui,PAL version,,
Mega Drive,Jeu,Sonic the Hedgehog,Très bon,Commun,15,Non,Non,Non,Oui,,,
PlayStation,Jeu,Final Fantasy VII,Excellent,Rare,50,Oui,Oui,Oui,Oui,3 CDs PAL,,
Nintendo 64,Jeu,GoldenEye 007,Bon,Commun,20,Oui,Non,Non,Oui,,,
GameCube,Jeu,The Legend of Zelda: Wind Waker,Excellent,Peu commun,40,Oui,Oui,Oui,Oui,PAL FR,,"""

    csv_path = tmp_path / "test_collection.csv"
    csv_path.write_text(content, encoding="utf-8")
    return csv_path
