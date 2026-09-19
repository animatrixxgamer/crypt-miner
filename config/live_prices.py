"""
Live crypto prices (USD) from CoinGecko — used so proofs/amounts sent to the
group correlate with the real current market price.

- Prices are cached ~90s and refreshed lazily on demand (proof sends, price
  screens) so a slow/unreachable API never blocks the bot.
- If fetching fails (no internet, rate limit), callers fall back to the
  admin-set static prices in COIN_PRICES.
"""
import asyncio
import logging
import time

logger = logging.getLogger(__name__)

# Our coin names -> CoinGecko ids
COINGECKO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "USDT": "tether",
    "SOL": "solana",
    "DOGE": "dogecoin",
    "LTC": "litecoin",
    "BCH": "bitcoin-cash",
    "DASH": "dash",
    "TRX": "tron",
    "BNB": "binancecoin",
    "XRP": "ripple",
    "TON": "the-open-network",
    "ADA": "cardano",
    "AVAX": "avalanche-2",
}

_live = {}          # coin -> float usd
_last_fetch = 0.0   # epoch seconds
_lock = asyncio.Lock()
_FRESH_SECONDS = 90
_TIMEOUT = 12


def live_price(coin: str):
    """Current live price if cached, else None (caller falls back)."""
    return _live.get(str(coin).upper())


def last_refresh_age() -> float:
    return time.time() - _last_fetch


async def refresh_live_prices(force: bool = False) -> dict:
    """Fetch USD prices for every supported coin. Safe to call often —
    throttles itself and never raises (returns whatever we have)."""
    global _last_fetch
    async with _lock:
        if not force and time.time() - _last_fetch < _FRESH_SECONDS and _live:
            return dict(_live)
        try:
            import httpx
            ids = ",".join(sorted(set(COINGECKO_IDS.values())))
            url = "https://api.coingecko.com/api/v3/simple/price"
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                r = await client.get(url, params={"ids": ids, "vs_currencies": "usd"})
                if r.status_code == 200:
                    data = r.json()
                    rev = {v: k for k, v in COINGECKO_IDS.items()}
                    got = 0
                    for cid, v in data.items():
                        coin = rev.get(cid)
                        if coin and isinstance(v, dict):
                            usd = v.get("usd")
                            if isinstance(usd, (int, float)) and usd > 0:
                                _live[coin] = float(usd)
                                got += 1
                    _last_fetch = time.time()
                    logger.info(f"Live prices refreshed: {got} coins")
                else:
                    logger.warning(f"CoinGecko returned HTTP {r.status_code}")
        except Exception as e:
            logger.warning(f"Live price fetch failed (using fallback prices): {e}")
        return dict(_live)
