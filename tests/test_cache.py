"""Tests for caching functionality."""

import pytest
import time
from pathlib import Path

from price_enricher.cache import PriceCache, build_cache_key


class TestPriceCache:
    """Tests for PriceCache class."""

    @pytest.fixture
    def cache(self, tmp_path: Path) -> PriceCache:
        """Create a temporary cache for testing."""
        return PriceCache(tmp_path / "test_cache.sqlite")

    def test_set_and_get(self, cache: PriceCache):
        """Test basic set and get."""
        cache.set("test", "key1", {"value": 123})
        result = cache.get("test", "key1")

        assert result == {"value": 123}

    def test_get_nonexistent(self, cache: PriceCache):
        """Test getting non-existent key."""
        result = cache.get("test", "nonexistent")
        assert result is None

    def test_ttl_expiration(self, cache: PriceCache):
        """Test TTL expiration."""
        # Set with very short TTL
        cache.set("test", "expiring", {"value": 1}, ttl_hours=0.0001)

        # Wait for expiration (about 0.36 seconds)
        time.sleep(0.5)

        result = cache.get("test", "expiring")
        assert result is None

    def test_namespace_isolation(self, cache: PriceCache):
        """Test namespace isolation."""
        cache.set("ns1", "key", {"value": 1})
        cache.set("ns2", "key", {"value": 2})

        assert cache.get("ns1", "key") == {"value": 1}
        assert cache.get("ns2", "key") == {"value": 2}

    def test_clear_namespace(self, cache: PriceCache):
        """Test clearing a namespace."""
        cache.set("ns1", "key1", {"value": 1})
        cache.set("ns1", "key2", {"value": 2})
        cache.set("ns2", "key1", {"value": 3})

        cache.clear_namespace("ns1")

        assert cache.get("ns1", "key1") is None
        assert cache.get("ns1", "key2") is None
        assert cache.get("ns2", "key1") == {"value": 3}

    def test_clear_all(self, cache: PriceCache):
        """Test clearing all entries."""
        cache.set("ns1", "key1", {"value": 1})
        cache.set("ns2", "key2", {"value": 2})

        cache.clear_all()

        assert cache.get("ns1", "key1") is None
        assert cache.get("ns2", "key2") is None

    def test_delete(self, cache: PriceCache):
        """Test deleting specific entry."""
        cache.set("test", "key1", {"value": 1})
        cache.set("test", "key2", {"value": 2})

        deleted = cache.delete("test", "key1")

        assert deleted is True
        assert cache.get("test", "key1") is None
        assert cache.get("test", "key2") == {"value": 2}

    def test_get_stats(self, cache: PriceCache):
        """Test cache statistics."""
        cache.set("ebay", "key1", {"value": 1})
        cache.set("ebay", "key2", {"value": 2})
        cache.set("rgp", "key1", {"value": 3})

        # Access some entries
        cache.get("ebay", "key1")
        cache.get("ebay", "key1")

        stats = cache.get_stats()

        assert "namespaces" in stats
        assert "ebay" in stats["namespaces"]
        assert stats["namespaces"]["ebay"]["count"] == 2
        assert stats["namespaces"]["ebay"]["hits"] >= 2

    def test_cleanup_expired(self, cache: PriceCache):
        """Test cleanup of expired entries."""
        cache.set("test", "valid", {"value": 1}, ttl_hours=1.0)
        cache.set("test", "expired", {"value": 2}, ttl_hours=0.0001)

        time.sleep(0.5)

        cleaned = cache.cleanup_expired()

        assert cleaned >= 1
        assert cache.get("test", "valid") == {"value": 1}
        assert cache.get("test", "expired") is None


class TestBuildCacheKey:
    """Tests for cache key building."""

    def test_basic_key(self):
        """Test basic key building."""
        key = build_cache_key(platform="SNES", title="Mario")
        assert "platform=SNES" in key
        assert "title=Mario" in key

    def test_consistent_ordering(self):
        """Test keys are consistently ordered."""
        key1 = build_cache_key(b="2", a="1", c="3")
        key2 = build_cache_key(c="3", a="1", b="2")
        assert key1 == key2

    def test_skips_none_values(self):
        """Test None values are skipped."""
        key = build_cache_key(a="1", b=None, c="3")
        assert "b=" not in key
        assert "a=1" in key
        assert "c=3" in key
