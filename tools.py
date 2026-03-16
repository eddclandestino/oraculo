"""
ORÁCULO Market Intelligence Tools — Production Implementation
=============================================================
Function calling tools for the Gemini Live API session.

Architecture:
- TOOL_DECLARATIONS: Sent to Gemini at session setup. Gemini reads the name,
  description, and parameter schema to decide when to call each tool.
- TOOL_FUNCTIONS: Maps tool names to async Python functions. The backend
  calls these when Gemini sends a tool_call message.

Data Sources:
- Alpha Vantage (primary): GLOBAL_QUOTE, NEWS_SENTIMENT, RSI, MACD, SMA, BBANDS, ATR
- yfinance (fallback + options): ticker.history(), ticker.option_chain()

Error Handling Strategy:
- Every function returns a dict, NEVER raises an exception to the caller
- On API failure: try fallback source -> if both fail, return an error dict
  with a human-readable message that Gemini can speak naturally
- On rate limit: return cached data if available, else graceful error
- On invalid symbol: return clear error message

Voice-Friendly Output:
- All numeric values are pre-formatted strings ("$582.34", "+2.15%", "1.2M")
- Interpretive labels included ("Overbought", "Bullish crossover", etc.)
- No raw arrays — top-N items only, pre-formatted
- Response dicts are flat (no nesting deeper than 1 level) except for small lists
"""

import asyncio
import logging
import math
import time
from typing import Optional

import httpx
import numpy as np

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# CACHING LAYER
# ═══════════════════════════════════════════════════════════════

_cache: dict[str, tuple[float, dict]] = {}
CACHE_TTL = 60  # seconds


def _get_cached(key: str) -> Optional[dict]:
    """Return cached result if fresh, else None."""
    if key in _cache:
        ts, data = _cache[key]
        if time.time() - ts < CACHE_TTL:
            logger.debug(f"Cache hit: {key}")
            return data
        del _cache[key]
    return None


def _set_cached(key: str, data: dict) -> None:
    """Store result in cache."""
    _cache[key] = (time.time(), data)


# ═══════════════════════════════════════════════════════════════
# SHARED HTTP CLIENT
# ═══════════════════════════════════════════════════════════════

_http_client: Optional[httpx.AsyncClient] = None


