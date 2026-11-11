import asyncio
import re

import time
import aiohttp

from flask import render_template

API_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

_cache: dict[str, tuple[float, dict]] = {}
TTL = 120

def format_usd(amount):
    return "${:,.2f}".format(amount)

async def lookup(
    symbol: str, session: aiohttp.ClientSession | None = None
) -> dict | None:
    if session is None:
        async with aiohttp.ClientSession() as session:
            return await lookup(symbol, session)

    symbol = symbol.strip().upper()

    if not re.fullmatch(r"[A-Z0-9.\-]{1,10}", symbol):
        return None

    try:
        async with session.get(
            API_URL.format(symbol=symbol),
            params={"interval": "1d", "range": "1d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=aiohttp.ClientTimeout(total=5),
        ) as response:
            if response.status != 200:
                return None

            data = await response.json(content_type=None)

        meta = data["chart"]["result"][0]["meta"]

        return {
            "name": meta.get("longName") or meta.get("shortName") or symbol,
            "price": float(meta["regularMarketPrice"]),
            "symbol": meta["symbol"],
        }
    except (
        aiohttp.ClientError,
        asyncio.TimeoutError,
        KeyError,
        IndexError,
        TypeError,
        ValueError,
    ):
        return None


# +- 10ms, в 35 раз быстрее
async def lookup_many(symbols: list[str]) -> list[dict | None]:
    now = time.monotonic()
    missing = [s for s in set(symbols) if s not in _cache or now - _cache[s][0] > 60]

    if missing:
        async with aiohttp.ClientSession() as session:
            results = await asyncio.gather(*(lookup(s, session) for s in missing))
            
        for s, quote in zip(missing, results):
            if quote:
                _cache[s] = (now, quote)

    return [_cache[s][1] if s in _cache else None for s in symbols]

# +- 350ms
# async def lookup_many(symbols: list[str]) -> list[dict | None]:
#     async with aiohttp.ClientSession() as session:
#         return await asyncio.gather(*(lookup(symbol, session) for symbol in symbols))


def error(template: str, message: str, **context):
    return render_template(template, error=message, **context), 400


if __name__ == "__main__":
    assert format_usd(1234567.89) == "$1,234,567.89"
    assert format_usd(1000) == "$1,000.00"
    assert format_usd(99.9) == "$99.90"
    assert format_usd(0) == "$0.00"
