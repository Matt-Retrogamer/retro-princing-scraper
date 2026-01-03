"""Currency conversion with caching and fallback rates."""

import httpx
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from price_enricher.cache import PriceCache, CACHE_NS_FX, TTL_FX, build_cache_key


# Fallback rates (EUR base) - updated periodically
# These are used if the API is unavailable
FALLBACK_RATES: dict[str, Decimal] = {
    "EUR": Decimal("1.0"),
    "USD": Decimal("0.92"),  # 1 USD = 0.92 EUR
    "GBP": Decimal("1.17"),  # 1 GBP = 1.17 EUR
    "JPY": Decimal("0.0061"),  # 1 JPY = 0.0061 EUR
    "CHF": Decimal("1.05"),  # 1 CHF = 1.05 EUR
    "CAD": Decimal("0.68"),  # 1 CAD = 0.68 EUR
    "AUD": Decimal("0.60"),  # 1 AUD = 0.60 EUR
    "SEK": Decimal("0.087"),  # 1 SEK = 0.087 EUR
    "NOK": Decimal("0.084"),  # 1 NOK = 0.084 EUR
    "DKK": Decimal("0.13"),  # 1 DKK = 0.13 EUR
    "PLN": Decimal("0.23"),  # 1 PLN = 0.23 EUR
    "CZK": Decimal("0.040"),  # 1 CZK = 0.040 EUR
}

# Free FX API endpoints (in order of preference)
FX_API_ENDPOINTS = [
    # exchangerate.host (no key required)
    "https://api.exchangerate.host/latest?base=EUR",
    # Open Exchange Rates (free tier, EUR base)
    "https://open.er-api.com/v6/latest/EUR",
]


class FXConverter:
    """
    Currency converter with caching and fallback rates.

    Uses free APIs to fetch current rates, with fallback to
    hardcoded rates if APIs are unavailable.
    """

    def __init__(self, cache: PriceCache | None = None):
        """Initialize converter with optional cache."""
        self.cache = cache
        self._rates: dict[str, Decimal] = {}
        self._rates_loaded = False

    async def _fetch_rates(self) -> dict[str, Decimal] | None:
        """Fetch current FX rates from API."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            for endpoint in FX_API_ENDPOINTS:
                try:
                    response = await client.get(endpoint)
                    response.raise_for_status()
                    data = response.json()

                    # Parse rates (handle different API response formats)
                    rates = self._parse_rates(data)
                    if rates:
                        return rates

                except (httpx.HTTPError, KeyError, ValueError) as e:
                    # Try next endpoint
                    continue

        return None

    def _parse_rates(self, data: dict[str, Any]) -> dict[str, Decimal] | None:
        """Parse FX rates from API response."""
        # Try different response formats
        rates_data = data.get("rates") or data.get("data") or {}

        if not rates_data:
            return None

        # Convert to Decimal
        rates = {"EUR": Decimal("1.0")}  # Base currency

        for currency, rate in rates_data.items():
            try:
                # These APIs return rates FROM EUR, so 1 EUR = X other
                # We need to invert: 1 other = ? EUR
                if rate and float(rate) > 0:
                    rates[currency] = Decimal("1") / Decimal(str(rate))
            except (ValueError, InvalidOperation):
                continue

        return rates if len(rates) > 1 else None

    async def _ensure_rates_loaded(self) -> None:
        """Ensure FX rates are loaded (from cache, API, or fallback)."""
        if self._rates_loaded:
            return

        # Try cache first
        if self.cache:
            cache_key = build_cache_key(type="fx_rates", base="EUR")
            cached = self.cache.get(CACHE_NS_FX, cache_key)
            if cached:
                self._rates = {k: Decimal(str(v)) for k, v in cached.items()}
                self._rates_loaded = True
                return

        # Fetch from API
        rates = await self._fetch_rates()

        if rates:
            self._rates = rates
            # Cache the rates
            if self.cache:
                cache_key = build_cache_key(type="fx_rates", base="EUR")
                # Convert Decimal to float for JSON serialization
                cacheable = {k: float(v) for k, v in rates.items()}
                self.cache.set(CACHE_NS_FX, cache_key, cacheable, ttl_hours=TTL_FX)
        else:
            # Use fallback rates
            self._rates = FALLBACK_RATES.copy()

        self._rates_loaded = True

    async def convert(
        self,
        amount: Decimal,
        from_currency: str,
        to_currency: str = "EUR",
    ) -> Decimal:
        """
        Convert amount from one currency to another.

        Args:
            amount: Amount to convert
            from_currency: Source currency code (e.g., 'USD', 'GBP')
            to_currency: Target currency code (default 'EUR')

        Returns:
            Converted amount
        """
        await self._ensure_rates_loaded()

        from_currency = from_currency.upper()
        to_currency = to_currency.upper()

        # Same currency, no conversion needed
        if from_currency == to_currency:
            return amount

        # Get rate to EUR
        if from_currency not in self._rates:
            # Try fallback
            rate = FALLBACK_RATES.get(from_currency)
            if not rate:
                raise ValueError(f"Unknown currency: {from_currency}")
        else:
            rate = self._rates[from_currency]

        # Convert to EUR
        eur_amount = amount * rate

        # If target is EUR, we're done
        if to_currency == "EUR":
            return eur_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # Convert from EUR to target currency
        if to_currency not in self._rates:
            raise ValueError(f"Unknown currency: {to_currency}")

        # Invert rate (rates are stored as X -> EUR, we need EUR -> X)
        to_rate = Decimal("1") / self._rates[to_currency]
        final_amount = eur_amount * to_rate

        return final_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    async def convert_to_eur(self, amount: Decimal, from_currency: str) -> Decimal:
        """Convenience method to convert to EUR."""
        return await self.convert(amount, from_currency, "EUR")

    def get_available_currencies(self) -> list[str]:
        """Get list of available currencies."""
        if not self._rates_loaded:
            return list(FALLBACK_RATES.keys())
        return list(self._rates.keys())


# Handle import for Decimal InvalidOperation
from decimal import InvalidOperation


def normalize_currency_code(code: str) -> str:
    """
    Normalize currency code to standard format.

    Handles common variations:
    - $ -> USD
    - £ -> GBP
    - € -> EUR
    - ¥ -> JPY
    """
    code = code.strip().upper()

    symbol_map = {
        "$": "USD",
        "£": "GBP",
        "€": "EUR",
        "¥": "JPY",
        "US$": "USD",
        "US DOLLAR": "USD",
        "DOLLAR": "USD",
        "EURO": "EUR",
        "POUND": "GBP",
        "YEN": "JPY",
    }

    return symbol_map.get(code, code)