def _get_http_client() -> httpx.AsyncClient:
    """Lazy-initialized shared async HTTP client with connection pooling."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _http_client


def _get_av_key() -> str:
    """Lazy-load Alpha Vantage API key to avoid circular imports."""
    from config import ALPHA_VANTAGE_API_KEY
    return ALPHA_VANTAGE_API_KEY or ""


# ═══════════════════════════════════════════════════════════════
# HELPER: Alpha Vantage error detection
# ═══════════════════════════════════════════════════════════════

def _av_response_ok(data: dict) -> bool:
    """
    Alpha Vantage returns 200 OK even on errors. Actual errors are
    indicated by top-level keys: "Error Message", "Note" (rate limit),
    or "Information" (invalid key / endpoint).
    """
    if "Error Message" in data:
        logger.warning(f"AV error: {data['Error Message']}")
        return False
    if "Note" in data:
        logger.warning(f"AV rate limit: {data['Note']}")
        return False
    if "Information" in data:
        logger.warning(f"AV info/error: {data['Information']}")
        return False
    return True


# ═══════════════════════════════════════════════════════════════
# HELPER: Voice-friendly number formatting
# ═══════════════════════════════════════════════════════════════

def _fmt_price(val) -> str:
    """Format a price value: $582.34"""
    try:
        return f"${float(val):,.2f}"
    except (ValueError, TypeError):
        return str(val)


def _fmt_pct(val) -> str:
    """Format a percentage: +2.15%"""
    try:
        v = float(str(val).replace("%", ""))
        sign = "+" if v > 0 else ""
        return f"{sign}{v:.2f}%"
    except (ValueError, TypeError):
        return str(val)


def _fmt_vol(val) -> str:
    """Format volume with K/M/B suffixes for voice readability."""
    try:
        v = float(str(val).replace(",", ""))
        if v >= 1_000_000_000:
            return f"{v / 1_000_000_000:.1f}B"
        elif v >= 1_000_000:
            return f"{v / 1_000_000:.1f}M"
        elif v >= 1_000:
            return f"{v / 1_000:.0f}K"
        else:
            return f"{int(v):,}"
    except (ValueError, TypeError):
        return str(val)


def _fmt_num(val, decimals=2) -> str:
    """Format a generic number."""
    try:
        return f"{float(val):,.{decimals}f}"
    except (ValueError, TypeError):
        return str(val)


def _safe_float(val, default=0.0) -> float:
    """Safely convert to float, handling NaN and None."""
    try:
        v = float(val)
        return default if math.isnan(v) else v
    except (ValueError, TypeError):
        return default


# ═══════════════════════════════════════════════════════════════
# TOOL DECLARATIONS (sent to Gemini at session config)
# ═══════════════════════════════════════════════════════════════

TOOL_DECLARATIONS = [
    {
        "name": "get_stock_quote",
        "description": (
            "Gets the current stock quote including price, change, change percent, "
            "volume, and day range for a given ticker symbol. Use this when the user "
            "asks about a stock price, or when you need to verify a price level you "
            "see on their screen. Works for stocks, ETFs, and indices."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "symbol": {
                    "type": "STRING",
                    "description": (
                        "The stock ticker symbol. Examples: 'SPY', 'AAPL', 'TSLA', "
                        "'QQQ', 'NVDA', 'MSFT', 'AMZN', 'META', 'GOOGL'"
                    ),
                }
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_market_news",
        "description": (
            "Gets the latest financial news headlines with brief summaries and "
            "sentiment scores. Use this when the user asks what is moving the "
            "market, or when you observe a sudden price move on their screen and "
            "want to explain why. Can search by ticker or by topic."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": (
                        "Search query. Can be a ticker symbol like 'AAPL' for "
                        "company-specific news, or a topic like 'Federal Reserve', "
                        "'tech earnings', 'oil prices', 'inflation data'."
                    ),
                },
                "limit": {
                    "type": "INTEGER",
                    "description": "Number of articles to return. Default 5, max 10.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_technical_indicators",
        "description": (
            "Gets key technical analysis indicators for a stock: RSI (14), MACD, "
            "SMA (20, 50, 200), Bollinger Bands, and ATR. Includes interpretive "
            "labels like 'Overbought' or 'Bullish crossover'. Use this when the "
            "user asks about technical conditions, or to confirm patterns you see "
            "on their chart."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "symbol": {
                    "type": "STRING",
                    "description": "The stock ticker symbol.",
                }
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_options_snapshot",
        "description": (
            "Gets options market data for a stock: put/call open interest ratio, "
            "max pain price, highest open interest strikes for calls and puts, "
            "and average implied volatility. Use this when the user asks about "
            "options positioning, dealer exposure, or when you see an options "
            "chain displayed on their screen."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "symbol": {
                    "type": "STRING",
                    "description": "The stock ticker symbol.",
                }
            },
            "required": ["symbol"],
        },
    },
]


# ═══════════════════════════════════════════════════════════════
# TOOL 1: get_stock_quote
# ═══════════════════════════════════════════════════════════════

async def get_stock_quote(symbol: str) -> dict:
    """
    Fetch current stock quote. Tries Alpha Vantage GLOBAL_QUOTE first,
    falls back to yfinance if AV fails or rate-limits.
    """
    symbol = symbol.upper().strip()
    cache_key = f"quote:{symbol}"

    cached = _get_cached(cache_key)
    if cached:
        return cached

    # ── Attempt 1: Alpha Vantage GLOBAL_QUOTE ──
    av_key = _get_av_key()
    if av_key:
        try:
            client = _get_http_client()
            resp = await client.get(
                "https://www.alphavantage.co/query",
                params={
                    "function": "GLOBAL_QUOTE",
                    "symbol": symbol,
                    "apikey": av_key,
                },
            )
            data = resp.json()

            if _av_response_ok(data) and "Global Quote" in data:
                gq = data["Global Quote"]
                if gq and "05. price" in gq:
                    result = {
                        "symbol": symbol,
                        "price": _fmt_price(gq["05. price"]),
                        "change": _fmt_price(gq["09. change"]).replace("$", ""),
                        "change_percent": _fmt_pct(gq["10. change percent"]),
                        "volume": _fmt_vol(gq["06. volume"]),
                        "day_high": _fmt_price(gq["03. high"]),
                        "day_low": _fmt_price(gq["04. low"]),
                        "previous_close": _fmt_price(gq["08. previous close"]),
                        "latest_trading_day": gq.get("07. latest trading day", "N/A"),
                        "source": "Alpha Vantage real-time",
                    }
                    _set_cached(cache_key, result)
                    return result
                else:
                    logger.warning(f"AV returned empty quote for {symbol}")
        except httpx.TimeoutException:
            logger.warning(f"AV timeout for GLOBAL_QUOTE {symbol}")
        except Exception as e:
            logger.warning(f"AV GLOBAL_QUOTE failed for {symbol}: {e}")

    # ── Attempt 2: yfinance fallback ──
    try:
        import yfinance as yf

        def _yf_quote():
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="5d")
            if hist.empty:
                return None

            latest = hist.iloc[-1]
            close = latest["Close"]
            prev_close = hist.iloc[-2]["Close"] if len(hist) >= 2 else close
            change = close - prev_close
            change_pct = (change / prev_close * 100) if prev_close != 0 else 0

            return {
                "symbol": symbol,
                "price": _fmt_price(close),
                "change": f"{'+' if change >= 0 else ''}{change:.2f}",
                "change_percent": _fmt_pct(change_pct),
                "volume": _fmt_vol(latest.get("Volume", 0)),
                "day_high": _fmt_price(latest.get("High", close)),
                "day_low": _fmt_price(latest.get("Low", close)),
                "previous_close": _fmt_price(prev_close),
                "latest_trading_day": str(hist.index[-1].date()),
                "source": "Yahoo Finance",
            }

        result = await asyncio.get_event_loop().run_in_executor(None, _yf_quote)
        if result:
            _set_cached(cache_key, result)
            return result
    except Exception as e:
        logger.error(f"yfinance quote fallback failed for {symbol}: {e}")

    return {
        "symbol": symbol,
        "error": (
            f"Unable to fetch quote for {symbol}. Both Alpha Vantage and Yahoo "
            "Finance are currently unavailable. The symbol may be invalid, or "
            "the APIs may be experiencing issues."
        ),
    }


# ═══════════════════════════════════════════════════════════════
# TOOL 2: get_market_news
# ═══════════════════════════════════════════════════════════════

async def get_market_news(query: str, limit: int = 5) -> dict:
    """
    Fetch market news from Alpha Vantage NEWS_SENTIMENT endpoint.
    Returns pre-formatted articles optimized for voice delivery.
    """
    query = query.strip()
    limit = max(1, min(int(limit) if limit else 5, 10))
    cache_key = f"news:{query}:{limit}"

    cached = _get_cached(cache_key)
    if cached:
        return cached

    av_key = _get_av_key()
    if not av_key:
        return {
            "query": query,
            "count": 0,
            "articles": [],
            "note": "News API key not configured. I can still analyze what I see on your screen.",
        }

    try:
        client = _get_http_client()

        # Determine if query is a ticker or a topic
        is_ticker = len(query) <= 5 and query.replace(".", "").isalpha()

        params = {
            "function": "NEWS_SENTIMENT",
            "apikey": av_key,
            "limit": min(limit * 2, 50),
            "sort": "LATEST",
        }
        if is_ticker:
            params["tickers"] = query.upper()
        else:
            params["topics"] = query

        resp = await client.get("https://www.alphavantage.co/query", params=params)
        data = resp.json()

        if not _av_response_ok(data):
            return {
                "query": query,
                "count": 0,
                "articles": [],
                "note": "News service is temporarily rate-limited. Try again in a minute.",
            }

        feed = data.get("feed", [])
        if not feed:
            return {
                "query": query,
                "count": 0,
                "articles": [],
                "note": f"No recent news articles found for '{query}'.",
            }

        # Process and format articles for voice
        articles = []
        for item in feed[:limit]:
            title = item.get("title", "Untitled")
            if len(title) > 120:
                title = title[:117] + "..."

            summary = item.get("summary", "")
            if len(summary) > 200:
                cut = summary[:200].rfind(".")
                summary = summary[:cut + 1] if cut > 100 else summary[:197] + "..."

            source = item.get("source", "Unknown")
            sentiment = item.get("overall_sentiment_label", "Neutral")
            time_pub = item.get("time_published", "")

            # Format time for voice: "20250312T143000" -> "2:30 PM"
            time_display = ""
            if time_pub and len(time_pub) >= 13:
                try:
                    hour = int(time_pub[9:11])
                    minute = time_pub[11:13]
                    ampm = "AM" if hour < 12 else "PM"
                    hour_12 = hour if hour <= 12 else hour - 12
                    hour_12 = 12 if hour_12 == 0 else hour_12
                    time_display = f"{hour_12}:{minute} {ampm}"
                except (ValueError, IndexError):
                    pass

            # Ticker relevance scores
            relevance = ""
            ticker_sentiments = item.get("ticker_sentiment", [])
            for ts in ticker_sentiments:
                if ts.get("ticker", "").upper() == query.upper():
                    score = _safe_float(ts.get("ticker_sentiment_score", 0))
                    if score > 0.15:
                        relevance = "Positive for " + query.upper()
                    elif score < -0.15:
                        relevance = "Negative for " + query.upper()
                    else:
                        relevance = "Neutral for " + query.upper()
                    break

            article = {
                "headline": title,
                "source": source,
                "summary": summary,
                "sentiment": sentiment,
            }
            if time_display:
                article["published"] = time_display
            if relevance:
                article["ticker_relevance"] = relevance

            articles.append(article)

        result = {
            "query": query,
            "count": len(articles),
            "articles": articles,
        }
        _set_cached(cache_key, result)
        return result

    except httpx.TimeoutException:
        logger.warning(f"News API timeout for query: {query}")
        return {
            "query": query,
            "count": 0,
            "articles": [],
            "note": "News service timed out. I can still analyze what I see on your screen.",
        }
    except Exception as e:
        logger.error(f"News fetch failed for '{query}': {e}")
        return {
            "query": query,
            "count": 0,
            "articles": [],
            "note": "News service encountered an error. Try asking again in a moment.",
        }


# ═══════════════════════════════════════════════════════════════
# TOOL 3: get_technical_indicators
# ═══════════════════════════════════════════════════════════════

async def get_technical_indicators(symbol: str) -> dict:
    """
    Fetch key technical indicators. Tries Alpha Vantage pre-computed
    endpoints first (RSI, MACD, SMA, BBANDS, ATR), falls back to
    yfinance + numpy local calculation.
    """
    symbol = symbol.upper().strip()
    cache_key = f"tech:{symbol}"

    cached = _get_cached(cache_key)
    if cached:
        return cached

    # ── Attempt 1: Alpha Vantage pre-computed indicators ──
    av_key = _get_av_key()
    if av_key:
        try:
            result = await _fetch_av_indicators(symbol, av_key)
            if result and "error" not in result:
                _set_cached(cache_key, result)
                return result
        except Exception as e:
            logger.warning(f"AV indicators failed for {symbol}: {e}")

    # ── Attempt 2: yfinance + local calculation ──
    try:
        result = await _calc_indicators_yfinance(symbol)
        if result and "error" not in result:
            _set_cached(cache_key, result)
            return result
        elif result:
            return result  # Return error dict from yfinance
    except Exception as e:
        logger.error(f"yfinance indicators failed for {symbol}: {e}")

    return {
        "symbol": symbol,
        "error": (
            f"Unable to calculate technical indicators for {symbol}. "
            "Both data sources are currently unavailable."
        ),
    }


async def _fetch_av_indicators(symbol: str, av_key: str) -> Optional[dict]:
    """
    Fetch RSI, MACD, SMA(20, 50, 200), BBANDS, ATR from Alpha Vantage
    in parallel using asyncio.gather for ~200ms total vs ~1.4s serial.
    """
    client = _get_http_client()
    base_params = {"symbol": symbol, "apikey": av_key, "interval": "daily"}

    async def _fetch_one(function: str, extra_params: dict = None) -> Optional[dict]:
        params = {**base_params, "function": function}
        if extra_params:
            params.update(extra_params)
        try:
            resp = await client.get("https://www.alphavantage.co/query", params=params)
            data = resp.json()
            if _av_response_ok(data):
                return data
        except Exception as e:
            logger.debug(f"AV {function} failed: {e}")
        return None

    # Fire all 7 requests in parallel
    (rsi_data, macd_data, sma20_data, sma50_data,
     sma200_data, bbands_data, atr_data) = await asyncio.gather(
        _fetch_one("RSI", {"time_period": "14", "series_type": "close"}),
        _fetch_one("MACD", {"series_type": "close"}),
        _fetch_one("SMA", {"time_period": "20", "series_type": "close"}),
        _fetch_one("SMA", {"time_period": "50", "series_type": "close"}),
        _fetch_one("SMA", {"time_period": "200", "series_type": "close"}),
        _fetch_one("BBANDS", {"time_period": "20", "series_type": "close"}),
        _fetch_one("ATR", {"time_period": "14"}),
    )

    # Need at least RSI to consider this a success
    if not rsi_data:
        return None

    def _latest_val(data: Optional[dict], value_key: str) -> Optional[str]:
        """Extract the most recent value from an AV technical indicator response."""
        if not data:
            return None
        for top_key in data:
            if top_key.startswith("Technical Analysis"):
                series = data[top_key]
                if series:
                    latest_date = next(iter(series))
                    return series[latest_date].get(value_key)
        return None

    # Extract latest values
    rsi_val = _safe_float(_latest_val(rsi_data, "RSI"))
    macd_val = _safe_float(_latest_val(macd_data, "MACD"))
    macd_signal = _safe_float(_latest_val(macd_data, "MACD_Signal"))
    macd_hist = _safe_float(_latest_val(macd_data, "MACD_Hist"))
    sma20 = _safe_float(_latest_val(sma20_data, "SMA"))
    sma50 = _safe_float(_latest_val(sma50_data, "SMA"))
    sma200 = _safe_float(_latest_val(sma200_data, "SMA"))
    bb_upper = _safe_float(_latest_val(bbands_data, "Real Upper Band"))
    bb_lower = _safe_float(_latest_val(bbands_data, "Real Lower Band"))
    atr_val = _safe_float(_latest_val(atr_data, "ATR"))

    # Try to get current price from cached quote
    current_price = sma20  # Approximate if no quote cached
    quote_cache = _get_cached(f"quote:{symbol}")
    if quote_cache:
        try:
            current_price = float(
                quote_cache.get("price", "0").replace("$", "").replace(",", "")
            )
        except (ValueError, TypeError):
            pass

    # Build interpretations
    rsi_interp = (
        "Overbought — could see pullback" if rsi_val > 70 else
        "Oversold — could see bounce" if rsi_val < 30 else
        "Slightly elevated" if rsi_val > 60 else
        "Slightly depressed" if rsi_val < 40 else
        "Neutral range"
    )

    macd_interp = (
        "Near crossover — watch closely" if abs(macd_hist) < 0.1 else
        "Bullish — MACD above signal line" if macd_hist > 0 else
        "Bearish — MACD below signal line"
    )

    # Bollinger Band position
    bb_position = ""
    if bb_upper > 0 and bb_lower > 0 and current_price > 0:
        bb_range = bb_upper - bb_lower
        if bb_range > 0:
            pct = (current_price - bb_lower) / bb_range * 100
            bb_position = (
                "At upper band — extended" if pct > 95 else
                "Near upper band — elevated" if pct > 80 else
                "At lower band — compressed" if pct < 5 else
                "Near lower band — depressed" if pct < 20 else
                f"{pct:.0f}% between lower and upper band"
            )

    # SMA alignment
    sma_trend = ""
    if sma20 > 0 and sma50 > 0 and sma200 > 0:
        if sma20 > sma50 > sma200:
            sma_trend = "Bullish alignment — 20 above 50 above 200"
        elif sma20 < sma50 < sma200:
            sma_trend = "Bearish alignment — 20 below 50 below 200"
        elif sma20 > sma50 and sma50 < sma200:
            sma_trend = "Mixed — short-term recovery within longer-term downtrend"
        else:
            sma_trend = "Mixed — no clear trend alignment"

    return {
        "symbol": symbol,
        "rsi_14": _fmt_num(rsi_val, 1),
        "rsi_interpretation": rsi_interp,
        "macd": _fmt_num(macd_val, 3),
        "macd_signal": _fmt_num(macd_signal, 3),
        "macd_histogram": _fmt_num(macd_hist, 3),
        "macd_interpretation": macd_interp,
        "sma_20": _fmt_price(sma20),
        "sma_50": _fmt_price(sma50),
        "sma_200": _fmt_price(sma200) if sma200 > 0 else "N/A",
        "sma_trend": sma_trend,
        "bollinger_upper": _fmt_price(bb_upper),
        "bollinger_lower": _fmt_price(bb_lower),
        "bollinger_position": bb_position,
        "atr_14": _fmt_num(atr_val, 2),
        "atr_interpretation": f"Average daily range of about {_fmt_price(atr_val)}",
        "source": "Alpha Vantage pre-computed indicators",
    }


async def _calc_indicators_yfinance(symbol: str) -> Optional[dict]:
    """
    Calculate technical indicators locally using yfinance historical data
    and numpy. Fallback when Alpha Vantage is unavailable.
    """
    import yfinance as yf

    def _calculate():
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="1y")

        if hist.empty:
            return {"error": f"No historical data found for {symbol}. The symbol may be invalid."}
        if len(hist) < 50:
            return {"error": f"Insufficient historical data for {symbol} — need at least 50 days, got {len(hist)}."}

        close = hist["Close"].values.astype(float)
        high = hist["High"].values.astype(float)
        low = hist["Low"].values.astype(float)
        current_price = close[-1]

        # RSI (14-period, Wilder's smoothing)
        deltas = np.diff(close)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gains[:14])
        avg_loss = np.mean(losses[:14])
        for i in range(14, len(gains)):
            avg_gain = (avg_gain * 13 + gains[i]) / 14
            avg_loss = (avg_loss * 13 + losses[i]) / 14
        rs = avg_gain / avg_loss if avg_loss > 0 else 100
        rsi_val = 100 - (100 / (1 + rs))

        # MACD (12, 26, 9)
        def _ema(data, span):
            alpha = 2 / (span + 1)
            result = np.zeros_like(data)
            result[0] = data[0]
            for i in range(1, len(data)):
                result[i] = alpha * data[i] + (1 - alpha) * result[i - 1]
            return result

        ema12 = _ema(close, 12)
        ema26 = _ema(close, 26)
        macd_line = ema12 - ema26
        signal_line = _ema(macd_line, 9)
        macd_hist = macd_line - signal_line

        # SMAs
        sma20 = float(np.mean(close[-20:]))
        sma50 = float(np.mean(close[-50:]))
        sma200 = float(np.mean(close[-200:])) if len(close) >= 200 else 0.0

        # Bollinger Bands (20-period, 2 std)
        bb_std = float(np.std(close[-20:]))
        bb_upper = sma20 + 2 * bb_std
        bb_lower = sma20 - 2 * bb_std

        # ATR (14-period)
        tr_values = []
        for i in range(1, len(close)):
            tr = max(
                high[i] - low[i],
                abs(high[i] - close[i - 1]),
                abs(low[i] - close[i - 1]),
            )
            tr_values.append(tr)
        atr_val = float(np.mean(tr_values[-14:])) if len(tr_values) >= 14 else 0.0

        # Interpretations
        rsi_interp = (
            "Overbought — could see pullback" if rsi_val > 70 else
            "Oversold — could see bounce" if rsi_val < 30 else
            "Slightly elevated" if rsi_val > 60 else
            "Slightly depressed" if rsi_val < 40 else
            "Neutral range"
        )

        macd_hist_val = float(macd_hist[-1])
        macd_interp = (
            "Near crossover — watch closely" if abs(macd_hist_val) < 0.1 else
            "Bullish — MACD above signal line" if macd_hist_val > 0 else
            "Bearish — MACD below signal line"
        )

        bb_range = bb_upper - bb_lower
        bb_pct = ((current_price - bb_lower) / bb_range * 100) if bb_range > 0 else 50
        bb_position = (
            "At upper band — extended" if bb_pct > 95 else
            "Near upper band" if bb_pct > 80 else
            "At lower band — compressed" if bb_pct < 5 else
            "Near lower band" if bb_pct < 20 else
            f"{bb_pct:.0f}% between bands"
        )

        if sma200 > 0:
            if sma20 > sma50 > sma200:
                sma_trend = "Bullish alignment — 20 above 50 above 200"
            elif sma20 < sma50 < sma200:
                sma_trend = "Bearish alignment — 20 below 50 below 200"
            else:
                sma_trend = "Mixed — no clear trend alignment"
        elif sma20 > sma50:
            sma_trend = "Short-term bullish — 20 above 50"
        else:
            sma_trend = "Short-term bearish — 20 below 50"

        return {
            "symbol": symbol,
            "current_price": _fmt_price(current_price),
            "rsi_14": _fmt_num(rsi_val, 1),
            "rsi_interpretation": rsi_interp,
            "macd": _fmt_num(float(macd_line[-1]), 3),
            "macd_signal": _fmt_num(float(signal_line[-1]), 3),
            "macd_histogram": _fmt_num(macd_hist_val, 3),
            "macd_interpretation": macd_interp,
            "sma_20": _fmt_price(sma20),
            "sma_50": _fmt_price(sma50),
            "sma_200": _fmt_price(sma200) if sma200 > 0 else "N/A — insufficient data",
            "sma_trend": sma_trend,
            "bollinger_upper": _fmt_price(bb_upper),
            "bollinger_lower": _fmt_price(bb_lower),
            "bollinger_position": bb_position,
            "atr_14": _fmt_num(atr_val, 2),
            "atr_interpretation": f"Average daily range of about {_fmt_price(atr_val)}",
            "source": "Calculated from Yahoo Finance historical data",
        }

    return await asyncio.get_event_loop().run_in_executor(None, _calculate)


# ═══════════════════════════════════════════════════════════════
# TOOL 4: get_options_snapshot
# ═══════════════════════════════════════════════════════════════

async def get_options_snapshot(symbol: str) -> dict:
    """
    Fetch options market snapshot using yfinance (primary — AV options
    require premium). Calculates P/C ratio, max pain, top strikes, IV.
    """
    symbol = symbol.upper().strip()
    cache_key = f"options:{symbol}"

    cached = _get_cached(cache_key)
    if cached:
        return cached

    try:
        import yfinance as yf

        def _fetch():
            ticker = yf.Ticker(symbol)

            # Get current price
            hist = ticker.history(period="1d")
            if hist.empty:
                return {"error": f"No price data found for {symbol}. The symbol may be invalid."}
            current_price = float(hist["Close"].iloc[-1])

            # Get available expirations
            try:
                expirations = ticker.options
            except Exception:
                return {"error": f"No options data available for {symbol}. This may not be an optionable security."}

            if not expirations:
                return {"error": f"No options expirations found for {symbol}."}

            # Use nearest expiration
            nearest_exp = expirations[0]
            try:
                chain = ticker.option_chain(nearest_exp)
            except Exception as e:
                return {"error": f"Failed to fetch options chain for {symbol} ({nearest_exp}): {str(e)}"}

            calls = chain.calls
            puts = chain.puts

            if calls.empty and puts.empty:
                return {"error": f"Options chain is empty for {symbol} expiring {nearest_exp}."}

            # ── Put/Call OI Ratio ──
            total_call_oi = _safe_float(calls["openInterest"].sum()) if "openInterest" in calls.columns else 0
            total_put_oi = _safe_float(puts["openInterest"].sum()) if "openInterest" in puts.columns else 0
            pcr = total_put_oi / total_call_oi if total_call_oi > 0 else 0

            pcr_interp = (
                "Strong bearish sentiment" if pcr > 1.5 else
                "Moderately bearish" if pcr > 1.0 else
                "Neutral" if pcr > 0.7 else
                "Moderately bullish" if pcr > 0.5 else
                "Strong bullish sentiment"
            )

            # ── Max Pain ──
            max_pain = _calculate_max_pain(calls, puts)

            # ── Top strikes by OI ──
            top_call_strikes = []
            if not calls.empty and "openInterest" in calls.columns:
                top_calls = calls.nlargest(3, "openInterest")
                for _, row in top_calls.iterrows():
                    oi = _safe_float(row.get("openInterest", 0))
                    if oi > 0:
                        top_call_strikes.append(
                            f"{_fmt_price(row['strike'])} ({_fmt_vol(oi)} OI)"
                        )

            top_put_strikes = []
            if not puts.empty and "openInterest" in puts.columns:
                top_puts = puts.nlargest(3, "openInterest")
                for _, row in top_puts.iterrows():
                    oi = _safe_float(row.get("openInterest", 0))
                    if oi > 0:
                        top_put_strikes.append(
                            f"{_fmt_price(row['strike'])} ({_fmt_vol(oi)} OI)"
                        )

            # ── Implied Volatility ──
            avg_call_iv = 0.0
            avg_put_iv = 0.0
            if "impliedVolatility" in calls.columns:
                valid = calls["impliedVolatility"].dropna()
                valid = valid[valid > 0]
                if len(valid) > 0:
                    avg_call_iv = float(valid.mean())
            if "impliedVolatility" in puts.columns:
                valid = puts["impliedVolatility"].dropna()
                valid = valid[valid > 0]
                if len(valid) > 0:
                    avg_put_iv = float(valid.mean())

            # ── Total volume today ──
            total_call_vol = _safe_float(calls["volume"].sum()) if "volume" in calls.columns else 0
            total_put_vol = _safe_float(puts["volume"].sum()) if "volume" in puts.columns else 0

            result = {
                "symbol": symbol,
                "current_price": _fmt_price(current_price),
                "nearest_expiration": nearest_exp,
                "expirations_available": len(expirations),
                "put_call_ratio": f"{pcr:.2f}",
                "pcr_interpretation": pcr_interp,
                "total_call_open_interest": _fmt_vol(total_call_oi),
                "total_put_open_interest": _fmt_vol(total_put_oi),
                "total_call_volume_today": _fmt_vol(total_call_vol),
                "total_put_volume_today": _fmt_vol(total_put_vol),
                "max_pain_price": _fmt_price(max_pain) if max_pain else "Unable to calculate",
                "max_pain_vs_current": _describe_max_pain(max_pain, current_price) if max_pain else "",
                "top_call_strikes_by_oi": top_call_strikes if top_call_strikes else ["No significant call OI"],
                "top_put_strikes_by_oi": top_put_strikes if top_put_strikes else ["No significant put OI"],
                "avg_call_implied_volatility": f"{avg_call_iv * 100:.1f}%" if avg_call_iv > 0 else "N/A",
                "avg_put_implied_volatility": f"{avg_put_iv * 100:.1f}%" if avg_put_iv > 0 else "N/A",
                "source": "Yahoo Finance options data",
            }
            return result

        result = await asyncio.get_event_loop().run_in_executor(None, _fetch)
        if result and "error" not in result:
            _set_cached(cache_key, result)
        return result

    except Exception as e:
        logger.error(f"Options snapshot failed for {symbol}: {e}")
        return {
            "symbol": symbol,
            "error": f"Unable to fetch options data for {symbol}: {str(e)}",
        }


def _calculate_max_pain(calls_df, puts_df) -> Optional[float]:
    """
    Calculate max pain: the strike price at which the total dollar value
    of outstanding options would cause the maximum loss for option holders.
    """
    try:
        call_oi = {}
        put_oi = {}
        all_strikes = set()

        if not calls_df.empty and "openInterest" in calls_df.columns:
            for _, row in calls_df.iterrows():
                strike = float(row["strike"])
                oi = _safe_float(row.get("openInterest", 0))
                all_strikes.add(strike)
                call_oi[strike] = oi

        if not puts_df.empty and "openInterest" in puts_df.columns:
            for _, row in puts_df.iterrows():
                strike = float(row["strike"])
                oi = _safe_float(row.get("openInterest", 0))
                all_strikes.add(strike)
                put_oi[strike] = oi

        if not all_strikes:
            return None

        min_pain = float("inf")
        max_pain_strike = None

        for settle_price in sorted(all_strikes):
            total_pain = 0.0

            # Calls: ITM when settle > strike
            for strike, oi in call_oi.items():
                if settle_price > strike:
                    total_pain += (settle_price - strike) * oi * 100

            # Puts: ITM when settle < strike
            for strike, oi in put_oi.items():
                if settle_price < strike:
                    total_pain += (strike - settle_price) * oi * 100

            if total_pain < min_pain:
                min_pain = total_pain
                max_pain_strike = settle_price

        return max_pain_strike

    except Exception as e:
        logger.debug(f"Max pain calculation failed: {e}")
        return None


def _describe_max_pain(max_pain: float, current_price: float) -> str:
    """Describe max pain relative to current price in voice-friendly terms."""
    if not max_pain or not current_price:
        return ""
    diff = max_pain - current_price
    pct = (diff / current_price) * 100
    if abs(pct) < 0.5:
        return "Max pain is very close to current price — options are near equilibrium"
    elif diff > 0:
        return f"Max pain is {abs(diff):.2f} above current price — potential gravitational pull higher"
    else:
        return f"Max pain is {abs(diff):.2f} below current price — potential gravitational pull lower"


# ═══════════════════════════════════════════════════════════════
# TOOL DISPATCHER
# ═══════════════════════════════════════════════════════════════

TOOL_FUNCTIONS = {
    "get_stock_quote": get_stock_quote,
    "get_market_news": get_market_news,
    "get_technical_indicators": get_technical_indicators,
    "get_options_snapshot": get_options_snapshot,
}
