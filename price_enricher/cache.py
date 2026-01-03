"""SQLite-based caching for API responses."""

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Generator


class PriceCache:
    """
    SQLite-based cache for API responses.

    Supports:
    - TTL (time-to-live) for cache entries
    - Separate namespaces for different data types
    - Automatic cleanup of expired entries
    """

    DEFAULT_TTL_HOURS = 24 * 7  # 1 week default

    def __init__(self, db_path: Path | str = "cache.sqlite"):
        """Initialize cache with database path."""
        self.db_path = Path(db_path)
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    namespace TEXT NOT NULL,
                    key_hash TEXT NOT NULL,
                    key_raw TEXT NOT NULL,
                    value TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    hit_count INTEGER DEFAULT 0,
                    PRIMARY KEY (namespace, key_hash)
                )
            """)

            # Create index for expiration cleanup
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_expires_at
                ON cache (expires_at)
            """)

            conn.commit()

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Get database connection with context manager."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _hash_key(key: str) -> str:
        """Create hash of cache key."""
        return hashlib.sha256(key.encode()).hexdigest()

    def get(self, namespace: str, key: str) -> Any | None:
        """
        Get cached value if exists and not expired.

        Args:
            namespace: Cache namespace (e.g., 'ebay', 'rgp', 'fx')
            key: Cache key (will be hashed)

        Returns:
            Cached value or None if not found/expired
        """
        key_hash = self._hash_key(key)
        now = time.time()

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT value, expires_at
                FROM cache
                WHERE namespace = ? AND key_hash = ? AND expires_at > ?
                """,
                (namespace, key_hash, now),
            )
            row = cursor.fetchone()

            if row:
                # Update hit count
                conn.execute(
                    """
                    UPDATE cache SET hit_count = hit_count + 1
                    WHERE namespace = ? AND key_hash = ?
                    """,
                    (namespace, key_hash),
                )
                conn.commit()

                return json.loads(row["value"])

            return None

    def set(
        self,
        namespace: str,
        key: str,
        value: Any,
        ttl_hours: float | None = None,
    ) -> None:
        """
        Set cache value with TTL.

        Args:
            namespace: Cache namespace
            key: Cache key (will be hashed)
            value: Value to cache (must be JSON-serializable)
            ttl_hours: Time-to-live in hours (default: 1 week)
        """
        key_hash = self._hash_key(key)
        now = time.time()
        ttl = ttl_hours if ttl_hours is not None else self.DEFAULT_TTL_HOURS
        expires_at = now + (ttl * 3600)

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO cache
                (namespace, key_hash, key_raw, value, created_at, expires_at, hit_count)
                VALUES (?, ?, ?, ?, ?, ?, 0)
                """,
                (namespace, key_hash, key, json.dumps(value), now, expires_at),
            )
            conn.commit()

    def delete(self, namespace: str, key: str) -> bool:
        """Delete a specific cache entry."""
        key_hash = self._hash_key(key)

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                DELETE FROM cache
                WHERE namespace = ? AND key_hash = ?
                """,
                (namespace, key_hash),
            )
            conn.commit()
            return cursor.rowcount > 0

    def clear_namespace(self, namespace: str) -> int:
        """Clear all entries in a namespace."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                DELETE FROM cache WHERE namespace = ?
                """,
                (namespace,),
            )
            conn.commit()
            return cursor.rowcount

    def clear_all(self) -> int:
        """Clear entire cache."""
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM cache")
            conn.commit()
            return cursor.rowcount

    def cleanup_expired(self) -> int:
        """Remove all expired entries."""
        now = time.time()

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                DELETE FROM cache WHERE expires_at < ?
                """,
                (now,),
            )
            conn.commit()
            return cursor.rowcount

    def get_stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        with self._get_connection() as conn:
            # Total entries by namespace
            cursor = conn.execute("""
                SELECT namespace, COUNT(*) as count, SUM(hit_count) as hits
                FROM cache
                GROUP BY namespace
            """)
            namespaces = {row["namespace"]: {"count": row["count"], "hits": row["hits"]} for row in cursor.fetchall()}

            # Expired count
            now = time.time()
            cursor = conn.execute(
                """
                SELECT COUNT(*) as count FROM cache WHERE expires_at < ?
                """,
                (now,),
            )
            expired = cursor.fetchone()["count"]

            # Database size
            db_size = self.db_path.stat().st_size if self.db_path.exists() else 0

            return {
                "namespaces": namespaces,
                "expired_entries": expired,
                "db_size_bytes": db_size,
                "db_path": str(self.db_path),
            }


# Cache namespaces
CACHE_NS_EBAY = "ebay"
CACHE_NS_RGP = "rgp"
CACHE_NS_FX = "fx"

# TTL settings (in hours)
TTL_EBAY = 24 * 3  # 3 days for eBay (prices change)
TTL_RGP = 24 * 7  # 1 week for RetroGamePrices
TTL_FX = 24  # 1 day for FX rates


def build_cache_key(**kwargs: Any) -> str:
    """
    Build a cache key from keyword arguments.

    Sorts keys for consistent ordering.
    """
    sorted_items = sorted(kwargs.items())
    parts = [f"{k}={v}" for k, v in sorted_items if v is not None]
    return "|".join(parts)
